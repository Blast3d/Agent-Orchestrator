"""Behavior checks for actual quota admission and parallel reservations."""
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'app'))
from usage_guard import Guard, now, stamp

class QuotaTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.guard = Guard(Path(self.folder.name))

    def tearDown(self):
        self.folder.cleanup()

    def observe(self, remaining=70, age=0, reset=None, key='agy:gemini-weekly'):
        self.guard.observe([{'id': key, 'remaining_pct': remaining, 'observed_at': stamp(now() - timedelta(seconds=age)),
            'reset_at': reset, 'max_age_seconds': 600, 'source': 'synthetic test'}], 'antigravity')

    def test_two_percent_cannot_start_even_tiny_work(self):
        self.observe(2)
        self.assertFalse(self.guard.check('gemini', 'tiny')['allowed'])

    def test_usage_warning_distinguishes_grok_build_and_bot(self):
        with patch.object(self.guard, 'status', return_value={'workers': [
                {'worker': 'grok', 'status': 'low'}, {'worker': 'grok-bot', 'status': 'held'}]}), \
                patch.object(self.guard, 'notify', return_value=True) as notify:
            self.guard.alerts()
        message = notify.call_args.args[1]
        self.assertIn('Grok Build', message)
        self.assertIn('Grok Bot', message)

    def test_unknown_and_stale_are_held(self):
        self.assertFalse(self.guard.check('grok')['allowed'])
        self.observe(age=601)
        self.assertFalse(self.guard.check('gemini')['allowed'])

    def test_warn_before_floor_and_include_task_size(self):
        self.observe(20)
        result = self.guard.check('gemini', 'tiny')
        self.assertEqual(result['status'], 'low')
        self.assertTrue(result['allowed'])
        self.assertFalse(self.guard.check('gemini', 'large')['allowed'])

    def test_grok_bot_never_borrows_cli_allowance_and_stale_readings_hold_it(self):
        self.observe(99, key='grok-weekly')
        self.assertTrue(self.guard.check('grok')['allowed'])
        self.assertFalse(self.guard.check('grok-bot')['allowed'])
        self.observe(18, key='grokbot-weekly')
        decision = self.guard.check('grok-bot', 'small', True, 'Bot task')
        self.assertTrue(decision['allowed'])
        self.assertEqual(decision['status'], 'low')
        self.assertEqual(self.guard.check('grok')['windows'][0]['available_pct'], 99)
        self.assertFalse(self.guard.check('grok-bot', 'medium')['allowed'])
        with self.guard.state() as data:
            data['windows']['grokbot-weekly']['observed_at'] = stamp(now() - timedelta(seconds=601))
        self.assertFalse(self.guard.check('grok-bot', 'tiny')['allowed'])

    def test_shared_pool_parallel_reservations_are_atomic(self):
        self.observe(22)
        self.guard.policy['worker_pools']['second-google-model'] = ['agy:gemini-weekly']
        with ThreadPoolExecutor(max_workers=2) as workers:
            results = list(workers.map(lambda worker: self.guard.check(worker, 'medium', True, 'parallel test'), ['gemini', 'second-google-model']))
        self.assertEqual(sum(result['allowed'] for result in results), 1)

    def test_completed_estimate_stays_held_until_new_reading(self):
        self.observe(22)
        result = self.guard.check('gemini', 'medium', True, 'work')
        self.guard.finish(result['reservation_id'], 'completed')
        self.assertFalse(self.guard.check('gemini', 'medium')['allowed'])
        self.observe(22)
        self.assertTrue(self.guard.check('gemini', 'medium')['allowed'])

    def test_reservations_from_an_earlier_window_period_do_not_hold_strict_admission(self):
        self.observe(30, key='grok-weekly')
        with self.guard.state() as data:
            data['reservations']['old'] = {'worker': 'grok', 'task': 'uncertain', 'pools': ['grok-weekly'],
                                           'estimate_pct': 20, 'created_at': stamp(now() - timedelta(days=8)),
                                           'finished_at': None, 'outcome': 'recovery_hold'}
        self.assertTrue(self.guard.check('grok')['allowed'])
        with self.guard.state() as data:
            data['reservations']['old']['created_at'] = stamp(now() - timedelta(days=1))
        self.assertFalse(self.guard.check('grok')['allowed'])

    def test_stale_or_missing_readers_queue_background_refresh_without_waiting(self):
        self.observe(90, age=3600, key='codex-weekly')
        self.guard.observe([{'id': 'grok-weekly', 'remaining_pct': 90, 'observed_at': stamp(now()),
                             'max_age_seconds': 600, 'source': 'synthetic test'}], 'grok')
        with patch('background_usage.request_refresh', return_value={'status': 'queued'}) as queue,                 patch.object(self.guard, 'refresh', side_effect=AssertionError('must not wait on a reader')):
            queued = self.guard.queue_stale_refreshes()
        called = sorted(call.args[1] for call in queue.call_args_list)
        # codex is stale; claude and antigravity have never been read; grok is fresh.
        self.assertEqual(called, ['antigravity', 'claude', 'codex'])
        self.assertTrue(all(call.kwargs['min_interval_seconds'] == 300 for call in queue.call_args_list))
        self.assertEqual(sorted(queued), called)

    def test_tightest_window_wins(self):
        self.guard.policy['worker_pools']['gemini'].append('second-window')
        self.observe(95)
        self.observe(2, key='second-window')
        self.assertFalse(self.guard.check('gemini')['allowed'])

    def test_reset_is_not_assumed_to_restore_quota(self):
        self.observe(90, reset=stamp(now() - timedelta(seconds=1)))
        self.assertFalse(self.guard.check('gemini')['allowed'])

    def test_failed_refresh_invalidates_previous_recent_read(self):
        self.observe(90)
        with self.guard.state() as data:
            data['refresh_errors']['antigravity'] = {'at': stamp(), 'error': 'network failure'}
        self.assertFalse(self.guard.check('gemini')['allowed'])

    def test_provider_rejection_cools_down_shared_pool(self):
        self.observe(95)
        self.guard.policy['worker_pools']['second-google-model'] = ['agy:gemini-weekly']
        self.guard.block('gemini')
        self.assertFalse(self.guard.check('second-google-model')['allowed'])

    def test_invalid_and_out_of_order_readings_do_not_inflate_quota(self):
        self.observe(5)
        self.observe(100, age=50)
        self.assertFalse(self.guard.check('gemini')['allowed'])
        with self.assertRaises(ValueError):
            self.observe(float('nan'))

    def test_local_worker_has_no_hosted_quota_gate(self):
        self.assertTrue(self.guard.check('local-chat', 'large')['allowed'])

    def test_partial_complete_snapshot_invalidates_omitted_window(self):
        def window(key):
            return {'id': key, 'remaining_pct': 80, 'observed_at': stamp(), 'source': 'test'}
        self.guard.observe([window('claude-five-hour'), window('claude-seven-day')], 'claude', complete=True)
        self.assertTrue(self.guard.check('claude')['allowed'])
        self.guard.observe([window('claude-five-hour')], 'claude', complete=True)
        self.assertFalse(self.guard.check('claude')['allowed'])

    def test_every_live_group_bucket_applies(self):
        payload = {'num_turns': 0, 'command': {'name': 'usage', 'data': {'groups': [
            {'name': 'Gemini Models', 'buckets': [{'id': 'gemini-weekly', 'remaining_fraction': .9},
                                                {'id': 'gemini-five-hour', 'remaining_fraction': .02}]},
            {'name': 'Claude and GPT models', 'buckets': [{'id': '3p-weekly', 'remaining_fraction': 1}]}]}}}
        with patch('usage_guard.subprocess.run') as run:
            run.return_value.returncode = 0
            run.return_value.stdout = json.dumps(payload)
            self.assertTrue(self.guard.refresh('antigravity')['antigravity']['ok'])
        self.assertFalse(self.guard.check('gemini')['allowed'])

    def test_snapshot_started_before_finish_keeps_reservation(self):
        self.observe(40)
        reservation = self.guard.check('gemini', 'large', True, 'race')
        payload = {'num_turns': 0, 'command': {'name': 'usage', 'data': {'groups': [
            {'name': 'Gemini Models', 'buckets': [{'id': 'gemini-weekly', 'remaining_fraction': .4}]},
            {'name': 'Claude and GPT models', 'buckets': [{'id': '3p-weekly', 'remaining_fraction': 1}]}]}}}
        def delayed_response(*args, **kwargs):
            self.guard.finish(reservation['reservation_id'], 'completed')
            import subprocess
            return subprocess.CompletedProcess([], 0, json.dumps(payload), '')
        with patch('usage_guard.subprocess.run', side_effect=delayed_response):
            self.guard.refresh('antigravity')
        self.assertEqual(self.guard.check('gemini')['windows'][0]['reserved_pct'], 15)

    def test_new_pools_inherit_in_flight_reservations(self):
        def window(key, remaining):
            return {'id': key, 'remaining_pct': remaining, 'observed_at': stamp(), 'source': 'test'}
        self.guard.observe([window('codex-five-hour', 90)], 'codex', complete=True,
            memberships={'codex': ['codex-five-hour']})
        self.assertTrue(self.guard.check('codex', 'large', True, 'running')['allowed'])
        self.guard.observe([window('codex-five-hour', 90), window('codex-weekly', 15)], 'codex', complete=True,
            memberships={'codex': ['codex-five-hour', 'codex-weekly']})
        self.assertFalse(self.guard.check('codex', 'small')['allowed'])

    def test_manual_source_does_not_change_pool_ownership(self):
        def window(key):
            return {'id': key, 'remaining_pct': 80, 'observed_at': stamp(), 'source': 'manual UI reading'}
        self.guard.observe([window('claude-five-hour'), window('claude-seven-day')], 'manual')
        self.guard.observe([window('claude-five-hour')], 'claude', complete=True)
        self.assertFalse(self.guard.check('claude')['allowed'])

    def test_older_complete_snapshot_cannot_resurrect_newer_omitted_window(self):
        current = now()
        ids = ['claude-five-hour', 'claude-seven-day']
        def window(key, at):
            return {'id': key, 'remaining_pct': 80, 'observed_at': stamp(at), 'source': 'test'}
        self.guard.observe([window(k, current - timedelta(seconds=20)) for k in ids],
            'claude', complete=True, memberships={'claude': ids})
        self.guard.observe([window(ids[0], current)], 'claude', complete=True,
            memberships={'claude': [ids[0]]})
        self.assertFalse(self.guard.check('claude')['allowed'])
        accepted = self.guard.observe([window(k, current - timedelta(seconds=10)) for k in ids],
            'claude', complete=True, memberships={'claude': ids})
        self.assertFalse(accepted)
        self.assertFalse(self.guard.check('claude')['allowed'])
        self.guard.observe([window(k, current + timedelta(seconds=1)) for k in ids],
            'claude', complete=True, memberships={'claude': ids})
        self.assertTrue(self.guard.check('claude')['allowed'])

    def test_older_complete_snapshot_cannot_delete_newer_discovered_pool(self):
        current = now()
        def window(key, at):
            return {'id': key, 'remaining_pct': 80, 'observed_at': stamp(at), 'source': 'test'}
        self.guard.observe([window('codex-weekly', current), window('codex-five-hour', current)],
            'codex', complete=True, memberships={'codex': ['codex-weekly', 'codex-five-hour']})
        self.guard.observe([window('codex-weekly', current - timedelta(seconds=5))],
            'codex', complete=True, memberships={'codex': ['codex-weekly']})
        self.assertTrue(self.guard.check('codex')['allowed'])
        self.assertEqual(len(self.guard.check('codex')['windows']), 2)

    def test_older_manual_reading_cannot_resurrect_newer_omitted_window(self):
        current = now()
        self.guard.observe([{'id': 'claude-five-hour', 'remaining_pct': 80,
            'observed_at': stamp(current), 'source': 'test'}], 'claude', complete=True)
        self.guard.observe([{'id': 'claude-seven-day', 'remaining_pct': 80,
            'observed_at': stamp(current - timedelta(seconds=5)), 'source': 'manual test'}], 'manual')
        self.assertFalse(self.guard.check('claude')['allowed'])

    def test_complete_snapshot_keeps_newer_manual_observation(self):
        current = now()
        self.guard.observe([{'id': 'claude-seven-day', 'remaining_pct': 2,
            'observed_at': stamp(current), 'source': 'manual test'}], 'manual')
        self.guard.observe([{'id': 'claude-five-hour', 'remaining_pct': 80,
            'observed_at': stamp(current - timedelta(seconds=5)), 'source': 'test'}], 'claude', complete=True)
        checked = self.guard.check('claude')
        self.assertFalse(checked['allowed'])
        self.assertEqual(len(checked['windows']), 2)

    def test_duplicate_snapshot_is_rejected_without_state_mutation(self):
        self.observe(2)
        observed = stamp()
        with self.assertRaisesRegex(ValueError, 'duplicate pool IDs'):
            self.guard.observe([{'id': 'agy:gemini-weekly', 'remaining_pct': remaining,
                'observed_at': observed, 'source': 'test'} for remaining in (2, 100)],
                'antigravity', complete=True, memberships={'gemini': ['agy:gemini-weekly']})
        checked = self.guard.check('gemini', 'large')
        self.assertFalse(checked['allowed'])
        self.assertEqual(checked['windows'][0]['remaining_pct'], 2)

    def test_invalid_estimates_cannot_create_reservations(self):
        self.observe(2)
        for value in (-15, 0, float('nan'), float('inf'), True, '3', 101):
            with self.subTest(value=value):
                self.guard.policy['estimates_pct']['large'] = value
                with self.assertRaises(ValueError):
                    self.guard.check('gemini', 'large', True, 'invalid-policy test')
        with self.guard.state() as data:
            self.assertEqual(data['reservations'], {})

    def test_invalid_persisted_policy_fails_at_load(self):
        self.guard.policy['floor_pct'] = float('nan')
        (self.guard.root / 'policy.json').write_text(json.dumps(self.guard.policy), encoding='utf-8')
        with self.assertRaises(ValueError):
            Guard(self.guard.root)

    def test_inconsistent_policy_thresholds_fail_closed(self):
        self.observe(90)
        self.guard.policy['floor_pct'] = 21
        with self.assertRaises(ValueError):
            self.guard.check('gemini')

    def test_strict_dashboard_uses_shared_readiness_page(self):
        self.guard.observe([{'id': 'notebooklm-chat-daily', 'remaining_pct': 80,
            'observed_at': stamp(), 'max_age_seconds': 30, 'source': 'manual test'}], 'manual')
        self.guard.dashboard()
        page = (self.guard.root / 'usage-dashboard.html').read_text(encoding='utf-8')
        self.assertIn('Bot readiness', page)
        self.assertIn('when its reading is missing or stale', page)
        self.assertIn('Technical details', page)
        self.assertNotIn('Codex coordinates the work.', page)

    def test_status_counts_unfinished_reservations_for_every_shared_pool_bot(self):
        self.observe(80)
        self.guard.policy['worker_pools']['second-google-model'] = ['agy:gemini-weekly']
        reserved = self.guard.check('gemini', 'small', True, 'work')
        with self.guard.state() as data:
            data['reservations']['old'] = {'worker': 'gemini', 'task': 'prior window',
                'pools': ['agy:gemini-weekly'], 'estimate_pct': 10,
                'created_at': stamp(now() - timedelta(days=8)), 'finished_at': None}
        counts = {item['worker']: item['active_reservation_count'] for item in self.guard.status()['workers']}
        self.assertEqual((counts['gemini'], counts['second-google-model']), (1, 1))
        self.guard.finish(reserved['reservation_id'], 'completed')
        counts = {item['worker']: item['active_reservation_count'] for item in self.guard.status()['workers']}
        self.assertEqual((counts['gemini'], counts['second-google-model']), (0, 0))

    def test_monitor_cancellation_stops_before_next_provider(self):
        stopped = False
        def adapter_response(*args, **kwargs):
            nonlocal stopped
            stopped = True
            import subprocess
            return subprocess.CompletedProcess([], 0, json.dumps({'windows': [{
                'id': 'codex-weekly', 'remaining_pct': 80, 'observed_at': stamp(), 'source': 'test'}]}), '')
        with patch('usage_guard.subprocess.run', side_effect=adapter_response) as run:
            result = self.guard.refresh(stop_requested=lambda: stopped)
        self.assertEqual(run.call_count, 1)
        self.assertEqual(set(result), {'codex'})
        self.assertTrue(result['codex']['ok'])

    def test_reader_timeout_reason_is_kept_without_admitting_old_allowance(self):
        self.guard.observe([{'id': key, 'remaining_pct': 90, 'observed_at': stamp(), 'source': 'test'}
                            for key in ('claude-five-hour', 'claude-seven-day')],
                           'claude', complete=True)
        payload = {'status': 'unknown',
                   'error': 'No complete Claude Code /usage panel appeared within the time limit.',
                   'windows': []}
        with patch('usage_guard.subprocess.run') as run:
            run.return_value.returncode = 2
            run.return_value.stdout = json.dumps(payload)
            result = self.guard.refresh('claude')
        self.assertIn('timed out', result['claude']['reason'])
        self.assertFalse(self.guard.check('claude')['allowed'])
        self.assertIn('timed out', self.guard.status()['refresh_errors']['claude']['error'])

    def test_reader_failure_never_echoes_arbitrary_private_output(self):
        for payload in ('PRIVATE_ACCOUNT_TRACE', json.dumps({'status': 'unknown',
                'error': 'PRIVATE_ACCOUNT_TRACE', 'windows': []})):
            with self.subTest(payload=payload), patch('usage_guard.subprocess.run') as run:
                run.return_value.returncode = 2
                run.return_value.stdout = payload
                result = self.guard.refresh('grok')
            self.assertNotIn('PRIVATE_ACCOUNT_TRACE', json.dumps(result))
            self.assertFalse(self.guard.check('grok')['allowed'])

    def test_nonzero_reader_with_success_shaped_json_is_not_accepted(self):
        payload = {'status': 'ok', 'windows': [{'id': 'grok-weekly', 'remaining_pct': 99,
                    'observed_at': stamp(), 'source': 'test'}]}
        with patch('usage_guard.subprocess.run') as run:
            run.return_value.returncode = 2
            run.return_value.stdout = json.dumps(payload)
            result = self.guard.refresh('grok')
        self.assertFalse(result['grok']['ok'])
        self.assertFalse(self.guard.check('grok')['allowed'])

    def test_explicit_refresh_ignores_stale_monitor_stop_file(self):
        (self.guard.root / 'monitor.stop').touch()
        payload = {'windows': [{'id': 'codex-weekly', 'remaining_pct': 80,
            'observed_at': stamp(), 'source': 'test'}]}
        with patch('usage_guard.subprocess.run') as run:
            run.return_value.returncode = 0
            run.return_value.stdout = json.dumps(payload)
            result = self.guard.refresh('codex')
        self.assertTrue(result['codex']['ok'])
        self.assertEqual(run.call_count, 1)

if __name__ == '__main__':
    unittest.main(verbosity=2)
