"""Local producer tests; no real Discord, OpenWhispr or provider calls."""
from concurrent.futures import ThreadPoolExecutor
import io
import json
from pathlib import Path
import sys
import tempfile
from threading import Event
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'app'))
from completion_notifications import (BridgeError, Notifications, OpenWhisprBridge,
                                      main, notify_question, report_text)
from coordinator_handoff import Coordinator
from init_run import create_run
from task_store import write_json


class ProducerTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.path = self.root / 'private' / 'notifications.sqlite'
        self.bridge = Mock()
        self.bridge.submit.return_value = {'status': 'accepted', 'reason': 'durably_admitted_not_delivery_proof'}
        self.client = Notifications(self.path, self.bridge)
        self.payload = {'identifier': 'orchestrator:run:final', 'kind': 'result',
                        'project': 'Project', 'text': '# Complete report\n\n**Fixed** all points. 😀'}

    def test_defaults_off_and_enabling_does_not_replay(self):
        self.assertFalse(self.client.status()['enabled'])
        self.assertEqual(self.client.submit(**self.payload)['reason'], 'disabled')
        self.assertEqual(self.client.status()['attempt_count'], 0)
        self.client.configure(True)
        self.bridge.submit.assert_not_called()
        restarted = Notifications(self.path, self.bridge)
        self.assertTrue(restarted.status()['enabled'])
        self.bridge.submit.assert_not_called()

    def test_full_report_admission_and_duplicate_survive_restart(self):
        self.client.configure(True)
        first = self.client.submit(**self.payload)
        self.assertEqual(first['status'], 'accepted')
        body = self.bridge.submit.call_args.args[0]
        self.assertEqual(body['text'], self.payload['text'])
        restarted = Notifications(self.path, self.bridge)
        repeated = restarted.submit(**self.payload)
        self.assertTrue(repeated['duplicate'])
        self.bridge.submit.assert_called_once()
        self.assertNotIn('text', restarted.status()['attempts'][0])
        self.assertNotIn(self.payload['text'].encode(), self.path.read_bytes())

    def test_reused_id_cannot_change_content_or_type_or_project(self):
        self.client.configure(True)
        self.client.submit(**self.payload)
        for changes in ({'text': 'Different'}, {'project': 'Another'}, {'kind': 'question'}):
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                self.client.submit(**dict(self.payload, **changes))
        self.bridge.submit.assert_called_once()

    def test_unknown_timeout_is_held_without_retry_or_exception_text(self):
        self.client.configure(True)
        self.bridge.submit.side_effect = TimeoutError('private token and report')
        result = self.client.submit(**self.payload)
        self.assertEqual(result['reason'], 'outcome_unknown')
        restarted = Notifications(self.path, self.bridge)
        self.assertTrue(restarted.submit(**self.payload)['duplicate'])
        self.bridge.submit.assert_called_once()
        self.assertNotIn('private token', json.dumps(restarted.status()))

    def test_crash_after_durable_admission_never_replays(self):
        self.client.configure(True)
        self.bridge.submit.side_effect = KeyboardInterrupt()
        with self.assertRaises(KeyboardInterrupt):
            self.client.submit(**self.payload)
        restarted = Notifications(self.path, self.bridge)
        result = restarted.submit(**self.payload)
        self.assertEqual(result['status'], 'held')
        self.assertEqual(result['reason'], 'in_flight_or_outcome_unknown')
        self.bridge.submit.assert_called_once()

    def test_concurrent_same_id_sends_once(self):
        entered, release = Event(), Event()
        def post(payload):
            entered.set()
            self.assertTrue(release.wait(5))
            return {'status': 'accepted', 'reason': 'durably_admitted_not_delivery_proof'}
        self.bridge.submit.side_effect = post
        self.client.configure(True)
        with ThreadPoolExecutor(max_workers=2) as pool:
            first = pool.submit(self.client.submit, **self.payload)
            self.assertTrue(entered.wait(5))
            try:
                second = Notifications(self.path, self.bridge).submit(**self.payload)
                self.assertTrue(second['duplicate'])
                self.assertEqual(second['status'], 'held')
            finally:
                release.set()
            self.assertEqual(first.result()['status'], 'accepted')
        self.bridge.submit.assert_called_once()

    def test_disabled_setting_and_rejected_admission_do_not_queue(self):
        self.client.configure(True)
        self.bridge.submit.return_value = {'status': 'held', 'reason': 'inactive'}
        self.assertEqual(self.client.submit(**self.payload)['reason'], 'inactive')
        self.client.configure(False)
        self.client.configure(True)
        self.assertTrue(self.client.submit(**self.payload)['duplicate'])
        self.bridge.submit.assert_called_once()

    def test_storage_failure_prevents_external_effect(self):
        self.client.configure(True)
        with patch.object(self.client, 'connect', side_effect=OSError('disk full')):
            with self.assertRaises(OSError):
                self.client.submit(**self.payload)
        self.bridge.submit.assert_not_called()

    def test_ledger_limit_preserves_history(self):
        self.client.configure(True)
        with patch('completion_notifications.MAX_ATTEMPTS', 1):
            self.client.submit(**self.payload)
            result = self.client.submit(**dict(self.payload, identifier='second-final'))
        self.assertEqual(result['reason'], 'ledger_full')
        self.assertEqual(self.client.status()['attempt_count'], 1)
        self.bridge.submit.assert_called_once()

    def test_report_utf8_bom_and_utf16_size_limit(self):
        path = self.root / 'report.txt'
        path.write_text(self.payload['text'], encoding='utf-8-sig')
        self.assertEqual(report_text(path), path.read_bytes().decode('utf-8-sig'))
        path.write_text('😀' * 50001, encoding='utf-8')
        with self.assertRaises(ValueError):
            report_text(path)
        path.write_text(' \n', encoding='utf-8')
        with self.assertRaises(ValueError):
            report_text(path)

    def test_explicit_question_requires_current_owner_and_unfinished_run(self):
        run, _ = create_run(self.root, 'question', 'Test question notification', project_id='test', lead='astra')
        state = Coordinator(run).read()
        identity = {key: state[key] for key in ('owner', 'session', 'generation')}
        path = self.root / 'question.txt'
        path.write_text('Which drawing should I use?', encoding='utf-8')
        self.client.configure(True)
        kwargs = dict(run=run, **identity, question_id='drawing-1', text_file=path, notifications=self.client)
        with self.assertRaises(ValueError):
            notify_question(**dict(kwargs, generation=identity['generation'] + 1))
        self.bridge.submit.assert_not_called()
        self.assertEqual(notify_question(**kwargs)['status'], 'accepted')
        self.assertEqual(self.bridge.submit.call_args.args[0]['type'], 'question')
        manifest = json.loads((run / 'run.json').read_text())
        write_json(run / 'run.json', dict(manifest, status='completed'))
        with self.assertRaises(ValueError):
            notify_question(**dict(kwargs, question_id='drawing-2'))
        self.bridge.submit.assert_called_once()

    def test_explicit_result_command_reruns_closeout_instead_of_saved_flag(self):
        args = ['notify', 'result', '--run', str(self.root), '--owner', 'astra',
                '--session', 'test', '--generation', '2', '--text-file', 'final.txt']
        with patch('orchestration_lifecycle.closeout_run', return_value={'status': 'held'}) as closeout:
            with patch('sys.stdout', new_callable=io.StringIO) as output:
                self.assertEqual(main(args), 2)
            self.assertEqual(json.loads(output.getvalue())['reason'], 'run_not_verified_complete')
        self.assertEqual(closeout.call_args.kwargs['final_report'], Path('final.txt'))


class BridgeTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.path = Path(temporary.name) / 'discovery.json'
        self.discovery = {'version': 1, 'port': 8210, 'token': 'a' * 64}
        self.path.write_text(json.dumps(self.discovery), encoding='utf-8')
        self.payload = {'id': 'stable', 'type': 'question', 'project': 'Project', 'text': 'Read 😀?'}
        self.response = Mock(status=202)
        self.response.read.return_value = json.dumps({'data': {'accepted': True, 'status': 'pending'}}).encode()
        self.connection = Mock()
        self.connection.getresponse.return_value = self.response
        self.factory = patch('completion_notifications.HTTPConnection', return_value=self.connection).start()
        self.addCleanup(patch.stopall)

    def test_literal_loopback_utf8_auth_and_receipt(self):
        result = OpenWhisprBridge(self.path).submit(self.payload)
        self.factory.assert_called_once_with('127.0.0.1', 8210, timeout=5)
        call = self.connection.request.call_args
        self.assertEqual(call.args, ('POST', '/v1/completion-notifications'))
        self.assertEqual(json.loads(call.kwargs['body']), self.payload)
        self.assertEqual(call.kwargs['headers']['Authorization'], 'Bearer ' + self.discovery['token'])
        self.assertEqual(result['status'], 'accepted')
        self.assertEqual(result['reason'], 'durably_admitted_not_delivery_proof')
        self.connection.close.assert_called_once()

    def test_discovery_cannot_select_remote_host_or_bad_port_or_token(self):
        for changes in ({'host': 'example.org'}, {'url': 'https://example.org'},
                        {'port': 8199}, {'port': 8220}, {'port': True},
                        {'port': '8210'}, {'token': 'secret\r\nInjected: yes'}):
            with self.subTest(changes=changes):
                self.path.write_text(json.dumps(dict(self.discovery, **changes)))
                with self.assertRaises(BridgeError):
                    OpenWhisprBridge(self.path).submit(self.payload)
        self.factory.assert_not_called()

    def test_redirect_is_not_followed_or_returned(self):
        self.response.status = 302
        self.response.getheader.return_value = 'https://remote.invalid/private'
        with self.assertRaisesRegex(BridgeError, '^redirect_refused$'):
            OpenWhisprBridge(self.path).submit(self.payload)
        self.connection.request.assert_called_once()
        self.response.getheader.assert_not_called()

    def test_error_body_is_not_exposed(self):
        self.response.status = 500
        self.response.read.return_value = b'{"error":"private token and report"}'
        with self.assertRaisesRegex(BridgeError, '^bridge_rejected$'):
            OpenWhisprBridge(self.path).submit(self.payload)
        self.response.read.assert_not_called()

    def test_invalid_receipts_and_bounded_read(self):
        for body in ({'data': {'accepted': 'true'}}, {'data': None}, []):
            self.response.read.return_value = json.dumps(body).encode()
            with self.subTest(body=body), self.assertRaises(BridgeError):
                OpenWhisprBridge(self.path).submit(self.payload)
        self.response.read.return_value = b'x' * 65537
        with self.assertRaisesRegex(BridgeError, 'invalid_receipt'):
            OpenWhisprBridge(self.path).submit(self.payload)
        self.response.read.assert_called_with(65537)

    def test_inactive_remote_is_held_with_allowlisted_reason(self):
        for reason, expected in [('inactive', 'inactive'), ('secret contact name', 'not_admitted')]:
            self.response.read.return_value = json.dumps({'data': {'accepted': False, 'reason': reason}}).encode()
            result = OpenWhisprBridge(self.path).submit(self.payload)
            self.assertEqual(result, {'status': 'held', 'reason': expected})


if __name__ == '__main__':
    unittest.main()
