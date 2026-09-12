"""Coalesced quota-only refresh requests that never wait on a provider CLI."""
import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import uuid

from usage_guard import Guard, REFRESH_PROVIDERS, file_lock, write_json


DEBOUNCE_SECONDS = 30
MAX_METADATA_BYTES = 4096
_REQUEST_ID = re.compile(r'[a-f0-9]{32}')


def _now():
    return datetime.now(timezone.utc)


def _receipt(status, provider, reason=None, **fields):
    result = {'status': status, 'provider': provider if provider in REFRESH_PROVIDERS else None}
    if reason:
        result['reason'] = reason
    result.update(fields)
    return result


def _locations(root, provider):
    if not isinstance(provider, str) or provider not in REFRESH_PROVIDERS:
        raise ValueError('Unsupported quota provider')
    root = Path(root).resolve()
    directory = root / 'background-usage'
    if directory.is_symlink() or not directory.resolve().is_relative_to(root):
        raise ValueError('Background usage directory was redirected')
    directory.mkdir(parents=True, exist_ok=True)
    paths = tuple(directory / (provider + suffix) for suffix in ('.json', '.launch.lock', '.worker.lock'))
    if any(path.is_symlink() or not path.resolve().is_relative_to(directory.resolve()) for path in paths):
        raise ValueError('Background usage evidence was redirected')
    return root, paths


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError('Duplicate metadata field')
        result[key] = value
    return result


def _timestamp(value):
    if not isinstance(value, str):
        raise ValueError('Invalid metadata timestamp')
    parsed = datetime.fromisoformat(value.replace('Z', '+00:00'))
    if parsed.tzinfo is None:
        raise ValueError('Metadata timestamp needs a timezone')
    return parsed


def _read(path, provider):
    try:
        with path.open('rb') as handle:
            raw = handle.read(MAX_METADATA_BYTES + 1)
        if len(raw) > MAX_METADATA_BYTES:
            return None
        value = json.loads(raw, object_pairs_hook=_unique_object)
        if (not isinstance(value, dict) or type(value.get('schema_version')) is not int
                or value['schema_version'] != 1
                or value.get('provider') != provider or not isinstance(value.get('request_id'), str)
                or not _REQUEST_ID.fullmatch(value['request_id'])
                or value.get('status') not in ('queued', 'running', 'completed', 'error')):
            return None
        _timestamp(value.get('requested_at'))
        if value.get('finished_at') is not None:
            _timestamp(value['finished_at'])
        return value
    except (OSError, ValueError, TypeError, RecursionError, OverflowError):
        return None


def _recent(value, current):
    if not value:
        return False
    # Future or corrupt timestamps cannot suppress refreshes indefinitely.
    for key in ('requested_at', 'finished_at'):
        if value.get(key):
            elapsed = (current - _timestamp(value[key])).total_seconds()
            if 0 <= elapsed < DEBOUNCE_SECONDS:
                return True
    return False


def _spawn(root, provider, request_id):
    script = Path(__file__).resolve()
    kwargs = {'cwd': str(script.parent), 'stdin': subprocess.DEVNULL,
              'stdout': subprocess.DEVNULL, 'stderr': subprocess.DEVNULL, 'close_fds': True}
    if os.name == 'nt':
        kwargs['creationflags'] = (subprocess.CREATE_NO_WINDOW | subprocess.DETACHED_PROCESS
                                   | subprocess.CREATE_NEW_PROCESS_GROUP)
    else:
        kwargs['start_new_session'] = True
    subprocess.Popen([sys.executable, str(script), '--root', str(root),
                      '--provider', provider, '--request-id', request_id], **kwargs)


def request_refresh(root, provider):
    """Return queued/coalesced/error immediately; collector failures are advisory."""
    try:
        root, (metadata, launch_lock, worker_lock) = _locations(root, provider)
        with file_lock(launch_lock, timeout=0):
            try:
                with file_lock(worker_lock, timeout=0):
                    pass
            except TimeoutError:
                return _receipt('coalesced', provider, 'A quota refresh is already running.')
            prior = _read(metadata, provider)
            current = _now()
            if _recent(prior, current):
                return _receipt('coalesced', provider, 'A quota refresh was requested recently.',
                                request_id=prior['request_id'], requested_at=prior['requested_at'])
            request_id = uuid.uuid4().hex
            value = {'schema_version': 1, 'provider': provider, 'request_id': request_id,
                     'requested_at': current.isoformat(), 'status': 'queued'}
            write_json(metadata, value)
            try:
                _spawn(root, provider, request_id)
            except Exception:
                value.update(status='error', finished_at=_now().isoformat())
                write_json(metadata, value)
                return _receipt('error', provider, 'The background quota reader could not be started.')
            return _receipt('queued', provider, request_id=request_id, requested_at=value['requested_at'])
    except TimeoutError:
        return _receipt('coalesced', provider, 'Another quota refresh request is being recorded.')
    except Exception:
        # No quota reader or metadata problem may turn an allowed task into a hold.
        return _receipt('error', provider, 'The background quota refresh could not be queued.')


def run_refresh(root, provider, request_id):
    """One detached helper, serialized by a lifetime lock separate from usage state."""
    try:
        if not isinstance(request_id, str) or not _REQUEST_ID.fullmatch(request_id):
            return _receipt('error', provider, 'The background quota request identity is invalid.')
        root, (metadata, launch_lock, worker_lock) = _locations(root, provider)
        with file_lock(worker_lock, timeout=0):
            with file_lock(launch_lock, timeout=1):
                value = _read(metadata, provider)
                if not value or value['request_id'] != request_id:
                    return _receipt('coalesced', provider, 'A newer quota refresh request replaced this one.')
                if value['status'] in ('completed', 'error'):
                    return _receipt('coalesced', provider, 'This quota refresh request already finished.')
                value.update(status='running', started_at=_now().isoformat())
                write_json(metadata, value)
            status = 'error'
            try:
                guard = Guard(root)
                results = guard.refresh(provider)
                if isinstance(results, dict) and isinstance(results.get(provider), dict) and results[provider].get('ok') is True:
                    status = 'completed'
                # Regenerate even after a collector timeout so the dashboard shows
                # cached readings and the latest advisory refresh failure.
                guard.dashboard()
            except Exception:
                status = 'error'
            with file_lock(launch_lock, timeout=1):
                latest = _read(metadata, provider)
                if latest and latest['request_id'] == request_id:
                    latest.update(status=status, finished_at=_now().isoformat())
                    write_json(metadata, latest)
            return _receipt(status, provider, request_id=request_id)
    except TimeoutError:
        return _receipt('coalesced', provider, 'Another quota refresh helper owns this provider.')
    except Exception:
        return _receipt('error', provider, 'The background quota refresh could not finish.')


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', required=True, type=Path)
    parser.add_argument('--provider', required=True, choices=REFRESH_PROVIDERS)
    parser.add_argument('--request-id', required=True)
    args = parser.parse_args(argv)
    result = run_refresh(args.root, args.provider, args.request_id)
    return 0 if result['status'] in ('completed', 'coalesced') else 1


if __name__ == '__main__':
    raise SystemExit(main())
