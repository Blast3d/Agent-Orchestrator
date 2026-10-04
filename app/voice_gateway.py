"""Opt-in, loopback-only voice job gateway for OpenWhispr.

This owns dispatch and durable request state. It does not own microphone, TTS,
window focus, or the OpenWhispr response inbox.
"""
import argparse
from contextlib import contextmanager
import hashlib
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import re
import secrets
import sqlite3
import subprocess
import sys
from threading import Lock, Thread
from datetime import datetime, timezone

from paths import ROOT

SCHEMA = 1
MAX_BODY = 32 * 1024
MAX_ACTIVE = 2
ID = re.compile(r'^[a-zA-Z0-9_-]{8,128}$')
SKILL_PLAN_ID = re.compile(r'^[a-f0-9]{32}$')
WORKERS = {'codex', 'claude', 'grok', 'gemini', 'local-chat', 'vscode-copilot'}


def now():
    return datetime.now(timezone.utc).isoformat()


def home():
    return Path(os.environ.get('LOCALAPPDATA') or Path.home() / 'AppData' / 'Local') / 'Agent-Orchestrator'


def descriptor_path():
    return home() / 'voice-gateway.json'


def write_private(path, content):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + '.' + secrets.token_hex(8) + '.tmp')
    try:
        with open(temporary, 'x', encoding='utf-8') as output:
            output.write(content)
        os.chmod(temporary, 0o600)
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def reviewed_skill_plan(text, project_id, plan_id):
    """Check the existing reviewed plan without inference or permission changes."""
    if project_id != 'openwhispr' or not isinstance(plan_id, str) or not SKILL_PLAN_ID.fullmatch(plan_id):
        raise ValueError('Invalid reviewed skill plan ID.')
    from skill_flow import delivery
    try:
        context = delivery(ROOT, project_id, plan_id)
    except OSError as exc:
        raise ValueError('Reviewed skill plan is unavailable; create and review a current plan.') from exc
    if context.get('project_id') != project_id or context.get('plan_id') != plan_id:
        raise ValueError('Reviewed skill plan does not belong to this OpenWhispr request.')
    task = context.get('task')
    if not isinstance(task, str) or ' '.join(text.split()) != ' '.join(task.split()):
        raise ValueError('Voice request must match the reviewed skill plan task; use the exact prepared request.')
    return context


class Journal:
    def __init__(self, path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.lock = Lock()
        with self.connect() as db:
            db.execute('''CREATE TABLE IF NOT EXISTS requests (
                id TEXT PRIMARY KEY, body_hash TEXT NOT NULL, text TEXT NOT NULL,
                project_id TEXT NOT NULL, conversation_id TEXT, status TEXT NOT NULL,
                question_id TEXT, answer TEXT, created_at TEXT NOT NULL, skill_plan_id TEXT)''')
            columns = {row[1] for row in db.execute('PRAGMA table_info(requests)')}
            if 'skill_plan_id' not in columns:
                db.execute('ALTER TABLE requests ADD COLUMN skill_plan_id TEXT')
            db.execute('''CREATE TABLE IF NOT EXISTS events (
                request_id TEXT NOT NULL, seq INTEGER NOT NULL, type TEXT NOT NULL,
                payload TEXT NOT NULL, at TEXT NOT NULL,
                PRIMARY KEY(request_id, seq))''')
            # Never replay an uncertain provider operation after a service crash.
            for (request_id,) in db.execute("SELECT id FROM requests WHERE status IN ('accepted','started','answered')"):
                self._event(db, request_id, 'outcome_unknown', {'reason': 'Gateway restarted before the result was recorded.'})
                db.execute('UPDATE requests SET status=? WHERE id=?', ('outcome_unknown', request_id))

    @contextmanager
    def connect(self):
        db = sqlite3.connect(self.path, timeout=10)
        db.row_factory = sqlite3.Row
        try:
            yield db
            db.commit()
        except Exception:
            db.rollback()
            raise
        finally:
            db.close()

    def _event(self, db, request_id, kind, payload):
        seq = db.execute('SELECT COALESCE(MAX(seq),0)+1 FROM events WHERE request_id=?', (request_id,)).fetchone()[0]
        db.execute('INSERT INTO events VALUES (?,?,?,?,?)',
                   (request_id, seq, kind, json.dumps(payload), now()))
        return seq

    def accept(self, body):
        request_id = body.get('request_id')
        value = body.get('text')
        project = body.get('project_id') or 'openwhispr'
        if (body.get('schema_version') != SCHEMA or not isinstance(request_id, str) or
                not ID.fullmatch(request_id) or not isinstance(value, str) or
                not value.strip() or len(value) > 12000 or project != 'openwhispr'):
            raise ValueError('Invalid voice request.')
        conversation = body.get('conversation_id')
        if conversation is not None and (not isinstance(conversation, str) or len(conversation) > 128):
            raise ValueError('Invalid conversation ID.')
        plan_id = body.get('skill_plan_id')
        if 'skill_plan_id' in body and (not isinstance(plan_id, str) or not SKILL_PLAN_ID.fullmatch(plan_id)):
            raise ValueError('Invalid reviewed skill plan ID.')
        digest = hashlib.sha256(json.dumps(body, sort_keys=True).encode()).hexdigest()
        with self.lock, self.connect() as db:
            existing = db.execute('SELECT body_hash,status FROM requests WHERE id=?', (request_id,)).fetchone()
            if existing:
                if existing['body_hash'] != digest:
                    raise Conflict('Request ID was reused with different content.')
                return {'request_id': request_id, 'status': existing['status'], 'reused': True}
            active = db.execute("SELECT COUNT(*) FROM requests WHERE status IN ('accepted','started','question','answered')").fetchone()[0]
            if active >= MAX_ACTIVE:
                raise Busy('Orchestrator already has two active voice requests.')
            if plan_id is not None:
                reviewed_skill_plan(value, project, plan_id)
            db.execute('''INSERT INTO requests
                (id,body_hash,text,project_id,conversation_id,status,question_id,answer,created_at,skill_plan_id)
                VALUES (?,?,?,?,?,?,?,?,?,?)''',
                       (request_id, digest, value.strip(), project, conversation, 'accepted', None, None, now(), plan_id))
            self._event(db, request_id, 'accepted', {})
        return {'request_id': request_id, 'status': 'accepted', 'reused': False}

    def get(self, request_id):
        with self.connect() as db:
            row = db.execute('SELECT id,status,project_id,conversation_id,question_id,skill_plan_id FROM requests WHERE id=?',
                             (request_id,)).fetchone()
            return dict(row) if row else None

    def events(self, request_id, after):
        with self.connect() as db:
            rows = db.execute('SELECT seq,type,payload,at FROM events WHERE request_id=? AND seq>? ORDER BY seq LIMIT 100',
                              (request_id, after)).fetchall()
            return [dict(schema_version=SCHEMA, request_id=request_id, seq=row['seq'],
                         type=row['type'], at=row['at'], payload=json.loads(row['payload'])) for row in rows]

    def transition(self, request_id, status, kind, payload):
        with self.lock, self.connect() as db:
            row = db.execute('SELECT status FROM requests WHERE id=?', (request_id,)).fetchone()
            if not row or row['status'] in ('final', 'failed', 'outcome_unknown', 'cancelled'):
                return False
            db.execute('UPDATE requests SET status=? WHERE id=?', (status, request_id))
            self._event(db, request_id, kind, payload)
            return True

    def question(self, request_id, question_id, text):
        if not ID.fullmatch(question_id) or not isinstance(text, str) or not text.strip():
            raise ValueError('Invalid question.')
        with self.lock, self.connect() as db:
            row = db.execute('SELECT status FROM requests WHERE id=?', (request_id,)).fetchone()
            if not row or row['status'] != 'started':
                raise Conflict('Request is not waiting for a question.')
            db.execute('UPDATE requests SET status=?,question_id=? WHERE id=?', ('question', question_id, request_id))
            self._event(db, request_id, 'question', {'question_id': question_id, 'text': text.strip()[:4000]})

    def answer(self, request_id, body):
        answer = body.get('text')
        question_id = body.get('question_id')
        if not isinstance(answer, str) or not answer.strip() or len(answer) > 12000:
            raise ValueError('Invalid answer.')
        with self.lock, self.connect() as db:
            row = db.execute('SELECT status,question_id,answer FROM requests WHERE id=?', (request_id,)).fetchone()
            if not row:
                raise KeyError(request_id)
            if row['status'] == 'answered' and row['question_id'] == question_id and row['answer'] == answer.strip():
                return {'status': 'answered', 'reused': True}
            if row['status'] != 'question' or row['question_id'] != question_id:
                raise Conflict('Question is no longer current.')
            db.execute('UPDATE requests SET status=?,answer=? WHERE id=?', ('answered', answer.strip(), request_id))
            self._event(db, request_id, 'answered', {'question_id': question_id})
        return {'status': 'answered', 'reused': False}


class Conflict(Exception):
    pass


class Busy(Exception):
    pass


class Dispatcher:
    def __init__(self, journal, worker):
        self.journal = journal
        self.worker = worker

    def start(self, request_id, *, continuation=False):
        Thread(target=self.run, args=(request_id, continuation), daemon=True).start()

    def run(self, request_id, continuation=False):
        if not self.journal.transition(request_id, 'started', 'started', {}):
            return
        with self.journal.connect() as db:
            row = db.execute('SELECT text,project_id,answer,question_id,skill_plan_id FROM requests WHERE id=?', (request_id,)).fetchone()
        if row['skill_plan_id'] is not None:
            try:
                reviewed_skill_plan(row['text'], row['project_id'], row['skill_plan_id'])
            except (ValueError, OSError) as exc:
                self.journal.transition(request_id, 'failed', 'failed', {'reason': str(exc)})
                return
        directory = home() / 'voice-jobs' / request_id
        directory.mkdir(parents=True, exist_ok=True)
        suffix = '-answer' if continuation else ''
        brief = directory / f'brief{suffix}.txt'
        output = directory / f'result{suffix}.json'
        prompt = ('Answer the user request below. If one missing fact makes an answer impossible, '
                  'return only a JSON object with keys "kind":"question" and "question":"your concise question". '
                  'Otherwise return the answer as ordinary text. Do not claim to have used tools you did not use.\n\n'
                  'User request:\n' + row['text'])
        if continuation:
            with self.journal.connect() as db:
                question = db.execute("SELECT payload FROM events WHERE request_id=? AND type='question' ORDER BY seq DESC LIMIT 1",
                                      (request_id,)).fetchone()
            if not question or not row['answer']:
                self.journal.transition(request_id, 'failed', 'failed', {'reason': 'Missing question or answer.'})
                return
            prompt += '\n\nClarification asked: ' + json.loads(question['payload'])['text'] + '\nUser answer: ' + row['answer']
        brief.write_text(prompt, encoding='utf-8')
        command = [sys.executable, str(ROOT / 'orchestrator.py'), 'run', self.worker,
                   '--prompt-file', str(brief), '--output', str(output), '--task', 'Voice request',
                   '--size', 'small', '--project', row['project_id'], '--assignment-id', 'voice-' + request_id + suffix]
        try:
            if row['skill_plan_id'] is not None:
                from orchestration_lifecycle import start_run
                from task_store import write_json
                packet = start_run(workspace=ROOT, name='voice-skill', objective=row['text'],
                                   query=row['text'][:500], project=row['project_id'],
                                   no_memory=True, root=ROOT)
                run = Path(packet['run'])
                manifest = json.loads((run / 'run.json').read_text(encoding='utf-8'))
                manifest.update(native_work=False, skill_plan_id=row['skill_plan_id'],
                                voice_request_id=request_id)
                write_json(run / 'run.json', manifest)
                command.extend(['--run', str(run), '--skill-plan', row['skill_plan_id'], '--no-auto-fallback'])
            completed = subprocess.run(command, cwd=ROOT, capture_output=True, text=True, timeout=1900)
            if not output.is_file():
                self.journal.transition(request_id, 'outcome_unknown', 'outcome_unknown',
                                        {'reason': 'Dispatcher did not produce a canonical export.'})
                return
            result = json.loads(output.read_text(encoding='utf-8'))
            status = result.get('status')
            if completed.returncode == 0 and status in ('awaiting_review', 'accepted') and result.get('response'):
                response = result['response'].strip()
                try:
                    typed = json.loads(response)
                except (ValueError, TypeError):
                    typed = None
                if (not continuation and isinstance(typed, dict) and typed.get('kind') == 'question'
                        and isinstance(typed.get('question'), str) and typed['question'].strip()):
                    self.journal.question(request_id, 'question-' + secrets.token_hex(12), typed['question'])
                else:
                    text = typed.get('text') if isinstance(typed, dict) and typed.get('kind') == 'final' else response
                    if not isinstance(text, str) or not text.strip():
                        raise ValueError('Worker returned an empty final answer.')
                    self.journal.transition(request_id, 'final', 'final',
                                            {'text': text, 'job_id': result.get('job_id'),
                                             'review_status': result.get('review_status')})
            elif status in ('recovery_required',) or result.get('execution_status') == 'uncertain':
                self.journal.transition(request_id, 'outcome_unknown', 'outcome_unknown',
                                        {'job_id': result.get('job_id'), 'reason': result.get('error') or status})
            else:
                self.journal.transition(request_id, 'failed', 'failed',
                                        {'job_id': result.get('job_id'), 'reason': result.get('reason') or status or 'Dispatch failed.'})
        except (OSError, ValueError, subprocess.TimeoutExpired) as exc:
            self.journal.transition(request_id, 'outcome_unknown', 'outcome_unknown',
                                    {'reason': type(exc).__name__})


def handler(journal, dispatcher, token):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def reply(self, status, value):
            data = json.dumps(value).encode()
            self.send_response(status)
            self.send_header('Content-Type', 'application/json')
            self.send_header('Content-Length', str(len(data)))
            self.send_header('Cache-Control', 'no-store')
            self.end_headers()
            self.wfile.write(data)

        def authorized(self):
            if self.headers.get('Authorization') != 'Bearer ' + token or self.headers.get('Origin'):
                self.reply(HTTPStatus.FORBIDDEN, {'error': 'Unauthorized'})
                return False
            return True

        def body(self):
            length = int(self.headers.get('Content-Length', '0'))
            if not 0 < length <= MAX_BODY:
                raise ValueError('Request body is missing or too large.')
            value = json.loads(self.rfile.read(length))
            if not isinstance(value, dict):
                raise ValueError('Expected JSON object.')
            return value

        def do_GET(self):
            if not self.authorized():
                return
            if self.path == '/v1/health':
                return self.reply(200, {'status': 'ready', 'schema_version': SCHEMA})
            match = re.fullmatch(r'/v1/requests/([a-zA-Z0-9_-]{8,128})(?:/events\?after=(\d+))?', self.path)
            if not match:
                return self.reply(404, {'error': 'Unknown route.'})
            request_id, after = match.groups()
            item = journal.get(request_id)
            if not item:
                return self.reply(404, {'error': 'Unknown request.'})
            return self.reply(200, {'events': journal.events(request_id, int(after))} if after is not None else item)

        def do_POST(self):
            if not self.authorized():
                return
            try:
                body = self.body()
                if self.path == '/v1/requests':
                    accepted = journal.accept(body)
                    self.reply(202, accepted)
                    if not accepted['reused']:
                        dispatcher.start(accepted['request_id'])
                    return
                match = re.fullmatch(r'/v1/requests/([a-zA-Z0-9_-]{8,128})/answers', self.path)
                if match:
                    outcome = journal.answer(match[1], body)
                    self.reply(200, outcome)
                    if not outcome['reused']:
                        dispatcher.start(match[1], continuation=True)
                    return
                self.reply(404, {'error': 'Unknown route.'})
            except Conflict as exc:
                self.reply(409, {'error': str(exc)})
            except Busy as exc:
                self.reply(429, {'error': str(exc)})
            except KeyError:
                self.reply(404, {'error': 'Unknown request.'})
            except json.JSONDecodeError:
                self.reply(400, {'error': 'Invalid request JSON.'})
            except ValueError as exc:
                self.reply(400, {'error': str(exc)})

    return Handler


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=['serve'])
    parser.add_argument('--port', type=int, default=8231)
    parser.add_argument('--worker', choices=sorted(WORKERS), default='codex',
                        help='Initial dispatcher worker; orchestration policy owns this choice.')
    args = parser.parse_args(argv)
    if not 1024 <= args.port <= 65535:
        parser.error('Choose an unprivileged port.')
    journal = Journal(home() / 'voice-jobs.sqlite')
    token = secrets.token_urlsafe(32)
    server = ThreadingHTTPServer(('127.0.0.1', args.port), handler(journal, Dispatcher(journal, args.worker), token))
    write_private(descriptor_path(), json.dumps({'schema_version': SCHEMA,
                  'url': f'http://127.0.0.1:{args.port}', 'token': token}))
    try:
        print(json.dumps({'status': 'ready', 'url': f'http://127.0.0.1:{args.port}',
                          'worker': args.worker}), flush=True)
        server.serve_forever()
    finally:
        server.server_close()
        try:
            if json.loads(descriptor_path().read_text(encoding='utf-8')).get('token') == token:
                descriptor_path().unlink(missing_ok=True)
        except (OSError, ValueError):
            pass


if __name__ == '__main__':
    raise SystemExit(main())
