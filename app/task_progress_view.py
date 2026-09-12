"""Read-only saved progress, preview, and handoff lineage for the task inbox."""
from __future__ import annotations

import json
import math
import os
import re
import stat
from datetime import datetime, timezone
from pathlib import Path

PROGRESS_MAX_BYTES = 64 * 1024
PREVIEW_MAX_BYTES = 128 * 1024
PREVIEW_DISPLAY_CHARS = 4000
MAX_HANDOFF_DEPTH = 8
_REPARSE = 0x400
_HEX = re.compile(r'^[a-f0-9]{32}$')
_SAFE_MODEL = re.compile(r'^[A-Za-z0-9._-]{1,100}$')
_SIZES = frozenset({'tiny', 'small', 'medium', 'large'})
_STATES = frozenset({
    'starting', 'waiting', 'thinking', 'answering', 'retrying', 'finished', 'failed',
})
_PROCESS = frozenset({
    'starting', 'running', 'exited', 'timeout', 'interrupted', 'process_io_error',
    'missing_terminal_result', 'output_limit',
})
_OUTPUT = frozenset({'stream-json', 'buffered-json'})
_INT_FIELDS = frozenset({
    'event_count', 'answer_chars', 'retry_count', 'malformed_events',
    'last_retry_status', 'stdout_chars', 'stderr_chars',
    'retained_answer_chars', 'observed_answer_chars',
})
_FLOAT_FIELDS = frozenset({
    'first_event_s', 'first_answer_s', 'last_event_s', 'elapsed_s',
    'timeout_seconds', 'last_retry_delay_ms',
})
_BOOL_FIELDS = frozenset({
    'terminal_received', 'preview_truncated', 'output_truncated', 'stderr_truncated',
})
_SAFE_PROGRESS_KEYS = (
    _INT_FIELDS | _FLOAT_FIELDS | _BOOL_FIELDS | {'state', 'model', 'process_status', 'output_mode'}
)


def _finite_nonnegative(value):
    try:
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            return False
        return math.isfinite(value) and value >= 0
    except OverflowError:
        return False


def _finite_positive(value):
    try:
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            return False
        return math.isfinite(value) and value > 0
    except OverflowError:
        return False


def _time(value):
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value.replace('Z', '+00:00'))
        if parsed.tzinfo is None:
            return None
        return parsed.astimezone(timezone.utc).isoformat()
    except (ValueError, OverflowError):
        return None


def _text(value, default=''):
    return value if isinstance(value, str) else default


def _canonical_size(data):
    if not isinstance(data, dict):
        return None
    size = data.get('size')
    if isinstance(size, str) and size in _SIZES:
        return size
    return None


def _canonical_timeout(data):
    if not isinstance(data, dict):
        return None
    value = data.get('timeout_seconds')
    if _finite_positive(value):
        return float(value) if isinstance(value, float) else value
    return None


def _is_redirected(path: Path) -> bool:
    """Missing ordinary paths are not redirects; dangling links still are."""
    try:
        info = os.lstat(path)
        return stat.S_ISLNK(info.st_mode) or bool(getattr(info, 'st_file_attributes', 0) & _REPARSE)
    except FileNotFoundError:
        return False
    except (OSError, ValueError):
        return True


def _inspect_file(path: Path, root: Path):
    """Inspect lexical containment and every component without following links."""
    try:
        candidate = Path(os.path.abspath(path))
        boundary = Path(os.path.abspath(root))
        candidate.relative_to(boundary)
        components = []
        for component in (*reversed(candidate.parents), candidate):
            info = os.lstat(component)
            if stat.S_ISLNK(info.st_mode) or getattr(info, 'st_file_attributes', 0) & _REPARSE:
                return None, None, 'redirected'
            if component != candidate and not stat.S_ISDIR(info.st_mode):
                return None, None, 'unreadable'
            components.append((component, info))
        if not stat.S_ISREG(components[-1][1].st_mode):
            return None, None, 'unreadable'
        return candidate, components, None
    except FileNotFoundError:
        return None, None, 'missing'
    except (OSError, ValueError):
        return None, None, 'unreadable'


def _contained_file(root: Path, *parts: str):
    candidate, _, problem = _inspect_file(root.joinpath(*parts), root)
    return candidate, problem


def _same_file(left, right) -> bool:
    try:
        return (left.st_dev, left.st_ino) == (right.st_dev, right.st_ino) and left.st_ino != 0
    except (OSError, ValueError, AttributeError):
        return False


def _bounded_read(path: Path, limit: int, root: Path | None = None) -> tuple[bytes | None, bool, str | None]:
    unsafe = 'The saved file could not be read safely.'
    boundary = root if root is not None else path.parent
    try:
        checked, before, problem = _inspect_file(path, boundary)
        if problem or type(limit) is not int or limit <= 0:
            return None, False, unsafe
        expected = before[-1][1]
        with open(checked, 'rb') as handle:
            opened = os.fstat(handle.fileno())
            if not stat.S_ISREG(opened.st_mode) or not _same_file(expected, opened):
                return None, False, unsafe
            data = handle.read(limit + 1)
            finished = os.fstat(handle.fileno())
        _, after, problem = _inspect_file(path, boundary)
        if problem or len(before) != len(after):
            return None, False, unsafe
        if any(a != b or not _same_file(old, new) for (a, old), (b, new) in zip(before, after)):
            return None, False, unsafe
        current = after[-1][1]
        if (not _same_file(opened, finished) or
                any((entry.st_size, entry.st_mtime_ns) != (expected.st_size, expected.st_mtime_ns)
                    for entry in (opened, finished, current))):
            return None, False, unsafe
        return data[:limit], len(data) > limit or expected.st_size > limit, None
    except (OSError, ValueError):
        return None, False, unsafe


def _mtime(path: Path):
    try:
        _, observations, problem = _inspect_file(path, path.parent)
        if problem:
            return None
        return datetime.fromtimestamp(observations[-1][1].st_mtime, timezone.utc).isoformat()
    except (OSError, ValueError, OverflowError):
        return None


def _clean_progress(raw: dict) -> tuple[dict, list[str]]:
    notices = []
    cleaned = {}
    for key, value in raw.items():
        if key not in _SAFE_PROGRESS_KEYS:
            continue
        if key == 'state':
            if isinstance(value, str) and value in _STATES:
                cleaned[key] = value
            else:
                notices.append('A saved progress state was not recognized.')
        elif key == 'model':
            if isinstance(value, str) and _SAFE_MODEL.fullmatch(value):
                cleaned[key] = value
        elif key == 'process_status':
            if isinstance(value, str) and value in _PROCESS:
                cleaned[key] = value
            else:
                notices.append('A saved process status was not recognized.')
        elif key == 'output_mode':
            if isinstance(value, str) and value in _OUTPUT:
                cleaned[key] = value
        elif key in _BOOL_FIELDS:
            if isinstance(value, bool):
                cleaned[key] = value
            else:
                notices.append('A saved progress flag was not a true or false value.')
        elif key in _INT_FIELDS:
            if value is None:
                continue  # Existing progress snapshots use null for unobserved events.
            if isinstance(value, int) and not isinstance(value, bool) and _finite_nonnegative(value):
                cleaned[key] = value
            else:
                notices.append('A saved progress count was not a usable number.')
        elif key in _FLOAT_FIELDS:
            if value is None:
                continue
            if key == 'timeout_seconds':
                if _finite_positive(value):
                    cleaned[key] = float(value) if isinstance(value, float) else value
                else:
                    notices.append('A saved progress measurement was not a usable number.')
            elif _finite_nonnegative(value):
                cleaned[key] = float(value) if isinstance(value, float) else value
            else:
                notices.append('A saved progress measurement was not a usable number.')
    return cleaned, notices


def _empty_progress():
    return {
        'present': False,
        'incomplete': True,
        'saved_at': None,
        'timeout_known': False,
    }


def _read_progress(job_id: str, workspaces_root: Path, notices: list[str]) -> dict:
    empty = _empty_progress()
    path, problem = _contained_file(workspaces_root, 'tasks', job_id, 'execution-progress.json')
    if path is None:
        if problem != 'missing':
            notices.append('Saved worker progress could not be read safely. Ask Codex to inspect the original task folder.')
        return empty
    payload, oversized, error = _bounded_read(path, PROGRESS_MAX_BYTES, Path(workspaces_root))
    if error:
        notices.append(error)
        return empty
    if payload is None:
        notices.append('The saved progress file could not be read.')
        return empty
    if oversized:
        notices.append('The saved progress file is larger than this view will open. Ask Codex to inspect the original evidence.')
        return {**empty, 'saved_at': _mtime(path)}
    try:
        text = payload.decode('utf-8-sig')
        parsed = json.loads(text)
    except (UnicodeDecodeError, ValueError, RecursionError, TypeError):
        notices.append('The saved progress summary could not be read.')
        return {**empty, 'saved_at': _mtime(path)}
    if not isinstance(parsed, dict):
        notices.append('The saved progress summary could not be read.')
        return {**empty, 'saved_at': _mtime(path)}
    cleaned, field_notices = _clean_progress(parsed)
    notices.extend(field_notices)
    timeout_known = 'timeout_seconds' in cleaned
    result = {
        'present': True,
        'incomplete': True,
        'saved_at': _mtime(path),
        'timeout_known': timeout_known,
    }
    result.update(cleaned)
    return result


def _read_preview(job_id: str, workspaces_root: Path, notices: list[str]) -> dict:
    preview = {
        'text': '',
        'truncated': False,
        'incomplete': True,
        'saved_at': None,
        'chars': 0,
    }
    path, problem = _contained_file(workspaces_root, 'tasks', job_id, 'partial-response.txt')
    if path is None:
        if problem != 'missing':
            notices.append('A saved draft answer could not be read safely.')
        return preview
    payload, oversized, error = _bounded_read(path, PREVIEW_MAX_BYTES, Path(workspaces_root))
    if error:
        notices.append(error if 'draft' in error.lower() else 'A saved draft answer could not be read safely.')
        return preview
    if payload is None:
        notices.append('The saved draft answer could not be read.')
        return preview
    if oversized:
        notices.append('The saved draft answer is larger than this view will open. The text below is an incomplete excerpt.')
    try:
        text = payload.decode('utf-8-sig', errors='replace')
    except (OSError, ValueError, UnicodeDecodeError):
        notices.append('The saved draft answer could not be read.')
        return preview
    chars = len(text)
    truncated = chars > PREVIEW_DISPLAY_CHARS or oversized
    display = text[:PREVIEW_DISPLAY_CHARS]
    preview.update({
        'text': display,
        'truncated': truncated,
        'incomplete': True,
        'saved_at': _mtime(path),
        'chars': min(chars, PREVIEW_DISPLAY_CHARS),
    })
    return preview


def _load_canonical_named(folder: Path, name: str, job_id: str):
    path, problem = _contained_file(folder.parent, folder.name, name)
    if path is None:
        return None, problem
    try:
        payload, oversized, error = _bounded_read(path, PROGRESS_MAX_BYTES * 8, folder.parent)
        if error or payload is None or oversized:
            return None, 'unreadable'
        data = json.loads(payload.decode('utf-8-sig'))
    except (OSError, ValueError, TypeError, RecursionError, UnicodeDecodeError):
        return None, 'unreadable'
    if not isinstance(data, dict) or data.get('job_id') != job_id or not isinstance(data.get('status'), str):
        return None, 'invalid'
    return data, None


def _safe_canonical(folder: Path, job_id: str):
    result, result_problem = _load_canonical_named(folder, 'result.json', job_id)
    record, record_problem = _load_canonical_named(folder, 'record.json', job_id)
    if result_problem == 'redirected' or record_problem == 'redirected':
        return None, None, 'redirected'
    if result_problem in ('unreadable', 'invalid'):
        return None, None, 'result_invalid'
    if result_problem == 'missing':
        if record_problem == 'missing':
            return None, None, 'missing'
        if record_problem is None and record is not None:
            return None, record, 'index_only'
        if record_problem in ('unreadable', 'invalid'):
            return None, None, 'unreadable'
        return None, None, 'unreadable'
    return result, record, None


def _handoff_id(data):
    value = data.get('handoff_from_job_id') if isinstance(data, dict) else None
    if isinstance(value, str) and _HEX.fullmatch(value):
        return value
    return None


def _project_token(data):
    if not isinstance(data, dict):
        return None
    for key in ('assignment_project_id', 'handoff_project', 'project_id', 'project'):
        value = data.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None


def _unverified_row(predecessor, title):
    return {
        'id': predecessor,
        'known': False,
        'verified': False,
        'worker': 'Unknown worker',
        'title': title,
        'status': 'unknown',
    }


def _lineage(data, job_id: str, tasks_root: Path, notices: list[str], current_known=False) -> list[dict]:
    rows = []
    seen = {job_id}
    current = data if isinstance(data, dict) else {}
    chain_verified = current_known is True and current.get('job_id') == job_id
    depth = 0
    predecessor = _handoff_id(current)
    if predecessor is None and isinstance(current.get('handoff_from_job_id'), str):
        notices.append('A recorded handoff reference is not a usable task id.')
        return rows
    while predecessor and depth < MAX_HANDOFF_DEPTH:
        depth += 1
        if predecessor in seen:
            notices.append('Saved handoff history repeats a task already listed. The rest of this lineage is not shown.')
            break
        seen.add(predecessor)
        folder = Path(tasks_root) / predecessor
        try:
            result, record, problem = _safe_canonical(folder, predecessor)
        except (OSError, ValueError):
            notices.append('An earlier handed-off task could not be read.')
            rows.append(_unverified_row(predecessor, 'Earlier task record was unreadable'))
            break
        if problem == 'missing':
            notices.append('An earlier handed-off task is missing from the saved task list.')
            rows.append(_unverified_row(predecessor, 'Earlier task not found in this saved list'))
            break
        if problem == 'redirected':
            notices.append('An earlier handed-off task could not be read safely.')
            rows.append(_unverified_row(predecessor, 'Earlier task could not be opened safely'))
            break
        if problem == 'result_invalid':
            notices.append('An earlier handed-off task has an unreadable canonical result. Its saved index is not treated as verified.')
            rows.append(_unverified_row(predecessor, 'Earlier task lineage is unknown'))
            break
        index_only = problem == 'index_only'
        chosen = result if result is not None else record
        if chosen is None:
            notices.append('An earlier handed-off task could not be read.')
            rows.append(_unverified_row(predecessor, 'Earlier task record was unreadable'))
            break
        current_project = _project_token(current)
        prior_project = _project_token(chosen)
        project_matched = bool(current_project and prior_project and current_project == prior_project)
        actual_project_matched = bool(project_matched and current.get('assignment_project_id') == current_project
                                      and chosen.get('assignment_project_id') == prior_project)
        if current_project or prior_project:
            if not current_project or not prior_project or current_project != prior_project:
                notices.append('An earlier handed-off task does not have matching saved project evidence, so this link is unverified.')
                rows.append(_unverified_row(predecessor, 'Earlier task could not be verified as this handoff'))
                break
        handed_to = chosen.get('handoff_to_job_id')
        if isinstance(handed_to, str) and _HEX.fullmatch(handed_to):
            expected = rows[-1]['id'] if rows else job_id
            if handed_to != expected:
                notices.append('An earlier handed-off task points at a different follow-on task.')
                rows.append(_unverified_row(predecessor, 'Earlier task did not match this handoff'))
                break
        if index_only:
            notices.append('An earlier handed-off task is shown from the saved index only; its canonical result was not found.')
        chain_verified = bool(chain_verified and not index_only and actual_project_matched)
        if not chain_verified:
            notices.append('This historical handoff link is unverified: canonical source and matching recorded project identity are required on both sides.')
        worker = _text(chosen.get('worker')).strip() or 'Unknown worker'
        title = _text(chosen.get('task')).strip() or 'Untitled task'
        status = _text(chosen.get('status'), 'unknown') or 'unknown'
        rows.append({
            'id': predecessor,
            'known': True,
            'verified': chain_verified,
            'index_only': index_only,
            'worker': worker,
            'title': title,
            'status': status,
            'finished_at': _time(chosen.get('finalized_at')),
        })
        current = chosen
        predecessor = _handoff_id(chosen)
        if predecessor is None and isinstance(chosen.get('handoff_from_job_id'), str):
            notices.append('A recorded handoff reference is not a usable task id.')
            break
    else:
        if predecessor and depth >= MAX_HANDOFF_DEPTH:
            notices.append('Saved handoff history is longer than this view will follow.')
    return rows


def enrich_task(row, data, *, tasks_root, workspaces_root, current_known=False):
    """Never writes evidence. Set current_known only for a validated canonical result."""
    notices = list(row.get('notices') or [])
    job_id = row.get('id') if isinstance(row.get('id'), str) else ''
    progress = _empty_progress()
    preview = {
        'text': '',
        'truncated': False,
        'incomplete': True,
        'saved_at': None,
        'chars': 0,
    }
    lineage = []
    recorded_size = _canonical_size(data if isinstance(data, dict) else {})
    recorded_timeout = _canonical_timeout(data if isinstance(data, dict) else {})
    if _HEX.fullmatch(job_id):
        progress = _read_progress(job_id, Path(workspaces_root), notices)
        preview = _read_preview(job_id, Path(workspaces_root), notices)
        try:
            lineage = _lineage(data if isinstance(data, dict) else {}, job_id, Path(tasks_root), notices, current_known=current_known)
        except (OSError, ValueError, TypeError, RecursionError):
            notices.append('Saved handoff history could not be read. Other tasks are still available.')
            lineage = []
    if recorded_size is not None:
        progress['size'] = recorded_size
    if recorded_timeout is not None:
        progress['timeout_seconds'] = recorded_timeout
        progress['timeout_known'] = True
    elif progress.get('present') and not progress.get('timeout_known'):
        notices.append('No recorded deadline was saved with this progress snapshot.')
    if progress.get('preview_truncated') is True:
        preview['truncated'] = True
        preview['source_truncated'] = True
    row['progress'] = progress
    row['preview'] = preview
    row['handoff'] = {'lineage': lineage}
    row['notices'] = notices
    if notices:
        row['group'] = 'Needs attention'
    return row
