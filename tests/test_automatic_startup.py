"""Automatic startup is optional; manual orchestration remains available."""
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'app'))
import automatic_startup
import orchestration_lifecycle as lifecycle


class AutomaticStartupTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)

    def test_missing_setting_defaults_off_without_writing(self):
        state = automatic_startup.describe(self.root)
        self.assertFalse(state['enabled'])
        self.assertTrue(state['manual_available'])
        self.assertEqual(state['provider_calls'], 0)
        self.assertEqual(list(self.root.iterdir()), [])

    def test_switch_persists_and_invalid_values_preserve_saved_state(self):
        self.assertTrue(automatic_startup.set_enabled(True, self.root)['enabled'])
        self.assertTrue(automatic_startup.describe(self.root)['enabled'])
        for value in ('false', 0, 1, None, []):
            with self.subTest(value=value), self.assertRaises(ValueError):
                automatic_startup.set_enabled(value, self.root)
        self.assertTrue(automatic_startup.describe(self.root)['enabled'])
        self.assertFalse(automatic_startup.set_enabled(False, self.root)['enabled'])
        self.assertFalse(automatic_startup.describe(self.root)['enabled'])

    def test_unreadable_or_invalid_setting_stops_automatic_startup(self):
        path = self.root / 'runtime/automatic-startup.json'
        path.parent.mkdir()
        for content in ('{', '[]', '{"enabled":"true"}', '{"enabled":1}', '{}'):
            with self.subTest(content=content):
                path.write_text(content, encoding='utf-8')
                state = automatic_startup.describe(self.root)
                self.assertFalse(state['enabled'])
                self.assertIn('reason', state)
                self.assertEqual(path.read_text(encoding='utf-8'), content)

    def test_off_returns_before_run_creation_context_or_memory(self):
        with patch.object(lifecycle, 'create_run') as create, \
                patch.object(lifecycle, 'load_operating_context') as context, \
                patch.object(lifecycle, 'BrainStore') as brain:
            result = lifecycle.start_run(automatic=True, root=self.root, brain_factory=brain)
        self.assertEqual(result['status'], 'disabled')
        self.assertEqual(result['provider_calls'], 0)
        self.assertFalse((self.root / '.orchestration').exists())
        create.assert_not_called()
        context.assert_not_called()
        brain.assert_not_called()

    def test_manual_start_reaches_existing_lifecycle_while_off(self):
        with patch.object(lifecycle, 'load_operating_context', side_effect=RuntimeError('manual reached')) as context:
            with self.assertRaisesRegex(RuntimeError, 'manual reached'):
                lifecycle.start_run(workspace=self.root, name='manual', objective='Explicit manual work', root=self.root)
            context.assert_called_once_with()

    def test_on_reaches_existing_lifecycle_for_automatic_start(self):
        automatic_startup.set_enabled(True, self.root)
        with patch.object(lifecycle, 'load_operating_context', side_effect=RuntimeError('automatic reached')):
            with self.assertRaisesRegex(RuntimeError, 'automatic reached'):
                lifecycle.start_run(automatic=True, workspace=self.root, name='automatic',
                                    objective='Substantial automatic work', root=self.root)


if __name__ == '__main__':
    unittest.main()
