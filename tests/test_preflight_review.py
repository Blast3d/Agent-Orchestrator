"""A no-inference hold may be rejected; uncertain provider work stays held."""
import json
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'app'))
from task_store import TaskStore, timestamp


class PreflightReviewTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.store = TaskStore(Path(self.temporary.name) / 'runs/tasks')

    def held(self, **updates):
        row = self.store.create(worker='gemini', task='Synthetic held preflight')
        row.update(status='held', execution_status='held', reason='Executable requires verification.',
                   finalized_at=timestamp(), storage_reservation_state='released', **updates)
        self.store.save(row['job_id'], row)
        return row

    def test_explicit_rejection_preserves_hold_and_does_not_write_memory(self):
        row = self.held()
        result = self.store.review(row['job_id'], 'rejected', 'Reviewer',
            'Superseded by a separately verified linked assignment after boundary verification.')
        self.assertEqual(result['status'], 'rejected')
        self.assertEqual(result['execution_status'], 'held')
        self.assertEqual(result['reason'], row['reason'])
        self.assertNotIn('memory_outcome', result)
        self.assertFalse((self.store.directory(row['job_id']) / 'memory-outcome.json').exists())

    def test_hold_cannot_be_accepted(self):
        row = self.held()
        with self.assertRaises(ValueError):
            self.store.review(row['job_id'], 'accepted', 'Reviewer', 'No executed answer exists to accept here.')

    def test_uncertain_or_unfinalized_holds_cannot_be_disposed(self):
        for field, value in [('started_at', timestamp()), ('reservation_id', 'reserved'),
            ('process_pid', 123), ('response', 'partial'), ('usage', {'input_tokens': 1}),
            ('provider_result', {'status': 'unknown'}), ('modelUsage', {'model': {}}),
            ('model', 'reported-model'), ('reservation_state', 'held_for_reconciliation'),
            ('storage_reservation_id', 'unreleased-storage'),
            ('storage_reservation_state', 'held_for_reconciliation'), ('finalized_at', None)]:
            with self.subTest(field=field):
                row = self.held()
                row[field] = value
                if field == 'storage_reservation_id':
                    row['storage_reservation_state'] = None
                self.store.save(row['job_id'], row)
                with self.assertRaises(ValueError):
                    self.store.review(row['job_id'], 'rejected', 'Reviewer',
                        'Do not discard provider work or an uncertain reservation.')
                saved = json.loads((self.store.directory(row['job_id']) / 'result.json').read_text())
                self.assertEqual(saved['status'], 'held')


if __name__ == '__main__':
    unittest.main()
