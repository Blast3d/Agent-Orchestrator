"""Save bounded review evidence after acceptance, without another model call.

The canonical task remains authoritative. Automatic episodes describe the task
and its review evidence; they do not pretend to summarize a worker's full answer.
An explicit curated payload is used instead when supplied by the reviewer.
"""
import json
from pathlib import Path
import re

from brain_store import BrainStore, digest, safe_path, scope
from storage_budget import StorageLimitError
from task_store import timestamp, write_json
from usage_guard import file_lock


RECEIPT_NAME = 'memory-outcome.json'
MAX_CANONICAL_BYTES = 8 * 1024**2
MAX_RECEIPT_BYTES = 16 * 1024


def _compact(value, maximum, fallback):
    if not isinstance(value, str):
        return fallback
    cleaned = ' '.join(''.join(c for c in value if ord(c) >= 32 or c in '\n\r\t').split())
    return (cleaned[:maximum - 1] + '…') if len(cleaned) > maximum else (cleaned or fallback)


def _location(task_store, job_id):
    tasks = Path(task_store.root).absolute()
    if tasks.name != 'tasks' or tasks.parent.name != 'runs':
        raise ValueError('Automatic memory requires the canonical runs/tasks store')
    root = tasks.parent.parent.resolve()
    directory = safe_path(root, task_store.directory(job_id))
    return root, directory


def _canonical(root, directory, job_id):
    path = safe_path(root, directory / 'result.json')
    if path.stat().st_size > MAX_CANONICAL_BYTES:
        raise ValueError('Canonical source task is oversized')
    result = json.loads(path.read_text(encoding='utf-8'))
    if not isinstance(result, dict) or result.get('job_id') != job_id:
        raise ValueError('Canonical source task identifier does not match')
    return result


def _payload(result, curated_payload):
    project = scope(result['assignment_project_id'])
    review = result['review']
    if curated_payload is not None:
        if not isinstance(curated_payload, dict):
            raise ValueError('Curated memory must be an object')
        if curated_payload.get('project_id', project) != project:
            raise ValueError('Remembered solution needs the exact task project')
        payload = dict(curated_payload)
        payload.update(project_id=project, source={'type': 'task', 'job_id': result['job_id']})
        return payload
    task = _compact(result.get('task'), 500, 'Completed worker task')
    category = _compact(result.get('category'), 60, 'general')
    evidence = _compact(review.get('note'), 800, 'Accepted canonical review')
    assignment = _compact(result.get('assignment_id'), 140, result['job_id'])
    return {
        'project_id': project, 'kind': 'episode',
        'title': _compact('Accepted review: ' + task, 160, 'Accepted task review'),
        'content': 'Task: ' + task + '\nReview evidence: ' + evidence +
                   '\nCompact review evidence; inspect the canonical task for the full answer.',
        'tags': ['automatic-review', category], 'importance': 0.35,
        'episode': {
            'problem': task,
            'action': 'Completed ' + category + ' assignment ' + assignment +
                      ' and submitted its answer for review.',
            'outcome': evidence,
        },
        'source': {'type': 'task', 'job_id': result['job_id'],
                   'review_sha256': digest(review)},
    }


def _error(exc):
    return _compact(str(exc), 300, type(exc).__name__) if isinstance(
        exc, (ValueError, StorageLimitError)) else type(exc).__name__


def record_accepted_outcome(task_store, job_id, *, brain_factory=BrainStore, curated_payload=None):
    """Return/save a memory receipt without changing the accepted task review.

    Calling again retries a failed write using the same canonical evidence. A
    completed receipt prevents duplicate writes and resurrection after Forget.
    Errors are returned separately so memory failure cannot erase acceptance.
    This function never reads the worker brief, starts a model, or uses a network.
    """
    if isinstance(curated_payload, dict) and 'memories' in curated_payload:
        from memory_bundle import record_accepted_bundle
        return record_accepted_bundle(task_store, job_id, curated_payload)
    receipt = {'schema_version': 1, 'job_id': job_id,
               'mode': 'curated' if curated_payload is not None else 'automatic',
               'status': 'error', 'memory_id': None, 'retryable': True}
    try:
        root, directory = _location(task_store, job_id)
        # Serialize retries and read the accepted evidence after taking the lock.
        with file_lock(safe_path(root, directory / 'memory.lock')):
            return _record_locked(root, directory, job_id, receipt, brain_factory, curated_payload)
    except Exception as exc:
        receipt.update(status='error', error=_error(exc), updated_at=timestamp())
        return receipt


def _record_locked(root, directory, job_id, receipt, brain_factory, curated_payload):
    receipt_path = safe_path(root, directory / RECEIPT_NAME)
    preserve_receipt = False
    try:
        existing = None
        if receipt_path.exists():
            preserve_receipt = True
            if receipt_path.stat().st_size > MAX_RECEIPT_BYTES:
                raise ValueError('Memory receipt is oversized; preserve it for repair')
            existing = json.loads(receipt_path.read_text(encoding='utf-8'))
            if (not isinstance(existing, dict) or existing.get('schema_version') != 1
                    or existing.get('job_id') != job_id
                    or existing.get('status') not in ('remembered', 'skipped', 'error', 'writing')
                    or existing.get('mode') not in ('automatic', 'curated')
                    or (existing.get('memory_id') is not None and not re.fullmatch(
                        r'[a-f0-9]{32}', str(existing['memory_id'])))
                    or any(existing.get(key) is not None and not re.fullmatch(
                        r'[a-f0-9]{64}', str(existing[key]))
                        for key in ('source_sha256', 'request_sha256'))):
                raise ValueError('Memory receipt cannot be verified; preserve it for repair')
            receipt.update(existing)
        # An unreadable canonical file must not erase a previous memory ID:
        # restoring the source later must still respect an intervening Forget.
        result = _canonical(root, directory, job_id)
        preserve_receipt = False
        if not (result.get('status') == result.get('review_status') == 'accepted'
                and result.get('execution_status') == 'succeeded' and result.get('finalized_at')):
            # Preserve any prior remembered ID if acceptance was later revoked.
            receipt.update(existing or {})
            receipt.update(status='skipped', reason='task_not_accepted', retryable=False)
        elif not result.get('assignment_project_id'):
            receipt.update(status='skipped', reason='missing_project', retryable=False)
        else:
            review = result.get('review')
            if (not isinstance(review, dict) or not isinstance(review.get('reviewer'), str)
                    or not review['reviewer'].strip() or not isinstance(review.get('note'), str)
                    or len(review['note'].strip()) < 20 or len(review['note'].split()) < 4):
                raise ValueError('Accepted source needs a named reviewer and validation evidence')
            payload = _payload(result, curated_payload)
            source_hash = digest({key: result.get(key) for key in (
                'job_id', 'assignment_project_id', 'response', 'finalized_at', 'review',
                'task', 'category', 'assignment_id')})
            request_hash = digest(payload)
            receipt.update(project_id=payload['project_id'], source_sha256=source_hash,
                           request_sha256=request_hash)
            if existing:
                receipt.update(existing)
                if existing.get('status') == 'skipped' and not existing.get('source_sha256'):
                    # A prior call made before acceptance/project assignment did
                    # not choose a memory request yet. Honor this review's choice.
                    receipt['mode'] = 'curated' if curated_payload is not None else 'automatic'
            if existing and existing.get('status') == 'writing' and not existing.get('memory_id'):
                # The process might have stopped after a successful proposal but
                # before recording its ID. That record might now be forgotten.
                # Never recreate it by guessing whether the write happened.
                preserve_receipt = True
                raise ValueError('Memory write completion is uncertain; inspect the receipt and brain before retrying')
            if (existing and existing.get('mode') != ('curated' if curated_payload is not None else 'automatic')
                    and existing.get('status') != 'skipped'):
                preserve_receipt = True
                raise ValueError('Retry needs the original memory mode and curated file, if supplied')
            if existing and existing.get('source_sha256') not in (None, source_hash):
                preserve_receipt = True
                raise ValueError('Accepted source changed; review the existing memory separately')
            if existing and existing.get('request_sha256') not in (None, request_hash):
                preserve_receipt = True
                raise ValueError('Memory request changed; preserve the original receipt and review separately')
            if receipt.get('reason') in ('forgotten', 'memory_removed', 'memory_inactive'):
                return receipt
            brain = brain_factory(root)
            candidate = None
            if receipt.get('memory_id'):
                try:
                    candidate = brain.get(receipt['memory_id'])
                except ValueError as exc:
                    if str(exc) != 'Memory not found':
                        raise
                    receipt.update(status='skipped', reason='memory_removed', retryable=False)
                if candidate and candidate['status'] not in ('active', 'pending'):
                    receipt.update(status='skipped', reason='forgotten' if candidate['status'] ==
                                   'deleted' else 'memory_inactive', retryable=False)
                    candidate = None
            else:
                receipt.update(status='writing', retryable=True, updated_at=timestamp())
                receipt.pop('error', None)
                receipt.pop('reason', None)
                write_json(receipt_path, receipt)
                candidate = brain.propose(payload)
                receipt['memory_id'] = candidate['id']
                write_json(receipt_path, receipt)
            if candidate:
                if (candidate.get('project_id') != payload['project_id'] or
                        candidate.get('source', {}).get('job_id') != job_id):
                    preserve_receipt = True
                    raise ValueError('Memory receipt points to a different task or project')
                if candidate['status'] == 'pending':
                    candidate = brain.approve(candidate['id'],
                        _compact(review['reviewer'], 100, 'Canonical reviewer'),
                        _compact(review['note'], 1000, 'Accepted canonical review evidence.'))
                receipt.update(status='remembered', memory_id=candidate['id'], retryable=False)
                receipt.pop('reason', None)
                receipt.pop('error', None)
                if candidate.get('write_status', {}).get('cleanup_pending'):
                    receipt['write_status'] = candidate['write_status']
        receipt['updated_at'] = timestamp()
        write_json(receipt_path, receipt)
        return receipt
    except Exception as exc:
        receipt.update(status='error', error=_error(exc), retryable=not preserve_receipt,
                       updated_at=timestamp())
        if not preserve_receipt:
            try:
                write_json(receipt_path, receipt)
            except Exception:
                receipt['receipt_error'] = 'Memory receipt could not be saved'
        return receipt


def main(argv=None):
    """Retry memory storage for one accepted canonical task; never re-run it."""
    import argparse
    from paths import ROOT
    from task_store import TaskStore

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('job_id')
    parser.add_argument('--root', type=Path, default=ROOT)
    parser.add_argument('--remember-file', type=Path,
                        help='Use this explicitly curated candidate instead of an automatic episode')
    args = parser.parse_args(argv)
    try:
        curated = None
        if args.remember_file:
            if args.remember_file.stat().st_size > 16384:
                raise ValueError('Memory candidate exceeds 16 KiB')
            curated = json.loads(args.remember_file.read_text(encoding='utf-8-sig'))
            if not isinstance(curated, dict):
                raise ValueError('Curated memory must be an object')
        result = record_accepted_outcome(TaskStore(args.root / 'runs/tasks'), args.job_id,
                                         curated_payload=curated)
    except Exception as exc:
        result = {'job_id': args.job_id, 'status': 'error', 'error': _error(exc)}
    print(json.dumps(result))
    return 2 if result['status'] == 'error' else 0


if __name__ == '__main__':
    raise SystemExit(main())
