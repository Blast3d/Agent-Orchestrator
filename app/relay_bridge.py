"""Read-only run summaries and a mailbox between a ChatGPT dot (Relay), the lead and the user.

Relay runs in OpenAI's cloud and reaches this PC only through local Codex threads
while the desktop app is connected. It is a collaborator, not a lead or a
dispatched worker: it can read run state and post messages, but only the
recorded lead dispatches, reviews, checkpoints or transfers. The user party is
Jacob speaking through OpenWhispr's opt-in Relay target.
"""
import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import sys
import uuid

from paths import ROOT, STATE, TASKS
from task_store import timestamp, write_json
from usage_guard import file_lock

PARTIES = ('relay', 'lead', 'user')
# Relay is the hub: the lead and the user each talk only to Relay.
ROUTES = {'relay': ('lead', 'user'), 'lead': ('relay',), 'user': ('relay',)}
KINDS = ('note', 'question', 'request', 'handoff', 'reply')
CLIENT_REF = re.compile(r'^[A-Za-z0-9_-]{8,128}$')
LEADS = ('claude', 'astra', 'sol')
MAX_SUBJECT = 200
MAX_BODY = 16 * 1024
MAX_MAILBOX_BYTES = 5 * 1024 * 1024
MAX_TASK_RECORDS = 2000
RUN_ID = re.compile(r'^[A-Za-z0-9][A-Za-z0-9._-]{0,159}$')
# Same thresholds as the Orchestrator Viewer's run picker.
CURRENT_SECONDS = 24 * 3600
RECENT_SECONDS = 7 * 24 * 3600
FINISHED = {'completed', 'complete', 'closed', 'cancelled', 'canceled', 'abandoned', 'failed', 'archived'}
CHECKPOINT_TAIL = 8


def relay_dir():
    return STATE / 'relay'


def default_workspaces():
    """The Orchestrator itself plus extra project roots from RELAY_WORKSPACES (os.pathsep-separated)."""
    extra = [Path(p) for p in os.environ.get('RELAY_WORKSPACES', '').split(os.pathsep) if p.strip()]
    return [ROOT, *extra]


def _object(path, maximum=1024 * 1024):
    with Path(path).open('rb') as stream:
        data = stream.read(maximum + 1)
    if len(data) > maximum:
        raise ValueError(f'{path.name} exceeds the bridge read limit')
    value = json.loads(data)
    if not isinstance(value, dict):
        raise ValueError(f'{path.name} is not an object')
    return value


def _time(value):
    try:
        parsed = datetime.fromisoformat(str(value).replace('Z', '+00:00'))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def _modified(path):
    try:
        return datetime.fromtimestamp(path.stat().st_mtime, timezone.utc)
    except OSError:
        return None


def _group(manifest, last, now):
    if str(manifest.get('project_id') or '').startswith('experiment-') or manifest.get('experiment_parent_run_id'):
        return 'experiments'
    age = (now - last).total_seconds() if last else None
    if str(manifest.get('status', '')).lower() not in FINISHED and age is not None and age <= CURRENT_SECONDS:
        return 'current'
    return 'recent' if age is not None and age <= RECENT_SECONDS else 'archived'


def _contained(base, path):
    """Resolve path and refuse links or junctions that lead outside base."""
    resolved = Path(path).resolve()
    if not resolved.is_relative_to(Path(base).resolve()):
        raise ValueError(f'{Path(path).name} points outside its run folder')
    return resolved


def _run_dirs(workspaces):
    seen = set()
    for workspace in workspaces:
        parent = Path(workspace) / '.orchestration'
        if not parent.is_dir():
            continue
        for path in parent.glob('*/coordinator.json'):
            if path.parent.name not in seen:
                seen.add(path.parent.name)
                yield Path(workspace), path.parent


def _summary(workspace, run_dir, now):
    _contained(Path(workspace) / '.orchestration', run_dir)
    state = _object(_contained(run_dir, run_dir / 'coordinator.json'))
    manifest = _object(_contained(run_dir, run_dir / 'run.json')) if (run_dir / 'run.json').exists() else {}
    checkpoint = state.get('checkpoint') if isinstance(state.get('checkpoint'), dict) else {}
    times = [t for t in (_time(state.get('checkpoint_at')), _modified(run_dir / 'coordinator.json')) if t]
    last = max(times) if times else None
    objective = str(checkpoint.get('objective') or manifest.get('objective') or '').strip()
    return {'id': run_dir.name, 'workspace': str(workspace), 'title': objective.splitlines()[0][:160] if objective else '',
            'lead_owner': state.get('owner'), 'generation': state.get('generation'),
            'coordinator_status': state.get('status'), 'run_status': manifest.get('status', 'unknown'),
            'project_id': manifest.get('project_id'), 'last_activity_at': last.isoformat() if last else None,
            'group': _group(manifest, last, now)}


def overview(workspaces=None, limit=15, include_archived=False, now=None):
    """Lead switch plus the newest runs; experiments and (by default) archived runs are left out."""
    now = now or datetime.now(timezone.utc)
    rows, errors = [], []
    for workspace, run_dir in _run_dirs(workspaces or default_workspaces()):
        try:
            rows.append(_summary(workspace, run_dir, now))
        except (OSError, ValueError, TypeError):
            errors.append({'id': run_dir.name, 'error': 'Saved run state could not be read'})
    rows = [r for r in rows if r['group'] != 'experiments' and (include_archived or r['group'] != 'archived')]
    rows.sort(key=lambda r: r['last_activity_at'] or '', reverse=True)
    try:
        from lead_selection import describe
        switch = {k: v for k, v in describe().items() if k in ('lead', 'runner_up', 'selected_at')}
    except (OSError, ValueError):
        switch = {'lead': 'unknown'}
    return {'lead_switch': switch, 'runs': rows[:max(1, min(int(limit), 100))], 'errors': errors,
            'checked_at': now.isoformat(), 'model_calls': 0}


def _find_run(run_id, workspaces):
    if not isinstance(run_id, str) or not RUN_ID.fullmatch(run_id):
        raise ValueError('run_id must be an exact run folder name')
    for workspace in workspaces:
        run_dir = Path(workspace) / '.orchestration' / run_id
        if (run_dir / 'coordinator.json').is_file():
            _contained(Path(workspace) / '.orchestration', run_dir)
            return Path(workspace), run_dir
    raise ValueError(f'No run named {run_id} in the bridged workspaces')


def _tasks_for(run_id):
    """Linked task summaries and whether the record scan hit its bound."""
    tasks = []
    for index, path in enumerate(TASKS.glob('*/record.json')):
        if index >= MAX_TASK_RECORDS:
            return sorted(tasks, key=lambda t: str(t.get('created_at') or '')), True
        try:
            job = _object(path)
        except (OSError, ValueError):
            continue
        if job.get('run_id') == run_id:
            tasks.append({k: job.get(k) for k in ('job_id', 'task', 'worker', 'status', 'execution_status',
                                                   'review_status', 'created_at')})
    return sorted(tasks, key=lambda t: str(t.get('created_at') or '')), False


def run_status(run_id, workspaces=None, now=None):
    """One run's lead, checkpoint, linked tasks, brief excerpt and deliverable names."""
    now = now or datetime.now(timezone.utc)
    workspace, run_dir = _find_run(run_id, workspaces or default_workspaces())
    state = _object(_contained(run_dir, run_dir / 'coordinator.json'))
    checkpoint = state.get('checkpoint') if isinstance(state.get('checkpoint'), dict) else {}
    tail = {k: list(checkpoint.get(k) or [])[-CHECKPOINT_TAIL:]
            for k in ('completed', 'next_steps', 'decisions', 'constraints', 'validation', 'artifacts')}
    brief = run_dir / 'brief.md'
    deliverables = run_dir / 'deliverables'
    tasks, truncated = _tasks_for(run_id)
    excerpt = None
    if brief.is_file():
        with _contained(run_dir, brief).open('rb') as stream:
            excerpt = stream.read(4000).decode('utf-8', errors='replace')
    if deliverables.is_dir():
        _contained(run_dir, deliverables)
    return {**_summary(workspace, run_dir, now), 'objective': checkpoint.get('objective'),
            'checkpoint_at': state.get('checkpoint_at'), 'checkpoint': tail,
            'open_jobs': len(checkpoint.get('open_jobs') or []),
            'handoff': {k: state.get('handoff', {}).get(k) for k in ('id', 'to', 'reason', 'prepared_at')}
            if isinstance(state.get('handoff'), dict) else None,
            'tasks': tasks, 'tasks_truncated': truncated, 'brief_excerpt': excerpt,
            'deliverables': sorted(p.name for p in deliverables.iterdir())[:50] if deliverables.is_dir() else [],
            'path': str(run_dir), 'model_calls': 0}


def _clean(value, name, maximum):
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f'{name} is required')
    if len(value) > maximum:
        raise ValueError(f'{name} exceeds {maximum} characters')
    return value.strip()


def _read_mailbox(path):
    """Well-formed messages only; a mailbox past the write cap plus one message is refused.

    Sequence numbers follow file order: a line whose seq does not exceed the previous
    one (an append made without the lock) takes the next number. OpenWhispr's
    relayTarget.readMailbox applies the same rule to the same lines.
    """
    if not path.exists():
        return []
    if path.stat().st_size > MAX_MAILBOX_BYTES + 4 * MAX_BODY:
        raise ValueError('Relay mailbox exceeds its size limit; archive runtime/relay/mailbox.jsonl')
    messages, last = [], 0
    for line in path.read_text(encoding='utf-8', errors='replace').splitlines():
        try:
            message = json.loads(line) if line.strip() else None
        except ValueError:
            continue
        seq = message.get('seq') if isinstance(message, dict) else None
        if not isinstance(seq, int) or isinstance(seq, bool):
            continue
        last = max(seq, last + 1)
        message['seq'] = last
        if (message.get('to') in PARTIES and isinstance(message.get('subject'), str)
                and isinstance(message.get('body'), str)):
            messages.append(message)
    return messages


def post(sender, subject, body, *, to=None, kind='note', run_id=None, lead=None, reply_to=None,
         client_ref=None, root=None):
    """Append one message. Relay addresses the lead (default) or the user; both of them address Relay.

    A repeated client_ref from the same sender returns the original only when all
    semantic fields match. Reusing an operation identity for different content is
    an error, never a silently accepted new message. CLI callers may still omit it.
    """
    if sender not in PARTIES:
        raise ValueError('sender must be relay, lead or user')
    to = to or ROUTES[sender][0]
    if to not in ROUTES[sender]:
        raise ValueError(f'{sender} messages can only go to ' + ' or '.join(ROUTES[sender]))
    if kind not in KINDS:
        raise ValueError('kind must be one of ' + ', '.join(KINDS))
    if lead is not None and (sender != 'lead' or lead not in LEADS):
        raise ValueError('lead identity applies only to lead messages: claude, astra or sol')
    if run_id is not None and not RUN_ID.fullmatch(str(run_id)):
        raise ValueError('run_id must be an exact run folder name')
    if client_ref is not None and (not isinstance(client_ref, str) or not CLIENT_REF.fullmatch(client_ref)):
        raise ValueError('client_ref must be 8 to 128 letters, digits, - or _')
    message = {'id': uuid.uuid4().hex[:12], 'at': timestamp(), 'from': sender, 'to': to, 'kind': kind,
               'subject': _clean(subject, 'subject', MAX_SUBJECT), 'body': _clean(body, 'body', MAX_BODY)}
    if lead:
        message['lead'] = lead
    if run_id:
        message['run_id'] = run_id
    if reply_to:
        message['reply_to'] = _clean(reply_to, 'reply_to', 64)
    if client_ref:
        message['client_ref'] = client_ref
    base = Path(root) if root else relay_dir()
    path = base / 'mailbox.jsonl'
    with file_lock(base / 'mailbox.lock'):
        existing = _read_mailbox(path)
        if client_ref:
            earlier = next((m for m in existing if m.get('from') == sender and m.get('client_ref') == client_ref), None)
            if earlier:
                fields = ('from', 'to', 'kind', 'subject', 'body', 'lead', 'run_id', 'reply_to')
                if any(earlier.get(field) != message.get(field) for field in fields):
                    raise ValueError('client_ref already belongs to a different message; use a new reference for a new operation')
                return dict(earlier, duplicate=True)
        if path.exists() and path.stat().st_size > MAX_MAILBOX_BYTES:
            raise ValueError('Relay mailbox is full; archive runtime/relay/mailbox.jsonl first')
        message['seq'] = max((m['seq'] for m in existing), default=0) + 1
        with path.open('a', encoding='utf-8', newline='\n') as stream:
            stream.write(json.dumps(message, ensure_ascii=False) + '\n')
    return message


def _read_state(cursors, reader):
    """(cursor, read seqs above it) for one reader; older integer cursors read as zero."""
    saved = cursors.get(reader, {})
    if not isinstance(saved, dict):
        return 0, set()
    return int(saved.get('cursor', 0)), {int(seq) for seq in saved.get('read', [])}


def pending_for_run(run_id, *, reader='lead', limit=10, excerpt=400, root=None):
    """Unread messages for reader scoped to one exact run, without changing any read state.

    Used by startup packets and closeout. Listing is not reading, accepting or executing.
    """
    if not isinstance(run_id, str) or not RUN_ID.fullmatch(run_id):
        raise ValueError('run_id must be an exact run folder name')
    base = Path(root) if root else relay_dir()
    empty = {'run_id': run_id, 'messages': [], 'more': 0, 'unscoped_unread': 0, 'marks_read': False}
    if not (base / 'mailbox.jsonl').exists():
        return empty
    with file_lock(base / 'mailbox.lock'):
        cursors = _object(base / 'cursors.json') if (base / 'cursors.json').exists() else {}
        cursor, read = _read_state(cursors, reader)
        unread = [m for m in _read_mailbox(base / 'mailbox.jsonl')
                  if m['to'] == reader and m['seq'] > cursor and m['seq'] not in read]
    scoped = [m for m in unread if m.get('run_id') == run_id]
    return {**empty, 'more': max(0, len(scoped) - limit),
            'unscoped_unread': sum(1 for m in unread if not m.get('run_id')),
            'messages': [{'id': m.get('id'), 'seq': m['seq'], 'from': m.get('from'), 'kind': m.get('kind'),
                          'at': m.get('at'), 'subject': m['subject'][:MAX_SUBJECT],
                          'excerpt': ' '.join(m['body'].split())[:excerpt]} for m in scoped[:limit]]}


def _mark_read(cursors_path, cursors, reader, addressed, received):
    cursor, read = _read_state(cursors, reader)
    read |= {m['seq'] for m in received}
    for seq in sorted(m['seq'] for m in addressed if m['seq'] > cursor):
        if seq not in read:
            break
        cursor = seq
    cursors[reader] = {'cursor': cursor, 'read': sorted(s for s in read if s > cursor)}
    write_json(cursors_path, cursors)


def acknowledge(reader, message_ids, *, root=None):
    """Acknowledge exact message IDs after the client has received them. Safe to retry."""
    if reader not in PARTIES:
        raise ValueError('reader must be relay, lead or user')
    if (not isinstance(message_ids, list) or not 1 <= len(message_ids) <= 200 or
            any(not isinstance(ident, str) or not re.fullmatch(r'[a-zA-Z0-9_-]{6,64}', ident)
                for ident in message_ids)):
        raise ValueError('message_ids must contain 1 to 200 valid message IDs')
    wanted = set(message_ids)
    base = Path(root) if root else relay_dir()
    with file_lock(base / 'mailbox.lock'):
        addressed = [m for m in _read_mailbox(base / 'mailbox.jsonl') if m['to'] == reader]
        received = [m for m in addressed if m.get('id') in wanted]
        if {m.get('id') for m in received} != wanted:
            raise ValueError('Every acknowledged message must exist and be addressed to this reader')
        cursors_path = base / 'cursors.json'
        cursors = _object(cursors_path) if cursors_path.exists() else {}
        _mark_read(cursors_path, cursors, reader, addressed, received)
    return {'reader': reader, 'acknowledged_ids': sorted(wanted), 'count': len(wanted)}


def inbox(reader, *, unread_only=True, mark_read=False, limit=50, run_id=None, root=None):
    """Messages addressed to reader. Peeks by default; acknowledge IDs after receipt.

    Read state per reader is a cursor (everything at or below it is read) plus the
    read sequence numbers above it; the cursor advances over contiguous read messages.
    """
    if reader not in PARTIES:
        raise ValueError('reader must be relay, lead or user')
    base = Path(root) if root else relay_dir()
    with file_lock(base / 'mailbox.lock'):
        cursors_path = base / 'cursors.json'
        cursors = _object(cursors_path) if cursors_path.exists() else {}
        cursor, read = _read_state(cursors, reader)
        addressed = [m for m in _read_mailbox(base / 'mailbox.jsonl') if m['to'] == reader]
        mine = [m for m in addressed
                if (not unread_only or (m['seq'] > cursor and m['seq'] not in read))
                and (run_id is None or m.get('run_id') == run_id)][:max(1, min(int(limit), 200))]
        if mark_read and mine:
            _mark_read(cursors_path, cursors, reader, addressed, mine)
    return {'reader': reader, 'messages': mine, 'count': len(mine)}


def thread(limit=40, run_id=None, root=None):
    """Both directions, newest last, without touching read cursors."""
    base = Path(root) if root else relay_dir()
    messages = [m for m in _read_mailbox(base / 'mailbox.jsonl') if run_id is None or m.get('run_id') == run_id]
    return {'messages': messages[-max(1, min(int(limit), 200)):], 'model_calls': 0}


def _bullets(items):
    return ''.join(f'  - {str(item)[:400]}\n' for item in items) or '  - (none)\n'


def handoff_markdown(workspaces=None, root=None, now=None):
    """Plain-language snapshot for Relay: who leads, what is active, what is waiting for Relay."""
    workspaces = workspaces or default_workspaces()
    view = overview(workspaces, now=now)
    lines = ['# Relay handoff', '',
             f"Generated {view['checked_at']} from the local Agent Orchestrator. Read-only snapshot; "
             'the recorded lead of each run coordinates it.', '',
             f"Lead switch for new runs: **{view['lead_switch'].get('lead')}** "
             f"(runner-up {view['lead_switch'].get('runner_up', 'unknown')}).", '', '## Runs', '']
    if not view['runs']:
        lines.append('No current or recent runs.')
    for row in view['runs']:
        lines.append(f"### {row['id']}  ({row['group']})")
        lines.append(f"- {row['title']}")
        lines.append(f"- Lead owner `{row['lead_owner']}`, generation {row['generation']}, "
                     f"coordinator {row['coordinator_status']}, run {row['run_status']}, last activity {row['last_activity_at']}")
        if row['group'] == 'current':
            try:
                detail = run_status(row['id'], workspaces, now=now)
            except (OSError, ValueError):
                detail = None
            if detail:
                lines.append('- Recently completed:\n' + _bullets(detail['checkpoint']['completed']).rstrip('\n'))
                lines.append('- Next steps:\n' + _bullets(detail['checkpoint']['next_steps']).rstrip('\n'))
                lines.append('- Decisions:\n' + _bullets(detail['checkpoint']['decisions']).rstrip('\n'))
        lines.append('')
    waiting = [m for m in thread(limit=200, root=root)['messages'] if m.get('to') == 'relay'][-10:]
    lines += ['## Latest messages to Relay (from the lead or from Jacob by voice)', '']
    lines += [f"- [{m['seq']}] id {m.get('id')} {m['at']} {m.get('lead') or m.get('from')} ({m['kind']}"
              f"{', run ' + m['run_id'] if m.get('run_id') else ''}): **{m['subject']}** — {m['body'][:600]}"
              for m in waiting] or ['- (none)']
    lines += ['', '## How Relay works with the Orchestrator', '',
              '- Read: MCP tools `relay_overview`, `relay_run_status`, `relay_handoff`, `relay_read_messages` '
              '(server `agentOrchestrator`), or this file. Reads peek; after receipt use `relay_ack_messages` with the received message IDs.',
              '- Write to the lead: `relay_post_to_lead` (note, question, request, handoff or reply), with a stable `client_ref`.',
              '- Answer Jacob\'s voice requests (from `user`): `relay_reply_to_user` with `reply_to` set to his '
              'message id and a stable `client_ref`. Preserve the same reference and payload across retries. '
              'kind `question` keeps the request open for his spoken answer; any other kind is the '
              'final answer that OpenWhispr reads back once.',
              '- Relay does not dispatch workers, accept reviews, edit checkpoints or claim leadership. '
              'Ask the lead with a `request` message instead.', '']
    return '\n'.join(lines)


def write_handoff(workspaces=None, root=None, now=None):
    base = Path(root) if root else relay_dir()
    base.mkdir(parents=True, exist_ok=True)
    path = base / 'RELAY_HANDOFF.md'
    text = handoff_markdown(workspaces, root=base, now=now)
    temporary = path.with_name(path.name + '.' + uuid.uuid4().hex + '.tmp')
    try:
        temporary.write_text(text, encoding='utf-8', newline='\n')
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)
    return {'path': str(path), 'bytes': len(text.encode('utf-8')), 'model_calls': 0}


def main(argv=None):
    parser = argparse.ArgumentParser(prog='orchestrator.py relay', description=__doc__.splitlines()[0])
    parser.add_argument('--workspace', action='append', default=[], help='Extra project root with a .orchestration folder')
    sub = parser.add_subparsers(dest='command', required=True)
    sub.add_parser('overview', help='Lead switch and current/recent runs')
    status = sub.add_parser('run', help='One run in detail')
    status.add_argument('run_id')
    sub.add_parser('handoff', help='Write runtime/relay/RELAY_HANDOFF.md')
    send = sub.add_parser('send', help='Post a message (the lead replies to Relay with --from lead)')
    send.add_argument('--from', dest='sender', choices=PARTIES, default='lead')
    send.add_argument('--to', choices=PARTIES, help='Only Relay chooses: lead (default) or user')
    send.add_argument('--as', dest='lead', choices=LEADS)
    send.add_argument('--kind', choices=KINDS, default='note')
    send.add_argument('--subject', required=True)
    body = send.add_mutually_exclusive_group(required=True)
    body.add_argument('--body')
    body.add_argument('--body-file')
    body.add_argument('--body-stdin', action='store_true', help='Read the UTF-8 body from standard input')
    send.add_argument('--run', dest='run_id')
    send.add_argument('--reply-to')
    send.add_argument('--client-ref', help='Stable operation ID; identical retries return the original, conflicting reuse fails')
    read = sub.add_parser('inbox', help='Unread messages for one party')
    read.add_argument('--for', dest='reader', choices=PARTIES, default='lead')
    read_mode = read.add_mutually_exclusive_group()
    read_mode.add_argument('--peek', action='store_true', help='Do not mark messages read (default)')
    read_mode.add_argument('--mark-read', action='store_true', help='Compatibility mode: mark before returning; prefer ack after receipt')
    read.add_argument('--all', action='store_true', help='Include messages already read')
    read.add_argument('--run', dest='run_id')
    ack = sub.add_parser('ack', help='Acknowledge exact message IDs after receiving them')
    ack.add_argument('--for', dest='reader', choices=PARTIES, default='lead')
    ack.add_argument('--id', dest='message_ids', action='append', required=True)
    log = sub.add_parser('thread', help='Both directions, newest last')
    log.add_argument('--limit', type=int, default=40)
    log.add_argument('--run', dest='run_id')
    args = parser.parse_args(argv)
    workspaces = default_workspaces() + [Path(p) for p in args.workspace]
    if args.command == 'overview':
        result = overview(workspaces)
    elif args.command == 'run':
        result = run_status(args.run_id, workspaces)
    elif args.command == 'handoff':
        result = write_handoff(workspaces)
    elif args.command == 'send':
        if args.body_stdin:
            text = sys.stdin.buffer.read(MAX_BODY * 4 + 1).decode('utf-8')
        else:
            text = Path(args.body_file).read_text(encoding='utf-8') if args.body_file else args.body
        result = post(args.sender, args.subject, text, to=args.to, kind=args.kind, run_id=args.run_id,
                      lead=args.lead if args.sender == 'lead' else None, reply_to=args.reply_to,
                      client_ref=args.client_ref)
    elif args.command == 'inbox':
        result = inbox(args.reader, unread_only=not args.all, mark_read=args.mark_read, run_id=args.run_id)
    elif args.command == 'ack':
        result = acknowledge(args.reader, args.message_ids)
    else:
        result = thread(args.limit, args.run_id)
    print(json.dumps(result, indent=2, ensure_ascii=False))
    return 0


if __name__ == '__main__':
    try:
        sys.exit(main())
    except ValueError as error:
        print(json.dumps({'error': str(error)}), file=sys.stderr)
        sys.exit(2)
