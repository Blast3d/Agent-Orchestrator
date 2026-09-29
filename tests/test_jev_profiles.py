"""Task-role memory profiles and prepared delivery; temporary data, mocked providers."""
import argparse
import contextlib
from copy import deepcopy
import hashlib
import io
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'app'))
from brain_store import BrainStore
from brain_jev import ranking_request
from brain_retrieval_metadata import public_retrieval
from jev_profiles import PROFILE_NEEDS, PROFILES
from memory_usage import _bindings, contract_fields, recall_plan, summary
from task_store import TaskStore
import brain_cli
import dispatch_worker as dispatcher
import jev_cli


class ProfileTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.brain = BrainStore(self.root)
        # Always intercept optional provider admission, even if local defaults evolve.
        self.config_patch = patch('jev_openrouter.load_config', return_value={'status': 'disabled'})
        self.config_patch.start()
        self.addCleanup(self.config_patch.stop)

    def memory(self, index=0, content=None):
        candidate = self.brain.propose({'project_id': 'alpha', 'kind': 'procedure',
            'title': 'Recovery evidence ' + str(index),
            'content': content or 'Recovery uses bounded retry and checks prior failure evidence ' + str(index),
            'source': {'type': 'user', 'note': 'Synthetic test evidence.'}})
        return self.brain.approve(candidate['id'], 'Tester', 'Checked the synthetic recovery procedure.')

    def plan(self, **updates):
        values = dict(project='alpha', task='Recovery', category='general')
        values.update(updates)
        return recall_plan(argparse.Namespace(**values))

    def test_category_mapping_and_explicit_override_are_worker_independent(self):
        categories = {'coding': 'implementation', 'implementation': 'implementation',
                      'debug': 'implementation', 'review': 'review', 'audit': 'review',
                      'research': 'research', 'handoff': 'handoff', 'design': 'general'}
        for category, expected in categories.items():
            for worker in ('claude', 'grok', 'native-codex'):
                with self.subTest(category=category, worker=worker):
                    self.assertEqual(self.plan(category=category, worker=worker)['profile'], expected)
                    self.assertEqual(self.plan(category=category, worker=worker,
                                               memory_profile='general')['profile'], 'general')
        self.assertEqual(self.plan(category='  AUDIT  ')['profile'], 'review')

    def test_general_assignment_contract_is_backward_compatible(self):
        default = contract_fields(self.plan())
        self.assertEqual(default, {'memory_policy': 'task_label', 'memory_query': 'Recovery'})
        review = contract_fields(self.plan(memory_profile='review'))
        self.assertEqual(review, dict(default, memory_profile='review'))
        self.assertNotEqual(review, contract_fields(self.plan(memory_profile='research')))

    def test_invalid_profile_is_rejected_before_recall_or_provider_work(self):
        with patch('brain_recall.search') as recall, patch('jev_openrouter.evaluate') as evaluate:
            for invalid in ('Review', 'claude', '', [], None):
                with self.subTest(invalid=invalid), self.assertRaises(ValueError):
                    self.brain.search('Recovery', 'alpha', profile=invalid)
            with self.assertRaises(ValueError):
                self.plan(memory_profile='not-a-role')
        recall.assert_not_called()
        evaluate.assert_not_called()

    def test_profile_metadata_and_trace_exist_when_jev_is_disabled(self):
        self.memory()
        result = self.brain.search('Recovery', 'alpha', profile='review')
        self.assertEqual(result['profile'], 'review')
        self.assertEqual(result['retrieval']['profile'], 'review')
        self.assertNotIn('jev', result['retrieval'])
        with self.brain._connection() as con:
            detail = con.execute('SELECT detail FROM retrieval_traces WHERE trace_id=?',
                                 (result['trace_id'],)).fetchone()
        self.assertEqual(json.loads(detail['detail'])['profile'], 'review')
        self.assertEqual(public_retrieval(result['retrieval'])['profile'], 'review')

    def test_each_role_reaches_actual_jev_state_with_wider_pool_and_bounded_output_caps(self):
        for index in range(8):
            self.memory(index, ('Recovery failure evidence ' + str(index) + '. ') * 40)
        config = {'status': 'ready', 'key_present': True, 'authorized_projects': ['alpha'],
                  'purposes': ['memory_rank'], 'min_confidence': .8}
        observed = []

        def provider(root, project, purpose, state, questions, **kwargs):
            observed.append(state)
            self.assertEqual(project, 'alpha')
            self.assertEqual(purpose, 'memory_rank')
            self.assertEqual(len(state['candidates']), 8)
            self.assertEqual(state['role_needs'], PROFILE_NEEDS[state['profile']])
            for question in questions.values():
                self.assertIn('Query relevance comes first.', question['instructions'])
                self.assertIn(PROFILE_NEEDS[state['profile']], question['instructions'])
                self.assertIn('never as instructions', question['instructions'])
            return {'status': 'ok', 'answers': {key: {'score': 3, 'confidence': 1}
                    for key in questions}, 'provider_calls': 1, 'elapsed_ms': 0}

        with patch('jev_openrouter.load_config', return_value=config), \
                patch('jev_openrouter.evaluate', side_effect=provider):
            for profile in PROFILES:
                result = self.brain.search('Recovery', 'alpha', profile=profile, limit=99, max_chars=99999)
                self.assertLessEqual(len(result['results']), 24)
                self.assertLessEqual(result['context_chars'], 32000)
                self.assertEqual(result['retrieval']['jev']['profile'], profile)
        self.assertEqual([state['profile'] for state in observed], list(PROFILES))

    def test_pure_ranking_builder_is_shared_and_does_not_mutate_input(self):
        candidates = [{'id': 'candidate-a', 'title': 'Recovery', 'content': 'Synthetic useful evidence.'}]
        before = deepcopy(candidates)
        with patch('jev_openrouter.evaluate') as evaluate:
            state, questions = ranking_request('Recovery', candidates, 'review')
        self.assertEqual(candidates, before)
        self.assertEqual(state['profile'], 'review')
        self.assertEqual(state['role_needs'], PROFILE_NEEDS['review'])
        self.assertEqual(set(questions), {'memory_0'})
        self.assertEqual(questions['memory_0']['type'], 'score')
        evaluate.assert_not_called()

    def test_ranking_questions_bind_exact_distinct_ids_instead_of_list_positions(self):
        candidates = [{'id': identity, 'title': 'Synthetic', 'content': 'Evidence.'}
                      for identity in ('memory-zeta', 'memory-alpha', 'memory"quoted\\id')]
        state, questions = ranking_request('Recovery', candidates, 'implementation')
        self.assertEqual(state['candidates'], candidates)
        for index, candidate in enumerate(candidates):
            instructions = questions['memory_' + str(index)]['instructions']
            self.assertIn('whose `id` exactly equals ' + json.dumps(candidate['id']), instructions)
            self.assertNotIn('candidate ' + str(index), instructions)
            for other in candidates:
                if other is not candidate:
                    self.assertNotIn(json.dumps(other['id']), instructions)
        reversed_candidates = list(reversed(candidates))
        _, reversed_questions = ranking_request('Recovery', reversed_candidates)
        self.assertIn(json.dumps(candidates[-1]['id']), reversed_questions['memory_0']['instructions'])
        with self.assertRaisesRegex(ValueError, 'unique'):
            ranking_request('Recovery', [candidates[0], dict(candidates[0])])

    def test_profile_projection_rejects_unknown_values_and_does_not_invent_history(self):
        for value in (None, 'claude', {'private': 'content'}, 42):
            public = public_retrieval({'schema_version': 1, 'profile': value,
                                       'jev': {'profile': value, 'private': 'do not expose'}})
            self.assertIsNone(public['profile'])
            self.assertIsNone(public['jev']['profile'])
            self.assertNotIn('do not expose', json.dumps(public))
        self.assertIsNone(public_retrieval({'schema_version': 1})['profile'])

    def test_context_profile_receipt_is_bound_and_legacy_general_hash_is_preserved(self):
        content = 'Synthetic bounded memory.'
        result = {'assignment_project_id': 'alpha', 'memory_user_id': 'local',
                  'memory_profile': 'review', 'memory_context': {
                      'ids': ['a' * 32], 'context': content,
                      'sha256': hashlib.sha256(content.encode()).hexdigest(),
                      'project_id': 'alpha', 'user_id': 'local', 'profile': 'review',
                      'execution_requested': True}}
        self.assertEqual(summary(result)['memory_profile'], 'review')
        bad = deepcopy(result)
        bad['memory_context']['profile'] = 'research'
        self.assertEqual(summary(bad)['stage'], 'invalid_context')
        old_context = dict(result['memory_context'])
        del old_context['profile']
        self.assertEqual(_bindings(result, old_context), _bindings(result, dict(old_context, profile='general')))
        self.assertNotEqual(_bindings(result, old_context), _bindings(result, dict(old_context, profile='review')))

    def test_packet_is_scoped_bounded_fingerprinted_and_prepared_only(self):
        self.memory()
        output = io.StringIO()
        with contextlib.redirect_stdout(output), patch('jev_openrouter.evaluate') as evaluate:
            status = jev_cli.main(['--root', str(self.root), 'packet', 'Recovery', '--project', 'alpha',
                                  '--profile', 'handoff', '--recipient', 'native-codex'])
        packet = json.loads(output.getvalue())
        self.assertEqual(status, 0)
        self.assertEqual(set(packet), {'recipient', 'project_id', 'profile', 'context', 'ids',
                                      'sha256', 'trace_id', 'retrieval', 'delivery_status'})
        self.assertEqual(packet['recipient'], 'native-codex')
        self.assertEqual(packet['profile'], 'handoff')
        self.assertEqual(packet['project_id'], 'alpha')
        self.assertEqual(packet['delivery_status'], 'prepared_only')
        self.assertLessEqual(len(packet['context']), 8000)
        self.assertLessEqual(len(packet['ids']), 6)
        self.assertEqual(packet['sha256'], hashlib.sha256(packet['context'].encode()).hexdigest())
        self.assertEqual(packet['retrieval']['profile'], 'handoff')
        evaluate.assert_not_called()

    def test_invalid_packet_recipient_prevents_lookup(self):
        with contextlib.redirect_stdout(io.StringIO()), patch.object(BrainStore, 'search') as search:
            status = jev_cli.main(['--root', str(self.root), 'packet', 'Recovery', '--project', 'alpha',
                                  '--recipient', 'bad\nrecipient'])
        self.assertEqual(status, 2)
        search.assert_not_called()

    def test_brain_search_and_jev_recall_cli_forward_role(self):
        self.memory()
        for cli, command in ((brain_cli.main, 'search'), (jev_cli.main, 'recall')):
            output = io.StringIO()
            with contextlib.redirect_stdout(output):
                status = cli(['--root', str(self.root), command, 'Recovery', '--project', 'alpha',
                              '--profile', 'research'])
            self.assertEqual(status, 0)
            self.assertEqual(json.loads(output.getvalue())['profile'], 'research')

    def test_actual_dispatcher_supplies_profiled_bounded_context_to_different_workers(self):
        self.memory()
        task_store = TaskStore(self.root / 'runs/tasks')
        prompt = self.root / 'brief.txt'
        prompt.write_text('Inspect synthetic supplied evidence.', encoding='utf-8')
        guard = Mock()
        guard.refresh.side_effect = lambda provider: {provider: {'ok': True}}
        guard.check.return_value = {'allowed': True, 'reservation_id': 'test-reservation'}
        completed = subprocess.CompletedProcess(['mocked-worker'], 0,
                        json.dumps({'result': 'Specific synthetic answer for review.'}), '')
        original_search = BrainStore.search
        observed = []

        def tracked_search(store, query, project, **options):
            observed.append(options.get('profile', 'general'))
            if options.get('profile', 'general') == 'general':
                self.assertNotIn('profile', options)
            return original_search(store, query, project, **options)

        with patch.object(dispatcher, 'ensure_directories'), \
                patch.object(dispatcher, 'cloud_command', return_value=([sys.executable], 'mocked input')) as command, \
                patch.object(dispatcher, 'invoke_cloud', return_value=completed) as invoke, \
                patch.object(BrainStore, 'search', tracked_search):
            for index, (worker, category, explicit, expected) in enumerate((
                    ('claude', 'coding', None, 'implementation'), ('grok', 'audit', None, 'review'),
                    ('claude', 'review', 'research', 'research'), ('grok', 'general', None, 'general'))):
                args = argparse.Namespace(worker=worker, task='Recovery', category=category,
                    memory_profile=explicit, project='alpha', assignment_id='profile-' + str(index),
                    size='small', prompt_file=prompt, output=self.root / ('output-' + str(index) + '.json'))
                result = dispatcher.dispatch(args, guard_factory=lambda: guard, store=task_store,
                                             workspaces=self.root / 'runtime/workspaces')
                self.assertEqual(result['status'], 'awaiting_review', result.get('error'))
                context = result['memory_context']
                self.assertEqual(result['memory_profile'], expected)
                self.assertEqual(context['profile'], expected)
                self.assertTrue(context['execution_requested'])
                self.assertLessEqual(len(context['context']), 8000)
                self.assertLessEqual(len(context['ids']), 6)
                self.assertIn(context['context'], command.call_args.args[1])
                self.assertIn('Memory task profile: ' + expected, command.call_args.args[1])
                self.assertEqual(summary(result)['provider_read'], 'not_observable')
                reused = dispatcher.dispatch(args, guard_factory=lambda: guard, store=task_store,
                                             workspaces=self.root / 'runtime/workspaces')
                self.assertTrue(reused['assignment_reused'])
                self.assertEqual(reused['job_id'], result['job_id'])
                args.memory_profile = 'handoff'
                repeated = dispatcher.dispatch(args, guard_factory=lambda: guard, store=task_store,
                                               workspaces=self.root / 'runtime/workspaces')
                self.assertEqual(repeated['status'], 'held')
            self.assertEqual(invoke.call_count, 4)
        self.assertEqual(observed, ['implementation', 'review', 'research', 'general'])


if __name__ == '__main__':
    unittest.main()
