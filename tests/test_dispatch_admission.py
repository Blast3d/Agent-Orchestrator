"""Winning features must hold or reuse assignments before any provider work."""
import argparse
from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'app'))
import dispatch_worker as dispatcher
import claude_models
from task_store import TaskStore


class AdmissionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.prompt = self.root / 'brief.txt'
        self.prompt.write_text('Goal: Explain this example\nInputs: A supplied example\nOutput: One paragraph\nChecks: Address the example clearly', encoding='utf-8')
        self.store = TaskStore(self.root / 'tasks')
        self.guard = Mock()
        self.guard.refresh.side_effect = lambda provider: {provider: {'ok': True}}
        self.guard.check.return_value = {'allowed': True, 'reservation_id': 'test-reservation'}
        self.factory = Mock(return_value=self.guard)
        self.invoke = Mock(return_value=subprocess.CompletedProcess([], 0, json.dumps({'result': 'A useful answer.'}), ''))
        self.args = argparse.Namespace(worker='claude', prompt_file=self.prompt, output=self.root / 'answer.json',
            size='small', task='Explain example', category='research', claude_model='sonnet', claude_effort='medium',
            require_brief_check=True, project='test-project', assignment_id='explain-v1', revision_of=None)
        policy = self.root / 'config/workers.json'
        policy.parent.mkdir()
        policy.write_text(json.dumps({'policy': {'paused_claude_model_families': ['fable']},
            'workers': [{'id': 'claude', 'requested_model': 'opus'}]}), encoding='utf-8')
        for item in (patch.object(dispatcher, 'ensure_directories'),
                     patch.object(claude_models, 'ROOT', self.root),
                     patch.object(dispatcher, 'invoke_cloud', self.invoke),
                     patch.object(dispatcher, 'cloud_command', return_value=([sys.executable], 'brief'))):
            item.start()
            self.addCleanup(item.stop)

    def run_task(self, args=None):
        return dispatcher.dispatch(args or self.args, guard_factory=self.factory, store=self.store,
                                   workspaces=self.root / 'workspaces')

    def test_complete_brief_is_saved_with_task(self):
        result = self.run_task()
        self.assertEqual(result['status'], 'awaiting_review')
        self.assertTrue(result['brief_check']['ok'])
        self.assertTrue(json.loads(Path(result['canonical_result']).read_text())['brief_check']['ok'])

    def test_configured_default_matches_command_receipt_and_assignment_contract(self):
        self.args.claude_model = None
        result = self.run_task()
        self.assertEqual(result['status'], 'awaiting_review')
        self.assertEqual(result['requested_model'], 'opus')
        self.assertEqual(dispatcher.cloud_command.call_args.args[3], 'opus')
        self.args.claude_model = 'opus'
        self.args.output = self.root / 'same-model.json'
        repeated = self.run_task()
        self.assertTrue(repeated['assignment_reused'])
        self.assertEqual(repeated['job_id'], result['job_id'])
        self.invoke.assert_called_once()

    def test_paused_models_hold_before_quota_process_or_output(self):
        for model in ('fable', 'claude-fable-5', 'claude-fable-5[1m]'):
            with self.subTest(model=model):
                self.args.claude_model = model
                result = self.run_task()
                self.assertEqual(result['status'], 'held')
                self.assertIn('paused by the user', result['reason'])
                self.assertIsNone(result['reservation_id'])
        self.factory.assert_not_called()
        self.invoke.assert_not_called()
        dispatcher.cloud_command.assert_not_called()
        self.assertFalse(self.args.output.exists())

    def test_explicit_sonnet_and_haiku_remain_available(self):
        self.args.assignment_id = self.args.project = None
        for model in ('sonnet', 'haiku'):
            with self.subTest(model=model):
                self.args.claude_model = model
                self.args.output = self.root / (model + '.json')
                result = self.run_task()
                self.assertEqual(result['requested_model'], model)
                self.assertEqual(result['status'], 'awaiting_review')

    def test_incomplete_strict_brief_has_durable_feedback_and_no_quota(self):
        self.prompt.write_text('Goal: Do useful work\nInputs: TODO', encoding='utf-8')
        result = self.run_task()
        self.assertEqual(result['status'], 'held')
        self.assertFalse(result['brief_check']['ok'])
        self.assertTrue(Path(result['canonical_result']).is_file())
        self.factory.assert_not_called()
        self.invoke.assert_not_called()

    def test_legacy_freeform_is_allowed_but_missing_sections_recorded(self):
        self.args.require_brief_check = False
        self.prompt.write_text('Describe this example briefly.', encoding='utf-8')
        result = self.run_task()
        self.assertEqual(result['status'], 'awaiting_review')
        self.assertFalse(result['brief_check']['ok'])
        self.invoke.assert_called_once()

    def test_invalid_utf8_is_held_even_without_strict_flag(self):
        self.args.require_brief_check = False
        self.prompt.write_bytes(b'\xff\xfeinvalid')
        result = self.run_task()
        self.assertEqual(result['status'], 'held')
        self.assertTrue(any('UTF-8' in error for error in result['brief_check']['errors']))
        self.factory.assert_not_called()

    def test_identical_assignment_returns_current_review_without_export_or_quota(self):
        first = self.run_task()
        self.store.review(first['job_id'], 'accepted', 'Codex', 'Checked the answer against the supplied example and required output.')
        self.factory.reset_mock()
        self.invoke.reset_mock()
        self.args.output = self.root / 'repeat.json'
        repeated = self.run_task()
        self.assertTrue(repeated['assignment_reused'])
        self.assertEqual((repeated['job_id'], repeated['status'], repeated['response']),
                         (first['job_id'], 'accepted', 'A useful answer.'))
        self.assertFalse(self.args.output.exists())
        self.factory.assert_not_called()
        self.invoke.assert_not_called()

    def test_changed_prompt_or_model_is_a_conflict_before_quota(self):
        self.run_task()
        self.factory.reset_mock()
        self.invoke.reset_mock()
        self.args.claude_model = 'opus'
        self.args.output = self.root / 'new.json'
        self.assertEqual(self.run_task()['status'], 'held')
        self.factory.assert_not_called()
        self.invoke.assert_not_called()
        self.assertFalse(self.args.output.exists())

    def test_deliberate_revision_creates_linked_new_job(self):
        first = self.run_task()
        self.args.assignment_id = 'explain-v2'
        self.args.revision_of = first['job_id']
        self.args.output = self.root / 'revision.json'
        result = self.run_task()
        self.assertNotEqual(result['job_id'], first['job_id'])
        self.assertEqual(result['revision_of'], first['job_id'])
        self.assertEqual(self.invoke.call_count, 2)

    def test_uncertain_job_is_reused_without_releasing_or_repeating(self):
        self.invoke.side_effect = dispatcher.WorkerInterrupted('timeout')
        first = self.run_task()
        self.factory.reset_mock()
        self.invoke.reset_mock()
        self.args.output = self.root / 'uncertain-repeat.json'
        repeated = self.run_task()
        self.assertEqual((repeated['job_id'], repeated['status']), (first['job_id'], 'recovery_required'))
        self.assertEqual(repeated['reservation_state'], 'held_for_reconciliation')
        self.factory.assert_not_called()
        self.invoke.assert_not_called()

    def test_simultaneous_identical_assignments_invoke_once(self):
        started, release = threading.Event(), threading.Event()
        original = self.invoke.return_value
        def slow(*args, **kwargs):
            started.set()
            if not release.wait(5):
                raise RuntimeError('Test rendezvous timed out')
            return original
        self.invoke.side_effect = slow
        with ThreadPoolExecutor(max_workers=2) as pool:
            first_future = pool.submit(self.run_task)
            self.assertTrue(started.wait(5))
            second_args = argparse.Namespace(**vars(self.args))
            second_args.output = self.root / 'concurrent.json'
            try:
                second = pool.submit(self.run_task, second_args).result(timeout=5)
                self.assertTrue(second['assignment_reused'])
            finally:
                release.set()
            first = first_future.result(timeout=5)
        self.assertEqual(first['job_id'], second['job_id'])
        self.invoke.assert_called_once()
        self.guard.check.assert_called_once()
        self.assertFalse(second_args.output.exists())

    def test_unkeyed_identical_opinions_remain_independent(self):
        self.args.project = self.args.assignment_id = None
        first = self.run_task()
        self.args.output = self.root / 'independent.json'
        second = self.run_task()
        self.assertNotEqual(first['job_id'], second['job_id'])
        self.assertEqual(self.invoke.call_count, 2)

    def handoff(self):
        from task_handoff import dispatch_with_handoff
        self.args.fallback_worker = ['grok']
        def run(args, store):
            return dispatcher.dispatch(args, store=store, guard_factory=self.factory,
                                       workspaces=self.root / 'workspaces')
        return dispatch_with_handoff(self.args, dispatch_fn=run, store=self.store)

    def test_quota_handoff_reuses_entire_chain_and_keeps_parent_result(self):
        self.guard.check.side_effect = [
            {'allowed': False, 'reasons': ['claude-weekly: task plus safety buffer exceeds available quota'],
             'windows': [{'id': 'claude-weekly', 'remaining_pct': 2, 'available_pct': 2}]},
            {'allowed': True, 'reservation_id': 'replacement-reservation'}]
        result = self.handoff()
        self.assertEqual(result['status'], 'awaiting_review')
        self.assertEqual([step['worker'] for step in result['handoff_chain']], ['claude', 'grok'])
        parent_id = result['handoff_chain'][0]['job_id']
        self.assertEqual(result['handoff_from_job_id'], parent_id)
        self.assertEqual(result['revision_of'], parent_id)
        self.assertEqual(json.loads(self.args.output.read_text())['status'], 'held')
        self.invoke.assert_called_once()
        self.factory.reset_mock()
        self.invoke.reset_mock()
        repeated = self.handoff()
        self.assertEqual(repeated['job_id'], result['job_id'])
        self.factory.assert_not_called()
        self.invoke.assert_not_called()

    def test_exited_rate_limit_rejection_hands_off_with_finished_reservation(self):
        self.invoke.side_effect = [subprocess.CompletedProcess([], 1, '', 'HTTP 429 rate limit'),
                                  subprocess.CompletedProcess([], 0, json.dumps({'result': 'A replacement answer.'}), '')]
        result = self.handoff()
        self.assertEqual(result['status'], 'awaiting_review')
        first = json.loads(self.args.output.read_text())
        self.assertEqual(first['failure_kind'], 'quota_exhausted')
        self.assertEqual(first['reservation_state'], 'finished_pending_fresh_quota')
        self.assertEqual(len(result['handoff_chain']), 2)
        self.guard.block.assert_called_once_with('claude')

    def test_timeout_is_not_an_automatic_quota_handoff(self):
        self.invoke.side_effect = dispatcher.WorkerInterrupted('timeout')
        result = self.handoff()
        self.assertEqual(result['status'], 'recovery_required')
        self.assertEqual(len(result['handoff_chain']), 1)
        self.invoke.assert_called_once()
        self.guard.finish.assert_not_called()

    def test_unknown_allowance_stays_held_even_with_fallback(self):
        self.guard.check.return_value = {'allowed': False, 'reasons': ['claude-weekly: usage unknown; refresh or supply a current account reading'], 'windows': []}
        result = self.handoff()
        self.assertEqual(result['status'], 'held')
        self.assertEqual(len(result['handoff_chain']), 1)
        self.invoke.assert_not_called()

    def test_unrelated_429_in_authentication_error_does_not_handoff(self):
        self.invoke.return_value = subprocess.CompletedProcess([], 1, '', 'Authentication failed while opening account-429.json')
        result = self.handoff()
        self.assertEqual(result['status'], 'failed')
        self.assertEqual(len(result['handoff_chain']), 1)
        self.invoke.assert_called_once()
        self.guard.block.assert_not_called()

    def test_quoted_answer_is_not_terminal_quota_evidence(self):
        completed = subprocess.CompletedProcess([], 1, json.dumps({'response': 'Example: HTTP 429 rate limit exceeded'}), 'Worker authentication failed')
        self.assertFalse(dispatcher.confirmed_quota_rejection(completed))
        completed.stdout = json.dumps({'error': {'code': 429, 'message': 'Too many requests'}})
        self.assertTrue(dispatcher.confirmed_quota_rejection(completed))


if __name__ == '__main__':
    unittest.main()
