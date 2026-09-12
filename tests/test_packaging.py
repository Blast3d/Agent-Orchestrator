import importlib.util
import json
from pathlib import Path
import sys
import tempfile
import unittest
import zipfile

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / 'app'), str(ROOT / 'scripts')]
from portable_setup import initialize
from package_app import extract_checked, source_files


class PackagingTests(unittest.TestCase):
    def test_bootstrap_preserves_existing_even_incomplete_data(self):
        with tempfile.TemporaryDirectory(prefix='package with spaces ') as folder:
            root = Path(folder)
            initialize(root, home=root / 'new user')
            files = ['config/workers.json', 'config/ollama-profile.json', 'runtime/policy.json']
            for name in files:
                (root / name).write_bytes(b'private custom settings\x00')
            result = initialize(root, home=root / 'different user')
            self.assertEqual(result['created'], [])
            for name in files:
                self.assertEqual((root / name).read_bytes(), b'private custom settings\x00')

    def test_configuration_uses_target_install_and_user(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            initialize(root, home=root / 'user')
            data = json.loads((root / 'config/workers.json').read_text())
            self.assertEqual(data['application_root'], str(root.resolve()))
            self.assertFalse(data['policy']['automatic_billable_fallback'])
            self.assertIn(str(root / 'user'), data['workers'][0]['executable'])

    def test_source_selection_excludes_private_state_and_copies(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            included = ['app/module.py', 'docs/guide.md', 'orchestrator.py', '.gitignore']
            excluded = ['runtime/policy.json', 'runs/task/prompt.md', '.orchestration/secret.md',
                        'config/workers.json', '.claude/settings.local.json', 'app/.env',
                        'app/credentials.json', 'app/secrets.json', 'app/private.sqlite',
                        'app/__pycache__/cache.pyc', 'archive/old.py']
            for relative in included + excluded:
                file = root / relative
                file.parent.mkdir(parents=True, exist_ok=True)
                file.write_text('fixture')
            self.assertEqual({str(p).replace('\\', '/') for p in source_files(root)}, set(included))

    def test_archive_traversal_is_rejected_before_extraction(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            archive = root / 'bad.zip'
            with zipfile.ZipFile(archive, 'w') as stream:
                stream.writestr('../escape.txt', 'bad')
            with self.assertRaises(ValueError):
                extract_checked(archive, root / 'extracted')
            self.assertFalse((root / 'escape.txt').exists())


if __name__ == '__main__':
    unittest.main()
