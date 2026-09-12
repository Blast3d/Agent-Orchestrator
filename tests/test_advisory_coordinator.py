"""Advisory collection does not change the five-percent leadership boundary."""
from copy import deepcopy
from datetime import timedelta
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'app'))
from coordinator_handoff import Coordinator
from coordinator_transfer import transfer
from init_run import create_run
from usage_guard import Guard, now, stamp


class CachedGuard(Guard):
    def __init__(self, root):
        super().__init__(root)
        self.policy.update(quota_admission_mode='advisory', worker_start_threshold_pct=20)
        self.queued = []
        self.queue_error = False
        self.observed = now() - timedelta(hours=2)

    def refresh(self, provider):
        raise AssertionError('Advisory coordinator paths must not wait on quota collection')

    def request_refresh(self, provider):
        self.queued.append(provider)
        if self.queue_error:
            raise OSError('synthetic launch failure with private provider details')
        return {'status': 'queued', 'provider': provider, 'request_id': 'a' * 32,
                'requested_at': stamp()}

    def cache(self, provider, remaining, *, reset_passed=False):
        keys = ['codex-weekly'] if provider == 'codex' else ['claude-five-hour', 'claude-seven-day']
        windows = [{'id': key, 'remaining_pct': remaining, 'observed_at': stamp(self.observed),
                    'source': 'synthetic cached fixture', 'max_age_seconds': 300} for key in keys]
        if reset_passed:
            for window in windows:
                window['reset_at'] = stamp(now() - timedelta(minutes=1))
        self.observe(windows, provider, memberships={provider: keys})


class AdvisoryCoordinatorTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.run, _ = create_run(self.root, 'advisory-lead', 'Finish a synthetic project')
        self.coordinator = Coordinator(self.run)
        self.initial = self.coordinator.read()
        self.checkpoint = deepcopy(self.initial['checkpoint'])
        self.guard = CachedGuard(self.root / 'quota')
        self.guard.cache('codex', 5)
        self.guard.cache('claude', 90)
        self.executable = self.root / 'fake-claude.exe'
        self.executable.write_text('Synthetic fixture; never executed', encoding='utf-8')
        self.started = []

    def launch(self, command, workspace):
        pending = self.coordinator.read()
        self.assertEqual(pending['status'], 'handoff_ready')
        self.assertEqual(pending['handoff']['launch']['status'], 'launching')
        self.assertEqual(command[command.index('--permission-mode') + 1], 'auto')
        self.started.append(command)
        return {'status': 'submitted', 'exit_code': 0}

    def transfer(self, launcher=None):
        return transfer(self.coordinator, self.checkpoint, 'astra', self.initial['session'], 1,
                        guard=self.guard, executable=self.executable, launcher=launcher or self.launch)

    def test_known_twenty_percent_is_worker_hold_but_only_five_percent_yields_lead(self):
        for remaining, expected in ((20, 'continue_astra'), (5.01, 'continue_astra'),
                                    (5, 'handoff_due'), (0, 'handoff_due')):
            with self.subTest(remaining=remaining):
                self.guard.cache('codex', remaining)
                self.assertFalse(self.guard.check('codex')['allowed'])
                result = self.coordinator.readiness(guard=self.guard)
                self.assertEqual(result['status'], expected)
                self.assertEqual(result['threshold_pct'], 5)
                self.assertEqual(result['remaining_pct'], remaining)
                self.assertEqual(result['quota_at_check']['reading_status'], 'cached')
        self.assertEqual(self.coordinator.read(), self.initial)

    def test_worker_reservations_do_not_lower_the_raw_lead_reading(self):
        self.guard.cache('codex', 25)
        reserved = self.guard.check('codex', 'large', reserve=True, task='Synthetic reserved worker')
        self.assertTrue(reserved['allowed'])
        self.assertFalse(self.guard.check('codex')['allowed'])
        result = self.coordinator.readiness(guard=self.guard)
        self.assertEqual(result['status'], 'continue_astra')
        self.assertEqual(result['remaining_pct'], 25)

    def test_cached_five_percent_qualifies_even_when_collector_launch_fails(self):
        self.guard.queue_error = True
        result = self.coordinator.readiness(guard=self.guard)
        self.assertEqual(result['status'], 'handoff_due')
        self.assertEqual(result['quota_refresh']['status'], 'error')
        self.assertNotIn('private provider', str(result))
        self.assertEqual(self.guard.queued, ['codex'])

    def test_missing_partial_unknown_or_reset_readings_never_auto_transfer(self):
        for situation in ('missing', 'partial', 'reset'):
            with self.subTest(situation=situation):
                with self.guard.state() as data:
                    data['worker_pools']['codex'] = ['codex-weekly']
                self.guard.cache('codex', 0)
                if situation == 'reset':
                    self.guard.cache('codex', 0, reset_passed=True)
                else:
                    with self.guard.state() as data:
                        if situation == 'missing':
                            data['windows'].pop('codex-weekly')
                        else:
                            data['worker_pools']['codex'].append('codex-missing')
                result = self.transfer()
                self.assertTrue(result['transfer_held'])
                self.assertEqual(result['transfer_readiness']['status'], 'unknown')
                self.assertEqual(self.coordinator.read(), self.initial)
                self.assertEqual(self.started, [])

    def test_confirmed_cooldown_is_not_reinterpreted_as_a_numeric_lead_trigger(self):
        self.guard.block('codex')
        result = self.transfer()
        self.assertTrue(result['transfer_held'])
        self.assertEqual(result['transfer_readiness']['status'], 'unknown')
        self.assertEqual(self.started, [])

    def test_transfer_records_cached_admission_and_survives_queue_failure(self):
        self.guard.queue_error = True
        result = self.transfer()
        self.assertEqual(result['status'], 'handoff_ready')
        self.assertEqual(result['handoff']['readiness']['threshold_pct'], 5)
        self.assertEqual(result['handoff']['launch']['quota_refresh']['status'], 'error')
        self.assertEqual(result['handoff']['launch']['quota_at_launch']['reading_status'], 'cached')
        self.assertTrue(result['handoff']['launch']['quota_at_launch']['allowed'])
        self.assertEqual(self.guard.queued, ['codex', 'claude'])
        self.assertEqual(len(self.started), 1)

    def test_low_recipient_refuses_transfer_without_yield_or_new_reservation(self):
        self.guard.cache('claude', 20)
        self.guard.queue_error = True
        result = self.transfer()
        self.assertTrue(result['transfer_held'])
        self.assertEqual(result['transfer_quota_refresh']['status'], 'error')
        self.assertFalse(result['transfer_quota']['allowed'])
        self.assertEqual(self.coordinator.read(), self.initial)
        with self.guard.state() as data:
            self.assertEqual(data['reservations'], {})
        self.assertEqual(self.started, [])

    def test_unknown_recipient_is_admitted_without_inventing_remaining_percentage(self):
        with self.guard.state() as data:
            data['windows'].pop('claude-five-hour')
            data['windows'].pop('claude-seven-day')
        result = self.transfer()
        admission = result['handoff']['launch']['quota_at_launch']
        self.assertTrue(admission['allowed'])
        self.assertEqual(admission['reading_status'], 'unknown')
        self.assertEqual(admission['windows'], [])
        self.assertEqual(len(self.started), 1)

    def test_manual_prepared_claim_queues_nonblocking_collection_and_is_idempotent(self):
        pending = self.coordinator.prepare('fable', 'manual', 1, True)
        self.guard.queue_error = True
        claimed = self.coordinator.claim(pending['handoff']['id'], 'receiving-session', 2, self.guard)
        self.assertEqual(claimed['owner'], 'fable')
        self.assertEqual(claimed['quota_refresh_at_claim']['status'], 'error')
        self.assertEqual(self.coordinator.claim(pending['handoff']['id'], 'receiving-session', 2, self.guard), claimed)
        self.assertEqual(self.guard.queued, ['claude'])
        with self.assertRaises(ValueError):
            self.coordinator.claim(pending['handoff']['id'], 'wrong-session', 2, self.guard)

    def test_claim_excludes_existing_launch_reservation_instead_of_double_charging(self):
        self.guard.cache('claude', 22)
        pending = self.transfer()
        launch = pending['handoff']['launch']
        self.assertFalse(self.guard.check('claude')['allowed'])
        self.guard.queue_error = True
        claimed = self.coordinator.claim(pending['handoff']['id'], launch['session'], 2, self.guard)
        self.assertEqual(claimed['owner'], 'fable')
        self.assertTrue(claimed['quota_at_claim']['allowed'])
        self.assertEqual(claimed['quota_refresh_at_claim']['status'], 'error')
        with self.guard.state() as data:
            self.assertEqual(list(data['reservations']), [launch['reservation_id']])
            self.assertIsNone(data['reservations'][launch['reservation_id']]['finished_at'])
        self.assertTrue(self.coordinator.role('astra', self.initial['session'])['may_continue_assigned_work'])

    def test_low_claim_stays_pending_and_pinned_session_is_required(self):
        pending = self.transfer()
        launch = pending['handoff']['launch']
        before = self.coordinator.path.read_bytes()
        with self.assertRaises(ValueError):
            self.coordinator.claim(pending['handoff']['id'], 'wrong-session', 2, self.guard)
        self.guard.cache('claude', 20)
        with self.assertRaises(ValueError):
            self.coordinator.claim(pending['handoff']['id'], launch['session'], 2, self.guard)
        self.assertEqual(self.coordinator.path.read_bytes(), before)
        with self.guard.state() as data:
            self.assertIsNone(data['reservations'][launch['reservation_id']]['finished_at'])

    def test_uncertain_launch_and_retry_keep_one_intent_and_one_reservation(self):
        calls = []
        def uncertain(command, workspace):
            calls.append(command)
            raise subprocess.TimeoutExpired('synthetic', 1)
        first = self.transfer(uncertain)
        queued = self.guard.queued.copy()
        self.assertEqual(first['handoff']['launch']['status'], 'uncertain')
        second = self.transfer(uncertain)
        self.assertTrue(second['transfer_reused'])
        self.assertEqual(second['handoff']['id'], first['handoff']['id'])
        self.assertEqual(self.guard.queued, queued)
        self.assertEqual(len(calls), 1)
        with self.guard.state() as data:
            self.assertEqual(len(data['reservations']), 1)
            self.assertIsNone(next(iter(data['reservations'].values()))['finished_at'])


if __name__ == '__main__':
    unittest.main()
