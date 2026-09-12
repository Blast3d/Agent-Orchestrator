from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from datetime import timedelta
import json
import multiprocessing
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'app'))

import usage_guard
from usage_guard import Guard, now, stamp


def success(provider='grok', remaining=80):
    identities = ('claude-five-hour', 'claude-seven-day') if provider == 'claude' else (provider + '-weekly',)
    return subprocess.CompletedProcess([], 0, json.dumps({'windows': [
        {'id': identity, 'remaining_pct': remaining, 'observed_at': stamp(),
         'max_age_seconds': 600, 'source': 'synthetic official-reader test'}
        for identity in identities]}), '')


def _refresh_process(root, requested, entered, release, calls, output):
    """Spawned Python test process; official collectors are always replaced."""
    guard = Guard(Path(root))
    real_lock = usage_guard.file_lock

    @contextmanager
    def watched_lock(path, *args, **kwargs):
        if Path(path).name == 'refresh-grok.lock':
            requested.put(True)
        with real_lock(path, *args, **kwargs):
            yield

    def fake_reader(*args, **kwargs):
        with calls.get_lock():
            calls.value += 1
            number = calls.value
        if number == 1:
            entered.set()
            if not release.wait(10):
                raise AssertionError('Synthetic cross-process test timed out')
        return success()

    with patch('usage_guard.file_lock', side_effect=watched_lock), \
         patch('usage_guard.subprocess.run', side_effect=fake_reader):
        output.put(guard.refresh('grok')['grok'])


class RefreshCoalescingTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.root = Path(self.folder.name)
        self.guard = Guard(self.root)

    def tearDown(self):
        self.folder.cleanup()

    def overlapping(self, response, callers=3):
        """Hold the first reader until every caller has reached the real lock."""
        real_lock = usage_guard.file_lock
        all_requested = threading.Event()
        first_reader = threading.Event()
        release = threading.Event()
        count_lock = threading.Lock()
        lock_calls = 0
        reader_calls = 0
        active = 0
        peak = 0

        @contextmanager
        def watched_lock(path, *args, **kwargs):
            nonlocal lock_calls
            if Path(path).name == 'refresh-grok.lock':
                with count_lock:
                    lock_calls += 1
                    if lock_calls == callers:
                        all_requested.set()
            with real_lock(path, *args, **kwargs):
                yield

        def reader(*args, **kwargs):
            nonlocal reader_calls, active, peak
            with count_lock:
                reader_calls += 1
                number = reader_calls
                active += 1
                peak = max(peak, active)
            try:
                if number == 1:
                    first_reader.set()
                    if not release.wait(5):
                        raise AssertionError('Test callers did not reach the provider lock')
                return response(number)
            finally:
                with count_lock:
                    active -= 1

        with patch('usage_guard.file_lock', side_effect=watched_lock), \
             patch('usage_guard.subprocess.run', side_effect=reader), \
             ThreadPoolExecutor(max_workers=callers) as pool:
            tasks = [pool.submit(self.guard.refresh, 'grok')]
            self.assertTrue(first_reader.wait(5))
            tasks.extend(pool.submit(Guard(self.root).refresh, 'grok') for _ in range(callers - 1))
            try:
                self.assertTrue(all_requested.wait(5))
            finally:
                release.set()
            results = [task.result(timeout=5)['grok'] for task in tasks]
        return results, reader_calls, peak

    def test_overlapping_successes_share_only_a_new_enough_snapshot(self):
        results, calls, peak = self.overlapping(lambda _: success())
        # First poll predates its waiters. One follow-up poll is new enough for
        # both; its second waiter reuses it instead of launching a third CLI.
        self.assertEqual(calls, 2)
        self.assertEqual(peak, 1)
        self.assertTrue(all(row['ok'] for row in results))
        self.assertEqual(sum(row.get('coalesced', False) for row in results), 1)
        self.assertTrue(self.guard.check('grok')['allowed'])

    def test_independent_processes_share_provider_lock_and_receipt(self):
        context = multiprocessing.get_context('spawn')
        requested, output = context.Queue(), context.Queue()
        entered, release = context.Event(), context.Event()
        calls = context.Value('i', 0)
        arguments = (str(self.root), requested, entered, release, calls, output)
        children = [context.Process(target=_refresh_process, args=arguments) for _ in range(3)]
        started = []
        try:
            children[0].start()
            started.append(children[0])
            self.assertTrue(entered.wait(10))
            for child in children[1:]:
                child.start()
                started.append(child)
            for _ in children:
                self.assertTrue(requested.get(timeout=10))
            release.set()
            results = [output.get(timeout=10) for _ in children]
            for child in started:
                child.join(timeout=10)
                self.assertEqual(child.exitcode, 0)
            self.assertEqual(calls.value, 2)
            self.assertTrue(all(row['ok'] for row in results))
            self.assertEqual(sum(row.get('coalesced', False) for row in results), 1)
        finally:
            release.set()
            for child in started:
                if child.is_alive():
                    child.terminate()
                child.join(timeout=5)
            requested.close()
            output.close()

    def test_one_inflight_failure_is_shared_without_serial_retry_storm(self):
        def failure(_):
            return subprocess.CompletedProcess([], 2, 'PRIVATE_TERMINAL_TRACE', '')
        results, calls, peak = self.overlapping(failure)
        self.assertEqual(calls, 1)
        self.assertEqual(peak, 1)
        self.assertTrue(all(not row['ok'] for row in results))
        self.assertEqual(sum(row.get('coalesced', False) for row in results), 2)
        self.assertNotIn('PRIVATE_TERMINAL_TRACE', json.dumps(results))
        self.assertFalse(self.guard.check('grok')['allowed'])

    def test_sequential_explicit_refreshes_each_collect(self):
        with patch('usage_guard.subprocess.run', side_effect=lambda *a, **kw: success()) as reader:
            first = self.guard.refresh('grok')['grok']
            second = self.guard.refresh('grok')['grok']
        self.assertEqual(reader.call_count, 2)
        self.assertNotIn('coalesced', first)
        self.assertNotIn('coalesced', second)

    def test_sequential_failure_can_be_retried_explicitly(self):
        with patch('usage_guard.subprocess.run', side_effect=[subprocess.CompletedProcess([], 2, '', ''), success()]) as reader:
            self.assertFalse(self.guard.refresh('grok')['grok']['ok'])
            self.assertTrue(self.guard.refresh('grok')['grok']['ok'])
        self.assertEqual(reader.call_count, 2)

    def test_different_providers_do_not_block_each_other(self):
        rendezvous = threading.Barrier(2)
        def reader(command, **kwargs):
            rendezvous.wait(timeout=5)
            return success(command[command.index('--provider') + 1])
        with patch('usage_guard.subprocess.run', side_effect=reader) as collector, ThreadPoolExecutor(max_workers=2) as pool:
            tasks = [pool.submit(self.guard.refresh, provider) for provider in ('grok', 'claude')]
            results = [task.result(timeout=5) for task in tasks]
        self.assertEqual(collector.call_count, 2)
        self.assertTrue(results[0]['grok']['ok'])
        self.assertTrue(results[1]['claude']['ok'])

    def test_monitor_cancellation_interrupts_lock_wait_without_another_poll(self):
        stopped = threading.Event()
        requested = threading.Event()
        real_lock = usage_guard.file_lock
        @contextmanager
        def watched_lock(path, *args, **kwargs):
            if Path(path).name == 'refresh-grok.lock':
                requested.set()
            with real_lock(path, *args, **kwargs):
                yield
        with real_lock(self.root / 'refresh-grok.lock'), \
             patch('usage_guard.file_lock', side_effect=watched_lock), \
             patch('usage_guard.subprocess.run') as reader, ThreadPoolExecutor(max_workers=1) as pool:
            task = pool.submit(self.guard.refresh, 'grok', stopped.is_set)
            self.assertTrue(requested.wait(5))
            stopped.set()
            self.assertEqual(task.result(timeout=2), {})
        reader.assert_not_called()

    def test_lock_timeout_holds_without_starting_another_reader(self):
        with usage_guard.file_lock(self.root / 'refresh-grok.lock'), \
             patch('usage_guard.REFRESH_LOCK_TIMEOUT', 0.01), \
             patch('usage_guard.subprocess.run') as reader:
            result = self.guard.refresh('grok')['grok']
        self.assertFalse(result['ok'])
        reader.assert_not_called()
        self.assertFalse(self.guard.check('grok')['allowed'])

    def test_snapshot_predating_finish_does_not_release_finished_reservation(self):
        self.guard.observe([{'id': 'grok-weekly', 'remaining_pct': 22,
                            'observed_at': stamp(), 'source': 'synthetic setup'}], 'grok')
        token = self.guard.check('grok', 'medium', True, 'synthetic work')['reservation_id']
        def reader(*args, **kwargs):
            # _refresh_one stamped poll start before entering this fake adapter.
            self.guard.finish(token, 'completed')
            return success(remaining=22)
        with patch('usage_guard.subprocess.run', side_effect=reader):
            self.assertTrue(self.guard.refresh('grok')['grok']['ok'])
        self.assertFalse(self.guard.check('grok', 'medium')['allowed'])
        with self.guard.state() as data:
            observation = data['windows']['grok-weekly']['observed_at']
            finished = data['reservations'][token]['finished_at']
        self.assertLess(observation, finished)
        self.assertIsNone(self.guard._coalesced_refresh_result('grok', finished))
        with patch('usage_guard.subprocess.run', side_effect=lambda *a, **kw: success(remaining=22)):
            self.assertTrue(self.guard.refresh('grok')['grok']['ok'])
        self.assertTrue(self.guard.check('grok', 'medium')['allowed'])

    def test_reuse_never_advances_observation_or_failure_time(self):
        requested = stamp(now() - timedelta(seconds=1))
        with patch('usage_guard.subprocess.run', side_effect=lambda *a, **kw: success()):
            self.guard.refresh('grok')
        with self.guard.state() as data:
            original = data['windows']['grok-weekly']['observed_at']
            receipt = data['refresh_attempts']['grok']['finished_at']
        self.assertTrue(self.guard._coalesced_refresh_result('grok', requested)['ok'])
        with self.guard.state() as data:
            self.assertEqual(data['windows']['grok-weekly']['observed_at'], original)
            self.assertEqual(data['refresh_attempts']['grok']['finished_at'], receipt)

    def test_stale_reset_missing_and_newer_failure_prevent_success_reuse(self):
        for problem in ('stale', 'reset', 'missing', 'failure'):
            with self.subTest(problem=problem):
                with patch('usage_guard.subprocess.run', side_effect=lambda *a, **kw: success()):
                    self.guard.refresh('grok')
                requested = stamp(now() - timedelta(seconds=700))
                with self.guard.state() as data:
                    if problem == 'stale':
                        data['windows']['grok-weekly']['observed_at'] = stamp(now() - timedelta(seconds=650))
                    elif problem == 'reset':
                        data['windows']['grok-weekly']['reset_at'] = stamp(now() - timedelta(seconds=1))
                    elif problem == 'missing':
                        data['windows'].pop('grok-weekly')
                    else:
                        data['refresh_errors']['grok'] = {'at': stamp(), 'error': 'synthetic newer failure'}
                self.assertIsNone(self.guard._coalesced_refresh_result('grok', requested))

    def test_invalid_provider_never_becomes_a_lock_path(self):
        with patch('usage_guard.subprocess.run') as reader:
            result = self.guard.refresh('../unexpected')
        self.assertFalse(result['../unexpected']['ok'])
        self.assertEqual(list(self.root.glob('refresh-*.lock')), [])
        reader.assert_not_called()


if __name__ == '__main__':
    unittest.main()
