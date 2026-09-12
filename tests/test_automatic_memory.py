"""Deterministic acceptance-to-memory tests; no hosted or local models."""
from concurrent.futures import ThreadPoolExecutor
from contextlib import redirect_stdout
import io
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'app'))
from automatic_memory import RECEIPT_NAME, main, record_accepted_outcome
from brain_store import BrainStore, digest
from storage_budget import StorageLimitError
from task_store import TaskStore, write_json


class AutomaticMemoryTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.store = TaskStore(self.root / 'runs/tasks')

    def task(self, **updates):
        result = self.store.create(worker='claude', task='Recover an interrupted assignment',
                                   category='review', assignment_project_id='alpha',
                                   assignment_id='recovery-v1')
        result.update(status='accepted', review_status='accepted', execution_status='succeeded',
                      finalized_at='2026-09-09T12:00:00+00:00', response='PRIVATE_RAW_ANSWER_DO_NOT_COPY',
                      review={'reviewer': 'Tester', 'reviewed_at': '2026-09-09T12:01:00+00:00',
                              'note': 'Verified recovery preserves the saved answer and avoids a duplicate worker call.'})
        result.update(updates)
        self.store.save(result['job_id'], result)
        return result['job_id']

    def read_task(self, job_id):
        return json.loads((self.store.directory(job_id) / 'result.json').read_text(encoding='utf-8'))

    def modify(self, job_id, **updates):
        result = self.read_task(job_id)
        result.update(updates)
        write_json(self.store.directory(job_id) / 'result.json', result)

    def receipt(self, job_id):
        return json.loads((self.store.directory(job_id) / RECEIPT_NAME).read_text(encoding='utf-8'))

    def test_accepted_review_stores_compact_evidence_without_model_or_raw_answer(self):
        job = self.task()
        canonical_before = self.read_task(job)
        with patch('subprocess.run', side_effect=AssertionError('No model invocation')), \
                patch('subprocess.Popen', side_effect=AssertionError('No background model')):
            receipt = record_accepted_outcome(self.store, job)
        self.assertEqual(receipt['status'], 'remembered', receipt)
        brain = BrainStore(self.root)
        memory = brain.get(receipt['memory_id'])
        self.assertEqual(memory['status'], 'active')
        self.assertEqual(memory['kind'], 'episode')
        self.assertEqual(memory['source']['review_sha256'], digest(canonical_before['review']))
        self.assertEqual(memory['episode']['outcome'], canonical_before['review']['note'])
        self.assertIn('review evidence', memory['content'])
        self.assertNotIn('PRIVATE_RAW_ANSWER', json.dumps(memory))
        self.assertNotIn('PRIVATE_RAW_ANSWER', json.dumps(receipt))
        self.assertLess(len(memory['content']), 1500)
        self.assertLess((self.store.directory(job) / RECEIPT_NAME).stat().st_size, 2048)
        self.assertEqual(self.read_task(job), canonical_before)
        self.assertEqual(len(brain.search('recovery', 'alpha')['results']), 1)
        self.assertEqual(len(brain.search('recovery', 'foreign')['results']), 0)

    def test_pending_rejected_failed_and_unfinalized_tasks_never_initialize_brain(self):
        cases = [{'status': 'awaiting_review', 'review_status': 'pending'},
                 {'status': 'rejected', 'review_status': 'rejected'},
                 {'execution_status': 'failed'}, {'finalized_at': None}]
        for fields in cases:
            with self.subTest(fields=fields):
                job = self.task(**fields)
                factory = Mock(side_effect=AssertionError('No memory admission'))
                receipt = record_accepted_outcome(self.store, job, brain_factory=factory)
                self.assertEqual(receipt['status'], 'skipped')
                self.assertEqual(receipt['reason'], 'task_not_accepted')
                factory.assert_not_called()

    def test_missing_project_skips_without_guessing_a_scope(self):
        job = self.task(assignment_project_id=None)
        factory = Mock(side_effect=AssertionError('No guessed project'))
        receipt = record_accepted_outcome(self.store, job, brain_factory=factory)
        self.assertEqual(receipt['reason'], 'missing_project')
        factory.assert_not_called()

    def test_repeated_storage_and_concurrent_retries_create_one_memory(self):
        job = self.task()
        with ThreadPoolExecutor(max_workers=3) as executor:
            receipts = list(executor.map(lambda _: record_accepted_outcome(self.store, job), range(3)))
        self.assertTrue(all(row['status'] == 'remembered' for row in receipts), receipts)
        self.assertEqual(len({row['memory_id'] for row in receipts}), 1)
        brain = BrainStore(self.root)
        before = brain.changes('alpha')
        again = record_accepted_outcome(self.store, job)
        self.assertEqual(again['memory_id'], receipts[0]['memory_id'])
        self.assertEqual(len(brain.list_memories('alpha')), 1)
        self.assertEqual(brain.changes('alpha'), before)

    def test_approval_failure_keeps_review_and_retry_approves_same_candidate(self):
        job = self.task()
        with patch.object(BrainStore, 'approve', side_effect=StorageLimitError('Synthetic full storage')):
            failed = record_accepted_outcome(self.store, job)
        self.assertEqual(failed['status'], 'error')
        self.assertTrue(failed['retryable'])
        self.assertEqual(self.read_task(job)['status'], 'accepted')
        self.assertEqual(BrainStore(self.root).get(failed['memory_id'])['status'], 'pending')
        retried = record_accepted_outcome(self.store, job)
        self.assertEqual(retried['status'], 'remembered', retried)
        self.assertEqual(retried['memory_id'], failed['memory_id'])
        self.assertEqual(len(BrainStore(self.root).list_memories('alpha')), 1)

    def test_brain_initialization_failure_is_separate_and_retryable(self):
        job = self.task()
        failed = record_accepted_outcome(self.store, job,
                    brain_factory=Mock(side_effect=StorageLimitError('Synthetic memory budget hold')))
        self.assertEqual(failed['status'], 'error')
        self.assertEqual(self.receipt(job)['status'], 'error')
        self.assertEqual(self.read_task(job)['status'], 'accepted')
        self.assertEqual(record_accepted_outcome(self.store, job)['status'], 'remembered')

    def test_explicit_curated_record_takes_precedence(self):
        job = self.task()
        curated = {'kind': 'procedure', 'title': 'Reconcile uncertain execution',
                   'content': 'Inspect the saved result before assigning recovery.',
                   'source': {'type': 'user', 'note': 'Must be replaced with canonical task source'}}
        receipt = record_accepted_outcome(self.store, job, curated_payload=curated)
        self.assertEqual(receipt['status'], 'remembered', receipt)
        memory = BrainStore(self.root).get(receipt['memory_id'])
        self.assertEqual(receipt['mode'], 'curated')
        self.assertEqual(memory['kind'], 'procedure')
        self.assertEqual(memory['title'], curated['title'])
        self.assertEqual(memory['source'], {'type': 'task', 'job_id': job})
        self.assertEqual(len(BrainStore(self.root).list_memories('alpha')), 1)
        self.assertEqual(curated['source']['type'], 'user', 'Caller payload must not be mutated')

    def test_foreign_curated_project_is_rejected_without_automatic_fallback(self):
        job = self.task()
        factory = Mock(side_effect=AssertionError('No scope crossing'))
        receipt = record_accepted_outcome(self.store, job, brain_factory=factory,
            curated_payload={'project_id': 'other', 'kind': 'fact', 'title': 'Foreign', 'content': 'Unrelated'})
        self.assertEqual(receipt['status'], 'error')
        self.assertIn('exact task project', receipt['error'])
        self.assertEqual(self.read_task(job)['status'], 'accepted')
        factory.assert_not_called()

    def test_changed_response_excludes_recall_and_preserves_original_receipt(self):
        job = self.task()
        saved = record_accepted_outcome(self.store, job)
        before = self.receipt(job)
        self.modify(job, response='Changed canonical answer')
        self.assertEqual(BrainStore(self.root).search('recovery', 'alpha')['results'], [])
        again = record_accepted_outcome(self.store, job)
        self.assertEqual(again['status'], 'error')
        self.assertFalse(again['retryable'])
        self.assertEqual(again['memory_id'], saved['memory_id'])
        self.assertEqual(self.receipt(job), before)
        self.assertEqual(len(BrainStore(self.root).list_memories('alpha')), 1)

    def test_changed_review_note_excludes_automatic_evidence_from_recall(self):
        job = self.task()
        saved = record_accepted_outcome(self.store, job)
        review = dict(self.read_task(job)['review'], note='A later review found the proposed recovery was incorrect.')
        self.modify(job, review=review)
        self.assertEqual(BrainStore(self.root).search('recovery', 'alpha')['results'], [])
        self.assertEqual(record_accepted_outcome(self.store, job)['status'], 'error')
        self.assertEqual(self.receipt(job)['memory_id'], saved['memory_id'])

    def test_revoking_review_prevents_recall_and_new_storage(self):
        job = self.task()
        saved = record_accepted_outcome(self.store, job)
        self.modify(job, status='rejected', review_status='rejected')
        self.assertEqual(BrainStore(self.root).search('recovery', 'alpha')['results'], [])
        again = record_accepted_outcome(self.store, job)
        self.assertEqual(again['reason'], 'task_not_accepted')
        self.assertEqual(again['memory_id'], saved['memory_id'])

    def test_forgetting_does_not_resurrect_on_retry(self):
        job = self.task()
        saved = record_accepted_outcome(self.store, job)
        brain = BrainStore(self.root)
        brain.forget(saved['memory_id'], 'Tester', 'Discard this temporary synthetic recovery evidence.')
        again = record_accepted_outcome(self.store, job)
        self.assertEqual(again['reason'], 'forgotten')
        self.assertEqual(again['status'], 'skipped')
        factory = Mock(side_effect=AssertionError('Known forgotten memories remain forgotten'))
        final = record_accepted_outcome(self.store, job, brain_factory=factory)
        self.assertEqual(final['reason'], 'forgotten')
        factory.assert_not_called()
        self.assertEqual(brain.search('recovery', 'alpha')['results'], [])

    def test_corrupt_receipt_is_preserved_and_does_not_create_memory(self):
        job = self.task()
        path = self.store.directory(job) / RECEIPT_NAME
        path.write_text('{broken receipt', encoding='utf-8')
        factory = Mock(side_effect=AssertionError('Corrupt receipt must be inspected'))
        receipt = record_accepted_outcome(self.store, job, brain_factory=factory)
        self.assertEqual(receipt['status'], 'error')
        self.assertFalse(receipt['retryable'])
        self.assertEqual(path.read_text(encoding='utf-8'), '{broken receipt')
        factory.assert_not_called()

    def test_long_metadata_and_review_are_bounded(self):
        job = self.task(task='recovery ' * 2000, category='review ' * 200,
                        assignment_id='assignment-' * 200,
                        review={'reviewer': 'Tester ' * 200, 'note': 'Verified recovery behavior ' * 1000})
        receipt = record_accepted_outcome(self.store, job)
        self.assertEqual(receipt['status'], 'remembered', receipt)
        memory = BrainStore(self.root).get(receipt['memory_id'])
        self.assertLessEqual(len(memory['title']), 160)
        self.assertLessEqual(len(memory['content']), 1500)
        self.assertTrue(all(len(value) <= 1000 for value in memory['episode'].values()))

    def test_cli_retries_one_accepted_task_without_changing_its_review(self):
        job = self.task()
        before = self.read_task(job)
        with redirect_stdout(io.StringIO()) as output, \
                patch('subprocess.run', side_effect=AssertionError('Retry must not execute worker')):
            code = main([job, '--root', str(self.root)])
        self.assertEqual(code, 0, output.getvalue())
        self.assertEqual(json.loads(output.getvalue())['status'], 'remembered')
        self.assertEqual(self.read_task(job), before)

    def test_invalid_task_identifier_cannot_escape_canonical_store(self):
        receipt = record_accepted_outcome(self.store, '../outside')
        self.assertEqual(receipt['status'], 'error')
        self.assertFalse((self.root / 'runs/outside').exists())

    def test_review_proof_rejects_malformed_and_noncanonical_digests(self):
        job = self.task()
        brain = BrainStore(self.root)
        for proof in ('invalid', '0' * 64, None):
            with self.subTest(proof=proof), self.assertRaises(ValueError):
                brain.propose({'project_id': 'alpha', 'kind': 'fact', 'title': 'Review proof',
                               'content': 'Synthetic review evidence.',
                               'source': {'type': 'task', 'job_id': job, 'review_sha256': proof}})

    def test_interrupted_proposal_without_receipt_id_is_held_without_resurrection(self):
        job = self.task()
        propose = BrainStore.propose

        def interrupted(brain, payload):
            propose(brain, payload)
            raise KeyboardInterrupt('Synthetic process interruption after commit')

        with patch.object(BrainStore, 'propose', interrupted), self.assertRaises(KeyboardInterrupt):
            record_accepted_outcome(self.store, job)
        before = self.receipt(job)
        self.assertEqual(before['status'], 'writing')
        self.assertIsNone(before['memory_id'])
        brain = BrainStore(self.root)
        candidate = brain.list_memories('alpha')[0]
        brain.forget(candidate['id'], 'Tester', 'Remove the uncertain synthetic candidate before a retry.')
        again = record_accepted_outcome(self.store, job)
        self.assertEqual(again['status'], 'error')
        self.assertIn('uncertain', again['error'])
        self.assertFalse(again['retryable'])
        self.assertEqual(self.receipt(job), before)
        self.assertEqual(brain.search('recovery', 'alpha')['results'], [])
        self.assertEqual(brain.list_memories('alpha', status='pending'), [])

    def test_interrupted_approval_recovers_the_same_committed_record(self):
        job = self.task()
        approve = BrainStore.approve

        def interrupted(brain, *args):
            approve(brain, *args)
            raise KeyboardInterrupt('Synthetic interruption after approval commit')

        with patch.object(BrainStore, 'approve', interrupted), self.assertRaises(KeyboardInterrupt):
            record_accepted_outcome(self.store, job)
        before = self.receipt(job)
        self.assertEqual(before['status'], 'writing')
        self.assertIsNotNone(before['memory_id'])
        again = record_accepted_outcome(self.store, job)
        self.assertEqual(again['status'], 'remembered', again)
        self.assertEqual(again['memory_id'], before['memory_id'])
        self.assertEqual(len(BrainStore(self.root).list_memories('alpha')), 1)

    def test_failed_curated_mode_cannot_silently_fall_back_to_automatic(self):
        job = self.task()
        first = record_accepted_outcome(self.store, job,
            curated_payload={'project_id': 'foreign', 'kind': 'fact', 'title': 'Wrong scope', 'content': 'Invalid'})
        self.assertEqual(first['status'], 'error')
        before = self.receipt(job)
        retried = record_accepted_outcome(self.store, job)
        self.assertEqual(retried['status'], 'error')
        self.assertFalse(retried['retryable'])
        self.assertIn('original memory mode', retried['error'])
        self.assertEqual(self.receipt(job), before)

    def test_receipt_pointing_to_another_task_is_preserved_for_repair(self):
        job = self.task()
        other = self.task()
        first = record_accepted_outcome(self.store, job)
        second = record_accepted_outcome(self.store, other)
        first['memory_id'] = second['memory_id']
        write_json(self.store.directory(job) / RECEIPT_NAME, first)
        retried = record_accepted_outcome(self.store, job)
        self.assertEqual(retried['status'], 'error')
        self.assertFalse(retried['retryable'])
        self.assertEqual(self.receipt(job), first)
        self.assertEqual(len(BrainStore(self.root).list_memories('alpha')), 2)

    def test_unreadable_canonical_source_does_not_erase_previous_memory_identity(self):
        job = self.task()
        saved = record_accepted_outcome(self.store, job)
        original = self.read_task(job)
        path = self.store.directory(job) / 'result.json'
        path.write_text('{interrupted canonical write', encoding='utf-8')
        failed = record_accepted_outcome(self.store, job)
        self.assertEqual(failed['status'], 'error')
        self.assertFalse(failed['retryable'])
        self.assertEqual(self.receipt(job), saved)
        brain = BrainStore(self.root)
        brain.forget(saved['memory_id'], 'Tester', 'Forget this synthetic memory while its source is unavailable.')
        write_json(path, original)
        recovered = record_accepted_outcome(self.store, job)
        self.assertEqual(recovered['reason'], 'forgotten')
        self.assertEqual(brain.search('recovery', 'alpha')['results'], [])

    def test_skipped_unreviewed_task_can_later_choose_curated_memory(self):
        job = self.task(status='awaiting_review', review_status='pending')
        skipped = record_accepted_outcome(self.store, job)
        self.assertEqual(skipped['reason'], 'task_not_accepted')
        self.modify(job, status='accepted', review_status='accepted')
        curated = {'kind': 'fact', 'title': 'Curated recovery guidance',
                   'content': 'Inspect the saved answer before repeating a worker.'}
        saved = record_accepted_outcome(self.store, job, curated_payload=curated)
        self.assertEqual(saved['status'], 'remembered', saved)
        self.assertEqual(saved['mode'], 'curated')
        retried = record_accepted_outcome(self.store, job, curated_payload=curated)
        self.assertEqual(retried['status'], 'remembered', retried)
        self.assertEqual(retried['memory_id'], saved['memory_id'])


if __name__ == '__main__':
    unittest.main()
