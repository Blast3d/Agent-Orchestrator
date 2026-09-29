"""Final memory delivery checks use local evidence; no provider/network calls."""
from copy import deepcopy
import json
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

sys.path[:0] = [str(Path(__file__).resolve().parents[1] / 'app'), str(Path(__file__).resolve().parent)]
import test_memory_usage as fixtures
import dispatch_worker as dispatcher
from brain_store import BrainStore
from task_performance import snapshot


class DeliveryFreshnessTests(unittest.TestCase):
    setUp = fixtures.DispatchMemoryUsageTests.setUp
    run_task = fixtures.DispatchMemoryUsageTests.run_task

    def admission(self, action):
        def check(*args, **kwargs):
            action()
            return {'allowed': True, 'reservation_id': 'test-token'}
        self.guard.check.side_effect = check

    def assert_unsent(self, result):
        self.assertEqual(result['status'], 'held')
        self.assertFalse(result['memory_context']['execution_requested'])
        self.assertEqual(result['memory_context']['delivery_validation']['status'], 'stale')
        self.assertIn(self.memory['id'], result['memory_context']['delivery_validation']['changed_ids'])
        self.assertEqual(snapshot(result)['memories_supplied'], 0)
        self.invoke.assert_not_called()
        self.assertTrue(self.command.call_args_list)
        self.assertTrue(all(call.kwargs.get('preflight') is True and call.args[1] == ''
                            for call in self.command.call_args_list))
        self.guard.finish.assert_called_once()

    def test_forget_during_admission_holds_without_retrieval_retry(self):
        self.admission(lambda: self.brain.forget(self.memory['id'], 'Tester', 'Forgotten during synthetic quota admission.'))
        original = BrainStore.search
        calls = []
        def tracked(store, *args, **kwargs):
            calls.append(1)
            return original(store, *args, **kwargs)
        with patch.object(BrainStore, 'search', tracked):
            result = self.run_task()
        self.assert_unsent(result)
        self.assertEqual(len(calls), 1)

    def test_forget_during_quota_refresh_holds(self):
        first = [True]
        def deferred(provider):
            if first:
                first.pop()
                self.brain.forget(self.memory['id'], 'Tester', 'Forgotten during synthetic quota refresh.')
            return {provider: {'ok': True}}
        self.guard.refresh.side_effect = deferred
        self.assert_unsent(self.run_task())

    def test_same_id_evidence_change_holds(self):
        def mutate():
            with self.brain._write() as con:
                con.execute('UPDATE memories SET content=? WHERE id=?', ('Revised text after recall.', self.memory['id']))
        self.admission(mutate)
        self.assert_unsent(self.run_task())

    def test_supersession_during_admission_holds(self):
        proposal = self.brain.propose({'project_id': 'alpha', 'kind': 'procedure', 'title': 'Other',
            'content': 'Current replacement procedure.', 'source': {'type': 'user', 'note': 'Synthetic replacement'}})
        newer = self.brain.approve(proposal['id'], 'Tester', 'Reviewed the replacement before this synthetic lookup.')
        self.admission(lambda: self.brain.supersede(self.memory['id'], newer['id'], 'Tester', 'Newer accepted evidence replaces the old procedure.'))
        self.assert_unsent(self.run_task())

    def test_full_fingerprint_catches_changed_tail_of_truncated_excerpt(self):
        recalled = self.brain.search('Recovery', 'alpha', record_trace=False)
        recalled = deepcopy(recalled)
        recalled['results'][0].update(content=self.memory['content'][:20] + '\u2026', truncated=True)
        fingerprints, initial = dispatcher.delivery_evidence_check(self.brain, recalled)
        self.assertEqual(initial['status'], 'current')
        with self.brain._write() as con:
            con.execute('UPDATE memories SET content=? WHERE id=?', (self.memory['content'] + ' changed tail', self.memory['id']))
        _, final = dispatcher.delivery_evidence_check(self.brain, recalled, fingerprints)
        self.assertEqual(final['status'], 'stale')

    def test_running_receipt_records_requested_memory_before_provider(self):
        observed = []
        completed = self.invoke.return_value
        def invoked(*args, **kwargs):
            files = list((self.root / 'runs/tasks').glob('*/result.json'))
            saved = json.loads(files[0].read_text(encoding='utf-8'))
            observed.append(saved['memory_context']['execution_requested'])
            return completed
        self.invoke.side_effect = invoked
        result = self.run_task()
        self.assertEqual(result['status'], 'awaiting_review')
        self.assertEqual(observed, [True])
        self.assertEqual(result['memory_context']['delivery_validation']['status'], 'current')


if __name__ == '__main__':
    unittest.main()
