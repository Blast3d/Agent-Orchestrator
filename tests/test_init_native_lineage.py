"""Run lineage records timing identity without granting conversation access."""
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'app'))
from init_run import create_run, main

SESSION = '1234abcd-1234-4567-8910-123456789abc'


class NativeLineageTests(unittest.TestCase):
    def test_cli_records_parent_without_binding_conversation_history(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            with patch.dict('os.environ', {'CODEX_THREAD_ID': SESSION}), patch.object(sys, 'argv',
                    ['init_run.py', '--workspace', directory, '--name', 'lineage-test', '--objective', 'Verify native lineage']), patch('builtins.print'):
                main()
            run = next((root / '.orchestration').iterdir())
            record = json.loads((run / 'run.json').read_text(encoding='utf-8'))
            self.assertEqual(record['native_parent_session_id'], SESSION)
            self.assertFalse((run / 'viewer-session.json').exists())

    def test_invalid_parent_is_rejected_before_creating_run(self):
        with tempfile.TemporaryDirectory() as directory:
            for invalid in ('wrong', '../' + SESSION, SESSION + '?prompt=unsafe', 42):
                with self.subTest(parent=invalid), self.assertRaises(ValueError):
                    create_run(directory, 'invalid-lineage', 'Reject invalid identity', native_parent_session_id=invalid)
            self.assertFalse((Path(directory) / '.orchestration').exists())

    def test_non_codex_run_needs_no_parent(self):
        with tempfile.TemporaryDirectory() as directory:
            _, record = create_run(directory, 'other-provider', 'Keep non-Codex creation compatible')
            self.assertNotIn('native_parent_session_id', record)


if __name__ == '__main__':
    unittest.main()
