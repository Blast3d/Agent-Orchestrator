"""Storage admission on temporary data only; no provider or local-model calls."""
from concurrent.futures import ThreadPoolExecutor
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'app'))
from storage_budget import StorageBudget, StorageLimitError


class StorageBudgetTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)

    def budget(self, **kwargs):
        settings = dict(brain_limit_bytes=32_000, total_limit_bytes=64_000,
                        recovery_reserve_bytes=1_000, throttle_fraction=1.0)
        settings.update(kwargs)
        return StorageBudget(self.root, **settings)

    def write(self, relative, size):
        path = self.root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b'x' * size)
        return path

    def test_scopes_include_wal_temporary_exports_and_backups(self):
        sizes = {'runtime/brain/brain.sqlite': 100,
                 'runtime/brain/brain.sqlite-wal': 200,
                 'runtime/brain/temp/work.tmp': 300,
                 'runtime/brain/exports/view.md': 400,
                 'runtime/brain/backups/old.sqlite': 500,
                 'runtime/usage.json': 600,
                 'runs/tasks/result.json': 700,
                 '.orchestration/example/brief.md': 800,
                 'app/source.py': 9_000,
                 'vendor/module.bin': 10_000,
                 'archive/old.zip': 11_000}
        for relative, size in sizes.items():
            self.write(relative, size)
        result = self.budget().status()
        self.assertEqual(result['brain_bytes'], 1_500)
        self.assertEqual(result['total_bytes'], 3_601)  # plus lock byte
        self.assertEqual(result['status'], 'ready')
        self.assertNotIn(str(self.root), json.dumps(result))

    def test_defaults_and_serializable_status(self):
        result = StorageBudget(self.root).status()
        self.assertEqual(result['brain_limit_bytes'], 1024 ** 3)
        self.assertEqual(result['total_limit_bytes'], 2 * 1024 ** 3)
        self.assertEqual(result['throttle_fraction'], 0.8)
        json.dumps(result)

    def test_brain_reservation_is_charged_to_both_caps(self):
        budget = self.budget()
        token = budget.reserve(10_000, 'brain')
        result = budget.status()
        self.assertEqual(result['brain_reserved_bytes'], 10_000)
        self.assertEqual(result['reserved_bytes'], 10_000)
        self.assertEqual(result['projected_brain_bytes'], 11_000)
        self.assertEqual(result['projected_total_bytes'], result['total_bytes'] + 11_000)
        self.assertTrue(budget.release(token))
        self.assertFalse(budget.release(token))

    def test_check_brain_bytes_are_included_in_total_once(self):
        result = self.budget().check(brain_bytes=2_000, total_bytes=3_000)
        self.assertEqual(result['projected_brain_bytes'], 3_000)
        self.assertEqual(result['projected_total_bytes'], result['total_bytes'] + 6_000)

    def test_brain_hard_cap_preserves_recovery_space(self):
        budget = self.budget()
        self.write('runtime/brain/data', 30_000)
        with self.assertRaises(StorageLimitError):
            budget.reserve(1_001, 'brain')
        self.assertEqual(budget.status()['reservation_count'], 0)
        budget.check(brain_bytes=1_000)

    def test_task_reservation_does_not_consume_brain_budget(self):
        budget = self.budget()
        self.write('runtime/brain/data', 31_000)
        token = budget.reserve(5_000, 'task')
        self.assertEqual(budget.status()['brain_reserved_bytes'], 0)
        self.assertTrue(budget.release(token))

    def test_total_cap_counts_all_held_reservations_and_recovery(self):
        budget = self.budget()
        budget.reserve(20_000, 'brain')
        budget.reserve(20_000, 'task')
        with self.assertRaises(StorageLimitError):
            budget.reserve(24_000, 'task')
        self.assertEqual(budget.status()['reservation_count'], 2)

    def test_default_throttle_holds_optional_writes_at_eighty_percent(self):
        budget = self.budget(throttle_fraction=0.8)
        self.write('runtime/brain/data', 24_600)
        self.assertEqual(budget.status()['status'], 'throttle')
        with self.assertRaises(StorageLimitError):
            budget.reserve(1, 'brain')
        # Additional managed tasks remain possible when only brain is full.
        budget.reserve(1, 'task')

    def test_projected_write_cannot_cross_throttle_threshold(self):
        budget = self.budget(throttle_fraction=0.8)
        with self.assertRaises(StorageLimitError):
            budget.reserve(24_600, 'brain')
        with self.assertRaises(StorageLimitError):
            budget.reserve(51_000, 'task')

    def test_explicit_release_restores_admission_after_allocations_are_used(self):
        budget = self.budget(throttle_fraction=0.8)
        token = budget.reserve(20_000, 'brain')
        self.write('runtime/brain/data', 20_000)
        with self.assertRaises(StorageLimitError):
            budget.check(brain_bytes=1)
        self.assertTrue(budget.release(token))
        budget.check(brain_bytes=1)

    def test_reservations_survive_new_instances_without_age_expiry(self):
        token = self.budget().reserve(20_000, 'brain', key='still-running')
        with patch('time.time', return_value=99_999_999_999):
            reopened = self.budget()
            self.assertEqual(reopened.status()['brain_reserved_bytes'], 20_000)
            with self.assertRaises(StorageLimitError):
                reopened.reserve(12_000, 'brain')
            reopened.release(token)

    def test_allocation_releases_when_body_fails(self):
        budget = self.budget()
        with self.assertRaisesRegex(ValueError, 'synthetic failure'):
            with budget.allocation(500) as token:
                self.assertEqual(budget.status()['reservation_count'], 1)
                self.assertEqual(len(token), 32)
                raise ValueError('synthetic failure')
        self.assertEqual(budget.status()['reservation_count'], 0)

    def test_lowered_budget_holds_new_work_but_can_release_older_reservation(self):
        token = self.budget().reserve(40_000, 'task')
        budget = self.budget(brain_limit_bytes=10_000, total_limit_bytes=20_000)
        self.assertEqual(budget.status()['status'], 'full')
        with self.assertRaises(StorageLimitError):
            budget.reserve(1)
        self.assertTrue(budget.release(token))
        budget.reserve(1)

    def test_key_retries_reuse_slot_and_conflicting_retries_fail(self):
        budget = self.budget()
        token = budget.reserve(100, 'task', key='job-id')
        self.assertEqual(token, self.budget().reserve(100, 'task', key='job-id'))
        self.assertEqual(budget.status()['reservation_count'], 1)
        with self.assertRaises(StorageLimitError):
            budget.reserve(101, 'task', key='job-id')
        with self.assertRaises(StorageLimitError):
            budget.reserve(100, 'brain', key='job-id')

    def test_reservation_count_bound_and_released_slot(self):
        budget = self.budget(max_reservations=2)
        token = budget.reserve(1)
        budget.reserve(1)
        original = budget.path.read_bytes()
        with self.assertRaises(StorageLimitError):
            budget.reserve(1)
        self.assertEqual(original, budget.path.read_bytes())
        budget.release(token)
        budget.reserve(1)

    def test_corrupt_state_fails_closed_without_overwrite(self):
        budget = self.budget()
        for raw in (b'{bad', b'[]', b'{"version":1,"version":1,"reservations":{}}',
                    b'{"version":true,"reservations":{}}',
                    b'{"version":1,"reservations":{"bad":{}}}'):
            with self.subTest(raw=raw):
                budget.path.write_bytes(raw)
                for action in (budget.status, lambda: budget.reserve(1), lambda: budget.release('missing')):
                    with self.assertRaises(StorageLimitError) as raised:
                        action()
                    self.assertNotIn(str(self.root), str(raised.exception))
                self.assertEqual(budget.path.read_bytes(), raw)

    def test_state_read_and_write_sizes_are_bounded(self):
        budget = self.budget(max_state_bytes=200)
        budget.path.write_bytes(b' ' * 201)
        with self.assertRaises(StorageLimitError):
            budget.status()
        budget.path.unlink()
        budget.reserve(1)
        original = budget.path.read_bytes()
        with self.assertRaises(StorageLimitError):
            budget.reserve(1, key='x' * 128)
        self.assertEqual(original, budget.path.read_bytes())

    def test_atomic_replace_failure_preserves_old_state_and_cleans_temp(self):
        budget = self.budget()
        budget.reserve(1)
        original = budget.path.read_bytes()
        with patch('storage_budget.os.replace', side_effect=OSError('private path')):
            with self.assertRaises(StorageLimitError) as raised:
                budget.reserve(2)
        self.assertNotIn('private path', str(raised.exception))
        self.assertEqual(original, budget.path.read_bytes())
        self.assertEqual(list(budget.runtime.glob('storage-reservations.*.tmp')), [])

    def test_linked_scope_and_linked_file_fail_closed(self):
        outside = self.root / 'outside'
        outside.mkdir()
        (outside / 'private').write_bytes(b'keep')
        scope = self.root / 'runs'
        try:
            scope.symlink_to(outside, target_is_directory=True)
        except OSError:
            self.skipTest('Creating symlinks is not available in this Windows session')
        budget = self.budget()
        with self.assertRaises(StorageLimitError):
            budget.status()
        scope.unlink()
        budget.runtime.joinpath('linked').symlink_to(outside / 'private')
        with self.assertRaises(StorageLimitError):
            budget.reserve(1)
        self.assertEqual((outside / 'private').read_bytes(), b'keep')

    def test_symlinked_state_cannot_be_read_or_overwritten(self):
        budget = self.budget()
        outside = self.root / 'outside.json'
        outside.write_text('{"private":"keep"}', encoding='utf-8')
        try:
            budget.path.symlink_to(outside)
        except OSError:
            self.skipTest('Creating symlinks is not available in this Windows session')
        with self.assertRaises(StorageLimitError):
            budget.reserve(1)
        self.assertEqual(outside.read_text(encoding='utf-8'), '{"private":"keep"}')

    def test_windows_junction_scope_is_rejected(self):
        if os.name != 'nt':
            self.skipTest('Windows junction verification')
        outside = self.root / 'outside'
        outside.mkdir()
        junction = self.root / 'runs'
        created = subprocess.run(['cmd.exe', '/c', 'mklink', '/J', str(junction), str(outside)],
                                 capture_output=True, text=True)
        if created.returncode:
            self.skipTest('Junction creation is unavailable')
        try:
            with self.assertRaises(StorageLimitError):
                self.budget().status()
        finally:
            os.rmdir(junction)
        self.assertTrue(outside.exists())

    def test_scan_bound_fails_closed(self):
        self.write('runs/one', 1)
        self.write('runs/two', 1)
        with self.assertRaises(StorageLimitError):
            self.budget(max_scan_entries=3).status()

    def test_independent_processes_share_atomic_admission(self):
        self.budget()
        app = str(Path(__file__).resolve().parents[1] / 'app')
        code = ('import sys; sys.path.insert(0, sys.argv[1]); '
                'from storage_budget import StorageBudget, StorageLimitError\n'
                'b=StorageBudget(sys.argv[2],brain_limit_bytes=32000,total_limit_bytes=64000,'
                'recovery_reserve_bytes=1000,throttle_fraction=1)\n'
                'try:\n b.reserve(20000); print("accepted")\n'
                'except StorageLimitError:\n print("held")\n')
        def submit(_):
            result = subprocess.run([sys.executable, '-c', code, app, str(self.root)],
                                    capture_output=True, text=True, timeout=20)
            self.assertEqual(result.returncode, 0, result.stderr)
            return result.stdout.strip()
        with ThreadPoolExecutor(max_workers=8) as pool:
            results = list(pool.map(submit, range(8)))
        self.assertEqual(results.count('accepted'), 3, results)
        self.assertEqual(results.count('held'), 5, results)
        self.assertEqual(self.budget().status()['reserved_bytes'], 60_000)

    def test_invalid_configuration_and_inputs(self):
        for changes in ({'throttle_fraction': float('nan')}, {'throttle_fraction': 0},
                        {'throttle_fraction': True}, {'brain_limit_bytes': 64_001},
                        {'recovery_reserve_bytes': 32_000}, {'max_reservations': 0}):
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                self.budget(**changes)
        budget = self.budget()
        for value in (-1, True, 1.2, '1', 10 ** 1000):
            with self.subTest(value=value), self.assertRaises(ValueError):
                budget.check(brain_bytes=value)
        for value in (0, -1, True):
            with self.assertRaises(ValueError):
                budget.reserve(value)
        with self.assertRaises(ValueError):
            budget.reserve(1, kind='other')
        with self.assertRaises(ValueError):
            budget.reserve(1, key='')


if __name__ == '__main__':
    unittest.main()
