"""Background quota collection is bounded, detached, and advisory to dispatch."""
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'app'))
import background_usage as background
from usage_guard import file_lock


class BackgroundUsageTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.current = datetime(2026, 9, 10, 5, 0, tzinfo=timezone.utc)
        self.clock = patch.object(background, '_now', return_value=self.current).start()
        self.launch = patch.object(background.subprocess, 'Popen').start()
        self.guard = patch.object(background, 'Guard').start()
        self.guard.return_value.refresh.return_value = {'claude': {'ok': True}}
        self.addCleanup(patch.stopall)

    def metadata(self, provider='claude'):
        return self.root / 'background-usage' / (provider + '.json')

    def state(self, provider='claude'):
        return json.loads(self.metadata(provider).read_text(encoding='utf-8'))

    def seed(self, **fields):
        directory = self.metadata().parent
        directory.mkdir(parents=True, exist_ok=True)
        value = {'schema_version': 1, 'provider': 'claude', 'request_id': 'a' * 32,
                 'status': 'running', 'requested_at': (self.current - timedelta(minutes=10)).isoformat()}
        value.update(fields)
        self.metadata().write_text(json.dumps(value), encoding='utf-8')

    def test_request_launches_fixed_hidden_detached_helper_and_never_collects_inline(self):
        result = background.request_refresh(self.root, 'claude')
        self.assertEqual(result['status'], 'queued')
        self.guard.assert_not_called()
        self.launch.assert_called_once()
        command = self.launch.call_args.args[0]
        self.assertEqual(command[:2], [sys.executable, str(Path(background.__file__).resolve())])
        self.assertEqual(command[2:6], ['--root', str(self.root.resolve()), '--provider', 'claude'])
        self.assertEqual(command[6:], ['--request-id', result['request_id']])
        options = self.launch.call_args.kwargs
        self.assertNotIn('shell', options)
        for stream in ('stdin', 'stdout', 'stderr'):
            self.assertEqual(options[stream], subprocess.DEVNULL)
        self.assertTrue(options['close_fds'])
        if os.name == 'nt':
            self.assertTrue(options['creationflags'] & subprocess.CREATE_NO_WINDOW)
            self.assertTrue(options['creationflags'] & subprocess.DETACHED_PROCESS)
            self.assertTrue(options['creationflags'] & subprocess.CREATE_NEW_PROCESS_GROUP)
        else:
            self.assertTrue(options['start_new_session'])
        self.launch.return_value.wait.assert_not_called()
        self.launch.return_value.communicate.assert_not_called()
        self.assertEqual(self.state()['request_id'], result['request_id'])

    def test_rapid_and_concurrent_requests_coalesce_to_one_launch_per_provider(self):
        with ThreadPoolExecutor(max_workers=8) as pool:
            results = list(pool.map(lambda _: background.request_refresh(self.root, 'claude'), range(16)))
        self.assertEqual(sum(item['status'] == 'queued' for item in results), 1)
        self.assertEqual(sum(item['status'] == 'coalesced' for item in results), 15)
        self.launch.assert_called_once()
        self.assertEqual(background.request_refresh(self.root, 'grok')['status'], 'queued')
        self.assertEqual(self.launch.call_count, 2)

    def test_recent_finished_refresh_and_request_debounce_expire(self):
        self.seed(status='completed', finished_at=self.current.isoformat())
        self.assertEqual(background.request_refresh(self.root, 'claude')['status'], 'coalesced')
        self.clock.return_value = self.current + timedelta(seconds=background.DEBOUNCE_SECONDS)
        self.assertEqual(background.request_refresh(self.root, 'claude')['status'], 'queued')
        self.launch.assert_called_once()

    def test_active_worker_lock_coalesces_even_with_old_or_missing_metadata(self):
        directory = self.metadata().parent
        with file_lock(directory / 'claude.worker.lock', timeout=0):
            result = background.request_refresh(self.root, 'claude')
        self.assertEqual(result['status'], 'coalesced')
        self.launch.assert_not_called()
        self.assertEqual(background.request_refresh(self.root, 'claude')['status'], 'queued')

    def test_contending_launch_lock_returns_without_waiting(self):
        with file_lock(self.metadata().parent / 'claude.launch.lock', timeout=0):
            result = background.request_refresh(self.root, 'claude')
        self.assertEqual(result['status'], 'coalesced')
        self.launch.assert_not_called()

    def test_dead_helper_metadata_does_not_require_or_trust_pid(self):
        self.seed(pid=os.getpid(), status='running')
        result = background.request_refresh(self.root, 'claude')
        self.assertEqual(result['status'], 'queued')
        self.assertNotIn('pid', self.state())
        self.assertNotEqual(result['request_id'], 'a' * 32)

    def test_malformed_oversized_duplicate_and_future_metadata_recover(self):
        for raw in ('{bad json', '[]', 'x' * (background.MAX_METADATA_BYTES + 1),
                    '{"schema_version":1,"schema_version":1}', '\ufeffbroken'):
            with self.subTest(raw=raw[:30]):
                self.metadata().parent.mkdir(parents=True, exist_ok=True)
                self.metadata().write_text(raw, encoding='utf-8')
                self.assertEqual(background.request_refresh(self.root, 'claude')['status'], 'queued')
        self.seed(requested_at=(self.current + timedelta(days=365)).isoformat())
        self.assertEqual(background.request_refresh(self.root, 'claude')['status'], 'queued')
        self.seed(requested_at='2026-09-10T05:00:00')
        self.assertEqual(background.request_refresh(self.root, 'claude')['status'], 'queued')

    def test_launch_failure_is_safe_debounced_and_retryable(self):
        self.launch.side_effect = OSError('secret token in provider setup')
        result = background.request_refresh(self.root, 'claude')
        self.assertEqual(result['status'], 'error')
        self.assertNotIn('secret', json.dumps(result))
        self.assertNotIn('secret', self.metadata().read_text())
        self.assertEqual(background.request_refresh(self.root, 'claude')['status'], 'coalesced')
        self.clock.return_value = self.current + timedelta(seconds=31)
        self.launch.side_effect = None
        self.assertEqual(background.request_refresh(self.root, 'claude')['status'], 'queued')

    def test_invalid_provider_and_metadata_io_failure_never_raise_into_dispatch(self):
        for provider in ('all', '../claude', 'paid-api', None):
            with self.subTest(provider=provider):
                self.assertEqual(background.request_refresh(self.root, provider)['status'], 'error')
        self.launch.assert_not_called()
        with patch.object(background, 'write_json', side_effect=PermissionError('private path')):
            result = background.request_refresh(self.root, 'claude')
        self.assertEqual(result['status'], 'error')
        self.assertNotIn('private path', json.dumps(result))
        self.launch.assert_not_called()

    def test_child_refreshes_then_regenerates_dashboard_without_holding_shared_state_lock(self):
        requested = background.request_refresh(self.root, 'claude')
        calls = []
        def refresh(provider):
            calls.append(provider)
            with file_lock(self.root / 'state.lock', timeout=0):
                with file_lock(self.metadata().parent / 'claude.launch.lock', timeout=0):
                    pass
            self.assertEqual(self.state()['status'], 'running')
            return {'claude': {'ok': True}}
        self.guard.return_value.refresh.side_effect = refresh
        result = background.run_refresh(self.root, 'claude', requested['request_id'])
        self.assertEqual(result['status'], 'completed')
        self.assertEqual(calls, ['claude'])
        self.guard.return_value.dashboard.assert_called_once_with()
        self.assertEqual(self.state()['status'], 'completed')
        self.assertEqual(background.run_refresh(self.root, 'claude', requested['request_id'])['status'], 'coalesced')
        self.assertEqual(calls, ['claude'])

    def test_child_timeout_result_still_refreshes_dashboard_and_keeps_no_raw_logs(self):
        requested = background.request_refresh(self.root, 'claude')
        self.guard.return_value.refresh.return_value = {'claude': {'ok': False, 'error': 'sensitive raw output'}}
        result = background.run_refresh(self.root, 'claude', requested['request_id'])
        self.assertEqual(result['status'], 'error')
        self.guard.return_value.dashboard.assert_called_once_with()
        self.assertNotIn('sensitive', self.metadata().read_text())
        self.assertNotIn('sensitive', json.dumps(result))

    def test_child_guard_or_dashboard_failure_is_recorded_and_retryable(self):
        for failure_target in ('refresh', 'dashboard'):
            with self.subTest(failure_target=failure_target):
                self.seed(status='queued')
                self.guard.return_value.refresh.side_effect = None
                self.guard.return_value.dashboard.side_effect = None
                getattr(self.guard.return_value, failure_target).side_effect = RuntimeError('sensitive provider details')
                result = background.run_refresh(self.root, 'claude', 'a' * 32)
                self.assertEqual(result['status'], 'error')
                self.assertEqual(self.state()['status'], 'error')
                self.assertNotIn('sensitive', self.metadata().read_text())
        self.clock.return_value = self.current + timedelta(seconds=31)
        self.assertEqual(background.request_refresh(self.root, 'claude')['status'], 'queued')

    def test_child_singleton_and_superseded_request_never_collect(self):
        self.seed(status='queued')
        with file_lock(self.metadata().parent / 'claude.worker.lock', timeout=0):
            result = background.run_refresh(self.root, 'claude', 'a' * 32)
        self.assertEqual(result['status'], 'coalesced')
        self.assertEqual(background.run_refresh(self.root, 'claude', 'b' * 32)['status'], 'coalesced')
        self.assertEqual(background.run_refresh(self.root, 'claude', 'invalid')['status'], 'error')
        self.guard.assert_not_called()


if __name__ == '__main__':
    unittest.main()
