"""Exercise real monitor lifetime locks without running provider collectors."""
from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

APP = Path(__file__).resolve().parents[1] / 'app'
sys.path.insert(0, str(APP))
import start_usage_monitor as monitor


class MonitorControlTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name) / 'runtime with spaces'
        self.root.mkdir()
        script = Path(self.temp.name) / 'fake monitor.py'
        script.write_text(
            'import sys, os, time\nfrom pathlib import Path\n'
            f'sys.path.insert(0, {str(APP)!r})\n'
            'from usage_guard import file_lock, write_json\n'
            'root = Path(sys.argv[2])\n'
            'with file_lock(root / "monitor.lock", timeout=1):\n'
            '    stop = root / "monitor.stop"\n'
            '    stop.unlink(missing_ok=True)\n'
            '    with (root / "launches.log").open("a") as log: log.write(str(os.getpid()) + "\\n")\n'
            '    write_json(root / "monitor-state.json", {"pid": os.getpid()})\n'
            '    deadline = time.monotonic() + 10\n'
            '    while not stop.exists() and time.monotonic() < deadline: time.sleep(.02)\n',
            encoding='utf-8')
        self.script_patch = patch.object(monitor, 'SCRIPT', script)
        self.script_patch.start()
        self.addCleanup(self.script_patch.stop)
        self.addCleanup(self.stop)

    def wait_off(self):
        deadline = time.monotonic() + 3
        while monitor.monitor_status(self.root)['running'] and time.monotonic() < deadline:
            time.sleep(.03)
        self.assertEqual(monitor.monitor_status(self.root)['status'], 'off')

    def stop(self):
        monitor.set_monitor_enabled(False, self.root)
        self.wait_off()

    def test_stale_receipt_and_stop_file_do_not_report_on_or_launch(self):
        (self.root / 'monitor-state.json').write_text(json.dumps({'pid': 123}), encoding='utf-8')
        (self.root / 'monitor.stop').touch()
        with patch.object(monitor.subprocess, 'Popen') as spawn:
            self.assertEqual(monitor.monitor_status(self.root)['status'], 'off')
            self.assertEqual(monitor.set_monitor_enabled(False, self.root)['status'], 'off')
            spawn.assert_not_called()

    def test_simultaneous_status_reads_do_not_mistake_each_other_for_a_monitor(self):
        with ThreadPoolExecutor(max_workers=6) as workers:
            values = list(workers.map(lambda _: monitor.monitor_status(self.root)['status'], range(60)))
        self.assertEqual(set(values), {'off'})

    def test_concurrent_on_is_single_instance_and_off_releases_lifetime_lock(self):
        with ThreadPoolExecutor(max_workers=2) as workers:
            results = list(workers.map(lambda _: monitor.set_monitor_enabled(True, self.root), range(2)))
        self.assertTrue(all(result['status'] == 'on' for result in results))
        self.assertEqual(len((self.root / 'launches.log').read_text().splitlines()), 1)
        result = monitor.set_monitor_enabled(False, self.root)
        self.assertIn(result['status'], ('stopping', 'off'))
        self.wait_off()
        self.assertTrue((self.root / 'monitor-state.json').exists())

    def test_on_off_on_keeps_windows_startup_unchanged(self):
        with patch.object(monitor, 'configure_startup') as startup:
            monitor.set_monitor_enabled(True, self.root)
            self.stop()
            self.assertEqual(monitor.set_monitor_enabled(True, self.root)['status'], 'on')
            self.assertEqual(len((self.root / 'launches.log').read_text().splitlines()), 2)
            startup.assert_not_called()

    def test_switch_rejects_non_boolean_without_launching(self):
        for value in ('false', 0, 1, None):
            with self.subTest(value=value), self.assertRaises(ValueError):
                monitor.set_monitor_enabled(value, self.root)


if __name__ == '__main__':
    unittest.main()
