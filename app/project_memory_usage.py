"""Bounded, read-only project memory telemetry from retained task evidence."""
from collections import OrderedDict
from copy import deepcopy
import hashlib
import itertools
import json
import os
from pathlib import Path
import re
import stat
import threading
import time

from brain_store import digest, scope
from memory_usage import _accepted, summary
from task_progress_view import _bounded_read, _contained_file, _is_redirected, _time
from task_index import MAX_INDEX_BYTES


MAX_FOLDERS = 2000
MAX_TASKS = 50
MAX_BYTES = 8 * 1024**2
INDEX_BUDGET = 4 * 1024**2
MAX_RESULT_BYTES = 2 * 1024**2
MAX_RECEIPT_BYTES = 96 * 1024
CACHE_SECONDS = 5
_CACHE = OrderedDict()
_LOCK = threading.Lock()
_JOB = re.compile(r'[a-f0-9]{32}')
_SHA = re.compile(r'[a-f0-9]{64}')
_WORKERS = ('codex', 'astra', 'claude', 'fable', 'grok', 'gemini', 'local-chat', 'native-review', 'vscode-copilot')


def invalidate(root, project_id):
    """Discard one cached sample after a local feedback/capture write."""
    key = (str(Path(root).absolute()), scope(project_id))
    with _LOCK:
        _CACHE.pop(key, None)


def _safe_directory(path, root):
    try:
        path.relative_to(root)
        for component in (*reversed(path.parents), path):
            if _is_redirected(component) or not stat.S_ISDIR(os.lstat(component).st_mode):
                return False
        return True
    except (OSError, ValueError):
        return False


def _read(path, root, maximum, budget):
    if budget[0] <= 1:
        return None, 'budget', None
    maximum = min(maximum, budget[0] - 1)
    raw, oversized, error = _bounded_read(path, maximum, root)
    # Account for the oversize probe and conservatively charge failed reads:
    # a concurrent replacement may be rejected after its bytes were read.
    budget[0] -= len(raw) + int(oversized) if raw is not None else maximum + 1
    fingerprint = hashlib.sha256(raw).hexdigest() if raw is not None else None
    if error or raw is None:
        return None, 'unreadable', fingerprint
    if oversized:
        return None, 'oversized', fingerprint
    try:
        data = json.loads(raw.decode('utf-8-sig'))
        return (data, None, fingerprint) if isinstance(data, dict) else (None, 'invalid', fingerprint)
    except (ValueError, TypeError, RecursionError):
        return None, 'invalid', fingerprint


def _label(value):
    if not isinstance(value, str):
        return 'Untitled task'
    value = ' '.join(''.join(c for c in value if ord(c) >= 32 or c in '\t\r\n').split())
    # Task labels are intentional display text. Do not carry incidental local
    # paths, URLs, account addresses or pasted prompt sections into this panel.
    if len(value) > 500 or any(mark in value for mark in ('## ', '```', 'BEGIN PROMPT')):
        return 'Task label withheld; inspect the task record'
    if re.search(r'(?i)\b[A-Z]:[\\/]|\\\\|(?<!\w)/(?:[^\s/]+/)+', value):
        return 'Task with a [path] reference; inspect the task record'
    value = re.sub(r'(?i)\b[A-Z]:[\\/][^\s]*|\\\\[^\s]+|(?<!\w)/(?:[^\s/]+/)+[^\s]*', '[path]', value)
    value = re.sub(r'(?i)\b(?:https?|file)://[^\s]+|[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}', '[private reference]', value)
    return value[:160] or 'Untitled task'


def _capture(receipt, problem, result):
    base = {'status': 'missing' if problem == 'missing' else 'unreadable', 'memory_count': 0,
            'historical': True, 'current_availability': 'not_checked'}
    if problem:
        return base
    if (not isinstance(receipt, dict) or type(receipt.get('schema_version')) is not int or
            receipt.get('schema_version') != 1 or
            receipt.get('job_id') != result['job_id'] or
            receipt.get('project_id', result['assignment_project_id']) != result['assignment_project_id'] or
            receipt.get('mode') not in ('automatic', 'curated', 'curated_bundle') or
            receipt.get('status') not in ('remembered', 'skipped', 'error', 'writing')):
        return dict(base, status='invalid')
    ids = receipt.get('memory_ids') if receipt['mode'] == 'curated_bundle' else (
        [receipt['memory_id']] if receipt.get('memory_id') is not None else [])
    if (not isinstance(ids, list) or len(ids) > 24 or
            any(not isinstance(identifier, str) or not _JOB.fullmatch(identifier) for identifier in ids) or
            len(set(ids)) != len(ids) or (receipt['status'] == 'remembered' and not ids)):
        return dict(base, status='invalid')
    source_hash = receipt.get('source_sha256')
    if source_hash is not None and (not isinstance(source_hash, str) or not _SHA.fullmatch(source_hash)):
        return dict(base, status='invalid')
    fields = ('job_id', 'assignment_project_id', 'response', 'review', 'finalized_at') if (
        receipt['mode'] == 'curated_bundle') else (
        'job_id', 'assignment_project_id', 'response', 'finalized_at', 'review', 'task', 'category', 'assignment_id')
    source_matches = source_hash == digest({key: result.get(key) for key in fields}) if source_hash else None
    # A receipt records a historical write. Even an unchanged task does not prove
    # a memory is currently active: it may have been forgotten or superseded.
    return dict(base, status=receipt['status'] if source_matches is not False else 'stale',
                memory_count=len(ids), source_matches=source_matches)


def _sample(root, project_id):
    output = {'project_id': project_id, 'tasks': [], 'scanned': 0, 'eligible': 0, 'remembered': 0,
              'missing_receipt': 0, 'unreadable': 0, 'truncated': False,
              'sample_scope': 'Retained sample, not lifetime coverage.',
              'capture_basis': 'Historical receipts; current memory availability is not checked.',
              'limits': {'folders': MAX_FOLDERS, 'tasks': MAX_TASKS, 'bytes': MAX_BYTES}}
    folders = root / 'runs/tasks'
    proof = []
    if not folders.exists():
        output['revision'] = digest(output)
        return output
    if not _safe_directory(folders, root):
        output.update(unreadable=1, truncated=True)
        output['revision'] = digest(output)
        return output
    try:
        paths = list(itertools.islice(folders.iterdir(), MAX_FOLDERS + 1))
    except OSError:
        output.update(unreadable=1, truncated=True)
        output['revision'] = digest(output)
        return output
    output['truncated'] = len(paths) > MAX_FOLDERS
    index_budget = [min(INDEX_BUDGET, MAX_BYTES)]
    candidates = []
    for folder in paths[:MAX_FOLDERS]:
        if not _JOB.fullmatch(folder.name):
            continue
        if index_budget[0] <= 0:
            output['truncated'] = True
            break
        output['scanned'] += 1
        record, problem, _ = _read(folder / 'record.json', root, MAX_INDEX_BYTES, index_budget)
        if problem or record.get('job_id') != folder.name:
            output['unreadable'] += 1
            if problem in ('oversized', 'budget'):
                output['truncated'] = True
            continue
        if record.get('assignment_project_id') != project_id:
            continue
        candidates.append((_time(record.get('created_at')) or '', folder))
    candidates.sort(key=lambda item: (item[0], item[1].name), reverse=True)
    output['truncated'] |= len(candidates) > MAX_TASKS
    budget = [MAX_BYTES - (min(INDEX_BUDGET, MAX_BYTES) - index_budget[0])]
    for _, folder in candidates[:MAX_TASKS]:
        if budget[0] <= 0:
            output['truncated'] = True
            break
        result, problem, fingerprint = _read(folder / 'result.json', root, MAX_RESULT_BYTES, budget)
        proof.append([folder.name, fingerprint, problem])
        if (problem or result.get('job_id') != folder.name or
                result.get('assignment_project_id') != project_id):
            output['unreadable'] += 1
            if problem in ('oversized', 'budget'):
                output['truncated'] = True
            continue
        context = result.get('memory_context')
        if (result.get('memory_user_id', 'local') != 'local' or
                (isinstance(context, dict) and context.get('user_id', 'local') != 'local')):
            output['unreadable'] += 1
            continue
        path, receipt_problem = _contained_file(root, 'runs', 'tasks', folder.name, 'memory-outcome.json')
        if path:
            receipt, receipt_problem, receipt_hash = _read(path, root, MAX_RECEIPT_BYTES, budget)
        else:
            receipt, receipt_hash = None, None
        proof.append([folder.name, receipt_hash, receipt_problem])
        capture = _capture(receipt, receipt_problem, result)
        if capture['status'] in ('unreadable', 'invalid'):
            output['unreadable'] += 1
        if receipt_problem in ('budget', 'oversized'):
            output['truncated'] = True
        eligible = _accepted(result)
        output['eligible'] += int(eligible)
        output['remembered'] += int(eligible and capture['status'] == 'remembered')
        output['missing_receipt'] += int(eligible and capture['status'] == 'missing')
        worker = result.get('worker')
        imported = result.get('imported_completed_artifact') is True
        memory = summary(result)
        # Explicit feedback notes can contain arbitrary user text. The project
        # rollup exposes the rating and timestamp; detailed review stays local.
        for key in ('note', 'reviewer'):
            memory['feedback'].pop(key, None)
        output['tasks'].append({'job_id': folder.name, 'task': _label(result.get('task')),
            'worker': worker if worker in _WORKERS else 'unknown',
            'created_at': _time(result.get('created_at')),
            'review_status': result.get('review_status') if result.get('review_status') in (
                'pending', 'accepted', 'rejected') else 'unknown',
            'imported_artifact': imported, 'memory': memory, 'capture': capture})
    output['tasks'].sort(key=lambda row: (row['created_at'] or '', row['job_id']), reverse=True)
    output['revision'] = digest({'sample': output, 'source_fingerprints': proof})
    return output


def project_summary(root, project_id, *, force=False):
    """Return up to 50 recent scoped tasks under fixed I/O and cache bounds."""
    root = Path(root).absolute()
    project_id = scope(project_id)
    key = (str(root), project_id)
    tick = time.monotonic()
    with _LOCK:
        cached = _CACHE.get(key)
        if not force and cached and tick - cached[0] < CACHE_SECONDS:
            _CACHE.move_to_end(key)
            return deepcopy(cached[1])
    value = _sample(root, project_id)
    with _LOCK:
        _CACHE[key] = (time.monotonic(), value)
        _CACHE.move_to_end(key)
        while len(_CACHE) > 32:
            _CACHE.popitem(last=False)
    return deepcopy(value)
