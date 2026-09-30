import json
from http.server import ThreadingHTTPServer
from pathlib import Path
import sys
import tempfile
from threading import Thread
import unittest
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.request import Request, urlopen

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'app'))
from voice_gateway import Dispatcher, Journal, handler


class FakeDispatcher:
    def __init__(self):
        self.started = []

    def start(self, request_id, *, continuation=False):
        self.started.append((request_id, continuation))


class VoiceGatewayTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.journal = Journal(Path(self.temp.name) / 'jobs.sqlite')
        self.dispatcher = FakeDispatcher()
        self.server = ThreadingHTTPServer(('127.0.0.1', 0), handler(self.journal, self.dispatcher, 'secret-token'))
        self.thread = Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.url = f'http://127.0.0.1:{self.server.server_port}'

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join()
        self.temp.cleanup()

    def call(self, route, body=None, token='secret-token'):
        data = json.dumps(body).encode() if body is not None else None
        request = Request(self.url + route, data=data,
                          headers={'Authorization': 'Bearer ' + token,
                                   'Content-Type': 'application/json'})
        with urlopen(request, timeout=3) as response:
            return response.status, json.load(response)

    def test_auth_idempotency_and_events(self):
        body = {'schema_version': 1, 'request_id': 'voice-1234', 'text': 'Hello', 'project_id': 'openwhispr'}
        with self.assertRaises(HTTPError) as denied:
            self.call('/v1/requests', body, token='wrong')
        self.assertEqual(denied.exception.code, 403)
        self.assertEqual(self.call('/v1/requests', body)[0], 202)
        self.assertTrue(self.call('/v1/requests', body)[1]['reused'])
        self.assertEqual(self.dispatcher.started, [('voice-1234', False)])
        with self.assertRaises(HTTPError) as conflict:
            self.call('/v1/requests', {**body, 'text': 'Changed'})
        self.assertEqual(conflict.exception.code, 409)
        self.assertEqual([event['type'] for event in self.call('/v1/requests/voice-1234/events?after=0')[1]['events']],
                         ['accepted'])

    def test_question_answer_and_uncertain_recovery(self):
        body = {'schema_version': 1, 'request_id': 'voice-5678', 'text': 'Which project?', 'project_id': 'openwhispr'}
        self.call('/v1/requests', body)
        self.journal.transition('voice-5678', 'started', 'started', {})
        self.journal.question('voice-5678', 'question-1', 'Which project?')
        status, answer = self.call('/v1/requests/voice-5678/answers', {'question_id': 'question-1', 'text': 'OpenWhispr'})
        self.assertEqual((status, answer['status']), (200, 'answered'))
        self.assertEqual(self.dispatcher.started[-1], ('voice-5678', True))
        self.assertTrue(self.call('/v1/requests/voice-5678/answers',
                                  {'question_id': 'question-1', 'text': 'OpenWhispr'})[1]['reused'])
        Journal(Path(self.temp.name) / 'jobs.sqlite')
        self.assertEqual(self.journal.get('voice-5678')['status'], 'outcome_unknown')

    def test_dispatcher_calls_existing_run_and_emits_typed_question_then_final(self):
        body = {'schema_version': 1, 'request_id': 'voice-9012', 'text': 'Fix it', 'project_id': 'openwhispr'}
        self.journal.accept(body)
        calls = []

        def fake_run(command, **_kwargs):
            calls.append(command)
            output = Path(command[command.index('--output') + 1])
            response = (json.dumps({'kind': 'question', 'question': 'Which file?'})
                        if len(calls) == 1 else 'The requested fix is ready.')
            output.write_text(json.dumps({'status': 'awaiting_review', 'response': response,
                                          'job_id': f'job-{len(calls)}', 'review_status': 'pending'}), encoding='utf-8')
            return type('Completed', (), {'returncode': 0})()

        with patch.dict('os.environ', {'LOCALAPPDATA': self.temp.name}), patch('voice_gateway.subprocess.run', fake_run):
            worker = Dispatcher(self.journal, 'codex')
            worker.run('voice-9012')
            self.assertEqual(self.journal.get('voice-9012')['status'], 'question')
            question_id = self.journal.get('voice-9012')['question_id']
            self.journal.answer('voice-9012', {'question_id': question_id, 'text': 'main.py'})
            worker.run('voice-9012', continuation=True)
        self.assertEqual(self.journal.get('voice-9012')['status'], 'final')
        self.assertIn('orchestrator.py', calls[0][1])
        self.assertEqual(calls[0][2:4], ['run', 'codex'])
        self.assertEqual(calls[1][calls[1].index('--assignment-id') + 1], 'voice-voice-9012-answer')
        self.assertEqual(self.journal.events('voice-9012', 0)[-1]['payload']['text'], 'The requested fix is ready.')


if __name__ == '__main__':
    unittest.main()
