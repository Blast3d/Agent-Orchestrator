"""Quota collection cannot gate advisory work; execution uncertainty still can."""
import unittest
from tests import test_dispatch_worker as fixtures
from worker_execution import WorkerInterrupted


class AdvisoryDispatchTests(unittest.TestCase):
    run_task = fixtures.DispatchTests.run_task

    def setUp(self):
        fixtures.DispatchTests.setUp(self)
        self.guard.policy = {'quota_admission_mode': 'advisory'}
        self.guard.refresh.side_effect = TimeoutError('collector must not be awaited')
        self.guard.request_refresh.return_value = {'status': 'queued'}
        self.guard.check.return_value.update(admission_mode='advisory', reading_status='cached',
                                             warnings=['latest quota refresh failed'])

    def test_output_is_claimed_before_refresh(self):
        def request(provider):
            self.assertTrue(self.args.output.exists())
            return {'status': 'queued'}
        self.guard.request_refresh.side_effect = request
        result = self.run_task()
        self.assertEqual(result['status'], 'awaiting_review')
        self.guard.refresh.assert_not_called()

    def test_background_launch_failure_is_a_warning_and_preserves_answer(self):
        self.guard.request_refresh.side_effect = OSError('launch failed')
        result = self.run_task()
        self.assertEqual(result['status'], 'awaiting_review')
        self.assertEqual(result['quota_before']['reading_status'], 'cached')
        self.assertEqual(result['background_quota_refresh']['before']['error'], 'OSError')
        self.assertEqual(result['background_quota_refresh']['after']['error'], 'OSError')
        self.guard.finish.assert_called_once()
        self.guard.refresh.assert_not_called()
        self.assertEqual(result['cleanup_errors'], [])

    def test_known_low_quota_does_not_start_primary_but_still_collects(self):
        self.guard.check.return_value = {'allowed': False, 'admission_mode': 'advisory',
                                         'threshold_pct': 20, 'status': 'held'}
        result = self.run_task()
        self.assertEqual(result['status'], 'held')
        self.invoke.assert_not_called()
        self.guard.request_refresh.assert_called_once_with('claude')
        self.guard.refresh.assert_not_called()

    def test_uncertain_provider_execution_still_retains_reservation(self):
        self.invoke.side_effect = WorkerInterrupted('timeout', True, 1234, {})
        result = self.run_task()
        self.assertEqual(result['execution_status'], 'uncertain')
        self.assertEqual(result['reservation_state'], 'held_for_reconciliation')
        self.guard.finish.assert_not_called()
        self.guard.refresh.assert_not_called()


if __name__ == '__main__':
    import unittest
    unittest.main()
