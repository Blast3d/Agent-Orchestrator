"""Synthetic identity, capability, and launch-boundary checks; never open a UI."""
import json
import subprocess
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'app'))

from coordinator_handoff import Coordinator
from coordinator_interaction import (codex_interaction, codex_support,
                                     open_codex_conversation, transcript_source)
from coordinator_viewer import ViewerStore
from init_run import create_run

SESSION = '1234abcd-1234-4567-8910-123456789abc'
OTHER = '9999abcd-1234-4567-8910-123456789abc'


def write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value), encoding='utf-8')


class CapabilityTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.home = Path(self.temp.name)
        self.extensions = self.home / '.vscode/extensions'
        self.extension = self.extensions / 'openai.chatgpt-26.903.61454-win32-x64'
        write(self.extensions / 'extensions.json', [
            {'identifier': {'id': 'openai.chatgpt'}, 'relativeLocation': self.extension.name}])
        write(self.extension / 'package.json', {'name': 'chatgpt', 'publisher': 'openai',
              'version': '26.903.61454', 'activationEvents': ['onUri']})
        self.source = self.extension / 'out/extension.js'
        self.source.parent.mkdir()
        self.source.write_text('registerUriHandler async handleUri( navigateToRoute( "/local" /:conversationId', encoding='utf-8')
        self.launcher = self.home / 'AppData/Local/Programs/Microsoft VS Code/Code.exe'
        self.launcher.parent.mkdir(parents=True)
        self.launcher.write_bytes(b'fixture')

    def test_active_installed_route_and_launcher_are_required(self):
        result = codex_support(self.home, platform='win32')
        self.assertTrue(result['available'])
        self.assertEqual(result['launcher'], str(self.launcher.resolve()))
        self.assertEqual(result['extension_version'], '26.903.61454')
        self.source.write_text('registerUriHandler but no conversation route', encoding='utf-8')
        self.assertFalse(codex_support(self.home, platform='win32')['available'])

    def test_stale_or_escaping_extension_location_is_not_used(self):
        for location in ('../foreign/extension', str(self.extension), 'openai.chatgpt-missing'):
            with self.subTest(location=location):
                write(self.extensions / 'extensions.json', [
                    {'identifier': {'id': 'openai.chatgpt'}, 'relativeLocation': location}])
                self.assertFalse(codex_support(self.home, platform='win32')['available'])

    def test_unsupported_platform_and_missing_activation_are_explained(self):
        self.assertFalse(codex_support(self.home, platform='linux')['available'])
        write(self.extension / 'package.json', {'name': 'chatgpt', 'publisher': 'openai'})
        result = codex_support(self.home, platform='win32')
        self.assertFalse(result['available'])
        self.assertTrue(result['reason'])

    def test_launch_is_fixed_uri_navigation_without_prompt_or_resume(self):
        support = codex_support(self.home, platform='win32')
        launched = Mock()
        launched.return_value.wait.return_value = 0
        with patch.dict('os.environ', {'ELECTRON_RUN_AS_NODE': '1'}):
            self.assertEqual(open_codex_conversation(SESSION, support, launch=launched), 'opened')
        self.assertNotIn('ELECTRON_RUN_AS_NODE', launched.call_args.kwargs['env'])
        self.assertEqual(launched.call_args.args[0], [str(self.launcher.resolve()), '--open-url',
                          'vscode://openai.chatgpt/local/' + SESSION])
        self.assertFalse(launched.call_args.kwargs.get('shell', False))
        for bad in (SESSION + '?prompt=unsafe', '../' + SESSION, None, '--resume', SESSION + '\n'):
            with self.subTest(identifier=bad), self.assertRaises(ValueError):
                open_codex_conversation(bad, support, launch=launched)
        self.assertEqual(launched.call_count, 1)

    def test_missing_launcher_after_check_fails_before_launch(self):
        support = codex_support(self.home, platform='win32')
        self.launcher.unlink()
        launched = Mock()
        with self.assertRaises(ValueError):
            open_codex_conversation(SESSION, support, launch=launched)
        launched.assert_not_called()

    def test_failed_editor_launch_is_reported_instead_of_claiming_delivery(self):
        launched = Mock()
        launched.return_value.wait.return_value = 9
        with self.assertRaisesRegex(ValueError, 'could not accept'):
            open_codex_conversation(SESSION, codex_support(self.home, platform='win32'), launch=launched)

    def test_new_editor_still_running_is_not_terminated(self):
        launched = Mock()
        launched.return_value.wait.side_effect = subprocess.TimeoutExpired('Code.exe', 1)
        self.assertEqual(open_codex_conversation(SESSION, codex_support(self.home, platform='win32'), launch=launched), 'opened')
        launched.return_value.terminate.assert_not_called()

    def test_capability_paths_are_not_in_public_interaction_metadata(self):
        action = codex_interaction(SESSION, 'vscode', codex_support(self.home, platform='win32'), history_available=True)
        self.assertTrue(action['available'])
        self.assertNotIn(str(self.home), json.dumps(action))

    def test_every_unsupported_codex_state_has_an_actionable_reason(self):
        support = {'available': True, 'reason': 'Open the exact conversation.'}
        cases = [(None, 'vscode', True, support), (SESSION, 'vscode', False, support),
                 (SESSION, 'cli', True, support), (SESSION, None, True, support),
                 (SESSION, 'vscode', True, {'available': False, 'reason': 'Install the supported extension.'})]
        for sid, source, history, capability in cases:
            action = codex_interaction(sid, source, capability, history_available=history)
            self.assertFalse(action['available'])
            self.assertTrue(action['reason'])


class InteractionStoreTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name) / 'workspace'
        self.root.mkdir()
        self.home = Path(self.temp.name) / 'home'
        self.home.mkdir()
        self.run, _ = create_run(self.root, 'interactive-test', 'Open only the selected existing conversation')
        self.coordinator = Coordinator(self.run)
        self.opened = Mock(return_value='opened')
        self.claude_opened = Mock(return_value='opened')
        self.collector = Mock(return_value=[])
        self.support = Mock(return_value={'available': True, 'reason': 'Open the exact conversation.'})
        self.store = ViewerStore(self.root, home=self.home, collector=self.collector,
                                 opener=self.claude_opened, async_listing=False,
                                 codex_opener=self.opened, codex_capability=self.support)
        self.transcript = self.home / '.codex/sessions/2026/09/09' / ('rollout-test-' + SESSION + '.jsonl')
        self.write_transcript()
        self.store.bind(self.run.name, 'codex', SESSION)

    def write_transcript(self, source='vscode', session=SESSION):
        write(self.transcript, {'type': 'session_meta', 'payload': {'id': session, 'source': source}})

    def payload(self):
        state = self.coordinator.read()
        return {'run': self.run.name, 'session': state['session'], 'generation': state['generation'],
                'kind': 'codex_conversation', 'session_id': SESSION, 'background_id': None}

    def test_exact_bound_vscode_conversation_opens_once_without_claude_collection(self):
        provider = self.store.provider(self.coordinator.read())
        self.assertTrue(provider['interaction']['available'])
        self.assertFalse(provider['can_attach'], 'Legacy attach continues to mean a Claude console.')
        self.opened.assert_not_called()
        result = self.store.interact(self.payload())
        self.assertEqual(result['model_calls'], 0)
        self.assertEqual(result['kind'], 'codex_conversation')
        self.opened.assert_called_once_with(SESSION, self.support.return_value)
        self.store.interact(self.payload())
        self.assertEqual(self.opened.call_count, 1)
        self.claude_opened.assert_not_called()
        self.collector.assert_not_called()

    def test_stale_generation_is_refused_before_provider_checks_or_launch(self):
        payload = self.payload()
        state = self.coordinator.read()
        state['generation'] += 1
        self.coordinator.save(state)
        with self.assertRaisesRegex(ValueError, 'coordinator changed'):
            self.store.interact(payload)
        self.support.assert_not_called()
        self.opened.assert_not_called()

    def test_wrong_thread_provider_or_background_cannot_be_substituted(self):
        for key, value in [('session_id', OTHER), ('kind', 'claude_console'),
                           ('background_id', SESSION[:8]), ('session_id', SESSION + '?prompt=unsafe')]:
            payload = self.payload()
            payload[key] = value
            with self.subTest(field=key), self.assertRaises(ValueError):
                self.store.interact(payload)
        self.opened.assert_not_called()
        self.claude_opened.assert_not_called()

    def test_different_run_cannot_reuse_the_selected_binding(self):
        other_run, _ = create_run(self.root, 'different-run', 'Unrelated run must not be opened')
        payload = self.payload()
        payload['run'] = other_run.name
        state = Coordinator(other_run).read()
        payload.update(session=state['session'], generation=state['generation'])
        with self.assertRaises(ValueError):
            self.store.interact(payload)
        self.opened.assert_not_called()

    def test_owner_change_invalidates_binding_even_with_current_generation(self):
        state = self.coordinator.read()
        state.update(owner='fable', session='new-fable-owner', generation=state['generation'] + 1)
        self.coordinator.save(state)
        with self.assertRaises(ValueError):
            self.store.interact(self.payload())
        self.opened.assert_not_called()
        self.claude_opened.assert_not_called()

    def test_removed_history_or_changed_source_does_not_open_new_cli_session(self):
        self.write_transcript(source='cli')
        with self.assertRaisesRegex(ValueError, 'original Codex application'):
            self.store.interact(self.payload())
        self.transcript.unlink()
        with self.assertRaisesRegex(ValueError, 'saved history'):
            self.store.interact(self.payload())
        self.opened.assert_not_called()

    def test_capability_is_rechecked_on_action_after_read_only_cache(self):
        self.assertTrue(self.store.provider(self.coordinator.read())['interaction']['available'])
        self.support.return_value = {'available': False, 'reason': 'The extension changed.'}
        with self.assertRaisesRegex(ValueError, 'extension changed'):
            self.store.interact(self.payload())
        self.assertEqual(self.support.call_count, 2)
        self.opened.assert_not_called()

    def test_metadata_reader_rejects_wrong_identity_or_oversized_metadata(self):
        self.assertEqual(transcript_source(self.transcript, SESSION), 'vscode')
        self.assertIsNone(transcript_source(self.transcript, OTHER))
        self.transcript.write_text('x' * 65537, encoding='utf-8')
        self.assertIsNone(transcript_source(self.transcript, SESSION))

    def test_normal_refresh_recovers_unavailable_capability_without_restarting_viewer(self):
        self.support.return_value = {'available': False, 'reason': 'Extension unavailable.'}
        self.assertFalse(self.store.provider(self.coordinator.read())['interaction']['available'])
        self.support.return_value = {'available': True, 'reason': 'Open the exact conversation.'}
        self.store._codex_support_checked -= 31
        self.assertTrue(self.store.provider(self.coordinator.read())['interaction']['available'])
        self.opened.assert_not_called()

    def test_interaction_request_rejects_extra_fields(self):
        payload = self.payload()
        payload['prompt'] = 'Never submit this text.'
        with self.assertRaises(ValueError):
            self.store.interact(payload)
        self.opened.assert_not_called()


if __name__ == '__main__':
    unittest.main()
