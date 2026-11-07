"""Turn finished native agent sessions into Brain candidates that wait for review.

A Claude Code SessionEnd hook (`hook`) or a sweep of recent Codex rollouts
(`scan-codex`) builds a rule-based summary with no model call: the first and last
requests, files changed, commands run, errors seen and the final reply. Sessions
with nothing durable are skipped: no requests, a single question with no tool use,
smoke tests, and short-lived setup failures. Broad claims that a tool "is broken"
are removed, because they harden into false rules once the tool is fixed.
Obvious secrets are redacted before anything is written.

Candidates live in runtime/brain/session-inbox/ and never reach recall until a
reviewer accepts one, which proposes and approves an ordinary episode whose
user-note source names the session. Rejected sessions and fingerprints are
remembered, so the same candidate is not offered again.
"""
import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import sqlite3
import sys
import time

from brain_store import BrainStore, digest, safe_path, scope, text
from storage_budget import StorageBudget, StorageLimitError
from usage_guard import file_lock, write_json

INBOX = Path('runtime/brain/session-inbox')
MAX_PENDING = 200
MAX_REJECTED = 2000
MAX_CANDIDATE_BYTES = 16 * 1024
MAX_TRANSCRIPT_BYTES = 64 * 1024**2
MAX_LOG = 50
MIN_SESSION_SECONDS = 30
_ID = re.compile(r'[a-f0-9]{32}')
EDIT_TOOLS = {'Edit', 'Write', 'MultiEdit', 'NotebookEdit'}
SHELL_TOOLS = {'Bash', 'PowerShell', 'shell', 'exec_command', 'local_shell', 'container.exec'}
SMOKE = re.compile(r'^\s*(?:echo|true|pwd|ls|dir|whoami|date|hostname|cls|clear|git (?:status|log|branch)|'
                   r'\S+ (?:--version|-v|version))\b', re.I)
SETUP_ERROR = re.compile(r'command not found|is not recognized as|no such file or directory|cannot find the path|'
                         r'enoent|permission denied|access is denied|modulenotfounderror|no module named|'
                         r'could not resolve host|network is unreachable|timed out|\b40[13]\b|rate limit|quota|'
                         r'not authenticated|login required', re.I)
BROKEN_CLAIM = re.compile(r"[^.!?\n]*\b(?:(?:is|are|seems?|appears?|looks?)\s+(?:to be\s+)?(?:completely\s+|totally\s+)?"
                          r"(?:broken|unusable|not working|non-functional)|(?:does not|doesn't|do not|don't|cannot|can't)"
                          r"\s+(?:ever\s+)?work)\b[^.!?\n]*[.!?]?", re.I)
SECRET = re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----[\s\S]*?-----END [A-Z ]*PRIVATE KEY-----"
                    r"|\bsk-(?:or-v1-|proj-|ant-)?[A-Za-z0-9_-]{16,}"
                    r"|\bgh[pousr]_[A-Za-z0-9]{20,}|\bgithub_pat_[A-Za-z0-9_]{20,}"
                    r"|\bAKIA[0-9A-Z]{16}\b|\bxox[abprs]-[A-Za-z0-9-]{10,}|\bAIza[0-9A-Za-z_-]{30,}"
                    r"|\bBearer\s+[A-Za-z0-9._~+/=-]{16,}", re.I)
ASSIGNMENT = re.compile(r"\b((?:api[_-]?key|access[_-]?token|token|secret|password|passwd)\s*[:=]\s*)"
                        r"(['\"]?)[^\s'\"]{6,}\2", re.I)
_WRAPPER = re.compile(r'^\s*<(?:command-name|command-message|command-args|local-command-[a-z]+|'
                      r'environment_context|user_instructions|turn_aborted|permissions[^>]*|'
                      r'user_shell_command|system-reminder)\b', re.I)
_REMINDER = re.compile(r'<system-reminder>[\s\S]*?</system-reminder>', re.I)


def _now():
    return datetime.now(timezone.utc).isoformat(timespec='seconds')


def redact(value):
    value = SECRET.sub('[redacted]', value)
    return ASSIGNMENT.sub(lambda match: match.group(1) + '[redacted]', value)


def _clip(value, maximum):
    value = ' '.join(str(value).split())
    return value if len(value) <= maximum else value[:maximum - 1].rstrip() + '…'


def _prompt(value):
    """Return a person's request, or None for harness wrappers and injected context."""
    if not isinstance(value, str):
        return None
    value = _REMINDER.sub('', value).strip()
    if not value or _WRAPPER.match(value) or value.startswith('# AGENTS.md instructions'):
        return None
    return value


def _facts(agent):
    return {'agent': agent, 'session_id': None, 'cwd': None, 'started_at': None, 'ended_at': None,
            'prompts': [], 'files': [], 'commands': [], 'errors': [], 'final': '', 'tool_calls': 0,
            'truncated': False, 'interactive': True}


def _stamp(facts, value):
    if isinstance(value, str) and value:
        facts['started_at'] = facts['started_at'] or value
        facts['ended_at'] = value


def _lines(path):
    path = Path(path)
    with path.open('rb') as handle:
        read = 0
        for raw in handle:
            read += len(raw)
            if read > MAX_TRANSCRIPT_BYTES:
                yield None
                return
            try:
                entry = json.loads(raw)
            except ValueError:
                continue
            if isinstance(entry, dict):
                yield entry


def _add(items, value, maximum=40):
    if value and value not in items and len(items) < maximum:
        items.append(value)


def parse_claude(path):
    facts = _facts('claude-code')
    for entry in _lines(path):
        if entry is None:
            facts['truncated'] = True
            break
        if entry.get('isSidechain') or entry.get('type') not in ('user', 'assistant'):
            continue
        facts['session_id'] = facts['session_id'] or entry.get('sessionId')
        facts['cwd'] = facts['cwd'] or entry.get('cwd')
        if str(entry.get('entrypoint', '')).startswith('sdk'):
            # Print-mode and SDK runs (including orchestrator workers) are reviewed as tasks.
            facts['interactive'] = False
        _stamp(facts, entry.get('timestamp'))
        message = entry.get('message') if isinstance(entry.get('message'), dict) else {}
        content = message.get('content')
        blocks = [{'type': 'text', 'text': content}] if isinstance(content, str) else (
            content if isinstance(content, list) else [])
        for block in blocks:
            if not isinstance(block, dict):
                continue
            kind = block.get('type')
            if entry['type'] == 'user' and kind == 'text' and not entry.get('isMeta'):
                prompt = _prompt(block.get('text'))
                if prompt:
                    facts['prompts'].append(prompt)
            elif entry['type'] == 'user' and kind == 'tool_result' and block.get('is_error'):
                body = block.get('content')
                if isinstance(body, list):
                    body = ' '.join(item.get('text', '') for item in body if isinstance(item, dict))
                if isinstance(body, str) and body.strip():
                    _add(facts['errors'], _clip(body.strip().splitlines()[0], 200), 12)
            elif entry['type'] == 'assistant' and kind == 'text' and isinstance(block.get('text'), str):
                if block['text'].strip():
                    facts['final'] = block['text'].strip()
            elif entry['type'] == 'assistant' and kind == 'tool_use':
                facts['tool_calls'] += 1
                name, data = block.get('name'), block.get('input') if isinstance(block.get('input'), dict) else {}
                if name in EDIT_TOOLS:
                    _add(facts['files'], data.get('file_path') or data.get('notebook_path'))
                elif name in SHELL_TOOLS and isinstance(data.get('command'), str):
                    _add(facts['commands'], _clip(data['command'].strip().splitlines()[0], 160), 30)
    return facts


def _codex_command(arguments):
    try:
        value = json.loads(arguments) if isinstance(arguments, str) else arguments
    except ValueError:
        return None
    if not isinstance(value, dict):
        return None
    command = value.get('cmd', value.get('command'))
    if isinstance(command, list):
        command = ' '.join(str(part) for part in command if isinstance(part, str))
        command = command.removeprefix('bash -lc ').removeprefix('powershell -Command ')
    return _clip(command.strip().splitlines()[0], 160) if isinstance(command, str) and command.strip() else None


def parse_codex(path):
    facts = _facts('codex')
    for entry in _lines(path):
        if entry is None:
            facts['truncated'] = True
            break
        payload = entry.get('payload') if isinstance(entry.get('payload'), dict) else {}
        _stamp(facts, entry.get('timestamp'))
        if entry.get('type') == 'session_meta':
            facts['session_id'] = facts['session_id'] or payload.get('id') or payload.get('session_id')
            facts['cwd'] = facts['cwd'] or payload.get('cwd')
            if payload.get('source') == 'exec' or 'exec' in str(payload.get('originator', '')).casefold():
                facts['interactive'] = False
            continue
        if entry.get('type') == 'turn_context':
            facts['cwd'] = facts['cwd'] or payload.get('cwd')
            continue
        if entry.get('type') != 'response_item':
            continue
        kind = payload.get('type')
        if kind == 'message' and payload.get('role') in ('user', 'assistant'):
            for item in payload.get('content') or []:
                if not isinstance(item, dict) or not isinstance(item.get('text'), str):
                    continue
                if payload['role'] == 'user' and item.get('type') == 'input_text':
                    prompt = _prompt(item['text'])
                    if prompt:
                        facts['prompts'].append(prompt)
                elif payload['role'] == 'assistant' and item['text'].strip():
                    facts['final'] = item['text'].strip()
        elif kind in ('function_call', 'custom_tool_call', 'local_shell_call'):
            facts['tool_calls'] += 1
            if kind == 'custom_tool_call' and payload.get('name') == 'apply_patch':
                for line in str(payload.get('input', '')).splitlines():
                    match = re.match(r'\*\*\* (?:Update|Add|Delete) File: (.+)', line)
                    if match:
                        _add(facts['files'], match.group(1).strip())
            else:
                command = _codex_command(payload.get('arguments') or payload.get('action'))
                if command and (payload.get('name') in SHELL_TOOLS or kind == 'local_shell_call'):
                    _add(facts['commands'], command, 30)
        elif kind in ('function_call_output', 'custom_tool_call_output'):
            output = payload.get('output')
            try:
                parsed = json.loads(output) if isinstance(output, str) and output.startswith('{') else None
            except ValueError:
                parsed = None
            metadata = parsed.get('metadata') if isinstance(parsed, dict) else None
            if isinstance(metadata, dict) and metadata.get('exit_code') not in (None, 0):
                body = parsed.get('output') if isinstance(parsed.get('output'), str) else ''
                first = next((line for line in body.splitlines() if line.strip()), 'non-zero exit')
                _add(facts['errors'], _clip(first, 200), 12)
    return facts


def _duration(facts):
    try:
        start = datetime.fromisoformat(facts['started_at'].replace('Z', '+00:00'))
        end = datetime.fromisoformat(facts['ended_at'].replace('Z', '+00:00'))
        return (end - start).total_seconds()
    except (AttributeError, ValueError, TypeError):
        return None


def skip_reason(facts):
    """Why a session holds nothing durable, or None when it deserves review."""
    if not facts.get('interactive', True):
        return 'non_interactive'
    if not facts['prompts']:
        return 'no_requests'
    if not facts['files'] and facts['tool_calls'] == 0 and len(facts['prompts']) <= 1:
        return 'no_activity'
    duration = _duration(facts)
    if not facts['files'] and duration is not None and duration < MIN_SESSION_SECONDS:
        return 'too_short'
    if (not facts['files'] and len(facts['prompts']) <= 2 and facts['commands']
            and len(facts['commands']) <= 3 and all(SMOKE.match(command) for command in facts['commands'])):
        return 'smoke_test'
    if (not facts['files'] and len(facts['prompts']) <= 2 and facts['errors']
            and all(SETUP_ERROR.search(error) for error in facts['errors'])):
        return 'setup_failure'
    return None


def build_candidate(facts, project_id):
    """Rule-based episode with evidence quotes; secrets are redacted first."""
    prompts = [redact(prompt) for prompt in facts['prompts']]
    final = redact(facts['final'])
    removed = BROKEN_CLAIM.findall(final)
    final = ' '.join(BROKEN_CLAIM.sub(' ', final).split())
    files = [redact(str(item)) for item in facts['files']]
    commands = [redact(item) for item in facts['commands']]
    errors = [redact(item) for item in facts['errors']]
    first = prompts[0]
    title = _clip('Session: ' + first.splitlines()[0], 160)
    action_parts = []
    if files:
        action_parts.append('Changed ' + str(len(files)) + ' file(s): ' + ', '.join(files[:12]) + '.')
    if commands:
        action_parts.append('Ran ' + str(len(commands)) + ' distinct command(s), including: '
                            + '; '.join(commands[:6]) + '.')
    action_parts.append(str(facts['tool_calls']) + ' tool call(s) across ' + str(len(prompts)) + ' request(s).')
    outcome = final or 'The session ended without a final written reply.'
    lines = ['Native ' + facts['agent'] + ' session ' + str(facts['session_id']) + ' in ' + str(facts['cwd'])
             + ' (' + str(facts['started_at']) + ' to ' + str(facts['ended_at']) + ').',
             'First request: ' + _clip(first, 500)]
    if len(prompts) > 1:
        lines.append('Last request: ' + _clip(prompts[-1], 400))
    lines += [' '.join(action_parts)]
    if errors:
        lines.append('Errors seen: ' + '; '.join(errors[:5]))
    lines.append('Final reply: ' + _clip(outcome, 900))
    lines.append('Rule-based summary of a native session; verify it against the code before approving.')
    warnings = []
    if removed:
        warnings.append('Removed ' + str(len(removed)) + ' broad "tool is broken" claim(s); confirm the current '
                        'tool state before restoring any of them.')
    if facts['truncated']:
        warnings.append('The transcript exceeded 64 MiB; only its beginning was summarized.')
    candidate = {
        'project_id': project_id, 'kind': 'episode', 'title': title, 'content': '\n'.join(lines)[:3000],
        'tags': sorted({'native-session', facts['agent']} | ({'files-changed'} if files else set())),
        'importance': .3,
        'episode': {'problem': _clip(first, 1000), 'action': _clip(' '.join(action_parts), 1000),
                    'outcome': _clip(outcome, 1000)},
    }
    evidence = [{'kind': 'first_request', 'quote': _clip(first, 300)}]
    if len(prompts) > 1:
        evidence.append({'kind': 'last_request', 'quote': _clip(prompts[-1], 300)})
    if final:
        evidence.append({'kind': 'final_reply', 'quote': _clip(final, 300)})
    if files:
        evidence.append({'kind': 'files', 'quote': _clip(', '.join(files), 300)})
    return candidate, evidence, warnings


def _known_projects(root):
    """Projects with current reviewed memories; read-only and best effort."""
    try:
        store = BrainStore(root, read_only=True)
        with store._connection() as con:
            return {row[0] for row in con.execute(
                "SELECT DISTINCT project_id FROM memories WHERE status='active' LIMIT 500")}
    except (ValueError, OSError, sqlite3.Error):
        return set()


def resolve_project(cwd, known):
    """Workspace marker first, then the git root's folder name when the Brain knows it."""
    if not isinstance(cwd, str) or not cwd:
        return None
    path = Path(cwd)
    for folder in (path, *path.parents):
        marker = folder / '.orchestration' / 'project.json'
        if marker.is_file():
            try:
                value = json.loads(marker.read_text(encoding='utf-8-sig')).get('project_id')
                return scope(value)
            except (OSError, ValueError, AttributeError):
                return None
        if (folder / '.git').exists():
            name = folder.name.casefold()
            return name if name in known else None
    name = path.name.casefold()
    return name if name in known else None


class Inbox:
    def __init__(self, root):
        self.root = Path(root).resolve()
        self.home = safe_path(self.root, self.root / INBOX)
        self.lock = self.home / 'inbox.lock'
        self.rejected_path = self.home / 'rejected.json'
        self.log_path = self.home / 'recent-captures.json'

    def _path(self, candidate_id):
        if not isinstance(candidate_id, str) or not _ID.fullmatch(candidate_id):
            raise ValueError('Inbox candidate IDs are 32 lowercase hex characters')
        return safe_path(self.root, self.home / (candidate_id + '.json'))

    def _read(self, path, default):
        try:
            return json.loads(path.read_text(encoding='utf-8'))
        except FileNotFoundError:
            return default

    def candidates(self):
        if not self.home.is_dir():
            return []
        found = []
        for path in sorted(self.home.glob('*.json')):
            if _ID.fullmatch(path.stem):
                try:
                    found.append(json.loads(path.read_text(encoding='utf-8')))
                except (OSError, ValueError):
                    continue
        return found

    def _log(self, outcome):
        recent = self._read(self.log_path, [])
        recent = (recent if isinstance(recent, list) else [])[-(MAX_LOG - 1):] + [outcome]
        write_json(self.log_path, recent)

    def capture(self, facts, *, known=None, budget=None):
        """Store one pending candidate, or return why the session was skipped."""
        session = facts.get('session_id')
        outcome = {'at': _now(), 'agent': facts['agent'], 'session_id': session, 'status': 'skipped'}
        if not isinstance(session, str) or not session:
            return dict(outcome, reason='no_session_id')
        candidate_id = digest([facts['agent'], session])[:32]
        outcome['candidate_id'] = candidate_id
        reason = skip_reason(facts)
        project = resolve_project(facts.get('cwd'), _known_projects(self.root) if known is None else known)
        if reason is None and project is None:
            reason = 'unknown_project'
        self.home.mkdir(parents=True, exist_ok=True)
        with file_lock(self.lock):
            if reason is None:
                rejected = self._read(self.rejected_path, [])
                existing = self._read(self._path(candidate_id), None)
                candidate, evidence, warnings = build_candidate(facts, project)
                fingerprint = digest([project, ' '.join(candidate['title'].casefold().split()),
                                      candidate['episode']['problem'].casefold(), sorted(facts['files'])])
                if any(item.get('session') == candidate_id or item.get('fingerprint') == fingerprint
                       for item in rejected if isinstance(item, dict)):
                    reason = 'rejected_before'
                elif existing and existing.get('status') != 'pending':
                    reason = 'already_reviewed'
                elif not existing and sum(1 for item in self.candidates() if item.get('status') == 'pending') >= MAX_PENDING:
                    reason = 'inbox_full'
            if reason:
                outcome['reason'] = reason
                self._log(outcome)
                return outcome
            record = {'schema_version': 1, 'id': candidate_id, 'status': 'pending', 'agent': facts['agent'],
                      'session_id': session, 'cwd': facts.get('cwd'), 'started_at': facts.get('started_at'),
                      'ended_at': facts.get('ended_at'), 'captured_at': _now(),
                      'request_count': len(facts['prompts']), 'project_id': project, 'fingerprint': fingerprint,
                      'candidate': candidate, 'evidence': evidence, 'warnings': warnings}
            encoded = json.dumps(record, ensure_ascii=False).encode('utf-8')
            if len(encoded) > MAX_CANDIDATE_BYTES:
                record['evidence'] = record['evidence'][:1]
                encoded = json.dumps(record, ensure_ascii=False).encode('utf-8')
            with (budget or StorageBudget(self.root)).allocation(len(encoded) * 2 + 4096, kind='brain'):
                write_json(self._path(candidate_id), record)
            outcome.update(status='refreshed' if existing else 'captured', project_id=project)
            self._log(outcome)
            return outcome

    def accept(self, candidate_id, reviewer, note, *, project_id=None, title=None, brain_factory=BrainStore):
        reviewer, note = text(reviewer, 'Reviewer', 100), text(note, 'Review note', 1000, 20)
        with file_lock(self.lock):
            path = self._path(candidate_id)
            record = self._read(path, None)
            if not record or record.get('status') != 'pending':
                raise ValueError('Only a pending inbox candidate can be accepted')
            payload = dict(record['candidate'])
            payload['project_id'] = scope(project_id or payload['project_id'])
            if title:
                payload['title'] = text(title, 'Title', 160)
            day = str(record.get('ended_at') or '')[:10]
            payload['source'] = {'type': 'user', 'note': _clip(
                'Reviewed native ' + record['agent'] + ' session ' + record['session_id'] + ' (' + day + ') by '
                + reviewer + ': ' + note, 1000)}
            brain = brain_factory(self.root)
            memory = brain.propose(payload)
            if memory['status'] == 'pending':
                memory = brain.approve(memory['id'], reviewer, note)
            record.update(status='accepted', memory_id=memory['id'], reviewed_by=reviewer,
                          reviewed_at=_now(), review_note=note, project_id=payload['project_id'])
            write_json(path, record)
            return {'status': 'accepted', 'candidate_id': candidate_id, 'memory_id': memory['id'],
                    'memory_status': memory['status'], 'project_id': payload['project_id']}

    def reject(self, candidate_id, reviewer, reason):
        reviewer, reason = text(reviewer, 'Reviewer', 100), text(reason, 'Reason', 1000, 10)
        with file_lock(self.lock):
            path = self._path(candidate_id)
            record = self._read(path, None)
            if not record or record.get('status') != 'pending':
                raise ValueError('Only a pending inbox candidate can be rejected')
            rejected = self._read(self.rejected_path, [])
            rejected = (rejected if isinstance(rejected, list) else [])[-(MAX_REJECTED - 1):]
            rejected.append({'session': candidate_id, 'fingerprint': record.get('fingerprint'),
                             'title': record['candidate']['title'], 'reason': reason,
                             'reviewer': reviewer, 'at': _now()})
            write_json(self.rejected_path, rejected)
            path.unlink()
            return {'status': 'rejected', 'candidate_id': candidate_id}

    def show(self, candidate_id):
        record = self._read(self._path(candidate_id), None)
        if record is None:
            raise ValueError('Inbox candidate not found')
        return record

    def listing(self, project_id=None, status='pending'):
        rows = [{'id': item['id'], 'status': item['status'], 'project_id': item.get('project_id'),
                 'agent': item.get('agent'), 'ended_at': item.get('ended_at'),
                 'title': item['candidate']['title'], 'warnings': len(item.get('warnings', []))}
                for item in self.candidates()
                if (status is None or item.get('status') == status)
                and (project_id is None or item.get('project_id') == project_id)]
        return sorted(rows, key=lambda row: str(row['ended_at']), reverse=True)


def hook(root, stream):
    """Claude Code SessionEnd hook: never raises and never blocks the session for long."""
    try:
        payload = json.loads(stream.read(65536) or '{}')
        transcript = payload.get('transcript_path') if isinstance(payload, dict) else None
        if not isinstance(transcript, str) or not transcript.endswith('.jsonl') or not Path(transcript).is_file():
            return {'status': 'skipped', 'reason': 'no_transcript'}
        facts = parse_claude(transcript)
        facts['session_id'] = facts['session_id'] or payload.get('session_id')
        facts['cwd'] = facts['cwd'] or payload.get('cwd')
        return Inbox(root).capture(facts)
    except Exception as exc:  # a memory hook must never break the agent session
        return {'status': 'error', 'error': type(exc).__name__}


def scan_codex(root, codex_home=None, *, hours=24, idle_minutes=10):
    """Capture recent Codex rollouts that have been idle for a while; safe to repeat."""
    codex_home = Path(codex_home or os.environ.get('CODEX_HOME') or Path.home() / '.codex')
    sessions = codex_home / 'sessions'
    if not sessions.is_dir():
        return {'status': 'skipped', 'reason': 'no_codex_sessions', 'results': []}
    newest, oldest = time.time() - idle_minutes * 60, time.time() - hours * 3600
    inbox, known, results = Inbox(root), _known_projects(root), []
    for path in sorted(sessions.rglob('rollout-*.jsonl')):
        try:
            modified = path.stat().st_mtime
        except OSError:
            continue
        if oldest <= modified <= newest:
            results.append(inbox.capture(parse_codex(path), known=known))
    counts = {}
    for item in results:
        key = item['status'] if item['status'] != 'skipped' else 'skipped:' + item.get('reason', '')
        counts[key] = counts.get(key, 0) + 1
    return {'status': 'ok', 'scanned': len(results), 'counts': counts, 'results': results[-50:]}


def main(argv=None):
    from paths import ROOT
    parser = argparse.ArgumentParser(prog='brain inbox', description=__doc__.splitlines()[0])
    parser.add_argument('--root', type=Path, default=ROOT)
    commands = parser.add_subparsers(dest='command', required=True)
    commands.add_parser('hook', help='Claude Code SessionEnd hook; reads the hook JSON on stdin')
    capture = commands.add_parser('capture', help='Capture one transcript file')
    capture.add_argument('--transcript', type=Path, required=True)
    capture.add_argument('--agent', choices=['claude-code', 'codex'], required=True)
    codex = commands.add_parser('scan-codex', help='Capture recent idle Codex rollouts')
    codex.add_argument('--hours', type=int, default=24)
    codex.add_argument('--idle-minutes', type=int, default=10)
    codex.add_argument('--codex-home', type=Path)
    listing = commands.add_parser('list')
    listing.add_argument('--project')
    listing.add_argument('--status', choices=['pending', 'accepted', 'all'], default='pending')
    show = commands.add_parser('show')
    show.add_argument('candidate_id')
    accept = commands.add_parser('accept', help='Approve a candidate into the Brain as a reviewed episode')
    accept.add_argument('candidate_id')
    accept.add_argument('--reviewer', required=True)
    accept.add_argument('--note', required=True, help='What you verified (at least 20 characters)')
    accept.add_argument('--project')
    accept.add_argument('--title')
    reject = commands.add_parser('reject', help='Discard a candidate and never offer it again')
    reject.add_argument('candidate_id')
    reject.add_argument('--reviewer', required=True)
    reject.add_argument('--reason', required=True)
    args = parser.parse_args(argv)
    if args.command == 'hook':
        hook(args.root, sys.stdin)
        return 0  # SessionEnd output is ignored; the outcome is logged in the inbox
    try:
        inbox = Inbox(args.root)
        if args.command == 'capture':
            facts = (parse_claude if args.agent == 'claude-code' else parse_codex)(args.transcript)
            result = inbox.capture(facts)
        elif args.command == 'scan-codex':
            result = scan_codex(args.root, args.codex_home, hours=max(1, args.hours),
                                idle_minutes=max(0, args.idle_minutes))
        elif args.command == 'list':
            result = inbox.listing(args.project, None if args.status == 'all' else args.status)
        elif args.command == 'show':
            result = inbox.show(args.candidate_id)
        elif args.command == 'accept':
            result = inbox.accept(args.candidate_id, args.reviewer, args.note,
                                  project_id=args.project, title=args.title)
        else:
            result = inbox.reject(args.candidate_id, args.reviewer, args.reason)
    except (ValueError, OSError, TimeoutError, StorageLimitError) as exc:
        print(json.dumps({'status': 'error', 'error': str(exc)}))
        return 2
    print(json.dumps(result, ensure_ascii=True))
    return 0


if __name__ == '__main__':
    sys.exit(main())
