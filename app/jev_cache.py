"""Bounded, optional decision cache. Never stores request state or evidence text."""
import hashlib
import json
import time

from storage_budget import StorageBudget
from usage_guard import file_lock, write_json

MAX_BYTES = 512 * 1024
MAX_ENTRIES = 128
REVISION = 1


def identity(root, project, user, purpose, request, context, policy):
    envelope = {'revision': REVISION, 'root': str(root.resolve()), 'project': project,
                'user': user, 'purpose': purpose, 'request': hashlib.sha256(request).hexdigest(),
                'context': context, 'policy': policy}
    raw = json.dumps(envelope, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()
    if len(raw) > 32768:
        raise ValueError('Cache context exceeds its bound')
    return hashlib.sha256(raw).hexdigest()


def _entries(root):
    from jev_openrouter import _path, _read, _number
    path = _path(root, 'jev-cache.json')
    if not path.exists():
        return {}
    value = _read(path, MAX_BYTES)
    if (not isinstance(value, dict) or value.get('schema_version') != REVISION
            or not isinstance(value.get('entries'), dict) or len(value['entries']) > MAX_ENTRIES):
        raise ValueError('Invalid decision cache')
    now = time.time()
    valid = {}
    for key, row in value['entries'].items():
        if (isinstance(row, dict) and set(row) == {'created', 'response'}
                and _number(row.get('created'), 0, now)
                and isinstance(row.get('response'), dict)):
            valid[key] = row
    return valid


def get(root, key, ttl, questions):
    from jev_openrouter import _path, _response, _number
    with file_lock(_path(root, 'jev-cache.lock')):
        row = _entries(root).get(key)
    if row is None:
        return None
    if not isinstance(row, dict) or not _number(row.get('created'), 0, time.time()):
        raise ValueError('Invalid decision cache entry')
    if time.time() - row['created'] > ttl:
        return None
    # Revalidate the complete provider envelope against this exact request schema.
    response = row.get('response')
    checked = _response(json.dumps(response, allow_nan=False).encode(), questions)
    return checked


def put(root, key, response, ttl):
    from jev_openrouter import _path, _number
    # Only already validated envelopes reach this function. No state is persisted.
    at = time.time()
    with StorageBudget(root).allocation(2 * MAX_BYTES, kind='task'):
        with file_lock(_path(root, 'jev-cache.lock')):
            entries = _entries(root)
            entries = {k: v for k, v in entries.items() if isinstance(v, dict)
                       and _number(v.get('created'), max(0, at - ttl), at)}
            entries[key] = {'created': at, 'response': response}
            ordered = sorted(entries, key=lambda k: (entries[k]['created'], k))
            while len(entries) > MAX_ENTRIES:
                entries.pop(ordered.pop(0))
            value = {'schema_version': REVISION, 'entries': entries}
            while len((json.dumps(value, indent=2, allow_nan=False) + '\n').encode()) > MAX_BYTES and ordered:
                entries.pop(ordered.pop(0), None)
            write_json(_path(root, 'jev-cache.json'), value)
