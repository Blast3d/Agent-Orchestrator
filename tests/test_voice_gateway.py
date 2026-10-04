import json
import hashlib
from http.server import ThreadingHTTPServer
from pathlib import Path
import sys
import sqlite3
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
        self.assertNotIn('--skill-plan', calls[0])
        self.assertNotIn('--no-auto-fallback', calls[0])
        self.assertEqual(calls[1][calls[1].index('--assignment-id') + 1], 'voice-voice-9012-answer')
        self.assertEqual(self.journal.events('voice-9012', 0)[-1]['payload']['text'], 'The requested fix is ready.')

    def plan_body(self, **changes):
        return {'schema_version': 1, 'request_id': 'voice-plan-1234',
                'text': 'Investigate a failing test', 'project_id': 'openwhispr',
                'skill_plan_id': 'a' * 32, **changes}

    def plan_delivery(self, **changes):
        return {'plan_id': 'a' * 32, 'project_id': 'openwhispr',
                'task': 'Investigate a failing test', **changes}

    def test_reviewed_skill_plan_is_durable_and_replay_is_not_a_new_delivery(self):
        body = self.plan_body(text='  Investigate\n a failing  test  ')
        with patch('skill_flow.delivery', return_value=self.plan_delivery()) as deliver:
            self.assertEqual(self.call('/v1/requests', body)[0], 202)
        deliver.assert_called_once()
        self.assertEqual(deliver.call_args.args[1:], ('openwhispr', 'a' * 32))
        self.assertEqual(self.journal.get(body['request_id'])['skill_plan_id'], 'a' * 32)
        with patch('skill_flow.delivery', side_effect=ValueError('Skill selection is stale')) as deliver:
            self.assertTrue(self.call('/v1/requests', body)[1]['reused'])
        deliver.assert_not_called()
        self.assertEqual(self.dispatcher.started, [(body['request_id'], False)])
        Journal(Path(self.temp.name) / 'jobs.sqlite')
        self.assertEqual(self.journal.get(body['request_id'])['skill_plan_id'], 'a' * 32)
        self.assertEqual(self.journal.get(body['request_id'])['status'], 'outcome_unknown')
        with self.assertRaises(HTTPError) as changed:
            self.call('/v1/requests', self.plan_body(skill_plan_id='b' * 32))
        self.assertEqual(changed.exception.code, 409)

    def test_invalid_skill_plan_id_rejected_before_validation_or_dispatch(self):
        with patch('skill_flow.delivery') as deliver:
            for value in (None, 8, 'a' * 31, 'A' * 32, '../plan', ''):
                with self.subTest(value=value):
                    with self.assertRaises(HTTPError) as denied:
                        self.call('/v1/requests', self.plan_body(skill_plan_id=value))
                    self.assertEqual(denied.exception.code, 400)
        deliver.assert_not_called()
        self.assertEqual(self.dispatcher.started, [])
        self.assertIsNone(self.journal.get('voice-plan-1234'))

    def test_unreviewed_stale_missing_and_cross_project_plans_are_rejected(self):
        failures = [ValueError('The lead must review and load skills before worker delivery.'),
                    ValueError('Skill selection is stale; refresh the catalog.'),
                    ValueError('That skill plan is unavailable in this project.'),
                    FileNotFoundError('missing local plan')]
        for failure in failures:
            with self.subTest(failure=failure), patch('skill_flow.delivery', side_effect=failure):
                with self.assertRaises(HTTPError) as denied:
                    self.call('/v1/requests', self.plan_body())
                self.assertEqual(denied.exception.code, 400)
        with patch('skill_flow.delivery', return_value=self.plan_delivery(project_id='agent-orchestrator')):
            with self.assertRaises(HTTPError) as denied:
                self.call('/v1/requests', self.plan_body())
            self.assertEqual(denied.exception.code, 400)
        self.assertEqual(self.dispatcher.started, [])

    def test_reviewed_plan_cannot_be_reused_for_a_different_utterance(self):
        with patch('skill_flow.delivery', return_value=self.plan_delivery()):
            with self.assertRaises(HTTPError) as denied:
                self.call('/v1/requests', self.plan_body(text='Run a different task'))
        self.assertEqual(denied.exception.code, 400)
        self.assertIn('match', json.loads(denied.exception.read())['error'].lower())
        self.assertEqual(self.dispatcher.started, [])

    def test_dispatcher_revalidates_and_passes_the_reviewed_plan_flag(self):
        body = self.plan_body()
        commands = []
        run = Path(self.temp.name) / 'voice-skill-run'
        run.mkdir()
        (run / 'run.json').write_text(json.dumps({'run_id': run.name}), encoding='utf-8')

        def fake_run(command, **_kwargs):
            commands.append(command)
            Path(command[command.index('--output') + 1]).write_text(json.dumps({
                'status': 'awaiting_review', 'response': 'Verified the relevant failing test.',
                'job_id': 'job-plan', 'review_status': 'pending'}), encoding='utf-8')
            return type('Completed', (), {'returncode': 0})()

        with patch.dict('os.environ', {'LOCALAPPDATA': self.temp.name}), \
                patch('skill_flow.delivery', return_value=self.plan_delivery()) as deliver, \
                patch('orchestration_lifecycle.start_run', return_value={'run': str(run)}) as startup, \
                patch('voice_gateway.subprocess.run', fake_run):
            self.journal.accept(body)
            Dispatcher(self.journal, 'codex').run(body['request_id'])
        self.assertEqual(deliver.call_count, 2)
        startup.assert_called_once()
        self.assertEqual(startup.call_args.kwargs['project'], 'openwhispr')
        self.assertNotIn('run', startup.call_args.kwargs)
        command = commands[0]
        self.assertEqual(command[command.index('--run') + 1], str(run))
        self.assertEqual(command[command.index('--skill-plan') + 1], 'a' * 32)
        self.assertIn('--no-auto-fallback', command)
        self.assertEqual(command[command.index('--project') + 1], 'openwhispr')
        self.assertEqual(self.journal.get(body['request_id'])['status'], 'final')
        manifest = json.loads((run / 'run.json').read_text(encoding='utf-8'))
        self.assertIs(manifest['native_work'], False)
        self.assertEqual(manifest['skill_plan_id'], 'a' * 32)
        self.assertEqual(manifest['voice_request_id'], body['request_id'])

    def test_skill_run_startup_failure_never_dispatches(self):
        body = self.plan_body()
        with patch('skill_flow.delivery', return_value=self.plan_delivery()):
            self.journal.accept(body)
        with patch.dict('os.environ', {'LOCALAPPDATA': self.temp.name}), \
                patch('skill_flow.delivery', return_value=self.plan_delivery()), \
                patch('orchestration_lifecycle.start_run', side_effect=ValueError('Invalid startup')) as startup, \
                patch('voice_gateway.subprocess.run') as provider:
            Dispatcher(self.journal, 'codex').run(body['request_id'])
        startup.assert_called_once()
        provider.assert_not_called()
        self.assertEqual(self.journal.get(body['request_id'])['status'], 'outcome_unknown')

    def test_stale_or_changed_task_before_dispatch_fails_without_provider(self):
        for failure in (ValueError('Reviewed context changed'), self.plan_delivery(task='Other task')):
            request_id = 'voice-stale-' + str(len(self.dispatcher.started)) + ('-error' if isinstance(failure, Exception) else '-task')
            with patch('skill_flow.delivery', return_value=self.plan_delivery()):
                self.journal.accept(self.plan_body(request_id=request_id))
            kwargs = {'side_effect': failure} if isinstance(failure, Exception) else {'return_value': failure}
            with patch.dict('os.environ', {'LOCALAPPDATA': self.temp.name}), \
                    patch('skill_flow.delivery', **kwargs), patch('voice_gateway.subprocess.run') as provider:
                Dispatcher(self.journal, 'codex').run(request_id)
            provider.assert_not_called()
            self.assertEqual(self.journal.get(request_id)['status'], 'failed')
            self.assertTrue(self.journal.events(request_id, 0)[-1]['payload']['reason'])

    def test_legacy_journal_migrates_without_changing_replay_hash(self):
        path = Path(self.temp.name) / 'legacy.sqlite'
        body = {'schema_version': 1, 'request_id': 'voice-legacy-1234', 'text': 'Legacy request',
                'project_id': 'openwhispr'}
        digest = hashlib.sha256(json.dumps(body, sort_keys=True).encode()).hexdigest()
        db = sqlite3.connect(path)
        try:
            db.execute('''CREATE TABLE requests (
                id TEXT PRIMARY KEY, body_hash TEXT NOT NULL, text TEXT NOT NULL,
                project_id TEXT NOT NULL, conversation_id TEXT, status TEXT NOT NULL,
                question_id TEXT, answer TEXT, created_at TEXT NOT NULL)''')
            db.execute('INSERT INTO requests VALUES (?,?,?,?,?,?,?,?,?)',
                       (body['request_id'], digest, body['text'], 'openwhispr', None, 'final', None, None, 'saved'))
            db.commit()
        finally:
            db.close()
        journal = Journal(path)
        self.assertIsNone(journal.get(body['request_id'])['skill_plan_id'])
        with patch('skill_flow.delivery') as deliver:
            self.assertTrue(journal.accept(body)['reused'])
        deliver.assert_not_called()
        self.assertEqual(journal.get(body['request_id'])['status'], 'final')

    def test_real_local_reviewed_plan_enforces_review_scope_and_stale_file(self):
        import skill_flow
        root = Path(self.temp.name) / 'skill-app'
        skill = root / 'fixtures' / 'debug' / 'SKILL.md'
        skill.parent.mkdir(parents=True)
        skill.write_text('---\nname: debug\ndescription: Diagnose a failing test.\n---\nRead the actual test failure first.\n', encoding='utf-8')
        config = root / 'config'
        config.mkdir()
        (config / 'skill-packs.json').write_text(json.dumps({
            'schema_version': 1, 'roots': {'local': '${orchestrator}/fixtures'},
            'packs': [{'id': 'engineering', 'label': 'Engineering', 'description': 'Diagnose tests.',
                       'skills': [{'id': 'debug', 'root': 'local', 'path': 'debug/SKILL.md'}]}],
            'projects': {'openwhispr': ['engineering'], 'agent-orchestrator': ['engineering']},
        }), encoding='utf-8')
        plan = skill_flow.recommend(root, 'openwhispr', 'Investigate a failing test', jev=False)
        other_project = skill_flow.recommend(root, 'agent-orchestrator', plan['task'], jev=False)
        with patch('voice_gateway.ROOT', root), patch('skill_flow.evaluate') as provider:
            with self.assertRaises(HTTPError) as unreviewed:
                self.call('/v1/requests', self.plan_body(skill_plan_id=plan['id']))
            self.assertEqual(unreviewed.exception.code, 400)
            with self.assertRaises(HTTPError) as wrong_project:
                self.call('/v1/requests', self.plan_body(skill_plan_id=other_project['id']))
            self.assertEqual(wrong_project.exception.code, 400)
            skill_flow.review(root, 'openwhispr', plan['id'], ['debug'], plan['manifest_sha256'],
                              'Lead', 'Checked current instructions and their fit for this exact request.')
            self.assertEqual(self.call('/v1/requests', self.plan_body(skill_plan_id=plan['id']))[0], 202)
            self.journal.transition('voice-plan-1234', 'final', 'final', {'text': 'Synthetic completed request.'})
            skill.write_text(skill.read_text(encoding='utf-8') + 'Changed instructions.\n', encoding='utf-8')
            with self.assertRaises(HTTPError) as stale:
                self.call('/v1/requests', self.plan_body(request_id='voice-file-stale-1234', skill_plan_id=plan['id']))
            self.assertEqual(stale.exception.code, 400)
            self.assertIn('stale', json.loads(stale.exception.read())['error'].lower())
        provider.assert_not_called()
        self.assertEqual(self.dispatcher.started, [('voice-plan-1234', False)])


if __name__ == '__main__':
    unittest.main()
