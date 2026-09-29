"""Repeat safety without live provider or quota operations."""
import argparse
from contextlib import redirect_stdout
import hashlib
from io import StringIO
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'app'))
import dispatch_worker as dispatcher
from assignment_receipts import AssignmentReceipts
from memory_usage import contract_fields, recall_plan
from task_store import TaskStore, timestamp
from usage_guard import Guard


class RepeatSafetyTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.prompt = self.root / 'brief.txt'
        self.prompt.write_text('Find concrete defects in the supplied code.', encoding='utf-8')
        self.store = TaskStore(self.root / 'tasks')
        self.output = self.root / 'original.json'
        self.args = argparse.Namespace(worker='claude', prompt_file=self.prompt,
            output=self.output, task='Audit dispatcher', size='small', category='general',
            project='repeat-safety', assignment_id='assignment-one', revision_of=None,
            no_memory=True, memory_query=None, memory_depth='auto', memory_profile=None,
            require_brief_check=False, timeout_seconds=None, claude_model=None,
            claude_effort='medium')
        self.guard = Mock()
        self.guard.policy = {}
        self.guard.refresh.side_effect = lambda provider: {provider: {'ok': True}}
        self.guard.check.return_value = {'allowed': True, 'reservation_id': 'reservation-test'}
        self.guard.open_reservation_jobs.return_value = []
        self.factory = Mock(return_value=self.guard)
        self.invoke = Mock(return_value=subprocess.CompletedProcess(['worker'], 0,
            json.dumps({'result': 'Answer to review.'}), ''))
        budget = Mock()
        budget.reserve.return_value = 'storage-test'
        for item in (patch.object(dispatcher, 'ensure_directories'),
                     patch.object(dispatcher, 'select_model', return_value='sonnet'),
                     patch.object(dispatcher, 'invoke_cloud', self.invoke),
                     patch.object(dispatcher, 'cloud_command', return_value=([sys.executable], 'brief')),
                     patch.object(dispatcher, 'load_operating_context', return_value={'context': 'guide'}),
                     patch.object(dispatcher, 'verify_run_startup', return_value=None),
                     patch.object(dispatcher, 'verify_request_context'),
                     patch.object(dispatcher, 'StorageBudget', return_value=budget)):
            item.start()
            self.addCleanup(item.stop)

    def arguments(self, **changes):
        values = vars(self.args).copy()
        values.update(changes)
        return argparse.Namespace(**values)

    def claim(self, assignment_id='assignment-one', output=None):
        args = self.arguments(assignment_id=assignment_id, output=output or self.output)
        metadata = {'worker': 'claude', 'task': args.task, 'size': args.size,
            'category': args.category, 'prompt_sha256': hashlib.sha256(self.prompt.read_bytes()).hexdigest(),
            'requested_output': str(args.output.absolute())}
        contract = {key: metadata[key] for key in ('worker', 'prompt_sha256', 'size', 'category')}
        contract.update(claude_model='sonnet', claude_effort='medium', require_brief_check=False)
        contract.update(contract_fields(recall_plan(args)))
        record, reused = AssignmentReceipts(self.store).claim(args.project, assignment_id,
            contract, metadata)
        self.assertFalse(reused)
        return record

    def finalize(self, record, status='awaiting_review'):
        result = dict(record, status=status,
            execution_status='held' if status == 'held' else 'failed' if status == 'failed' else 'succeeded',
            finalized_at=timestamp(), export_status='written', cleanup_errors=[])
        self.store.save(record['job_id'], result)
        return result

    def dispatch(self, **changes):
        return dispatcher.dispatch(self.arguments(**changes), guard_factory=self.factory,
            store=self.store, workspaces=self.root / 'workspaces')

    def main(self, assignment_id, output):
        argv = ['claude', '--prompt-file', str(self.prompt), '--output', str(output),
            '--task', self.args.task, '--size', 'small', '--project', self.args.project,
            '--assignment-id', assignment_id, '--no-memory']
        stdout = StringIO()
        with patch.object(dispatcher, 'TASKS', self.store.root), patch.object(dispatcher, 'Guard', return_value=self.guard):
            with redirect_stdout(stdout):
                code = dispatcher.main(argv)
        return code, json.loads(stdout.getvalue())

    def test_incomplete_reuse_exits_two_without_provider_call(self):
        record = self.claim()
        code, summary = self.main('assignment-one', self.root / 'repeat.json')
        self.assertEqual(code, 2)
        self.assertEqual(summary['job_id'], record['job_id'])
        self.assertEqual(summary['reuse_state'], 'incomplete')
        self.assertEqual(summary['export_status'], 'unclaimed')
        self.assertIn('has not finished', summary['reason'])
        self.assertFalse((self.root / 'repeat.json').exists())
        self.invoke.assert_not_called()

    def test_final_reuse_exit_codes(self):
        for status, expected in [('awaiting_review', 0), ('held', 2), ('failed', 1)]:
            with self.subTest(status=status):
                assignment = 'final-' + status.replace('_', '-')
                output = self.root / (assignment + '.json')
                self.finalize(self.claim(assignment, output), status)
                code, summary = self.main(assignment, output)
                self.assertEqual(code, expected)
                self.assertEqual(summary['reuse_state'], 'final')
                self.assertEqual(summary['export_status'], 'original')
        self.invoke.assert_not_called()

    def test_new_output_is_copy_and_canonical_is_unchanged(self):
        record = self.finalize(self.claim())
        canonical = Path(record['canonical_result'])
        before = canonical.read_bytes()
        target = self.root / 'copy.json'
        repeated = self.dispatch(output=target)
        self.assertEqual(repeated['export_status'], 'written')
        self.assertEqual(repeated['reuse_state'], 'final')
        exported = json.loads(target.read_text(encoding='utf-8'))
        self.assertTrue(exported['assignment_reused'])
        self.assertEqual(exported['repeated_requested_output'], str(target.absolute()))
        self.assertEqual(canonical.read_bytes(), before)
        self.invoke.assert_not_called()

    def test_existing_new_output_fails_export(self):
        self.finalize(self.claim())
        target = self.root / 'existing.json'
        target.write_text('keep', encoding='utf-8')
        code, summary = self.main('assignment-one', target)
        self.assertEqual(code, 1)
        self.assertEqual(summary['export_status'], 'failed')
        self.assertEqual(summary['export_error'], 'FileExistsError')
        self.assertEqual(target.read_text(encoding='utf-8'), 'keep')
        self.invoke.assert_not_called()

    def test_original_output_is_not_written_again(self):
        self.finalize(self.claim())
        self.output.write_text('original marker', encoding='utf-8')
        repeated = self.dispatch()
        self.assertEqual(repeated['export_status'], 'original')
        self.assertEqual(self.output.read_text(encoding='utf-8'), 'original marker')
        self.invoke.assert_not_called()

    def test_open_reservation_holds_automatic_identity_before_reservation(self):
        digest = hashlib.sha256(self.prompt.read_bytes()).hexdigest()
        earlier = self.store.create(worker='claude', prompt_sha256=digest,
            task='Earlier attempt', size='small', category='general')
        self.guard.open_reservation_jobs.return_value = [earlier['job_id']]
        target = self.root / 'automatic.json'
        args = dict(assignment_id='auto-' + 'a' * 32, output=target,
                    automatic_fallbacks_applied=True, automatic_assignment_id_added=True)
        result = self.dispatch(**args)
        self.assertEqual(result['status'], 'held')
        self.assertIn(earlier['job_id'], result['reason'])
        self.assertEqual(result['unresolved_repeat_of'], earlier['job_id'])
        self.assertIsNone(result['reservation_id'])
        # The repeat is recorded as a held task; nothing is reserved or sent.
        self.assertEqual(self.store.directory(result['job_id']).name, result['job_id'])
        self.guard.check.assert_not_called()
        self.invoke.assert_not_called()

    def test_chosen_identity_and_different_brief_can_proceed(self):
        digest = hashlib.sha256(self.prompt.read_bytes()).hexdigest()
        earlier = self.store.create(worker='claude', prompt_sha256=digest,
            task='Earlier attempt', size='small', category='general')
        self.guard.open_reservation_jobs.return_value = [earlier['job_id']]
        chosen = self.dispatch(assignment_id='chosen', output=self.root / 'chosen.json',
                               automatic_fallbacks_applied=True, automatic_assignment_id_added=False)
        self.assertEqual(chosen['status'], 'awaiting_review')
        self.guard.open_reservation_jobs.assert_not_called()
        other_prompt = self.root / 'other.txt'
        other_prompt.write_text('Find a different concrete defect.', encoding='utf-8')
        different = self.dispatch(assignment_id='auto-' + 'b' * 32,
            automatic_fallbacks_applied=True, automatic_assignment_id_added=True,
            prompt_file=other_prompt, output=self.root / 'different.json')
        self.assertEqual(different['status'], 'awaiting_review')
        self.assertEqual(self.invoke.call_count, 2)

    def test_guard_open_jobs_filters_worker_and_finished_reservations(self):
        guard = Guard(self.root / 'guard')
        job = 'a' * 32
        with guard.state() as data:
            data['reservations'] = {
                'open': {'worker': 'claude', 'task': 'Audit [job:' + job + ']', 'finished_at': None},
                'finished': {'worker': 'claude', 'task': 'Audit [job:' + 'b' * 32 + ']', 'finished_at': timestamp()},
                'other': {'worker': 'codex', 'task': 'Audit [job:' + 'c' * 32 + ']', 'finished_at': None}}
        self.assertEqual(guard.open_reservation_jobs('claude'), [job])


if __name__ == '__main__':
    unittest.main()
