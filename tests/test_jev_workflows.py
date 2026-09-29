"""Workflow permission, source-race and advisory behavior; no provider calls."""
import contextlib
import io
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'app'))
from brain_store import BrainStore
import jev_workflows as workflows
from jev_workflow_cli import main


class JevWorkflowTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.brain = BrainStore(self.root)
        self.config = {'status': 'ready', 'key_present': True, 'authorized_projects': ['alpha'],
                       'purposes': [item['workflow'] for item in workflows.workflow_catalog()],
                       'min_confidence': .8}
        mocked = patch('jev_openrouter.load_config', return_value=self.config)
        mocked.start()
        self.addCleanup(mocked.stop)
        self.builder = patch('jev_workflows._builder', side_effect=self.build)
        self.builder.start()
        self.addCleanup(self.builder.stop)

    @staticmethod
    def build(workflow, payload):
        if workflow in ('skills', 'event'):
            fields = 'catalogue' if workflow == 'skills' else 'handlers'
            options = {item['id']: item['description'] for item in payload[fields]}
            options['none'] = 'No suitable choice.'
            return payload, {'judgment': {'type': 'choice', 'instructions': 'Choose one.', 'criteria': options}}
        if workflow == 'handoff':
            return payload, {'judgment': {'type': 'score', 'instructions': 'Score relevance.', 'criteria': ['Unrelated', 'Relevant']}}
        other = {'duplicates': ('duplicate', 'insufficient'), 'relations': ('related_to', 'none'),
                 'stale': ('applicable', 'insufficient'), 'durability': ('novel', 'insufficient'),
                 'recover': ('applicable', 'insufficient')}
        if workflow in other:
            return payload, {'judgment': {'type': 'choice', 'instructions': 'Review evidence.',
                                          'criteria': {k: k for k in other[workflow]}}}
        return payload, {'judgment': {'type': 'choice', 'instructions': 'Review supplied evidence.',
                                     'criteria': {'supported': 'Supported by evidence.',
                                                  'insufficient': 'More evidence is required.'}}}

    def memory(self, content='Retry policy uses bounded backoff.', **extra):
        candidate = dict(project_id='alpha', kind='fact', title='Retry policy', content=content,
                         source={'type': 'user', 'note': 'Synthetic test source.'})
        candidate.update(extra)
        item = self.brain.propose(candidate)
        return self.brain.approve(item['id'], 'Tester', 'Checked the complete synthetic source.')

    @staticmethod
    def response(choice='supported', confidence=1, **extra):
        return dict({'status': 'ok', 'answers': {'judgment': {'type': 'choice', 'choice': choice,
                      'confidence': confidence}}, 'provider_calls': 1, 'model': 'synthetic',
                     'provider': 'TypeSafe', 'request_id': 'fixture', 'input_tokens': 10,
                     'output_tokens': 3, 'cost_usd': .0001, 'elapsed_ms': 1}, **extra)

    def run_case(self, memory=None, workflow='memory_support', payload=None, response=None):
        payload = payload or {'memory_ids': [memory['id']], 'claim': 'Retry is bounded.'}
        def provider(root, project, name, state, questions, **kwargs):
            if response is not None:
                return response
            answers = {key: {'type': q['type'], 'confidence': 1,
                       q['type']: next(iter(q['criteria'])) if q['type'] == 'choice' else 1}
                       for key, q in questions.items()}
            return self.response(answers=answers)
        with patch('jev_openrouter.evaluate', side_effect=provider) as evaluate:
            result = workflows.run_workflow(self.root, 'alpha', workflow, payload)
        return result, evaluate

    def test_catalogue_covers_fourteen_workflows_without_mutable_shared_state(self):
        catalog = workflows.workflow_catalog()
        self.assertEqual(len(catalog), 14)
        catalog[0]['required_fields'].clear()
        self.assertEqual(workflows.workflow_catalog()[0]['required_fields'], ['query'])

    def test_project_and_purpose_require_authorization_before_sources_or_provider(self):
        for change in ({'authorized_projects': ['other']}, {'purposes': []},
                       {'status': 'disabled'}, {'key_present': False}):
            with self.subTest(change=change), patch('jev_openrouter.load_config', return_value=dict(self.config, **change)):
                result, evaluate = self.run_case(payload={'memory_ids': ['a' * 32], 'claim': 'X'})
                self.assertEqual(result['status'], 'unavailable')
                evaluate.assert_not_called()

    def test_source_scopes_and_pending_records_are_rejected(self):
        foreign = self.memory(project_id='beta')
        remote = self.memory(user_id='another')
        pending = self.brain.propose({'project_id': 'alpha', 'kind': 'fact', 'title': 'Pending',
            'content': 'Unreviewed fact.', 'source': {'type': 'user', 'note': 'Synthetic source.'}})
        for item in (foreign, remote, pending):
            result, evaluate = self.run_case(item)
            self.assertEqual(result['status'], 'invalid_input')
            self.assertEqual(result['source_ids'], [])
            evaluate.assert_not_called()

    def test_success_is_source_bound_and_has_no_write(self):
        item = self.memory()
        before = self.brain.get(item['id'])
        result, evaluate = self.run_case(item)
        self.assertEqual(result['status'], 'ok')
        self.assertTrue(result['requires_review'])
        self.assertTrue(result['advisory_only'])
        self.assertEqual(result['source_ids'], [item['id']])
        self.assertEqual(len(result['sources'][0]['source_sha256']), 64)
        self.assertEqual(self.brain.get(item['id']), before)
        self.assertEqual(evaluate.call_args.kwargs['user_id'], 'local')
        self.assertIn(item['id'], evaluate.call_args.kwargs['cache_context']['sources'])
        self.assertNotIn(item['content'], json.dumps(result))

    def test_empty_evidence_does_not_call(self):
        result, evaluate = self.run_case(payload={'claim': 'X'})
        self.assertEqual(result['status'], 'insufficient_evidence')
        evaluate.assert_not_called()

    def test_forget_during_cache_hit_retracts_entire_proposal(self):
        item = self.memory()
        def forget(*args, **kwargs):
            self.brain.forget(item['id'], 'Tester', 'Synthetic concurrent forgetting.')
            return self.response(provider_calls=0, cache_hit=True)
        with patch('jev_openrouter.evaluate', side_effect=forget):
            result = workflows.run_workflow(self.root, 'alpha', 'memory_support',
                {'memory_ids': [item['id']], 'claim': 'X'})
        self.assertEqual(result['status'], 'source_changed')
        self.assertEqual(result['source_ids'], [])
        self.assertEqual(result['suggestions'], [])
        self.assertEqual(result['decision']['provider_calls'], 0)

    def test_source_changes_are_checked_after_provider(self):
        for field, value in [('content', 'Changed after scoring.'), ('project_id', 'other'),
                             ('valid_to', '2000-01-01T00:00:00Z'), ('review_note', 'Changed review.')]:
            item = self.memory('Fixture ' + field)
            def mutate(*args, **kwargs):
                with self.brain._connection() as con:
                    con.execute('UPDATE memories SET ' + field + '=? WHERE id=?', (value, item['id']))
                    con.commit()
                return self.response()
            with self.subTest(field=field), patch('jev_openrouter.evaluate', side_effect=mutate):
                result = workflows.run_workflow(self.root, 'alpha', 'memory_support',
                    {'memory_ids': [item['id']], 'claim': 'X'})
            self.assertEqual(result['status'], 'source_changed')
            self.assertEqual(result['judgments'], [])

    def test_revoking_permission_withholds_result(self):
        item = self.memory()
        def revoke(*args, **kwargs):
            self.config['purposes'] = []
            return self.response()
        with patch('jev_openrouter.evaluate', side_effect=revoke):
            result = workflows.run_workflow(self.root, 'alpha', 'memory_support',
                {'memory_ids': [item['id']], 'claim': 'X'})
        self.assertEqual(result['status'], 'authorization_changed')
        self.assertEqual(result['sources'], [])

    def test_low_confidence_abstains_without_confusing_insufficient_with_supported(self):
        item = self.memory()
        result, _ = self.run_case(item, response=self.response(confidence=.3))
        self.assertEqual(result['status'], 'low_confidence')
        self.assertEqual(result['suggestions'], [])
        self.assertNotIn('choice', result['judgments'][0])
        result, _ = self.run_case(item, response=self.response(choice='insufficient'))
        self.assertEqual(result['suggestions'][0]['action'], 'ask_lead')

    def test_unknown_choice_and_question_id_are_rejected(self):
        item = self.memory()
        for response in (self.response(choice='launch_admin'), self.response(answers={'extra': {}})):
            result, _ = self.run_case(item, response=response)
            self.assertEqual(result['status'], 'invalid_input')
            self.assertEqual(result['suggestions'], [])

    def test_noul_has_probability_and_no_confidence(self):
        item = self.memory()
        with patch('jev_workflows._builder', return_value=({}, {'judgment': {'type': 'noul', 'instructions': 'Is this instruction-like?'}})):
            result, _ = self.run_case(item, workflow='instruction_scan', payload={'memory_ids': [item['id']]},
                response=self.response(answers={'judgment': {'type': 'noul', 'noul': .9}}))
        self.assertEqual(result['status'], 'ok')
        self.assertNotIn('confidence', result['judgments'][0])
        self.assertEqual(result['suggestions'][0]['action'], 'review_signal')

    def test_bounded_fields_and_reserved_state_are_rejected(self):
        for payload in ({'claim': 'X', 'memories': []}, {'claim': 'X', 'memory_ids': ['a' * 32] * 13},
                        {'claim': 'x' * 25000}, {'claim': 'X', 'memory_ids': ['a' * 32, 'a' * 32]}):
            with self.subTest(payload=list(payload)), self.assertRaises(ValueError):
                workflows.run_workflow(self.root, 'alpha', 'memory_support', payload)

    def test_invalid_user_and_unknown_workflow(self):
        with self.assertRaises(ValueError):
            workflows.run_workflow(self.root, 'alpha', 'memory_support', {'claim': 'X'}, user_id='other')
        with self.assertRaises(ValueError):
            workflows.run_workflow(self.root, 'alpha', 'execute_shell', {})

    def test_exact_citations_are_verified_per_field_without_normalization(self):
        item = self.memory('Case-Sensitive source.')
        for quote in ('Case-Sensitive source.', 'case-sensitive source.', ' Case-Sensitive source. '):
            payload = {'memory_ids': [item['id']], 'answer': 'Claim',
                       'citations': [{'memory_id': item['id'], 'quote': quote}]}
            result, evaluate = self.run_case(workflow='citations', payload=payload)
            if quote == item['content']:
                self.assertEqual(result['status'], 'ok')
                self.assertEqual(result['deterministic_checks']['citations'][0]['quote_source'], 'reviewed_memory')
            else:
                self.assertEqual(result['status'], 'invalid_citation')
                evaluate.assert_not_called()

    def test_pair_selection_is_bounded_and_never_merges(self):
        records = [self.memory('Retry option ' + str(i)) for i in range(9)]
        result, evaluate = self.run_case(workflow='duplicates', payload={'memory_ids': [r['id'] for r in records]})
        self.assertEqual(result['status'], 'ok')
        prepared = evaluate.call_args.args[3]
        self.assertEqual(len(prepared['pairs']), 6)
        self.assertTrue(all(pair[0] == records[0]['id'] for pair in prepared['pairs']))
        self.assertTrue(all(self.brain.get(r['id'])['status'] == 'active' for r in records))

    def test_recovery_requires_episodes_and_reports_version_mismatch(self):
        fact = self.memory()
        result, evaluate = self.run_case(workflow='recover', payload={'memory_ids': [fact['id']], 'error': 'E', 'task': 'Fix E'})
        self.assertEqual(result['status'], 'insufficient_evidence')
        evaluate.assert_not_called()
        episode = self.memory('Reviewed fix.', kind='episode',
            episode={'problem': 'E', 'action': 'Retry', 'outcome': 'Recovered'})
        result, _ = self.run_case(workflow='recover', payload={'memory_ids': [episode['id']], 'error': 'E', 'task': 'Fix E',
            'current_versions': {'app': '2'}, 'memory_versions': {episode['id']: {'app': '1'}}})
        self.assertEqual(result['deterministic_checks']['version_applicability'][episode['id']], 'version_mismatch')

    def test_skill_and_event_choices_use_explicit_catalogue_without_memory(self):
        for workflow, payload, field in [('skills', {'task': 'Review', 'catalogue': {'review': 'Code review'}}, 'catalogue'),
                                         ('event', {'event': 'Finished', 'handlers': {'record': 'Record completion'}}, 'handlers')]:
            result, evaluate = self.run_case(workflow=workflow, payload=payload, response=self.response(choice='none'))
            self.assertEqual(result['status'], 'ok')
            self.assertIsInstance(evaluate.call_args.args[3][field], list)

    def test_stale_computes_numeric_age_in_code(self):
        item = self.memory(valid_from='2020-01-01T00:00:00Z')
        result, _ = self.run_case(workflow='stale', payload={'memory_ids': [item['id']], 'as_of': '2020-01-03T00:00:00Z'})
        self.assertEqual(result['deterministic_checks']['age_days'][item['id']], 2)
        self.assertEqual(result['deterministic_checks']['action_limit'], 'propose_refresh_only')

    def test_handoff_preserves_canonical_ownership_and_detects_generation_change(self):
        item = self.memory()
        folder = self.root / '.orchestration' / 'run-1'
        folder.mkdir(parents=True)
        (folder / 'run.json').write_text(json.dumps({'run_id': 'run-1', 'project_id': 'alpha', 'tasks': [{'job_id': 'b' * 32}]}))
        state = {'run_id': 'run-1', 'owner': 'astra', 'session': 'session', 'generation': 2, 'status': 'active'}
        (folder / 'coordinator.json').write_text(json.dumps(state))
        payload = {'memory_ids': [item['id']], 'run_id': 'run-1'}
        result, _ = self.run_case(workflow='handoff', payload=payload)
        self.assertEqual(result['deterministic_checks']['run_context']['owner'], 'astra')
        def handoff(*args, **kwargs):
            (folder / 'coordinator.json').write_text(json.dumps(dict(state, generation=3)))
            return self.response()
        with patch('jev_openrouter.evaluate', side_effect=handoff):
            result = workflows.run_workflow(self.root, 'alpha', 'handoff', payload)
        self.assertEqual(result['status'], 'source_changed')
        self.assertEqual(result['deterministic_checks'], {})

    def test_cli_lists_without_connection_and_rejects_external_payload_paths(self):
        with contextlib.redirect_stdout(io.StringIO()) as output:
            self.assertEqual(main(['--root', str(self.root), 'workflows']), 0)
        self.assertEqual(len(json.loads(output.getvalue())['workflows']), 14)
        with contextlib.redirect_stdout(io.StringIO()) as output:
            code = main(['--root', str(self.root), 'workflow', 'event', '--project', 'alpha', '--file', '../outside.json'])
        self.assertEqual(code, 2)
        self.assertEqual(json.loads(output.getvalue())['status'], 'invalid_input')

    def test_real_evidence_builders_fit_adapter_and_preserve_independent_targets(self):
        self.builder.stop()
        item = self.memory()
        ids = [item['id']]
        cases = {
            'evidence_review': {'memory_ids': ids, 'query': 'Retry policy'},
            'instruction_scan': {'memory_ids': ids},
            'memory_support': {'memory_ids': ids, 'claim': 'Retries are bounded.'},
            'sufficiency': {'memory_ids': ids, 'question': 'Are retries bounded?', 'answer': 'Yes.'},
            'sensitivity': {'memory_ids': ids, 'intended_use': 'Share with the authorized reviewer.'},
            'citations': {'memory_ids': ids, 'answer': 'Retry is bounded.',
                          'citations': [{'memory_id': item['id'], 'quote': 'bounded backoff'}]},
        }
        def provider(root, project, workflow, state, questions, **kwargs):
            import jev_openrouter
            jev_openrouter._request(state, questions)
            answers = {}
            for key, question in questions.items():
                kind = question['type']
                if kind == 'noul':
                    answers[key] = {'type': kind, 'noul': .1}
                else:
                    answers[key] = {'type': kind, 'confidence': 1,
                                    kind: next(iter(question['criteria'])) if kind == 'choice' else 1}
            return self.response(answers=answers)
        with patch('jev_openrouter.evaluate', side_effect=provider):
            for workflow, payload in cases.items():
                with self.subTest(workflow=workflow):
                    result = workflows.run_workflow(self.root, 'alpha', workflow, payload)
                    self.assertEqual(result['status'], 'ok', result['reason'])
                    self.assertTrue(all('target' in j for j in result['judgments']))
                    if workflow == 'evidence_review':
                        self.assertEqual(len(result['judgments']), 4)

    def test_builder_cannot_expand_allowed_handler_choices(self):
        bad = {'judgment': {'type': 'choice', 'instructions': 'Choose handler.',
                           'criteria': {'launch_admin': 'Launch an unauthorized process.', 'none': 'Defer.'}}}
        with patch('jev_workflows._builder', return_value=({}, bad)):
            result, evaluate = self.run_case(workflow='event', payload={'event': 'E', 'handlers': {'record': 'Record E'}})
        self.assertEqual(result['status'], 'invalid_input')
        evaluate.assert_not_called()

    def test_citation_cannot_cross_content_and_episode_boundary(self):
        item = self.memory('Content end', kind='episode', episode={'problem': 'Problem start',
                           'action': 'Test action', 'outcome': 'Test outcome'})
        result, evaluate = self.run_case(workflow='citations', payload={'memory_ids': [item['id']], 'answer': 'Claim',
            'citations': [{'memory_id': item['id'], 'quote': 'Content end\nProblem start'}]})
        self.assertEqual(result['status'], 'invalid_citation')
        evaluate.assert_not_called()

    def test_provider_failure_preserves_telemetry_without_suggestion(self):
        item = self.memory()
        result, _ = self.run_case(item, response={'status': 'rate_limited', 'provider_calls': 0,
            'answers': {}, 'cache': {'status': 'miss'}})
        self.assertEqual(result['status'], 'rate_limited')
        self.assertEqual(result['suggestions'], [])
        self.assertEqual(result['decision']['cache']['status'], 'miss')

    def test_changed_canonical_task_evidence_invalidates_unchanged_memory_row(self):
        job_id = 'c' * 32
        path = self.root / 'runs' / 'tasks' / job_id / 'result.json'
        path.parent.mkdir(parents=True)
        canonical = {'job_id': job_id, 'assignment_project_id': 'alpha', 'status': 'accepted',
            'review_status': 'accepted', 'execution_status': 'succeeded', 'finalized_at': '2026-01-01T00:00:00Z',
            'response': 'Reviewed source answer.', 'review': {'note': 'Reviewed evidence.'}}
        path.write_text(json.dumps(canonical))
        item = self.memory(source={'type': 'task', 'job_id': job_id})
        def change_source(*args, **kwargs):
            path.write_text(json.dumps(dict(canonical, response='Different answer.')))
            return self.response()
        with patch('jev_openrouter.evaluate', side_effect=change_source):
            result = workflows.run_workflow(self.root, 'alpha', 'memory_support',
                {'memory_ids': [item['id']], 'claim': 'X'})
        self.assertEqual(result['status'], 'source_changed')
        self.assertEqual(result['suggestions'], [])
        result, evaluate = self.run_case(item)
        self.assertEqual(result['status'], 'invalid_input')
        evaluate.assert_not_called()

    def test_superseding_evidence_during_request_withholds_proposals(self):
        old = self.memory('Old reviewed fact.')
        new = self.memory('Replacement reviewed fact.')
        def supersede(*args, **kwargs):
            self.brain.supersede(old['id'], new['id'], 'Tester', 'Replacement verified for this test.')
            return self.response()
        with patch('jev_openrouter.evaluate', side_effect=supersede):
            result = workflows.run_workflow(self.root, 'alpha', 'memory_support',
                {'memory_ids': [old['id']], 'claim': 'X'})
        self.assertEqual(result['status'], 'source_changed')
        self.assertEqual(result['suggestions'], [])

    def test_real_curation_builders_fit_adapter_and_preserve_advisory_targets(self):
        self.builder.stop()
        first = self.memory('Reviewed retry policy.')
        second = self.memory('Retry backoff policy is bounded.')
        ids = [first['id'], second['id']]
        cases = {
            'duplicates': {'memory_ids': ids}, 'relations': {'memory_ids': ids},
            'stale': {'memory_ids': ids},
            'durability': {'memory_ids': ids, 'claim': 'Retries remain bounded.'},
        }
        def provider(root, project, workflow, state, questions, **kwargs):
            import jev_openrouter
            jev_openrouter._request(state, questions)
            if workflow == 'stale':
                self.assertEqual(set(state['deterministic_checks']), set(ids))
            answers = {key: {'type': question['type'], 'confidence': 1,
                       question['type']: next(iter(question['criteria'])) if question['type'] == 'choice' else 1}
                       for key, question in questions.items()}
            return self.response(answers=answers)
        with patch('jev_openrouter.evaluate', side_effect=provider):
            for workflow, payload in cases.items():
                with self.subTest(workflow=workflow):
                    result = workflows.run_workflow(self.root, 'alpha', workflow, payload)
                    self.assertEqual(result['status'], 'ok', result['reason'])
                    self.assertTrue(all('target' in j for j in result['judgments']))
                    self.assertTrue(all(s['requires_review'] for s in result['suggestions']))

    def test_real_orchestration_builders_and_version_constraint(self):
        self.builder.stop()
        episode = self.memory('Reviewed recovery.', kind='episode',
            episode={'problem': 'Timeout', 'action': 'Bounded retry', 'outcome': 'Recovered'})
        folder = self.root / '.orchestration' / 'run-real'
        folder.mkdir(parents=True)
        (folder / 'run.json').write_text(json.dumps({'run_id': 'run-real', 'project_id': 'alpha', 'tasks': [{'job_id': 'd' * 32}]}))
        context = {'run_id': 'run-real', 'owner': 'astra', 'session': 's', 'generation': 2, 'status': 'active',
                   'checkpoint': {'open_jobs': ['worker'], 'authorization': ['Approved task'], 'next_steps': ['Review result']}}
        (folder / 'coordinator.json').write_text(json.dumps(context))
        cases = {
            'recover': {'memory_ids': [episode['id']], 'task': 'Recover service', 'error': 'Timeout',
                        'current_versions': {'app': '2'}, 'memory_versions': {episode['id']: {'app': '1'}}},
            'skills': {'task': 'Inspect code', 'catalogue': {'review': 'Review code'}},
            'event': {'event': 'Unknown event', 'handlers': {}},
            'handoff': {'memory_ids': [episode['id']], 'run_id': 'run-real'},
        }
        def provider(root, project, workflow, state, questions, **kwargs):
            import jev_openrouter
            jev_openrouter._request(state, questions)
            answers = {key: {'type': question['type'], 'confidence': 1,
                       question['type']: next(iter(question['criteria'])) if question['type'] == 'choice' else 1}
                       for key, question in questions.items()}
            return self.response(answers=answers)
        with patch('jev_openrouter.evaluate', side_effect=provider):
            for workflow, payload in cases.items():
                with self.subTest(workflow=workflow):
                    result = workflows.run_workflow(self.root, 'alpha', workflow, payload)
                    self.assertEqual(result['status'], 'partial' if workflow == 'recover' else 'ok')
                    self.assertTrue(all('target' in j for j in result['judgments']))
                    if workflow == 'recover':
                        blocked = [j for j in result['judgments'] if j['status'] == 'blocked_by_version_check']
                        self.assertEqual(len(blocked), 1)
                        self.assertFalse(any(s['question_id'] == blocked[0]['question_id'] for s in result['suggestions']))
                    if workflow == 'handoff':
                        self.assertEqual(result['delivery_status'], 'prepared_only')
                        self.assertEqual(result['deterministic_checks']['run_context']['job_ids'], ['d' * 32])
                        self.assertEqual(result['deterministic_checks']['run_context']['next_steps'], ['Review result'])

    def test_real_handoff_builder_withholds_changed_checkpoint(self):
        self.builder.stop()
        item = self.memory()
        folder = self.root / '.orchestration' / 'race-run'
        folder.mkdir(parents=True)
        (folder / 'run.json').write_text(json.dumps({'run_id': 'race-run', 'project_id': 'alpha', 'tasks': []}))
        state = {'run_id': 'race-run', 'owner': 'astra', 'session': 's', 'generation': 1, 'status': 'active',
                 'checkpoint': {'open_jobs': ['review']}}
        (folder / 'coordinator.json').write_text(json.dumps(state))
        def mutate(root, project, workflow, prepared, questions, **kwargs):
            changed = dict(state, checkpoint={'open_jobs': []})
            (folder / 'coordinator.json').write_text(json.dumps(changed))
            return self.response(answers={key: {'type': 'score', 'score': 1, 'confidence': 1} for key in questions})
        with patch('jev_openrouter.evaluate', side_effect=mutate):
            result = workflows.run_workflow(self.root, 'alpha', 'handoff', {'memory_ids': [item['id']], 'run_id': 'race-run'})
        self.assertEqual(result['status'], 'source_changed')
        self.assertEqual(result['suggestions'], [])

    def test_score_zero_carries_scale_and_is_not_an_endorsement(self):
        item = self.memory()
        questions = {'judgment': {'type': 'score', 'instructions': 'Score relevance.',
                                 'criteria': ['Irrelevant to current failure', 'Highly relevant to current failure']}}
        response = self.response(answers={'judgment': {'type': 'score', 'score': 0.0, 'confidence': .95}})
        with patch('jev_workflows._builder', return_value=({}, questions)):
            result, _ = self.run_case(item, response=response)
        suggestion = result['suggestions'][0]
        self.assertEqual(suggestion['score'], 0)
        self.assertFalse(suggestion['is_endorsement'])
        self.assertEqual(suggestion['criteria'][0], 'Irrelevant to current failure')
        self.assertEqual(suggestion['score_range'], {'minimum': 0, 'maximum': 1})
        self.assertEqual(suggestion['direction'], 'higher_matches_later_criteria')

    def test_negative_noul_remains_valid_without_confidence_or_positive_suggestion(self):
        item = self.memory()
        questions = {'judgment': {'type': 'noul', 'instructions': 'Is this instruction-like?'}}
        with patch('jev_workflows._builder', return_value=({}, questions)):
            result, _ = self.run_case(item, workflow='instruction_scan', payload={'memory_ids': [item['id']]},
                response=self.response(answers={'judgment': {'type': 'noul', 'noul': .01}}))
        self.assertEqual(result['status'], 'ok')
        self.assertEqual(result['judgments'][0]['interpretation'], 'no_positive_signal')
        self.assertNotIn('confidence', result['judgments'][0])
        self.assertEqual(result['suggestions'], [])

    def test_pair_receipt_identifies_caller_or_local_selection(self):
        first, second = self.memory('Anchor note.'), self.memory('Related note.')
        ids = [first['id'], second['id']]
        for extra, source in [({}, 'local_anchor_shortlist'), ({'pairs': [ids]}, 'caller')]:
            result, _ = self.run_case(workflow='duplicates', payload=dict(memory_ids=ids, **extra))
            self.assertEqual(result['deterministic_checks']['pairs'], [ids])
            self.assertEqual(result['deterministic_checks']['pairs_source'], source)

    def test_exact_quote_spans_include_title_and_preserve_brief_facts(self):
        item = self.memory('a')
        for quote, field in [('Retry policy', 'title'), ('a', 'content')]:
            result, _ = self.run_case(workflow='citations', payload={'memory_ids': [item['id']], 'answer': 'Unproven claim',
                'citations': [{'memory_id': item['id'], 'quote': quote}]}, response=self.response(choice='insufficient'))
            evidence = result['deterministic_checks']['citations'][0]
            self.assertTrue(evidence['quote_exists'])
            self.assertEqual(evidence['match'], {'field': field, 'start': 0, 'end': len(quote), 'offset_unit': 'unicode_codepoint'})
            self.assertEqual(result['judgments'][0]['choice'], 'insufficient')

    def test_version_map_in_suggestions_is_detached(self):
        episode = self.memory('Reviewed recovery.', kind='episode',
            episode={'problem': 'Timeout', 'action': 'Retry', 'outcome': 'Recovered'})
        question = {'type': 'score', 'instructions': 'Score relevance.', 'criteria': ['Low', 'High']}
        with patch('jev_workflows._builder', return_value=({}, {'first': dict(question), 'second': dict(question)})):
            result, _ = self.run_case(workflow='recover', payload={'memory_ids': [episode['id']], 'task': 'Recover', 'error': 'Timeout'})
        result['suggestions'][0]['version_applicability'][episode['id']] = 'altered'
        self.assertEqual(result['suggestions'][1]['version_applicability'][episode['id']], 'unknown')
        self.assertEqual(result['deterministic_checks']['version_applicability'][episode['id']], 'unknown')

    def test_recovery_builder_cannot_introduce_an_applicability_alias(self):
        episode = self.memory('Recovery.', kind='episode', episode={'problem': 'E', 'action': 'A', 'outcome': 'O'})
        question = {'type': 'choice', 'instructions': 'Judge applicability.', 'criteria': {'usable': 'Apply fix.', 'insufficient': 'Unknown.'}}
        with patch('jev_workflows._builder', return_value=({}, {'judgment': question})):
            result, evaluate = self.run_case(workflow='recover', payload={'memory_ids': [episode['id']], 'error': 'E', 'task': 'T'})
        self.assertEqual(result['status'], 'invalid_input')
        evaluate.assert_not_called()

    def test_corrupt_episode_shape_fails_with_bounded_receipt(self):
        item = self.memory()
        with self.brain._connection() as con:
            con.execute('UPDATE memories SET episode=? WHERE id=?', ('null', item['id']))
            con.commit()
        result, evaluate = self.run_case(workflow='citations', payload={'memory_ids': [item['id']], 'answer': 'A',
            'citations': [{'memory_id': item['id'], 'quote': 'Retry'}]})
        self.assertEqual(result['status'], 'invalid_input')
        evaluate.assert_not_called()

    def test_source_handoff_rejects_generation_zero(self):
        folder = self.root / '.orchestration' / 'invalid-generation'
        folder.mkdir(parents=True)
        (folder / 'run.json').write_text(json.dumps({'run_id': folder.name, 'project_id': 'alpha', 'tasks': []}))
        (folder / 'coordinator.json').write_text(json.dumps({'run_id': folder.name, 'owner': 'astra', 'session': 's',
                                                           'generation': 0, 'status': 'active'}))
        with self.assertRaises(ValueError):
            workflows._run_context(self.root, folder.name, 'alpha')

    def test_cache_dependency_identity_ignores_unrelated_memory_but_binds_selected_source(self):
        selected = self.memory('Selected reviewed fact.')
        first, first_call = self.run_case(selected)
        self.memory('Unrelated reviewed addition.')
        second, second_call = self.run_case(selected)
        self.assertEqual(first['request_binding'], second['request_binding'])
        self.assertEqual(first_call.call_args.kwargs['cache_context'], second_call.call_args.kwargs['cache_context'])
        with self.brain._connection() as con:
            con.execute('UPDATE memories SET content=? WHERE id=?', ('Changed selected fact.', selected['id']))
            con.commit()
        third, third_call = self.run_case(selected)
        self.assertNotEqual(second['request_binding']['sha256'], third['request_binding']['sha256'])
        self.assertNotEqual(second_call.call_args.kwargs['cache_context'], third_call.call_args.kwargs['cache_context'])
        self.brain.forget(selected['id'], 'Tester', 'Explicitly forget selected test evidence.')
        forgotten, forbidden_call = self.run_case(selected)
        self.assertEqual(forgotten['status'], 'invalid_input')
        forbidden_call.assert_not_called()

    def test_relation_pairs_keep_direction_while_duplicate_pairs_are_symmetric(self):
        self.builder.stop()
        first, second = self.memory('Source fact.'), self.memory('Target fact.')
        ids = [first['id'], second['id']]
        payload = {'memory_ids': ids, 'pairs': [ids, list(reversed(ids))]}
        related, call = self.run_case(workflow='relations', payload=payload)
        self.assertEqual(related['status'], 'ok')
        self.assertEqual(len(related['judgments']), 2)
        self.assertEqual(related['deterministic_checks']['pairs'], payload['pairs'])
        self.assertEqual(related['deterministic_checks']['pairs_source'], 'caller')
        duplicate, rejected = self.run_case(workflow='duplicates', payload=payload)
        self.assertEqual(duplicate['status'], 'invalid_input')
        rejected.assert_not_called()


if __name__ == '__main__':
    unittest.main()
