"""Synthetic checks; never contact an actual model or read user chat history."""
import argparse
import io
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'app'))
import vscode_bots
import dispatch_worker as dispatcher
from task_store import TaskStore


class DiscoveryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.path = self.root / 'endpoint.json'
        self.data = {'version': 1, 'port': 12345, 'token': 'a' * 64,
            'instance': '11111111-1111-1111-1111-111111111111'}
        self.path.write_text(json.dumps(self.data), encoding='utf-8')

    def test_endpoint_rejects_malformed_address_and_tokens(self):
        for key, value in [('port', True), ('port', 70000), ('token', 'bad'), ('instance', '../wrong')]:
            self.path.write_text(json.dumps(dict(self.data, **{key: value})))
            with self.assertRaises(ValueError): vscode_bots.endpoint(self.path)

    def test_discovery_returns_no_token_and_ignores_stale_receipts(self):
        payload = {'service': 'vscode-orchestrator-bots', 'version': 1, 'enabled': True,
            'model': {'id': 'model-test', 'vendor': 'copilot'}, 'busy': False}
        with patch.object(vscode_bots, 'request', return_value=io.BytesIO(json.dumps(payload).encode())):
            selected = vscode_bots.select_bridge(self.root)
        self.assertNotIn('token', selected)
        self.assertEqual(selected['model']['id'], 'model-test')
        with patch.object(vscode_bots, 'request', side_effect=OSError):
            self.assertEqual(vscode_bots.discover(self.root), [])

    def test_ambiguous_windows_and_busy_worker_are_not_chosen_arbitrarily(self):
        for rows in [[{'enabled': True, 'busy': False}] * 2, [{'enabled': True, 'busy': True}], []]:
            with patch.object(vscode_bots, 'discover', return_value=rows):
                with self.assertRaises(ValueError): vscode_bots.select_bridge()

    def test_redirects_are_never_followed(self):
        with self.assertRaises(ValueError):
            vscode_bots.NoRedirect().redirect_request(None, None, 302, '', {}, 'https://example.com')


class DispatchTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.prompt = self.root / 'brief.txt'; self.prompt.write_text(
            '# Goal\nValidate task dispatch.\n# Inputs\nSynthetic text.\n# Output\nA short answer.\n# Checks\nReturn supplied text only.\n')
        self.args = argparse.Namespace(worker='vscode-copilot', prompt_file=self.prompt,
            output=self.root / 'answer.json', task='Synthetic test', size='tiny', require_brief_check=True)
        self.guard = Mock(); self.guard.policy = {'quota_admission_mode': 'advisory'}
        self.guard.check.return_value = {'allowed': True, 'reservation_id': 'test'}
        self.store = TaskStore(self.root / 'tasks')
        self.completed = subprocess.CompletedProcess([], 0, json.dumps({'result': 'Reviewed separately',
            'model': 'copilot-model-test', 'usage': None}), '')
        self.completed.progress = {'terminal_received': True}
        self.invoke = Mock(return_value=self.completed)
        self.bridge = {'endpoint': str(self.root / 'endpoint.json'), 'model': {'id': 'copilot-model-test'}}
        for item in (patch.object(dispatcher, 'ensure_directories'),
                     patch.object(vscode_bots, 'select_bridge', return_value=self.bridge),
                     patch.object(dispatcher, 'invoke_cloud', self.invoke)):
            item.start(); self.addCleanup(item.stop)

    def run_task(self):
        return dispatcher.dispatch(self.args, guard_factory=lambda: self.guard,
            store=self.store, workspaces=self.root / 'workspaces')

    def test_task_uses_existing_guard_deadline_and_review_state(self):
        result = self.run_task()
        self.assertEqual(result['status'], 'awaiting_review')
        self.assertEqual(result['requested_model'], 'copilot-model-test')
        self.assertEqual(self.invoke.call_args.kwargs['timeout_seconds'], 120)
        self.assertIn('--output-format', self.invoke.call_args.args[0])
        self.assertEqual(result['usage'], None)
        self.guard.finish.assert_called_once()
        self.guard.request_refresh.assert_not_called()
        self.guard.refresh.assert_not_called()

    def test_disconnected_stream_retains_reservation(self):
        self.completed.returncode = 1
        self.completed.progress = {'terminal_received': False}
        result = self.run_task()
        self.assertEqual(result['execution_status'], 'uncertain')
        self.assertEqual(result['reservation_state'], 'held_for_reconciliation')
        self.guard.finish.assert_not_called()

    def test_missing_bridge_is_held_before_quota_or_inference(self):
        with patch.object(vscode_bots, 'select_bridge', side_effect=ValueError('Enable VS Code Bots.')):
            result = self.run_task()
        self.assertEqual(result['status'], 'held')
        self.invoke.assert_not_called()
        self.guard.check.assert_not_called()

    def test_quota_hold_prevents_a_model_request(self):
        self.guard.check.return_value = {'allowed': False}
        self.assertEqual(self.run_task()['status'], 'held')
        self.invoke.assert_not_called()


if __name__ == '__main__':
    unittest.main()
