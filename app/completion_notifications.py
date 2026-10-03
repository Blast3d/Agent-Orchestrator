"""Opt-in final-report/question producer; OpenWhispr owns delivery and Busy mode.

No worker completion hooks or background replay. A durable attempt is recorded
before contacting OpenWhispr; an ambiguous attempt is never automatically retried.
"""
import argparse
from contextlib import contextmanager
import hashlib
from http.client import HTTPConnection
import json
import os
from pathlib import Path
import re
import sqlite3

from coordinator_handoff import Coordinator, read_object
from task_store import timestamp
from usage_guard import file_lock

MAX_TEXT = 100000  # OpenWhispr's limit is UTF-16 code units, not Python characters.
MAX_ATTEMPTS = 1000
QUESTION_ID = re.compile(r'[a-zA-Z0-9_-]{1,64}')


def state_path():
    base = Path(os.environ.get('LOCALAPPDATA') or Path.home() / 'AppData' / 'Local')
    return base / 'Agent-Orchestrator' / 'completion-notifications.sqlite'


def report_text(path):
    with Path(path).open('rb') as stream:
        raw = stream.read(MAX_TEXT * 4 + 4)
    if len(raw) > MAX_TEXT * 4 + 3:
        raise ValueError('Final report exceeds the size limit')
    value = raw.decode('utf-8-sig')
    if not value.strip() or len(value.encode('utf-16-le')) // 2 > MAX_TEXT:
        raise ValueError('Report must contain 1 to 100000 UTF-16 characters')
    return value


class BridgeError(Exception):
    """Fixed public reason; never include tokens, remote errors or report text."""


class OpenWhisprBridge:
    def __init__(self, discovery=None):
        self.discovery = Path(discovery or os.environ.get('OPENWHISPR_CLI_BRIDGE_FILE')
                              or Path.home() / '.openwhispr' / 'cli-bridge.json')

    def submit(self, payload):
        with self.discovery.open('rb') as stream:
            raw = stream.read(8193)
        if len(raw) > 8192:
            raise BridgeError('invalid_discovery')
        value = json.loads(raw.decode('utf-8-sig'))
        if (not isinstance(value, dict) or type(value.get('port')) is not int
                or not 8200 <= value['port'] <= 8219
                or not isinstance(value.get('token'), str)
                or not re.fullmatch(r'[a-f0-9]{64}', value['token'])
                or set(value) - {'version', 'port', 'token'}):
            raise BridgeError('invalid_discovery')
        # Literal loopback only. http.client does not use proxy environment
        # variables or follow redirects; discovery cannot supply a destination.
        connection = HTTPConnection('127.0.0.1', value['port'], timeout=5)
        try:
            body = json.dumps(payload, ensure_ascii=False).encode('utf-8')
            connection.request('POST', '/v1/completion-notifications', body=body,
                               headers={'Authorization': 'Bearer ' + value['token'],
                                        'Content-Type': 'application/json'})
            response = connection.getresponse()
            if 300 <= response.status < 400:
                raise BridgeError('redirect_refused')
            if response.status not in (200, 202):
                raise BridgeError('bridge_rejected')
            raw = response.read(65537)
            if len(raw) > 65536:
                raise BridgeError('invalid_receipt')
            result = json.loads(raw.decode('utf-8'))
            data = result.get('data') if isinstance(result, dict) else None
            if not isinstance(data, dict) or type(data.get('accepted')) is not bool:
                raise BridgeError('invalid_receipt')
            if not data['accepted']:
                reason = data.get('reason')
                return {'status': 'held', 'reason': reason if reason in (
                    'inactive', 'historical_or_helper') else 'not_admitted'}
            return {'status': 'accepted', 'reason': 'durably_admitted_not_delivery_proof'}
        finally:
            connection.close()


class Notifications:
    def __init__(self, path=None, bridge=None):
        self.path = Path(path or state_path())
        self.bridge = bridge or OpenWhisprBridge()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as db:
            db.execute('CREATE TABLE IF NOT EXISTS settings (id INTEGER PRIMARY KEY CHECK(id=1), enabled INTEGER NOT NULL)')
            db.execute('INSERT OR IGNORE INTO settings VALUES (1,0)')
            db.execute('''CREATE TABLE IF NOT EXISTS attempts (
                id TEXT PRIMARY KEY, content_sha256 TEXT NOT NULL, type TEXT NOT NULL,
                status TEXT NOT NULL, reason TEXT NOT NULL, created_at TEXT NOT NULL)''')
        # Windows inherits this user's profile ACL; POSIX gets a private file.
        os.chmod(self.path, 0o600)

    @contextmanager
    def connect(self):
        db = sqlite3.connect(self.path, timeout=5)
        db.row_factory = sqlite3.Row
        try:
            db.execute('PRAGMA synchronous=FULL')
            db.execute('BEGIN IMMEDIATE')
            yield db
            db.commit()
        except Exception:
            db.rollback()
            raise
        finally:
            db.close()

    def configure(self, enabled):
        if type(enabled) is not bool:
            raise ValueError('enabled must be a boolean')
        with self.connect() as db:
            db.execute('UPDATE settings SET enabled=? WHERE id=1', (int(enabled),))
        return self.status()

    @staticmethod
    def public(row):
        value = {key: row[key] for key in ('id', 'type', 'status', 'reason', 'created_at')}
        if value['status'] == 'attempting':
            value.update(status='held', reason='in_flight_or_outcome_unknown')
        return value

    def status(self):
        with self.connect() as db:
            enabled = bool(db.execute('SELECT enabled FROM settings WHERE id=1').fetchone()[0])
            rows = db.execute('SELECT * FROM attempts ORDER BY rowid DESC LIMIT 30').fetchall()
            total = db.execute('SELECT COUNT(*) FROM attempts').fetchone()[0]
        return {'enabled': enabled, 'attempt_count': total, 'attempts': [self.public(row) for row in rows]}

    def submit(self, *, identifier, kind, project, text):
        if (kind not in ('result', 'question') or not isinstance(identifier, str)
                or not identifier or len(identifier) > 512
                or not isinstance(project, str) or len(project) > 160
                or '\n' in project or '\r' in project or not isinstance(text, str)
                or not text.strip() or len(text.encode('utf-16-le')) // 2 > MAX_TEXT):
            raise ValueError('Invalid notification content')
        payload = {'id': identifier, 'type': kind, 'project': project, 'text': text}
        digest = hashlib.sha256(json.dumps(payload, sort_keys=True, ensure_ascii=False).encode('utf-8')).hexdigest()
        with self.connect() as db:
            row = db.execute('SELECT * FROM attempts WHERE id=?', (identifier,)).fetchone()
            if row:
                if row['content_sha256'] != digest:
                    raise ValueError('Notification ID already belongs to different content')
                return dict(self.public(row), duplicate=True)
            if not db.execute('SELECT enabled FROM settings WHERE id=1').fetchone()[0]:
                return {'status': 'skipped', 'reason': 'disabled'}
            if db.execute('SELECT COUNT(*) FROM attempts').fetchone()[0] >= MAX_ATTEMPTS:
                return {'status': 'held', 'reason': 'ledger_full'}
            db.execute('INSERT INTO attempts VALUES (?,?,?,?,?,?)',
                       (identifier, digest, kind, 'attempting', 'outcome_unknown', timestamp()))
        # The transaction above commits before any external effect. Another
        # process or a restarted client sees this identity and does not resend.
        try:
            outcome = self.bridge.submit(payload)
            if outcome.get('status') not in ('accepted', 'held'):
                raise BridgeError('invalid_receipt')
        except Exception as exc:
            outcome = {'status': 'held', 'reason': str(exc) if isinstance(exc, BridgeError)
                       else 'outcome_unknown'}
        with self.connect() as db:
            db.execute('UPDATE attempts SET status=?,reason=? WHERE id=?',
                       (outcome['status'], outcome['reason'], identifier))
        return dict(outcome, id=identifier)


def notify_verified_closeout(run, manifest, result, report, *, notifications=None):
    """Called by closeout only after current evidence passes, under its locks."""
    if (result.get('status') != 'completed' or manifest.get('status') != 'completed'
            or result.get('run_id') != Path(run).name
            or manifest.get('run_id') != result['run_id']
            or manifest.get('completed_utc') != result.get('checked_at')
            or not result.get('checks')
            or any(row.get('status') != 'passed' for row in result['checks'])):
        return {'status': 'held', 'reason': 'run_not_verified_complete'}
    client = notifications or Notifications()
    if not client.status()['enabled']:
        return {'status': 'skipped', 'reason': 'disabled'}
    project = str(manifest.get('display_name') or result['project_id']).replace('\r', ' ').replace('\n', ' ')[:160]
    return client.submit(identifier='orchestrator:' + result['run_id'] + ':final',
                         kind='result', project=project, text=report_text(report))


def notify_question(run, *, owner, session, generation, question_id, text_file, notifications=None):
    if not isinstance(question_id, str) or not QUESTION_ID.fullmatch(question_id):
        raise ValueError('Question ID must contain 1 to 64 letters, digits, dashes or underscores')
    text = report_text(text_file)
    coordinator = Coordinator(run)
    with file_lock(coordinator.lock):
        coordinator.require_owner(coordinator.read(), owner, session, generation)
        manifest = read_object(coordinator.run / 'run.json')
        if (manifest.get('run_id') != coordinator.run.name
                or manifest.get('status') not in ('planning', 'in_progress')):
            raise ValueError('Questions require the exact current unfinished run')
        project = str(manifest.get('display_name') or manifest.get('project_id') or coordinator.run.name)
        project = project.replace('\r', ' ').replace('\n', ' ')[:160]
        return (notifications or Notifications()).submit(
            identifier='orchestrator:' + coordinator.run.name + ':question:' + question_id,
            kind='question', project=project, text=text)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest='command', required=True)
    for name in ('status', 'enable', 'disable'):
        commands.add_parser(name)
    notify = commands.add_parser('notify')
    notify.add_argument('type', choices=('result', 'question'))
    notify.add_argument('--run', type=Path, required=True)
    notify.add_argument('--owner', required=True)
    notify.add_argument('--session', required=True)
    notify.add_argument('--generation', type=int, required=True)
    notify.add_argument('--text-file', type=Path, required=True)
    notify.add_argument('--question-id')
    args = parser.parse_args(argv)
    try:
        if args.command in ('status', 'enable', 'disable'):
            client = Notifications()
            result = client.status() if args.command == 'status' else client.configure(args.command == 'enable')
        elif args.type == 'result':
            if args.question_id:
                raise ValueError('Question ID is only valid for questions')
            # Never trust a saved completed flag alone. Re-run the maintained
            # evidence checks with the current lead identity before notification.
            from orchestration_lifecycle import closeout_run
            closeout = closeout_run(args.run, owner=args.owner, session=args.session,
                                    generation=args.generation, final_report=args.text_file)
            result = closeout.get('completion_notification', {
                'status': 'held', 'reason': 'run_not_verified_complete'})
        else:
            result = notify_question(args.run, owner=args.owner, session=args.session,
                                     generation=args.generation, question_id=args.question_id,
                                     text_file=args.text_file)
        print(json.dumps(result, ensure_ascii=True))
        return 2 if result.get('status') in ('held', 'skipped') else 0
    except Exception as exc:
        # Exception strings can contain file contents, URLs or auth details.
        print(json.dumps({'status': 'held', 'reason': type(exc).__name__}))
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
