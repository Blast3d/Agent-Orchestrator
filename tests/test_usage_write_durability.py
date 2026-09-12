"""Failed quota writes must preserve the last complete state and admission holds."""
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'app'))
from usage_guard import Guard, write_json


class UsageWriteDurabilityTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.root = Path(self.folder.name)
        self.path = self.root / 'usage.json'
        self.original = b'{"reservations": {"held": {"finished_at": null}}}\n'
        self.path.write_bytes(self.original)

    def tearDown(self):
        self.folder.cleanup()

    def assert_preserved(self):
        self.assertEqual(self.path.read_bytes(), self.original)
        self.assertEqual(list(self.root.glob('*.tmp')), [])

    def test_complete_bytes_are_synced_before_replacing_old_state(self):
        value = {'reservations': {'held': {'finished_at': None}}, 'label': 'caf\u00e9'}
        expected = (json.dumps(value, indent=2) + '\n').encode('utf-8')
        events = []
        real_fsync, real_replace = os.fsync, os.replace

        def sync(handle):
            self.assertEqual(self.path.read_bytes(), self.original)
            temporary, = self.root.glob('*.tmp')
            # Reading through another handle proves the Python buffer was flushed.
            self.assertEqual(temporary.read_bytes(), expected)
            events.append('sync')
            real_fsync(handle)

        def replace(source, target):
            self.assertEqual(events, ['sync'])
            self.assertEqual(target, self.path)
            self.assertEqual(source.parent, target.parent)
            self.assertEqual(self.path.read_bytes(), self.original)
            events.append('replace')
            real_replace(source, target)

        with patch('usage_guard.os.fsync', side_effect=sync), \
                patch('usage_guard.os.replace', side_effect=replace):
            write_json(self.path, value)
        self.assertEqual(events, ['sync', 'replace'])
        self.assertEqual(self.path.read_bytes(), expected)
        self.assertEqual(list(self.root.glob('*.tmp')), [])

    def test_partial_serialization_failure_preserves_old_state_and_cleans_temp(self):
        with self.assertRaises(TypeError):
            write_json(self.path, {'first': 'written before invalid value', 'invalid': object()})
        self.assert_preserved()

    def test_flush_failure_preserves_old_state_and_cleans_temp(self):
        real_open = Path.open

        class FailingFlush:
            def __init__(self, handle):
                self.handle = handle

            def __enter__(self):
                return self

            def __exit__(self, *args):
                self.handle.close()

            def write(self, text):
                return self.handle.write(text)

            def flush(self):
                raise OSError('injected flush failure')

        def open_file(path, *args, **kwargs):
            handle = real_open(path, *args, **kwargs)
            return FailingFlush(handle) if path.suffix == '.tmp' else handle

        with patch.object(Path, 'open', open_file), \
                patch('usage_guard.os.replace') as replace, \
                self.assertRaisesRegex(OSError, 'injected flush failure'):
            write_json(self.path, {'replacement': True})
        replace.assert_not_called()
        self.assert_preserved()

    def test_fsync_failure_preserves_old_state_and_cleans_temp(self):
        with patch('usage_guard.os.fsync', side_effect=OSError('injected sync failure')), \
                patch('usage_guard.os.replace') as replace, \
                self.assertRaisesRegex(OSError, 'injected sync failure'):
            write_json(self.path, {'replacement': True})
        replace.assert_not_called()
        self.assert_preserved()

    def test_replace_failure_preserves_old_state_and_cleans_temp(self):
        with patch('usage_guard.os.replace', side_effect=PermissionError('injected replace failure')), \
                self.assertRaisesRegex(PermissionError, 'injected replace failure'):
            write_json(self.path, {'replacement': True})
        self.assert_preserved()

    def test_exclusive_temp_collision_does_not_change_either_existing_file(self):
        with patch('usage_guard.uuid.uuid4') as unique:
            unique.return_value.hex = 'collision'
            temporary = self.path.with_name(self.path.name + '.collision.tmp')
            temporary.write_bytes(b'belongs to another writer')
            with self.assertRaises(FileExistsError):
                write_json(self.path, {'replacement': True})
        self.assertEqual(self.path.read_bytes(), self.original)
        self.assertEqual(temporary.read_bytes(), b'belongs to another writer')

    def test_corrupt_existing_state_is_never_silently_reinitialized(self):
        guard = Guard(self.root)
        for damaged in (b'\x00' * 182245, b'', b'{"reservations":', b'\xff'):
            with self.subTest(length=len(damaged)):
                self.path.write_bytes(damaged)
                with patch('usage_guard.write_json') as write, \
                        self.assertRaises((json.JSONDecodeError, UnicodeDecodeError)):
                    guard.check('local-chat', reserve=True, task='must not start')
                write.assert_not_called()
                self.assertEqual(self.path.read_bytes(), damaged)
                self.assertEqual(list(self.root.glob('*.tmp')), [])

    def test_failed_reservation_commit_does_not_publish_an_admission(self):
        self.path.unlink()
        guard = Guard(self.root)
        guard.check('local-chat')
        before = self.path.read_bytes()
        with patch('usage_guard.os.fsync', side_effect=OSError('injected sync failure')), \
                self.assertRaisesRegex(OSError, 'injected sync failure'):
            guard.check('local-chat', reserve=True, task='must not start')
        self.assertEqual(self.path.read_bytes(), before)
        self.assertEqual(json.loads(before)['reservations'], {})
        self.assertEqual(list(self.root.glob('*.tmp')), [])


if __name__ == '__main__':
    unittest.main()
