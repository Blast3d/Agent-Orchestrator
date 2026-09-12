"""Bounded, read-only native Codex timing evidence for an exactly bound parent.

The local session index is not a provider heartbeat. Spawn edges stay open after
an agent finishes a turn, and a reusable session can contain several assignments.
Only timing/type fields from lifecycle events leave this module; no conversation
text, reasoning, prompt, provider credentials, or transcript paths are returned.
"""
from __future__ import annotations

from collections import OrderedDict
from datetime import datetime, timezone
import json
import math
import os
from pathlib import Path
import re
import sqlite3
import threading
import time

from task_progress_view import _inspect_file

UUID = re.compile(r'[a-f0-9]{8}(?:-[a-f0-9]{4}){3}-[a-f0-9]{12}')
NAME = re.compile(r'[A-Za-z][A-Za-z0-9_-]{0,99}')
MAX_AGENTS = 32
MAX_TAIL_BYTES = 64 * 1024
MAX_SOURCE_BYTES = 8192
QUERY_SECONDS = .25
LIFECYCLE = frozenset(('task_started', 'task_complete', 'task_completed', 'turn_aborted'))
_CACHE = OrderedDict()
_CACHE_LOCK = threading.Lock()


def _instant(value):
    try:
        if not isinstance(value, str) or len(value) > 64:
            return None
        parsed = datetime.fromisoformat(value.replace('Z', '+00:00'))
        return parsed.astimezone(timezone.utc) if parsed.tzinfo else None
    except (ValueError, OverflowError):
        return None


def _epoch(value, divisor=1):
    try:
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value < 0:
            return None
        return datetime.fromtimestamp(value / divisor, timezone.utc)
    except (ValueError, OverflowError, OSError):
        return None


def _iso(value):
    return value.isoformat() if value else None


def _identity(info):
    return (info.st_dev, info.st_ino)


def _same_components(components):
    try:
        for path, original in components:
            fresh = os.lstat(path)
            if _identity(fresh) != _identity(original) or fresh.st_mode != original.st_mode:
                return False
            if getattr(fresh, 'st_file_attributes', 0) & 0x400:
                return False
        return True
    except OSError:
        return False


def _tail_events(path, sessions, identifier):
    """Cache only sanitized events, keyed by verified file identity and revision."""
    if not isinstance(path, str) or not Path(path).name.endswith('-' + identifier + '.jsonl'):
        return [], 'Saved lifecycle timing does not match the verified native session.'
    candidate, components, problem = _inspect_file(Path(path), sessions)
    if problem:
        return [], 'Saved lifecycle timing is unavailable.'
    info = components[-1][1]
    key = (str(candidate), _identity(info), info.st_size, info.st_mtime_ns)
    with _CACHE_LOCK:
        cached = _CACHE.get(key)
        if cached is not None:
            _CACHE.move_to_end(key)
            return list(cached), None
    try:
        with candidate.open('rb') as stream:
            opened = os.fstat(stream.fileno())
            if _identity(opened) != _identity(info) or not _same_components(components):
                return [], 'Saved lifecycle timing changed during inspection.'
            start = max(0, opened.st_size - MAX_TAIL_BYTES)
            stream.seek(start)
            data = stream.read(min(MAX_TAIL_BYTES, opened.st_size))
            after = os.fstat(stream.fileno())
            if _identity(after) != _identity(info) or not _same_components(components):
                return [], 'Saved lifecycle timing changed during inspection.'
        if start:
            # A bounded tail can begin inside a large event. Never parse its suffix.
            boundary = data.find(b'\n')
            data = data[boundary + 1:] if boundary >= 0 else b''
        events = []
        for line in data.splitlines(keepends=True):
            if not line.endswith(b'\n'):
                continue
            try:
                row = json.loads(line)
            except (ValueError, UnicodeError, RecursionError):
                continue
            if not isinstance(row, dict) or row.get('type') != 'event_msg':
                continue
            payload = row.get('payload')
            if (not isinstance(payload, dict) or not isinstance(payload.get('type'), str)
                    or payload['type'] not in LIFECYCLE):
                continue
            instant = _instant(row.get('timestamp'))
            if instant is None:
                continue
            began = _epoch(payload.get('started_at'))
            if began is None and payload['type'] == 'task_started':
                began = instant
            ended = _epoch(payload.get('completed_at'))
            duration = payload.get('duration_ms')
            try:
                valid_duration = (not isinstance(duration, bool) and isinstance(duration, (int, float))
                    and math.isfinite(duration) and 0 <= duration <= 365 * 86400 * 1000)
            except OverflowError:
                valid_duration = False
            events.append({'timestamp': instant, 'type': payload['type'], 'started_at': began,
                'completed_at': ended, 'duration_seconds': round(duration / 1000, 3) if valid_duration else None})
        events = sorted(events, key=lambda row: row['timestamp'])[-128:]
        # A growing file is useful evidence but must be read again on the next poll.
        if (after.st_size, after.st_mtime_ns) == (info.st_size, info.st_mtime_ns):
            with _CACHE_LOCK:
                _CACHE[key] = tuple(events)
                _CACHE.move_to_end(key)
                while len(_CACHE) > 128:
                    _CACHE.popitem(last=False)
        return events, None
    except (OSError, ValueError, TypeError):
        return [], 'Saved lifecycle timing is unavailable.'


def _database_rows(home, parent, names):
    base = Path(home) / '.codex'
    path, components, problem = _inspect_file(base / 'state_5.sqlite', base)
    if problem:
        return None, 'Native session metadata is unavailable.'
    # SQLite may read these companions even in read-only mode. Reject redirects.
    companion_components = []
    for suffix in ('-wal', '-shm', '-journal'):
        _, inspected, companion_problem = _inspect_file(Path(str(path) + suffix), base)
        if companion_problem not in (None, 'missing'):
            return None, 'Native session metadata is unavailable.'
        companion_components.extend(inspected or [])
    connection = None
    try:
        connection = sqlite3.connect(path.as_uri() + '?mode=ro', uri=True, timeout=QUERY_SECONDS)
        connection.execute('PRAGMA query_only=ON')
        deadline = time.monotonic() + QUERY_SECONDS
        connection.set_progress_handler(lambda: int(time.monotonic() > deadline), 1000)
        required = {'threads': {'id', 'rollout_path', 'source', 'agent_path', 'created_at_ms', 'updated_at_ms'},
            'thread_spawn_edges': {'parent_thread_id', 'child_thread_id', 'status'}}
        for table, columns in required.items():
            available = {row[1] for row in connection.execute('PRAGMA table_info(' + table + ')')}
            if not columns.issubset(available):
                return None, 'Native session metadata uses an unsupported schema.'
        placeholders = ','.join('?' for _ in names)
        rows = connection.execute('SELECT t.id,t.agent_path,t.created_at_ms,t.updated_at_ms,t.source,t.rollout_path '
            'FROM threads t JOIN thread_spawn_edges e ON t.id=e.child_thread_id '
            'WHERE e.parent_thread_id=? AND t.agent_path IN (' + placeholders + ') '
            'AND length(t.source)<=? AND length(t.rollout_path)<=4096 LIMIT ?',
            [parent, *('/root/' + name for name in names), MAX_SOURCE_BYTES, MAX_AGENTS * 2 + 1]).fetchall()
        if not _same_components(components + companion_components):
            return None, 'Native session metadata changed during inspection.'
        if len(rows) > MAX_AGENTS * 2:
            return None, 'Native session metadata exceeded the bounded lookup.'
        return rows, None
    except (sqlite3.Error, OSError, ValueError):
        return None, 'Native session metadata is unavailable or busy.'
    finally:
        if connection is not None:
            connection.close()


def snapshot(home, parent_session_id, names, run_started_at=None, run_ended_at=None):
    """Return safe timing metadata by child name, never guess a parent or session.

    Run bounds exclude follow-up work from an older run. A session beginning
    before this run contributes only an explicitly timed turn inside the bounds.
    A tail is deliberately incomplete; its last turn duration is not total work.
    """
    output = {'agents': {}, 'status': 'unavailable',
        'checked_at': datetime.now(timezone.utc).isoformat(), 'reason': None}
    if not isinstance(parent_session_id, str) or not UUID.fullmatch(parent_session_id):
        output['reason'] = 'Bind the exact native parent session to read worker timing.'
        return output
    if not isinstance(names, (list, tuple, set)) or len(names) > MAX_AGENTS:
        output['reason'] = 'Native worker timing exceeded the bounded lookup.'
        return output
    requested = sorted({name for name in names if isinstance(name, str) and NAME.fullmatch(name)})
    if not requested:
        output['status'] = 'available'
        return output
    lower, upper = _instant(run_started_at), _instant(run_ended_at)
    if ((run_started_at is not None and lower is None) or (run_ended_at is not None and upper is None)
            or (lower and upper and upper < lower)):
        output['reason'] = 'Selected run timing bounds are invalid.'
        return output
    rows, error = _database_rows(home, parent_session_id, requested)
    if error:
        output['reason'] = error
        return output
    candidates = {}
    for identifier, agent_path, created_ms, updated_ms, source_text, rollout_path in rows:
        if not isinstance(identifier, str) or not UUID.fullmatch(identifier):
            continue
        try:
            source = json.loads(source_text)['subagent']['thread_spawn']
            if source.get('parent_thread_id') != parent_session_id or source.get('agent_path') != agent_path:
                continue
        except (ValueError, TypeError, KeyError, AttributeError, RecursionError):
            continue
        name = agent_path.removeprefix('/root/')
        candidates.setdefault(name, []).append((identifier, created_ms, updated_ms, rollout_path))
    for name in requested:
        matches = candidates.get(name, [])
        if len(matches) != 1:
            continue
        identifier, created_ms, updated_ms, path = matches[0]
        created, saved = _epoch(created_ms, 1000), _epoch(updated_ms, 1000)
        if created is None or saved is None or saved < created:
            continue
        if (upper and created > upper) or (lower and saved < lower):
            continue
        events, tail_error = _tail_events(path, Path(home) / '.codex' / 'sessions', identifier)
        events = [event for event in events if (not lower or event['timestamp'] >= lower)
            and (not upper or event['timestamp'] <= upper) and event['timestamp'] >= created]
        latest = events[-1] if events else None
        # Prefer exact event times. Saved index time is bookkeeping, not a heartbeat.
        bounded_saved = saved if (not lower or saved >= lower) and (not upper or saved <= upper) else None
        updated = max(filter(None, (bounded_saved, latest['timestamp'] if latest else None)), default=None)
        started = created if not lower or created >= lower else None
        elapsed_label = 'Session span (includes pauses)'
        latest_started = latest['started_at'] if latest else None
        latest_completed = latest['completed_at'] if latest else None
        latest_duration = latest['duration_seconds'] if latest else None
        if latest_started and ((lower and latest_started < lower) or latest_started < created
                or latest_started > latest['timestamp']):
            latest_started, latest_completed, latest_duration = None, None, None
        if latest_completed and (latest_started is None or latest_completed < latest_started
                or (upper and latest_completed > upper) or latest_completed > latest['timestamp']):
            latest_completed, latest_duration = None, None
        if latest_started is None or latest_completed is None:
            latest_duration = None
        if started is None and latest_started:
            started = latest_started
            elapsed_label = 'Latest turn span (includes waits)'
        elapsed = max(0, (updated - started).total_seconds()) if updated and started else None
        state = {'task_started': 'active', 'task_complete': 'idle',
            'task_completed': 'idle', 'turn_aborted': 'interrupted'}.get(latest['type'] if latest else None, 'unknown')
        reason = 'Saved native session timing; session span includes pauses and follow-up turns. This is not a live heartbeat.'
        if latest and state == 'idle':
            reason += ' The latest recorded turn completed; the session may be reused.'
        if lower and created < lower:
            reason = 'This native session was reused; timing covers only the latest recorded turn inside this run. This is not total task time or a live heartbeat.'
        if updated is None:
            reason = 'No saved native timing could be attributed to this run within its bounds.'
        output['agents'][name] = {'source': 'codex_local_metadata', 'started_at': _iso(started),
            'updated_at': _iso(updated), 'elapsed_seconds': round(elapsed, 1) if elapsed is not None else None,
            'elapsed_label': elapsed_label, 'deadline_at': None,
            'session_started_at': _iso(created), 'session_updated_at': _iso(bounded_saved),
            'last_turn_started_at': _iso(latest_started), 'last_turn_completed_at': _iso(latest_completed),
            'last_turn_duration_seconds': latest_duration, 'last_event_at': _iso(latest['timestamp']) if latest else None,
            'last_event': latest['type'] if latest else None, 'lifecycle_state': state,
            'reason': reason, 'warning': tail_error}
    output['status'] = 'available' if len(output['agents']) == len(requested) else 'partial'
    if output['status'] == 'partial':
        output['reason'] = 'Some native workers have no unique, verified child session for this parent and run.'
    return output
