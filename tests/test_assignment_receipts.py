"""Assignment receipts preserve identity across concurrency, crashes and revisions."""
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import sys
import tempfile
from threading import Barrier
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'app'))
import assignment_receipts as receipts
from task_store import TaskStore, timestamp, write_json


class AssignmentReceiptTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.store = TaskStore(self.root / 'tasks')
        self.receipts = receipts.AssignmentReceipts(self.store)
        self.contract = {'worker': 'claude', 'prompt_sha256': hashlib.sha256(b'brief').hexdigest(),
                         'size': 'small', 'category': 'review', 'claude_model': 'sonnet',
                         'claude_effort': 'medium', 'require_brief_check': False}

    def metadata(self, contract=None, output='first.json'):
        contract = contract or self.contract
        return {**{k: contract[k] for k in ('worker', 'prompt_sha256', 'size', 'category')},
                'task': 'Check the supplied code', 'prompt_bytes': 5,
                'requested_output': str(self.root / output)}

    def claim(self, project='sample', assignment='review-1', contract=None, output='first.json',
              revision_of=None):
        contract = contract or self.contract
        return self.receipts.claim(project, assignment, contract, self.metadata(contract, output), revision_of)

    def index_path(self, project='sample'):
        return self.receipts.root / (hashlib.sha256(project.encode()).hexdigest() + '.json')

    def read_index(self):
        return json.loads(self.index_path().read_text(encoding='utf-8'))

    def finalize(self, record, status='accepted', execution='succeeded', **updates):
        current = dict(record, status=status, execution_status=execution,
                       review_status='accepted' if status == 'accepted' else 'pending',
                       ended_at=timestamp(), finalized_at=timestamp(), cleanup_errors=[], **updates)
        self.store.save(record['job_id'], current)
        return current

    def test_intent_is_durable_before_task_creation(self):
        create = self.store.create
        def inspect_intent(**metadata):
            entry = self.read_index()['assignments']['review-1']
            self.assertEqual(entry['state'], 'intent')
            self.assertIsNone(entry['job_id'])
            self.assertEqual(entry['intent_id'], metadata['assignment_intent_id'])
            return create(**metadata)
        with patch.object(self.store, 'create', side_effect=inspect_intent):
            record, reused = self.claim()
        self.assertFalse(reused)
        self.assertEqual(self.read_index()['assignments']['review-1']['job_id'], record['job_id'])

    def test_memory_policy_and_effective_query_are_frozen_together(self):
        contract = dict(self.contract, memory_policy='task_label', memory_query='Recovery')
        first, _ = self.claim(contract=contract)
        self.finalize(first)
        repeated, reused = self.claim(contract=contract)
        self.assertTrue(reused)
        self.assertEqual(repeated['job_id'], first['job_id'])
        for changed in (dict(self.contract, memory_policy='disabled'),
                        dict(contract, memory_query='New task label'),
                        dict(contract, memory_policy='explicit')):
            with self.subTest(contract=changed), self.assertRaises(receipts.AssignmentConflict):
                self.claim(contract=changed)

    def test_invalid_memory_policy_or_query_pair_stops_before_task_creation(self):
        for fields in ({'memory_policy': 'unknown'}, {'memory_policy': 'task_label'},
                       {'memory_policy': 'explicit'},
                       {'memory_policy': 'disabled', 'memory_query': 'Recovery'}):
            with self.subTest(fields=fields), self.assertRaises(ValueError):
                self.claim(contract=dict(self.contract, **fields))
        self.assertEqual(list(self.store.root.glob('*/record.json')), [])

    def test_concurrent_claims_create_exactly_one_task_across_helpers(self):
        barrier = Barrier(8)
        def submit(index):
            barrier.wait(timeout=5)
            return receipts.AssignmentReceipts(TaskStore(self.root / 'tasks')).claim(
                'sample', 'review-1', self.contract, self.metadata(output=str(index) + '.json'))
        with ThreadPoolExecutor(max_workers=8) as pool:
            results = list(pool.map(submit, range(8)))
        self.assertEqual(len({record['job_id'] for record, _ in results}), 1)
        self.assertEqual(sum(not reused for _, reused in results), 1)
        self.assertEqual(len(list(self.store.root.glob('*/record.json'))), 1)

    def test_reuse_ignores_export_path_and_returns_latest_reviewed_answer(self):
        original, _ = self.claim()
        current = self.finalize(original, response='Latest saved answer',
                                review={'reviewer': 'Codex', 'note': 'Checked against the supplied source.'})
        with patch.object(self.store, 'create') as create:
            repeated, reused = self.claim(output='different.json')
        self.assertTrue(reused)
        self.assertEqual(repeated['job_id'], original['job_id'])
        self.assertEqual(repeated['response'], current['response'])
        self.assertEqual(repeated['review'], current['review'])
        self.assertEqual(repeated['requested_output'], original['requested_output'])
        self.assertFalse((self.root / 'different.json').exists())
        create.assert_not_called()

    def test_preparing_task_reuses_record_without_inventing_a_result(self):
        first, _ = self.claim()
        second, reused = self.claim()
        self.assertTrue(reused)
        self.assertEqual(first, second)
        self.assertFalse((self.store.directory(first['job_id']) / 'result.json').exists())

    def test_reuse_never_restarts_running_failed_or_uncertain_work(self):
        first, _ = self.claim()
        for status, execution in [('running', 'running'), ('failed', 'failed'),
                                  ('recovery_required', 'uncertain')]:
            with self.subTest(status=status):
                current = dict(first, status=status, execution_status=execution)
                self.store.save(first['job_id'], current)
                with patch.object(self.store, 'create') as create:
                    repeated, reused = self.claim()
                self.assertTrue(reused)
                self.assertEqual(repeated['status'], status)
                self.assertEqual(repeated['job_id'], first['job_id'])
                create.assert_not_called()

    def test_each_changed_contract_field_conflicts(self):
        self.claim()
        changes = [
            {'worker': 'grok', 'claude_model': None, 'claude_effort': None},
            {'prompt_sha256': 'a' * 64}, {'size': 'medium'}, {'category': 'coding'},
            {'claude_model': 'opus'}, {'claude_effort': 'high'}, {'require_brief_check': True}]
        for fields in changes:
            with self.subTest(fields=fields), patch.object(self.store, 'create') as create:
                with self.assertRaises(receipts.AssignmentConflict):
                    self.claim(contract=dict(self.contract, **fields))
                create.assert_not_called()
        self.assertEqual(len(list(self.store.root.glob('*/record.json'))), 1)

    def test_distinct_keys_and_projects_preserve_independent_opinions(self):
        a, _ = self.claim()
        b, _ = self.claim(assignment='review-2')
        c, _ = self.claim(project='another-project')
        self.assertEqual(len({a['job_id'], b['job_id'], c['job_id']}), 3)

    def test_invalid_identifiers_and_contract_fail_before_receipt_creation(self):
        for invalid in ('', '../escape', 'folder/name', 'two words', 'x' * 161, None):
            with self.subTest(identifier=invalid):
                with self.assertRaises(ValueError):
                    self.claim(project=invalid)
                with self.assertRaises(ValueError):
                    self.claim(assignment=invalid)
        for changes in ({'prompt_sha256': 'bad'}, {'size': 'huge'}, {'require_brief_check': 1},
                        {'category': ''}, {'claude_effort': None}, {'output': 'extra.json'}):
            with self.subTest(contract=changes), self.assertRaises(ValueError):
                self.claim(contract=dict(self.contract, **changes))
        self.assertFalse(self.receipts.root.exists())
        self.assertEqual(list(self.store.root.iterdir()), [])

    def test_mismatched_or_assignment_owned_metadata_is_rejected(self):
        for metadata in (dict(self.metadata(), worker='grok'),
                         dict(self.metadata(), assignment_id='injected'),
                         dict(self.metadata(), status='accepted'),
                         dict(self.metadata(), prompt_bytes=float('nan'))):
            with self.subTest(metadata=metadata), self.assertRaises(ValueError):
                self.receipts.claim('sample', 'review-1', self.contract, metadata)
        self.assertFalse(self.receipts.root.exists())

    def test_failed_intent_write_creates_no_task(self):
        with patch.object(receipts, 'write_json', side_effect=OSError('disk full')):
            with self.assertRaises(receipts.AssignmentIncomplete):
                self.claim()
        self.assertEqual(list(self.store.root.iterdir()), [])
        record, reused = self.claim()
        self.assertFalse(reused)
        self.assertEqual(record['status'], 'preparing')

    def test_task_creation_failure_leaves_intent_and_blocks_automatic_retry(self):
        with patch.object(self.store, 'create', side_effect=OSError('task write failed')):
            with self.assertRaises(receipts.AssignmentIncomplete):
                self.claim()
        self.assertEqual(self.read_index()['assignments']['review-1']['state'], 'intent')
        with patch.object(self.store, 'create') as create:
            with self.assertRaises(receipts.AssignmentIncomplete):
                self.claim()
            create.assert_not_called()

    def test_crash_after_task_creation_never_creates_a_second_task(self):
        calls = []
        def fail_final_write(path, value):
            calls.append(deepcopy(value))
            if len(calls) == 2:
                raise OSError('receipt commit interrupted')
            write_json(path, value)
        with patch.object(receipts, 'write_json', side_effect=fail_final_write):
            with self.assertRaises(receipts.AssignmentIncomplete):
                self.claim()
        self.assertEqual(len(list(self.store.root.glob('*/record.json'))), 1)
        self.assertEqual(self.read_index()['assignments']['review-1']['state'], 'intent')
        with patch.object(self.store, 'create') as create:
            with self.assertRaises(receipts.AssignmentIncomplete):
                self.claim()
            create.assert_not_called()

    def test_process_interruption_preserves_claim_intent(self):
        with patch.object(self.store, 'create', side_effect=KeyboardInterrupt):
            with self.assertRaises(KeyboardInterrupt):
                self.claim()
        with self.assertRaises(receipts.AssignmentIncomplete):
            self.claim()
        self.assertEqual(list(self.store.root.iterdir()), [])

    def test_invalid_json_or_duplicate_fields_fail_closed(self):
        self.claim()
        for malformed in ('{invalid', '[]', '{"schema_version":1,"schema_version":1}'):
            with self.subTest(malformed=malformed):
                self.index_path().write_text(malformed, encoding='utf-8')
                with patch.object(self.store, 'create') as create:
                    with self.assertRaises(receipts.AssignmentIncomplete):
                        self.claim()
                    create.assert_not_called()

    def test_tampered_receipt_identity_fails_closed(self):
        self.claim()
        original = self.read_index()
        edits = [
            lambda d: d.update(project_id='another'),
            lambda d: d.update(schema_version=True),
            lambda d: d['assignments']['review-1'].update(contract_sha256='a' * 64),
            lambda d: d['assignments']['review-1'].update(job_id='../escape'),
            lambda d: d['assignments']['review-1'].update(state='complete'),
            lambda d: d['assignments'].update(other=dict(d['assignments']['review-1'], assignment_id='other'))]
        for edit in edits:
            with self.subTest(edit=edit):
                data = deepcopy(original)
                edit(data)
                write_json(self.index_path(), data)
                with self.assertRaises(receipts.AssignmentIncomplete):
                    self.claim()
        self.assertEqual(len(list(self.store.root.glob('*/record.json'))), 1)

    def test_deleted_index_or_receipt_does_not_erase_existing_identity(self):
        self.claim()
        original = self.read_index()
        self.index_path().unlink()
        with self.assertRaises(receipts.AssignmentIncomplete):
            self.claim()
        original['assignments'].clear()
        write_json(self.index_path(), original)
        with self.assertRaises(receipts.AssignmentIncomplete):
            self.claim()
        self.assertEqual(len(list(self.store.root.glob('*/record.json'))), 1)

    def test_missing_record_or_final_result_cannot_claim_accepted_work(self):
        original, _ = self.claim()
        self.finalize(original, response='Saved response')
        directory = self.store.directory(original['job_id'])
        (directory / 'result.json').unlink()
        with self.assertRaises(receipts.AssignmentIncomplete):
            self.claim()
        (directory / 'record.json').unlink()
        with self.assertRaises(receipts.AssignmentIncomplete):
            self.claim()
        self.assertEqual(len(list(self.store.root.iterdir())), 1)

    def test_corrupt_result_is_not_replaced_with_preparing_record(self):
        record, _ = self.claim()
        (self.store.directory(record['job_id']) / 'result.json').write_text('{partial', encoding='utf-8')
        with self.assertRaises(receipts.AssignmentIncomplete):
            self.claim()

    def test_mismatched_task_identity_fails_closed(self):
        record, _ = self.claim()
        for changes in ({'assignment_id': 'other'}, {'prompt_sha256': 'b' * 64},
                        {'canonical_result': str(self.root / 'unrelated.json')}):
            with self.subTest(changes=changes):
                write_json(self.store.directory(record['job_id']) / 'record.json', dict(record, **changes))
                with self.assertRaises(receipts.AssignmentIncomplete):
                    self.claim()

    def test_corrupt_lifecycle_and_worker_settings_fail_closed(self):
        record, _ = self.claim()
        changes = [{'status': 'unknown-state'}, {'execution_status': None},
                   {'review_status': 'probably-done'}, {'cleanup_errors': [123]},
                   {'status': 'accepted', 'execution_status': 'succeeded', 'review_status': 'accepted'},
                   {'requested_model': 'opus'}, {'requested_effort': 'high'},
                   {'require_brief_check': True}]
        for update in changes:
            with self.subTest(update=update):
                write_json(self.store.directory(record['job_id']) / 'record.json', dict(record, **update))
                with self.assertRaises(receipts.AssignmentIncomplete):
                    self.claim()

    def test_revision_links_new_key_to_finalized_predecessor(self):
        previous, _ = self.claim()
        self.finalize(previous, status='failed', execution='failed',
                      reservation_id='reservation-1', reservation_state='finished_pending_fresh_quota')
        changed = dict(self.contract, prompt_sha256='b' * 64)
        record, reused = self.claim(assignment='review-2', contract=changed, revision_of=previous['job_id'])
        self.assertFalse(reused)
        self.assertEqual(record['revision_of'], previous['job_id'])
        self.assertNotEqual(record['job_id'], previous['job_id'])
        repeated, reused = self.claim(assignment='review-2', contract=changed, revision_of=previous['job_id'])
        self.assertTrue(reused)
        self.assertEqual(record['job_id'], repeated['job_id'])

    def test_revision_accepts_finalized_held_or_successful_work(self):
        for status, execution in [('held', 'held'), ('accepted', 'succeeded'),
                                  ('awaiting_review', 'succeeded')]:
            with self.subTest(status=status):
                previous, _ = self.claim(assignment=status)
                self.finalize(previous, status=status, execution=execution)
                revised, reused = self.claim(assignment=status + '-new', revision_of=previous['job_id'])
                self.assertFalse(reused)
                self.assertEqual(revised['revision_of'], previous['job_id'])

    def test_revision_requires_known_other_assignment_in_same_project(self):
        original, _ = self.claim()
        self.finalize(original)
        for project, assignment, revision in [('sample', 'review-1', original['job_id']),
                                               ('other', 'review-2', original['job_id']),
                                               ('sample', 'review-2', 'b' * 32)]:
            with self.subTest(project=project, assignment=assignment, revision=revision):
                with self.assertRaises(receipts.AssignmentConflict):
                    self.claim(project=project, assignment=assignment, revision_of=revision)
        with self.assertRaises(ValueError):
            self.claim(assignment='review-2', revision_of='../invalid')

    def test_revision_keeps_unfinished_or_uncertain_predecessor_held(self):
        original, _ = self.claim()
        states = [
            dict(status='preparing', execution_status='pending'),
            dict(status='running', execution_status='running', finalized_at=timestamp()),
            dict(status='recovery_required', execution_status='uncertain', finalized_at=timestamp()),
            dict(status='failed', execution_status='failed', finalized_at=timestamp(),
                 reservation_id='r', reservation_state='held_for_reconciliation'),
            dict(status='failed', execution_status='failed', finalized_at=timestamp(),
                 reservation_id='r', reservation_state='cleanup_failed'),
            dict(status='failed', execution_status='failed', finalized_at=timestamp(), reservation_id='r'),
            dict(status='failed', execution_status='failed', finalized_at=timestamp(),
                 cleanup_errors=[{'step': 'finish', 'error': 'OSError'}])]
        for updates in states:
            with self.subTest(updates=updates):
                current = dict(original, **updates)
                write_json(self.store.directory(original['job_id']) / 'result.json', current)
                write_json(self.store.directory(original['job_id']) / 'record.json', current)
                with self.assertRaises(receipts.AssignmentIncomplete):
                    self.claim(assignment='revision', revision_of=original['job_id'])
        self.assertEqual(len(list(self.store.root.glob('*/record.json'))), 1)


if __name__ == '__main__':
    unittest.main()
