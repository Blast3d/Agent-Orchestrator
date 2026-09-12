"""Model defaults and reversible family pauses without Claude or quota calls."""
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'app'))
import claude_models
from dispatch_worker import cloud_command


class ClaudeModelTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.config = self.root / 'config/workers.json'
        self.config.parent.mkdir()
        patched = patch.object(claude_models, 'ROOT', self.root)
        patched.start()
        self.addCleanup(patched.stop)
        self.write()

    def write(self, value=None):
        value = value if value is not None else {
            'policy': {'paused_claude_model_families': ['fable']},
            'workers': [{'id': 'claude', 'requested_model': 'opus'}],
            'coordinator_handoff': {'backup_model': 'opus'},
        }
        self.config.write_text(json.dumps(value), encoding='utf-8')

    def test_worker_and_coordinator_defaults_read_configured_opus(self):
        self.assertEqual(claude_models.select_model(), 'opus')
        self.assertEqual(claude_models.select_model('coordinator'), 'opus')
        command, _ = cloud_command('claude', 'Supplied synthetic text', self.root)
        self.assertEqual(command[command.index('--model') + 1], 'opus')

    def test_pause_covers_alias_pinned_names_and_context_variants(self):
        for model in ('fable', 'fable[1m]', 'claude-fable', 'claude-fable-5',
                      'claude-fable-5[1m]', 'CLAUDE-FABLE-5-20260911', 'best', 'best[1m]'):
            with self.subTest(model=model):
                with self.assertRaisesRegex(ValueError, 'paused by the user'):
                    claude_models.select_model(requested=model)
                with self.assertRaisesRegex(ValueError, 'paused by the user'):
                    claude_models.require_model_allowed(model)
                with self.assertRaisesRegex(ValueError, 'paused by the user'):
                    cloud_command('claude', 'Supplied text', self.root, model)

    def test_other_families_remain_explicitly_selectable(self):
        for model in ('sonnet', 'opus', 'haiku', 'opus[1m]', 'claude-opus-4-6'):
            with self.subTest(model=model):
                self.assertEqual(claude_models.select_model(requested=model), model)
                self.assertEqual(claude_models.require_model_allowed(model), model)

    def test_config_changes_are_fresh_and_pause_has_no_expiry(self):
        self.write({'policy': {'paused_claude_model_families': []}})
        self.assertEqual(claude_models.require_model_allowed('fable'), 'fable')
        self.write()
        with self.assertRaisesRegex(ValueError, 'paused by the user'):
            claude_models.require_model_allowed('fable')
        self.write({'workers': [{'id': 'claude', 'requested_model': 'haiku'}]})
        self.assertEqual(claude_models.select_model(), 'haiku')

    def test_missing_default_fields_preserve_legacy_values(self):
        self.write({})
        self.assertEqual(claude_models.select_model(), 'sonnet')
        self.assertEqual(claude_models.select_model('coordinator'), 'claude-fable-5')

    def test_invalid_policy_or_model_is_rejected(self):
        for value in ([], {'policy': None}, {'policy': {'paused_claude_model_families': 'fable'}},
                      {'policy': {'paused_claude_model_families': [None]}},
                      {'policy': {'paused_claude_model_families': ['Fable']}},
                      {'workers': None}, {'workers': [{'id': 'claude', 'requested_model': None}]},
                      {'workers': [{'id': 'claude'}, {'id': 'claude'}]}):
            with self.subTest(config=value):
                self.write(value)
                with self.assertRaises(ValueError):
                    claude_models.select_model()
        self.write({'coordinator_handoff': []})
        with self.assertRaises(ValueError):
            claude_models.select_model('coordinator')
        self.write()
        for model in ('', ' ', '--model', 'opus; command', 5):
            with self.subTest(model=model):
                with self.assertRaises(ValueError):
                    claude_models.require_model_allowed(model)

    def test_unreadable_policy_holds_explicit_models_too(self):
        self.config.write_text('{broken', encoding='utf-8')
        with self.assertRaisesRegex(ValueError, 'could not be read'):
            claude_models.select_model(requested='opus')
        self.config.unlink()
        with self.assertRaisesRegex(ValueError, 'could not be read'):
            claude_models.require_model_allowed('opus')


if __name__ == '__main__':
    unittest.main()
