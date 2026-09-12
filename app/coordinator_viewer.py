"""Local, read-only coordinator history with deliberate console interaction."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import hmac
import json
import os
from pathlib import Path
import re
import subprocess
import threading
import time
from urllib.parse import parse_qs, urlsplit

from brain_dashboard import DashboardHandler, DashboardServer
from claude_models import require_model_allowed
from coordinator_handoff import Coordinator
from coordinator_transfer import CLAUDE, HIDDEN, open_viewer
from coordinator_interaction import (codex_interaction, codex_support,
                                     open_codex_conversation, transcript_source)
from paths import ROOT
from usage_guard import file_lock, write_json
from start_usage_monitor import monitor_status, set_monitor_enabled

SERVICE = 'orchestrator-session-viewer'
UUID = re.compile(r'[a-fA-F0-9]{8}(?:-[a-fA-F0-9]{4}){3}-[a-fA-F0-9]{12}')
SHORT_ID = re.compile(r'[a-f0-9]{8}')
MAX_HISTORY_BYTES = 512 * 1024


def now():
    return datetime.now(timezone.utc).isoformat()


def contained(base, path):
    base, path = Path(base).resolve(), Path(path).resolve()
    if not path.is_relative_to(base):
        raise ValueError('Selected file is outside the permitted folder.')
    return path


def object_file(path, maximum=1024 * 1024):
    with Path(path).open('rb') as stream:
        data = stream.read(maximum + 1)
    if len(data) > maximum:
        raise ValueError('Saved metadata exceeds the viewer limit.')
    value = json.loads(data)
    if not isinstance(value, dict):
        raise ValueError('Saved metadata is not an object.')
    return value


def visible_message(row, provider, offset):
    if not isinstance(row, dict):
        return None
    if provider == 'codex':
        if row.get('type') != 'response_item':
            return None
        message = row.get('payload', {})
        if not isinstance(message, dict) or message.get('type') != 'message':
            return None
        if message.get('channel') not in (None, 'final', 'commentary'):
            return None
        role = message.get('role')
    else:
        role = row.get('type')
        message = row.get('message', {})
    if role not in ('user', 'assistant') or not isinstance(message, dict):
        return None
    content = message.get('content', [])
    if isinstance(content, str):
        content = [{'type': 'text', 'text': content}]
    if not isinstance(content, list):
        return None
    text = []
    for block in content:
        if not isinstance(block, dict):
            continue
        if block.get('type') in ('text', 'input_text', 'output_text') and isinstance(block.get('text'), str):
            text.append(block['text'])
        elif block.get('type') in ('image', 'input_image'):
            text.append('[Image attachment — open the original session to view it.]')
    if not text:
        return None
    return {'id': str(offset), 'role': role, 'text': '\n\n'.join(text),
            'timestamp': row.get('timestamp'), 'kind': 'message'}


def history_page(path, provider, before=None, limit=40):
    """Read one bounded window backwards; cursors are byte boundaries, not line counts.

    Tool payloads and private reasoning are never returned. A partial final record
    is retried when the latest page refreshes. No index or transcript copy is made.
    """
    notices = []
    with Path(path).open('rb') as stream:
        stat = os.fstat(stream.fileno())
        # Identity stays stable while a transcript grows, including tiny fixtures.
        identity = hashlib.sha256((str(Path(path).resolve()) + ':' + str(stat.st_ino)).encode()
                                  + stream.readline(4096)).hexdigest()[:20]
        end = stat.st_size
        if before is not None:
            match = re.fullmatch(r'([0-9]{1,20}):([a-f0-9]{20})', str(before))
            if not match or match[2] != identity or int(match[1]) > end:
                raise ValueError('History changed. Refresh the latest conversation before loading older messages.')
            end = int(match[1])
        start = max(0, end - MAX_HISTORY_BYTES)
        stream.seek(start)
        data = stream.read(end - start)
        if start:
            # Do not mistake a suffix of a JSON record for a complete record.
            stream.seek(start - 1)
            if stream.read(1) != b'\n':
                boundary = data.find(b'\n')
                if boundary < 0:
                    notices.append('An oversized record crosses this page. Continue to older history; open the original session for that record.')
                    return {'available': True, 'messages': [], 'before': f'{start}:{identity}', 'has_more': True,
                            'notice': ' '.join(notices), 'revision': f'{identity}:{stat.st_size}:{stat.st_mtime_ns}'}
                if boundary + 1 == len(data):
                    notices.append('An oversized record crosses this page. Continue to older history; open the original session for that record.')
                    return {'available': True, 'messages': [], 'before': f'{start}:{identity}', 'has_more': True,
                            'notice': ' '.join(notices), 'revision': f'{identity}:{stat.st_size}:{stat.st_mtime_ns}'}
                start += boundary + 1
                data = data[boundary + 1:]
        lines = data.splitlines(keepends=True)
        position = end
        messages = []
        cursor = start
        for line in reversed(lines):
            position -= len(line)
            if not line.endswith(b'\n'):
                notices.append('The newest record is still being saved; it will appear after refresh.')
                continue
            try:
                row = json.loads(line)
            except (ValueError, UnicodeError):
                notices.append('An unreadable saved record was skipped.')
                continue
            item = visible_message(row, provider, position)
            if item:
                messages.append(item)
                if len(messages) >= limit:
                    cursor = position
                    break
    return {'available': True, 'messages': list(reversed(messages)),
            'before': f'{cursor}:{identity}' if cursor else None, 'has_more': cursor > 0,
            'notice': ' '.join(dict.fromkeys(notices)), 'revision': f'{identity}:{stat.st_size}:{stat.st_mtime_ns}'}


class ViewerStore:
    def __init__(self, root, home=None, collector=None, opener=None, async_listing=True,
                 codex_opener=None, codex_capability=None):
        self.root = Path(root).resolve()
        self.home = Path(home or Path.home()).resolve()
        self.collector = collector or self._collect
        self.opener = opener or open_viewer
        self.codex_opener = codex_opener or open_codex_conversation
        self.codex_capability = codex_capability or (lambda: codex_support(self.home))
        self._codex_support = None
        self._codex_support_checked = 0
        self._lock = threading.Lock()
        self._cache = None
        self._collected = 0
        self._opened = {}
        self._collection_lock = threading.Lock()
        self._collecting = False
        self.async_listing = async_listing

    def _collect(self):
        from dispatch_worker import worker_environment
        result = subprocess.run([str(CLAUDE), 'agents', '--json', '--all', '--cwd', str(self.root)],
                                capture_output=True, text=True, encoding='utf-8', timeout=15,
                                creationflags=HIDDEN, env=worker_environment())
        if result.returncode:
            raise RuntimeError('Claude session status could not be read.')
        value = json.loads(result.stdout)
        if not isinstance(value, list):
            raise RuntimeError('Claude returned an unsupported session listing.')
        return value

    def _refresh_listing(self):
        # Never hold the page-state lock while waiting for the CLI. History and
        # checkpoint reads must remain responsive even when the daemon hangs.
        with self._collection_lock:
            try:
                rows = self.collector()
                if not isinstance(rows, list):
                    raise ValueError('Invalid listing')
                value = {'rows': rows, 'checked_at': now(), 'error': None}
            except (OSError, ValueError, RuntimeError, subprocess.SubprocessError):
                value = {'rows': [], 'checked_at': now(),
                         'error': 'Live Claude status is unavailable. Saved history remains readable.'}
            with self._lock:
                self._cache = value
                self._collected = time.monotonic()
                self._collecting = False
            return value

    def listing(self, force=False):
        if force or not self.async_listing:
            with self._lock:
                if not force and self._cache and time.monotonic() - self._collected < 5:
                    return dict(self._cache, refreshing=False)
            return dict(self._refresh_listing(), refreshing=False)
        with self._lock:
            if not self._collecting and (self._cache is None or time.monotonic() - self._collected >= 5):
                self._collecting = True
                threading.Thread(target=self._refresh_listing, daemon=True).start()
            return dict(self._cache or {'rows': [], 'checked_at': None, 'error': None},
                        refreshing=self._collecting)

    def run_path(self, run_id):
        if not isinstance(run_id, str) or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_-]{0,159}', run_id):
            raise ValueError('Choose a saved run from the viewer.')
        return contained(self.root, self.root / '.orchestration' / run_id)

    def state(self, run_id):
        path = self.run_path(run_id)
        for filename in ('coordinator.json', 'run.json'):
            object_file(contained(path, path / filename))
        return Coordinator(path).read(), object_file(path / 'run.json')

    def runs(self):
        parent = contained(self.root, self.root / '.orchestration')
        rows, errors = [], []
        for path in parent.glob('*/coordinator.json'):
            try:
                state, manifest = self.state(path.parent.name)
                rows.append(self.summary(state, manifest))
            except (OSError, ValueError, KeyError, TypeError):
                errors.append({'id': path.parent.name, 'error': 'Saved coordinator state needs recovery.'})
        rows.sort(key=lambda row: str(row.get('checkpoint_at') or ''), reverse=True)
        return {'runs': rows, 'errors': errors, 'model_calls': 0}

    def summary(self, state, manifest):
        from local_services import memory_projects
        return {**{key: state.get(key) for key in ('owner', 'session', 'generation', 'status', 'checkpoint_at')},
                'id': state['run_id'], 'objective': state['checkpoint']['objective'],
                'memory_projects': memory_projects(self.root, state['run_id'], manifest),
                'run_status': manifest.get('status', 'unknown'),
                'background_id': state.get('handoff', {}).get('launch', {}).get('background_id')}

    def _job(self, identifier):
        if not isinstance(identifier, str) or not SHORT_ID.fullmatch(identifier):
            return {}
        path = contained(self.home / '.claude', self.home / '.claude/jobs' / identifier / 'state.json')
        try:
            value = object_file(path)
            if Path(value.get('cwd', '')).resolve() != self.root:
                return {'identity_error': 'Saved session metadata belongs to a different workspace.'}
            if value.get('daemonShort') and value['daemonShort'] != identifier:
                return {'identity_error': 'Saved session identifiers disagree.'}
            return value
        except (OSError, ValueError, TypeError):
            return {}

    def _binding(self, state):
        path = self.run_path(state['run_id'])
        try:
            binding = object_file(contained(path, path / 'viewer-session.json'))
            if binding.get('owner') == state['owner'] and binding.get('session') == state['session']:
                return binding
        except (OSError, ValueError, TypeError):
            pass
        return {}

    def _claude(self, state, force=False):
        listing = self.listing(force)
        handoff = state.get('handoff', {})
        launch = handoff.get('launch', {})
        # Older handoffs did not save the model; they launched Fable explicitly.
        model = launch.get('model') or handoff.get('receiving_model') or 'claude-fable-5'
        model_label = ('Opus' if model == 'opus' or str(model).startswith('claude-opus-')
                       else 'Fable' if 'fable' in str(model).lower() else str(model))
        permission_error = None
        try:
            require_model_allowed(model)
        except ValueError as exc:
            permission_error = str(exc)
        identifier = launch.get('background_id')
        # A pending receiver is visible during transfer; an unrelated old handoff is not.
        eligible = state['owner'] == 'fable' or state['status'] == 'handoff_ready'
        rows = [row for row in listing['rows'] if isinstance(row, dict) and row.get('kind') == 'background'
                and isinstance(row.get('id'), str) and SHORT_ID.fullmatch(row['id'])
                and isinstance(row.get('cwd'), str) and Path(row['cwd']).resolve() == self.root]
        if not eligible:
            rows, identifier = [], None
        if identifier:
            matches = [row for row in rows if row['id'] == identifier]
        else:
            expected = launch.get('name') or (
                ('Claude Opus coordinator ' if model_label == 'Opus' else 'Fable coordinator ')
                + str(handoff.get('id', ''))[:8])
            matches = [row for row in rows if handoff.get('id') and row.get('name') == expected]
            if len(matches) == 1:
                identifier = matches[0]['id']
            elif not matches and eligible and handoff.get('id'):
                # Recover an old receipt after the daemon exits, without selecting another session.
                candidates = []
                for path in (self.home / '.claude/jobs').glob('*/state.json'):
                    job = self._job(path.parent.name)
                    try:
                        intent = json.loads(job.get('intent', '{}'))
                    except (ValueError, TypeError):
                        continue
                    if (isinstance(intent, dict) and intent.get('handoff_id') == handoff['id']
                            and Path(intent.get('run_directory', '')).resolve() == self.run_path(state['run_id'])):
                        candidates.append(path.parent.name)
                if len(candidates) == 1:
                    identifier = candidates[0]
        row = matches[0] if len(matches) == 1 else {}
        job = self._job(identifier)
        # A session can switch models after launch. A saved Opus receipt must
        # not authorize interaction with a session now reporting paused Fable.
        for reported_model in (row.get('model'), job.get('model')):
            if reported_model:
                try:
                    require_model_allowed(reported_model)
                except ValueError as exc:
                    permission_error = str(exc)
        actual = row.get('sessionId') or job.get('sessionId')
        identity_error = job.get('identity_error')
        if not isinstance(actual, str) or not UUID.fullmatch(actual):
            actual = None
        if row.get('sessionId') and job.get('sessionId') and row['sessionId'] != job['sessionId']:
            identity_error = 'Saved and live session identifiers disagree.'
        if identity_error:
            row, actual = {}, None
        waiting = row.get('waitingFor') or job.get('waitingFor')
        live_state = str(row.get('state') or row.get('status') or '')
        if not waiting and re.search(r'wait|permission|approval|needs.input', live_state, re.I):
            waiting = live_state
        fallback = 'unavailable' if listing['error'] else 'checking' if listing.get('refreshing') else 'archived'
        terminal = {'done', 'completed', 'stopped', 'exited', 'failed', 'killed', 'archived', 'expired', 'cancelled', 'canceled'}
        ended = (str(row.get('status', '')).lower() in terminal
                 or str(row.get('state', '')).lower() in terminal)
        active_states = {'running', 'working', 'active', 'idle', 'waiting', 'waiting_for_input',
                         'waiting-for-input', 'needs_input', 'needs-input', 'attached', 'detached',
                         'starting', 'thinking', 'busy', 'permission', 'approval'}
        recognized = (str(row.get('status', '')).lower() in active_states
                      or str(row.get('state', '')).lower() in active_states
                      or bool(waiting and row))
        if identity_error:
            reason = identity_error + ' Refresh after recovering the correct session mapping.'
        elif permission_error:
            reason = permission_error + ' Saved history remains available.'
        elif listing['error']:
            reason = 'Live Claude status could not be confirmed. Saved history is available; refresh to retry the status check.'
        elif listing.get('refreshing') and listing['checked_at'] is None:
            reason = 'Checking the exact Claude session. The console becomes available when that check finishes.'
        elif not identifier:
            reason = 'This run has no confirmed Claude background session to attach.'
        elif not row:
            reason = 'The saved Claude session is no longer listed as available. Its saved history can still be viewed.'
        elif not actual:
            reason = 'Claude did not provide an exact conversation identity. Refresh before opening its console.'
        elif ended:
            reason = 'This Claude session has ended. Its saved history remains readable; there is no running console to attach.'
        elif not recognized:
            reason = 'Claude reported an unrecognized session state. Refresh before opening its console.'
        else:
            reason = 'Open the exact existing Claude session in a terminal to send messages and answer approvals.'
        can_attach = bool(row and actual and not listing['error'] and not permission_error and not ended and recognized)
        return {'name': 'Claude / ' + model_label, 'model': model,
                'status': row.get('status') or row.get('state') or fallback,
                'state': row.get('state') or job.get('state', 'unknown'),
                'waiting_for': waiting,
                'checked_at': listing['checked_at'], 'error': identity_error or listing['error'],
                'refreshing': listing.get('refreshing', False),
                'background_id': identifier, 'session_id': actual,
                'can_attach': can_attach,
                'interaction': {'kind': 'claude_console', 'label': 'Open ' + model_label + ' console',
                                'available': can_attach, 'reason': reason,
                                'session_id': actual, 'background_id': identifier},
                'history_available': False, 'history_note': '', 'format': 'claude'}

    def _transcript(self, provider):
        actual = provider.get('session_id')
        if not isinstance(actual, str) or not UUID.fullmatch(actual):
            return None
        if provider['format'] == 'claude':
            project = re.sub(r'[^a-zA-Z0-9-]', '-', str(self.root))
            base = self.home / '.claude/projects'
            path = contained(base, base / project / (actual + '.jsonl'))
            return path if path.is_file() else None
        base = self.home / '.codex/sessions'
        paths = list(base.glob('**/rollout-*' + actual + '.jsonl'))
        if len(paths) != 1:
            return None
        path = contained(base, paths[0])
        # Verify metadata instead of relying only on a filename match.
        with path.open('rb') as stream:
            line = stream.readline(65537)
        if len(line) > 65536:
            return None
        row = json.loads(line)
        if row.get('type') != 'session_meta' or row.get('payload', {}).get('id') != actual:
            return None
        return path

    def provider(self, state, force=False):
        binding = self._binding(state)
        if state['owner'] == 'astra' and state['status'] != 'handoff_ready':
            provider = {'name': 'Codex / ASTRA', 'format': 'codex', 'status': 'checkpoint only', 'state': 'unknown',
                        'session_id': binding.get('session_id') if binding.get('provider') == 'codex' else None,
                        'background_id': None, 'checked_at': now(), 'waiting_for': None, 'error': None,
                        'can_attach': False, 'history_available': False,
                        'history_note': 'Send messages and answer approvals in the original Codex conversation. Live execution status is not exposed by this viewer.'}
        elif state['owner'] == 'fable' or state['status'] == 'handoff_ready':
            provider = self._claude(state, force)
        else:
            provider = {'name': str(state['owner']), 'format': 'unknown', 'status': 'unsupported',
                        'state': 'unknown', 'session_id': None, 'background_id': None,
                        'checked_at': now(), 'waiting_for': None, 'error': None,
                        'can_attach': False, 'history_available': False, 'history_note': '',
                        'interaction': {'kind': 'unavailable', 'label': 'Interaction unavailable',
                                        'available': False, 'session_id': None, 'background_id': None,
                                        'reason': 'Continue in this coordinator provider\'s original application.'}}
        path = None
        try:
            path = self._transcript(provider)
            provider['history_available'] = bool(path)
        except (OSError, ValueError, TypeError):
            provider['history_available'] = False
        if provider['format'] == 'codex':
            if self._codex_support is None or force or time.monotonic() - self._codex_support_checked >= 30:
                self._codex_support = self.codex_capability()
                self._codex_support_checked = time.monotonic()
            provider['interaction'] = codex_interaction(
                provider['session_id'], transcript_source(path, provider['session_id']),
                self._codex_support, history_available=provider['history_available'])
        if not provider['history_available']:
            provider['history_note'] += ' No saved transcript is mapped to this coordinator session.'
        return provider

    def detail(self, run_id):
        state, manifest = self.state(run_id)
        from task_activity import snapshot
        summary = self.summary(state, manifest)
        return dict(summary, checkpoint=state['checkpoint'], provider=self.provider(state),
                    activity=snapshot(self.root, run_id, summary['memory_projects'], home=self.home))

    def history(self, run_id, before=None):
        state, _ = self.state(run_id)
        provider = self.provider(state)
        path = self._transcript(provider)
        if not path:
            return {'available': False, 'messages': [], 'before': None, 'has_more': False,
                    'notice': provider['history_note'], 'revision': ''}
        return history_page(path, provider['format'], before)

    def bind(self, run_id, provider, session_id):
        if provider != 'codex' or not isinstance(session_id, str) or not UUID.fullmatch(session_id):
            raise ValueError('Bind requires an exact Codex conversation UUID.')
        coordinator = Coordinator(self.run_path(run_id))
        with file_lock(coordinator.lock):
            state = coordinator.read()
            if state['owner'] != 'astra' or state['status'] != 'active':
                raise ValueError('Only the active ASTRA coordinator can be bound to Codex history.')
            if not self._transcript({'format': 'codex', 'session_id': session_id}):
                raise ValueError('The exact saved Codex conversation was not found.')
            value = {'owner': state['owner'], 'session': state['session'], 'provider': provider,
                     'session_id': session_id, 'bound_at': now()}
            write_json(contained(coordinator.run, coordinator.run / 'viewer-session.json'), value)
            return {'ok': True, 'model_calls': 0}

    def attach(self, request):
        from dispatch_worker import worker_environment
        if not isinstance(request, dict) or set(request) != {'run', 'session', 'generation', 'background_id'}:
            raise ValueError('Select the current coordinator before opening its console.')
        coordinator = Coordinator(self.run_path(request['run']))
        # The same lock protects a lead claim, so a stale tab cannot open the wrong lead.
        with file_lock(coordinator.lock):
            state = coordinator.read()
            provider = self.provider(state, force=True)
            if (request['session'] != state['session'] or type(request['generation']) is not int
                    or request['generation'] != state['generation']
                    or request['background_id'] != provider.get('background_id') or not provider['can_attach']):
                raise ValueError('The coordinator changed or its console is unavailable. Refresh before opening it.')
            identifier = provider['background_id']
            with self._lock:
                if time.monotonic() - self._opened.get(identifier, -100) < 3:
                    return {'ok': True, 'status': 'already opened'}
                outcome = self.opener(CLAUDE, identifier, self.root, worker_environment())
                if outcome != 'opened':
                    raise ValueError('An interactive console could not be opened on this platform.')
                self._opened[identifier] = time.monotonic()
            return {'ok': True, 'status': 'opened'}

    def interact(self, request):
        """Open only the exact provider conversation displayed in a current tab."""
        from dispatch_worker import worker_environment
        fields = {'run', 'session', 'generation', 'kind', 'session_id', 'background_id'}
        if not isinstance(request, dict) or set(request) != fields:
            raise ValueError('Select the current coordinator before opening its conversation.')
        coordinator = Coordinator(self.run_path(request['run']))
        with file_lock(coordinator.lock):
            state = coordinator.read()
            if (request['session'] != state['session'] or type(request['generation']) is not int
                    or request['generation'] != state['generation']):
                raise ValueError('The coordinator changed. Refresh before opening its conversation.')
            provider = self.provider(state, force=True)
            interaction = provider['interaction']
            if not interaction['available']:
                raise ValueError(interaction['reason'])
            if any(request[field] != interaction[field] for field in ('kind', 'session_id', 'background_id')):
                raise ValueError('The selected conversation changed. Refresh before opening it.')
            key = interaction['kind'] + ':' + interaction['session_id']
            with self._lock:
                if time.monotonic() - self._opened.get(key, -100) < 3:
                    return {'ok': True, 'status': 'already opened', 'kind': interaction['kind'], 'model_calls': 0}
                if interaction['kind'] == 'codex_conversation':
                    outcome = self.codex_opener(interaction['session_id'], self._codex_support)
                elif interaction['kind'] == 'claude_console':
                    outcome = self.opener(CLAUDE, interaction['background_id'], self.root, worker_environment())
                else:
                    raise ValueError('This provider does not support a verified interaction action.')
                if outcome != 'opened':
                    raise ValueError('The selected conversation could not be opened on this platform.')
                self._opened[key] = time.monotonic()
            return {'ok': True, 'status': 'opened', 'kind': interaction['kind'], 'model_calls': 0}


class ViewerServer(DashboardServer):
    def __init__(self, root, port=0, store=None):
        super().__init__(root, port)
        self.RequestHandlerClass = ViewerHandler
        self.viewer_token = self.brain_token
        self.page_id = 'viewer'
        self.store = store or ViewerStore(root)


class ViewerHandler(DashboardHandler):
    server_version = 'OrchestratorViewer/1'

    def _gate(self, mutation=False):
        if len(self.path) > 2048 or not self.path.startswith('/') or self.path.startswith('//') or '#' in self.path:
            self._error(400, 'Invalid request address.'); return False
        if self.headers.get_all('Host', []) != [self.server.origin.removeprefix('http://')]:
            self._error(403, 'Use the loopback address from the viewer launcher.'); return False
        origins = self.headers.get_all('Origin', [])
        if (origins and origins != [self.server.origin]) or (mutation and origins != [self.server.origin]):
            self._error(403, 'This request must come from the viewer.'); return False
        if (self.headers.get('Sec-Fetch-Site') not in (None, 'none', 'same-origin')
                and not self._same_site_page_navigation()):
            self._error(403, 'This request must come from the viewer.'); return False
        if self.path.startswith('/api/'):
            tokens = self.headers.get_all('X-Viewer-Token', [])
            if len(tokens) != 1 or not hmac.compare_digest(tokens[0].encode(), self.server.viewer_token.encode()):
                self._error(403, 'Reload the viewer to reconnect securely.'); return False
        return True

    def _handle_error(self, error):
        if isinstance(error, ValueError):
            self._error(400, str(error))
        else:
            self._error(500, 'Saved session data could not be read. Refresh or inspect the original application.')

    def do_GET(self):
        if not self._gate(): return
        address = urlsplit(self.path)
        try:
            if self._workspace_page(address):
                return
            if address.path == '/system-map' and not address.query:
                return self._system_map()
            if address.path == '/':
                from coordinator_viewer_page import PAGE
                return self._reply(200, PAGE.replace('__TOKEN__', self.server.viewer_token).replace('__NONCE__', self.server.nonce), 'text/html; charset=utf-8')
            if address.path == '/health':
                return self._reply(200, {'service': SERVICE, 'version': 1, 'instance_id': self.server.instance_id})
            if address.path == '/favicon.ico':
                return self._reply(204, '', 'image/svg+xml')
            if address.path not in ('/api/runs', '/api/run', '/api/history', '/api/services', '/api/usage-monitor'):
                return self._error(404, 'This page is not available.')
            params = parse_qs(address.query, max_num_fields=3)
            allowed = {'run', 'before'} if address.path == '/api/history' else {'run'} if address.path == '/api/run' else set()
            if set(params) - allowed or any(len(v) != 1 for v in params.values()):
                raise ValueError('Invalid run or history selection.')
            if address.path == '/api/usage-monitor':
                value = monitor_status()
            elif address.path == '/api/services':
                value = self._services()
            elif address.path == '/api/runs':
                value = self.server.store.runs()
            elif address.path == '/api/run':
                value = self.server.store.detail(params.get('run', [''])[0])
            else:
                value = self.server.store.history(params.get('run', [''])[0], params.get('before', [None])[0])
            self._reply(200, value)
        except Exception as error:
            self._handle_error(error)

    def do_POST(self):
        if not self._gate(mutation=True): return
        if self.path not in ('/api/attach', '/api/interact', '/api/open', '/api/usage-monitor'):
            return self._error(404, 'This action is not available.')
        try:
            body = self._body()
            if body is not None:
                if self.path == '/api/usage-monitor':
                    if not isinstance(body, dict) or set(body) != {'enabled'} or type(body['enabled']) is not bool:
                        raise ValueError('Choose On or Off for the usage monitor.')
                    value = set_monitor_enabled(body['enabled'])
                elif self.path == '/api/open':
                    value = self._open_service(body)
                elif self.path == '/api/interact':
                    value = self.server.store.interact(body)
                else:
                    value = self.server.store.attach(body)
                self._reply(200, value)
        except Exception as error:
            self._handle_error(error)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, default=ROOT)
    parser.add_argument('--port', type=int, default=0)
    parser.add_argument('--state-file', type=Path)
    args = parser.parse_args(argv)
    with ViewerServer(args.root, args.port) as server:
        state = {'service': SERVICE, 'version': 1, 'pid': os.getpid(), 'origin': server.origin, 'instance_id': server.instance_id}
        if args.state_file:
            state_file = contained(args.root / 'runtime', args.state_file)
            state_file.parent.mkdir(parents=True, exist_ok=True)
            write_json(state_file, state)
        from workspace_navigation import write_navigation_script
        try:
            write_navigation_script(server.root, server.page_id, server.origin)
        except OSError:
            print('Saved report links could not be refreshed; live dashboard navigation remains available.', flush=True)
        print('Orchestrator viewer: ' + server.origin + '/', flush=True)
        try:
            server.serve_forever(poll_interval=.2)
        except KeyboardInterrupt:
            pass
        finally:
            if args.state_file:
                try:
                    if object_file(state_file).get('instance_id') == server.instance_id:
                        state_file.unlink(missing_ok=True)
                except (OSError, ValueError):
                    pass
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
