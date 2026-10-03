"""Installing startup instructions preserves personal guidance and is repeatable."""
import importlib.util
from pathlib import Path
import tempfile
import unittest

SPEC = importlib.util.spec_from_file_location('global_install', Path(__file__).resolve().parents[1] / 'scripts/install_global.py')
INSTALL = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(INSTALL)


class StartupHookTests(unittest.TestCase):
    def test_every_discovery_mirror_preserves_provider_identity_verbatim(self):
        with tempfile.TemporaryDirectory() as directory:
            root, home = Path(directory) / 'app', Path(directory) / 'home'
            source = root / 'skills/multi-model-orchestrator'
            (source / 'references').mkdir(parents=True)
            guide = 'Claude is an Anthropic model. Codex is an OpenAI model.\n'
            (source / 'SKILL.md').write_text(guide, encoding='utf-8')
            (source / 'references/provider-routing.md').write_text('Use authorized routes.\n', encoding='utf-8')
            stale = home / '.agents/skills/multi-model-orchestrator/SKILL.md'
            stale.parent.mkdir(parents=True)
            stale.write_text('Codex is an Anthropic model.', encoding='utf-8')
            for _ in range(2):
                mirrors = INSTALL.install_orchestrator_skill_mirrors(root, home)
                self.assertEqual(len(mirrors), 3)
                for mirror in mirrors:
                    self.assertEqual((mirror / 'SKILL.md').read_bytes(), (source / 'SKILL.md').read_bytes())
                    self.assertEqual((mirror / 'references/provider-routing.md').read_bytes(),
                                     (source / 'references/provider-routing.md').read_bytes())

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

    def test_hook_explains_the_lead_switch_and_own_identity(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'CLAUDE.md'
            INSTALL.install_startup_hook(path, Path(directory) / 'app')
            content = path.read_text(encoding='utf-8')
            self.assertIn('lead selected', content)
            self.assertIn('--lead', content)
            for name in ('Claude', 'ASTRA', 'Sol'):
                self.assertIn(name, content)

    def test_ambiguous_markers_preserve_the_file(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'CLAUDE.md'
            original = INSTALL.STARTUP_BEGIN + '\nExisting personal text'
            path.write_text(original, encoding='utf-8')
            with self.assertRaises(ValueError):
                INSTALL.install_startup_hook(path)
            self.assertEqual(path.read_text(encoding='utf-8'), original)
