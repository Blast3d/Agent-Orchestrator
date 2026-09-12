"""Request provenance and reviewer ratings; no provider or network calls."""
import argparse
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'app'))
from brain_store import BrainStore, digest
from memory_usage import contract_fields, recall_plan, record_feedback, summary
from task_store import TaskStore, timestamp
import dispatch_worker as dispatcher


class MemoryUsageTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.store = TaskStore(self.root / 'runs/tasks')
        self.result = self.store.create(worker='claude', task='Recovery evidence', size='small',
            assignment_project_id='alpha', assignment_id='test', memory_user_id='local',
            memory_lookup_requested=True, memory_policy='task_label')
        content = 'PRIVATE MEMORY CONTENT - scoped recovery evidence'
        self.result.update(status='accepted', execution_status='succeeded', review_status='accepted',
            response='PRIVATE WORKER ANSWER', finalized_at=timestamp(),
            review={'reviewer': 'Reviewer', 'note': 'Checked the saved answer against task requirements.',
                    'reviewed_at': timestamp()},
            memory_context={'ids': ['a' * 32], 'context': content,
                'sha256': hashlib.sha256(content.encode()).hexdigest(), 'project_id': 'alpha',
                'user_id': 'local', 'trace_id': 'b' * 32, 'lookup_ms': 2.25, 'elapsed_ms': 3.5,
                'execution_requested': True, 'query': 'PRIVATE QUERY'})
        self.store.save(self.result['job_id'], self.result)

    def saved(self):
        return json.loads((self.store.directory(self.result['job_id']) / 'result.json').read_text())

    def feedback(self, rating='helped', reviewer='Reviewer', note='The recovery procedure prevented a duplicate execution.'):
        return record_feedback(self.store, self.result['job_id'], rating, reviewer, note)

    def save(self):
        self.store.save(self.result['job_id'], self.result)

    def test_public_summary_distinguishes_requested_input_from_provider_read(self):
        value = summary(self.result)
        self.assertEqual(value['stage'], 'execution_requested')
        self.assertEqual(value['provider_read'], 'not_observable')
        self.assertEqual(value['feedback']['status'], 'not_evaluated')
        self.assertEqual(value['memory_count'], 1)
        self.assertEqual(value['trace_id'], 'b' * 32)
        self.assertEqual(value['lookup_ms'], 2.25)
        self.assertNotIn('PRIVATE', json.dumps(value))
        self.assertNotIn('query', value)
        self.assertNotIn('context', value)

    def test_old_missing_and_explicitly_disabled_telemetry_differ(self):
        self.assertEqual(summary({})['stage'], 'not_recorded')
        self.assertEqual(summary({'memory_lookup_requested': False})['stage'], 'not_requested')
        self.assertEqual(summary({'memory_lookup_requested': True})['stage'], 'lookup_missing')

    def test_empty_lookup_and_prepared_input_differ(self):
        self.result['memory_context']['execution_requested'] = False
        self.assertEqual(summary(self.result)['stage'], 'prepared')
        self.result['memory_context']['ids'] = []
        self.assertEqual(summary(self.result)['stage'], 'empty')
        self.result['memory_context']['execution_requested'] = True
        self.assertEqual(summary(self.result)['stage'], 'empty')

    def test_missing_or_invalid_telemetry_never_becomes_zero_latency(self):
        context = self.result['memory_context']
        for key in ('trace_id', 'lookup_ms', 'elapsed_ms', 'user_id'):
            context.pop(key)
        value = summary(self.result)
        self.assertEqual(value['stage'], 'execution_requested')
        self.assertIsNone(value['lookup_ms'])
        self.assertIsNone(value['elapsed_ms'])
        self.assertIsNone(value['trace_id'])
        self.assertIsNone(value['user_id'])
        context.update(lookup_ms=float('nan'), elapsed_ms=True, trace_id='invalid')
        value = summary(self.result)
        self.assertIsNone(value['lookup_ms'])
        self.assertIsNone(value['elapsed_ms'])
        self.assertIsNone(value['trace_id'])

    def test_corrupt_context_or_scope_is_unverifiable(self):
        for field, value in [('context', 'changed'), ('sha256', 'wrong'),
                             ('project_id', 'beta'), ('user_id', 'other'),
                             ('ids', ['bad']), ('ids', ['a' * 32, 'a' * 32]),
                             ('execution_requested', 'true')]:
            with self.subTest(field=field, value=value):
                changed = deepcopy(self.result)
                changed['memory_context'][field] = value
                self.assertEqual(summary(changed)['stage'], 'invalid_context')

    def test_feedback_preserves_source_proofs_and_exposes_only_reviewed_judgment(self):
        original = self.saved()
        value = self.feedback()
        saved = self.saved()
        self.assertEqual(value['feedback']['status'], 'reviewed')
        self.assertEqual(value['feedback']['rating'], 'helped')
        for key in ('response', 'review', 'finalized_at', 'memory_context', 'status', 'review_status'):
            self.assertEqual(saved[key], original[key])
        proof_keys = ('job_id', 'assignment_project_id', 'response', 'finalized_at')
        self.assertEqual(digest({key: saved[key] for key in proof_keys}),
                         digest({key: original[key] for key in proof_keys}))
        self.assertLess(len(json.dumps(saved['memory_feedback'])), 2000)

    def test_identical_feedback_is_idempotent_and_current_rating_can_be_revised(self):
        self.feedback()
        first = self.saved()['memory_feedback']
        self.feedback()
        self.assertEqual(self.saved()['memory_feedback'], first)
        value = self.feedback('neutral', note='Review could not identify a useful effect from this memory.')
        self.assertEqual(value['feedback']['rating'], 'neutral')

    def test_source_response_review_project_or_context_change_invalidates_old_rating(self):
        self.feedback()
        baseline = self.saved()
        changes = [lambda r: r.update(response='Changed answer'),
                   lambda r: r['review'].update(note='Changed validation evidence after initial acceptance.'),
                   lambda r: r.update(assignment_project_id='beta'),
                   lambda r: r['memory_context'].update(user_id='other'),
                   lambda r: r['memory_context'].update(ids=['c' * 32]),
                   lambda r: r['memory_context'].update(trace_id='d' * 32),
                   lambda r: r['memory_context'].update(execution_requested=False)]
        for change in changes:
            with self.subTest(change=change):
                self.result = deepcopy(baseline)
                change(self.result)
                self.save()
                self.assertEqual(summary(self.result)['feedback']['status'], 'stale')
                with self.assertRaises(ValueError):
                    self.feedback()
                self.assertEqual(self.saved()['memory_feedback'], baseline['memory_feedback'])

    def test_held_pending_failed_and_unfinalized_sources_cannot_be_rated(self):
        for changes in ({'status': 'held'}, {'execution_status': 'failed'},
                        {'review_status': 'pending'}, {'finalized_at': None},
                        {'finalized_at': 'not a timestamp'}, {'review': {}}):
            with self.subTest(changes=changes):
                original = deepcopy(self.result)
                self.result.update(changes)
                self.save()
                with self.assertRaises(ValueError):
                    self.feedback()
                self.assertNotIn('memory_feedback', self.saved())
                self.result = original

    def test_empty_prepared_and_missing_user_context_cannot_be_rated(self):
        for changes in ({'ids': []}, {'execution_requested': False}, {'user_id': None}):
            with self.subTest(changes=changes):
                original = deepcopy(self.result)
                self.result['memory_context'].update(changes)
                self.save()
                with self.assertRaises(ValueError):
                    self.feedback()
                self.result = original

    def test_rating_reviewer_and_note_bounds_are_validated_before_writes(self):
        for kwargs in ({'rating': 'success'}, {'reviewer': ''}, {'reviewer': 'x' * 101},
                       {'note': 'ok'}, {'note': 'x' * 1001}, {'note': 'Some invalid control \x00 inside a note.'}):
            with self.subTest(kwargs=kwargs), self.assertRaises(ValueError):
                self.feedback(**kwargs)
        self.assertNotIn('memory_feedback', self.saved())

    def test_unrelated_memory_receipts_do_not_invalidate_feedback(self):
        self.feedback()
        self.result = self.saved()
        self.result['memory_outcome'] = {'status': 'remembered', 'memory_id': 'e' * 32}
        self.result['contribution_audit'] = {'status': 'updated'}
        self.assertEqual(summary(self.result)['feedback']['status'], 'reviewed')

    def test_feedback_keeps_existing_reviewed_memory_source_current(self):
        brain = BrainStore(self.root)
        memory = brain.propose({'project_id': 'alpha', 'kind': 'procedure',
            'title': 'Recovery validation', 'content': 'Retain reviewed recovery evidence.',
            'source': {'type': 'task', 'job_id': self.result['job_id'],
                       'review_sha256': digest(self.result['review'])}})
        brain.approve(memory['id'], 'Reviewer', 'Checked the saved source and validated this recovery procedure.')
        self.feedback()
        self.assertIn(memory['id'], [row['id'] for row in brain.search('Recovery validation', 'alpha')['results']])

    def test_default_uses_bounded_task_label_and_explicit_opt_out_is_frozen(self):
        args = argparse.Namespace(project='alpha', task='Recovery ' * 100, memory_query=None)
        plan = recall_plan(args)
        self.assertEqual(plan['policy'], 'task_label')
        self.assertLessEqual(len(plan['query']), 500)
        self.assertTrue(plan['enabled'])
        args.no_memory = True
        self.assertEqual(contract_fields(recall_plan(args)), {'memory_policy': 'disabled'})
        args.memory_query = 'recovery'
        with self.assertRaises(ValueError):
            recall_plan(args)

    def test_unscoped_tasks_do_not_search_and_explicit_query_needs_project(self):
        args = argparse.Namespace(task='Recovery', project=None)
        self.assertEqual(recall_plan(args)['policy'], 'unscoped')
        args.memory_query = 'recovery'
        with self.assertRaises(ValueError):
            recall_plan(args)


class DispatchMemoryUsageTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.store = TaskStore(self.root / 'runs/tasks')
        self.brain = BrainStore(self.root)
        memory = self.brain.propose({'project_id': 'alpha', 'kind': 'procedure', 'title': 'Recovery',
            'content': 'Preserve unknown execution evidence before scheduling another worker.',
            'source': {'type': 'user', 'note': 'Synthetic test input'}})
        self.memory = self.brain.approve(memory['id'], 'Tester', 'Checked this synthetic recovery procedure for the test.')
        self.prompt = self.root / 'brief.txt'
        self.prompt.write_text('PRIVATE BRIEF - review supplied material only.')
        self.args = argparse.Namespace(worker='claude', task='Recovery', project='alpha',
            assignment_id='recovery-test', size='small', prompt_file=self.prompt,
            output=self.root / 'answer.json')
        self.guard = Mock()
        self.guard.refresh.side_effect = lambda provider: {provider: {'ok': True}}
        self.guard.check.return_value = {'allowed': True, 'reservation_id': 'test-token'}
        self.invoke = Mock(return_value=subprocess.CompletedProcess(['worker'], 0,
            json.dumps({'result': 'Specific answer for independent review.'}), ''))

    def run_task(self):
        with patch.object(dispatcher, 'ensure_directories'), \
                patch.object(dispatcher, 'cloud_command', return_value=([sys.executable], 'prompt')) as command, \
                patch.object(dispatcher, 'invoke_cloud', self.invoke):
            value = dispatcher.dispatch(self.args, guard_factory=lambda: self.guard,
                store=self.store, workspaces=self.root / 'runtime/workspaces')
            self.command = command
            return value

    def test_default_recall_is_scoped_records_trace_and_enters_requested_prompt(self):
        value = self.run_task()
        context = value['memory_context']
        self.assertEqual(value['status'], 'awaiting_review')
        self.assertEqual(value['memory_policy'], 'task_label')
        self.assertEqual(context['query'], 'Recovery')
        self.assertEqual(context['project_id'], 'alpha')
        self.assertEqual(context['user_id'], 'local')
        self.assertEqual(context['ids'], [self.memory['id']])
        self.assertEqual(len(context['trace_id']), 32)
        self.assertGreaterEqual(context['elapsed_ms'], context['lookup_ms'])
        self.assertIn(context['context'], self.command.call_args.args[1])
        self.assertTrue(context['execution_requested'])
        self.assertEqual(summary(value)['feedback']['status'], 'not_evaluated')

    def test_default_assignment_reuse_does_not_repeat_lookup_or_model(self):
        first = self.run_task()
        with patch.object(BrainStore, 'search', side_effect=AssertionError('No repeated lookup')):
            second = self.run_task()
        self.assertTrue(second['assignment_reused'])
        self.assertEqual(second['job_id'], first['job_id'])
        self.assertEqual(self.invoke.call_count, 1)
        self.args.no_memory = True
        self.assertEqual(self.run_task()['status'], 'held')
        self.assertEqual(self.invoke.call_count, 1)

    def test_changed_default_task_label_changes_assignment_identity(self):
        self.run_task()
        self.args.task = 'Different recovery evidence'
        self.assertEqual(self.run_task()['status'], 'held')
        self.assertEqual(self.invoke.call_count, 1)

    def test_explicit_opt_out_prevents_lookup(self):
        self.args.no_memory = True
        with patch.object(BrainStore, 'search', side_effect=AssertionError('Opted out')):
            value = self.run_task()
        self.assertNotIn('memory_context', value)
        self.assertEqual(summary(value)['stage'], 'not_requested')
        self.assertEqual(value['memory_policy'], 'disabled')

    def test_quota_hold_keeps_prepared_context_unrequested(self):
        self.guard.check.return_value = {'allowed': False}
        value = self.run_task()
        self.assertEqual(value['status'], 'held')
        self.assertEqual(summary(value)['stage'], 'prepared')
        self.assertIsNotNone(summary(value)['trace_id'])
        self.invoke.assert_not_called()

    def test_brief_hold_keeps_lookup_incomplete_instead_of_claiming_retrieval(self):
        self.args.require_brief_check = True
        with patch.object(BrainStore, 'search', side_effect=AssertionError('Held before lookup')):
            value = self.run_task()
        self.assertEqual(value['status'], 'held')
        self.assertEqual(summary(value)['stage'], 'lookup_missing')
        self.invoke.assert_not_called()

    def test_fallback_revalidates_default_recall_after_memory_is_forgotten(self):
        from task_handoff import dispatch_with_handoff
        self.args.fallback_worker = ['grok']
        self.args.category = 'review'
        calls = []
        def admission(*args, **kwargs):
            calls.append(args[0])
            if len(calls) == 1:
                self.brain.forget(self.memory['id'], 'Tester', 'Removed before the fallback checks current evidence.')
                return {'allowed': False,
                    'reasons': ['window: task plus safety buffer exceeds available quota'],
                    'windows': [{'id': 'window', 'remaining_pct': 5, 'available_pct': 5}]}
            return {'allowed': True, 'reservation_id': 'test-token'}
        self.guard.check.side_effect = admission
        def run_step(args, *, store):
            return dispatcher.dispatch(args, guard_factory=lambda: self.guard, store=store,
                workspaces=self.root / 'runtime/workspaces')
        with patch.object(dispatcher, 'ensure_directories'), \
                patch.object(dispatcher, 'cloud_command', return_value=([sys.executable], 'prompt')), \
                patch.object(dispatcher, 'invoke_cloud', self.invoke):
            value = dispatch_with_handoff(self.args, dispatch_fn=run_step, store=self.store)
        self.assertEqual(calls, ['claude', 'grok'])
        self.assertEqual(value['worker'], 'grok')
        self.assertEqual(value['memory_context']['ids'], [])
        self.assertEqual(summary(value)['stage'], 'empty')
        first = json.loads(self.args.output.read_text())
        self.assertEqual(first['memory_context']['ids'], [self.memory['id']])
        self.assertFalse(first['memory_context']['execution_requested'])
        plan = json.loads(next((self.root / 'runs/handoffs').glob('*.json')).read_text())
        self.assertEqual(plan['contract']['memory_policy'], 'task_label')
        self.assertEqual(plan['contract']['memory_query'], 'Recovery')
        self.assertTrue(plan['steps'][1]['memory_context_changed'])
        self.invoke.assert_called_once()


if __name__ == '__main__':
    unittest.main()
