"""Installing startup instructions preserves personal guidance and is repeatable."""
import importlib.util
from pathlib import Path
import tempfile
import unittest

SPEC = importlib.util.spec_from_file_location('global_install', Path(__file__).resolve().parents[1] / 'scripts/install_global.py')
INSTALL = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(INSTALL)


class StartupHookTests(unittest.TestCase):
    def test_preserves_personal_text_and_replaces_only_managed_block(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'AGENTS.md'
            personal = '# Personal instructions\nKeep existing workspace locations.\n'
            path.write_text(personal, encoding='utf-8')
            INSTALL.install_startup_hook(path, Path(directory) / 'app-one')
            first = path.read_bytes()
            INSTALL.install_startup_hook(path, Path(directory) / 'app-one')
            self.assertEqual(path.read_bytes(), first)
            INSTALL.install_startup_hook(path, Path(directory) / 'app-two')
            content = path.read_text(encoding='utf-8')
            self.assertTrue(content.startswith(personal))
            self.assertEqual(content.count(INSTALL.STARTUP_BEGIN), 1)
            self.assertIn('app-two', content)
            self.assertNotIn('app-one', content)

    def test_ambiguous_markers_preserve_the_file(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'CLAUDE.md'
            original = INSTALL.STARTUP_BEGIN + '\nExisting personal text'
            path.write_text(original, encoding='utf-8')
            with self.assertRaises(ValueError):
                INSTALL.install_startup_hook(path)
            self.assertEqual(path.read_text(encoding='utf-8'), original)
