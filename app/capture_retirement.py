"""Preserve and retire a provably unwritten invalid native import after replacement."""
from contextlib import ExitStack
import hashlib
import json
import os
from pathlib import Path
import re
import uuid

from brain_store import BrainStore, date, digest, safe_path, scope, text
from coordinator_handoff import Coordinator
from memory_bundle import capture_source_hash, read_json, resolve_capture_run, validate_bundle, validate_capture_fields
from task_store import timestamp, write_json
from usage_guard import file_lock

RECEIPT = 'capture-retirement.json'
JOB = re.compile(r'[a-f0-9]{32}')


def _directory(root, job):
    if not isinstance(job, str) or not JOB.fullmatch(job):
        raise ValueError('Retirement requires an exact canonical task identifier')
    return safe_path(root, root / 'runs/tasks' / job)


def _artifact(root, run, job, project):
    directory = _directory(root, job)
    path = safe_path(root, directory / 'result.json')
    result = read_json(path, root, 8 * 1024**2)
    if (not isinstance(result, dict) or result.get('job_id') != job
            or result.get('run_id') != run.name or result.get('assignment_project_id') != project
            or result.get('run_directory', str(root / '.orchestration' / run.name)) != str(run)
            or result.get('status') != 'accepted' or result.get('review_status') != 'accepted'
            or result.get('execution_status') != 'succeeded' or not result.get('finalized_at')
            or result.get('imported_completed_artifact') is not True
            or result.get('artifact_origin') != 'reviewed-run-closeout'
            or result.get('worker') != 'native-review' or result.get('category') != 'memory-curation'
            or type(result.get('provider_calls')) is not int or result['provider_calls'] != 0
            or result.get('started_at')):
        raise ValueError('Retirement requires an accepted native import from the exact run and project')
    review = result.get('review')
    if not isinstance(review, dict):
        raise ValueError('Native import lacks its canonical review')
    text(review.get('reviewer'), 'Reviewer', 100)
    text(review.get('note'), 'Review note', 1000, 20)
    if not review.get('reviewed_at'):
        raise ValueError('Native import lacks its review timestamp')
    date(review['reviewed_at'])
    payload = json.loads(result['response'])
    if not isinstance(payload, dict) or not {'knowledge', 'evidence'} <= payload.keys():
        raise ValueError('Native import lacks its original bundle and evidence')
    bundle, proof = payload['knowledge'], payload['evidence']
    validate_bundle(bundle, project)
    capture_id = bundle.get('capture_id')
    if not isinstance(capture_id, str) or not re.fullmatch(r'[a-z][a-z0-9_-]{0,59}', capture_id):
        raise ValueError('Native capture identifier cannot be verified')
    evidence = bundle.get('evidence')
    if not isinstance(evidence, list) or not 1 <= len(evidence) <= 12 or not isinstance(proof, list):
        raise ValueError('Native capture needs bounded evidence')
    actual = []
    for relative in evidence:
        if not isinstance(relative, str) or Path(relative).is_absolute():
            raise ValueError('Native capture evidence must stay within its run')
        evidence_path = safe_path(run, run / relative)
        with evidence_path.open('rb') as handle:
            raw = handle.read(2 * 1024**2 + 1)
        if len(raw) > 2 * 1024**2:
            raise ValueError('Native capture evidence exceeds 2 MiB')
        actual.append({'path': relative, 'sha256': hashlib.sha256(raw).hexdigest()})
    if actual != proof:
        raise ValueError('Native capture evidence changed after review')
    journal = read_json(run / 'review' / ('memory-capture-' + capture_id + '.json'), run)
    identity = digest({'bundle': bundle, 'proof': proof, 'reviewer': review['reviewer'], 'note': review['note']})
    receipt = read_json(directory / 'memory-outcome.json', root)
    source = capture_source_hash(result)
    if (not isinstance(journal, dict) or journal.get('job_id') != job or journal.get('request_sha256') != identity
            or journal.get('run_directory', str(root / '.orchestration' / run.name)) != str(run)
            or not isinstance(receipt, dict) or receipt.get('schema_version') != 1
            or receipt.get('job_id') != job or receipt.get('project_id') != project
            or receipt.get('mode') != 'curated_bundle' or receipt.get('source_sha256') != source
            or receipt.get('request_sha256') != digest(bundle)):
        raise ValueError('Native capture journal or receipt does not match the canonical source')
    return result, bundle, proof, journal, receipt, hashlib.sha256(path.read_bytes()).hexdigest()


def _verify(root, run, failed, replacement, project, brain):
    if failed == replacement:
        raise ValueError('Retirement requires a different valid replacement import')
    original = _artifact(root, run, failed, project)
    corrected = _artifact(root, run, replacement, project)
    result, bundle, proof, journal, receipt, source_hash = original
    if (journal.get('status') not in ('error', 'importing') or receipt.get('status') != 'error'
            or receipt.get('retryable') is not True or receipt.get('memory_id') is not None
            or 'memory_id' not in receipt
            or receipt.get('memory_ids') != [] or receipt.get('memory_keys') or receipt.get('relation_ids')
            or receipt.get('relation_evidence')):
        raise ValueError('Only a verified retryable zero-write error can be retired')
    if journal['status'] == 'error':
        outcome = journal.get('memory_outcome')
        if (not isinstance(outcome, dict) or outcome.get('job_id') != failed or outcome.get('status') != 'error'
                or outcome.get('retryable') is not True or outcome.get('memory_id') is not None
                or outcome.get('memory_ids', []) != []):
            raise ValueError('Failed native capture journal has inconsistent memory evidence')
    try:
        validate_capture_fields(bundle, project, reviewed_at=result['review']['reviewed_at'])
    except ValueError as exc:
        validation_error = str(exc)
    else:
        raise ValueError('Retirement is limited to invalid candidate fields rejected before memory insertion')
    if corrected[2] != proof or {r['key'] for r in corrected[1]['memories']} != {r['key'] for r in bundle['memories']}:
        raise ValueError('Replacement must cover the same reviewed evidence and memory keys')
    if (corrected[3].get('status') != 'remembered' or corrected[4].get('status') != 'remembered'
            or corrected[4].get('retryable') is not False):
        raise ValueError('Replacement must have a verified remembered capture')
    if set(corrected[4].get('memory_keys', {})) != {row['key'] for row in corrected[1]['memories']}:
        raise ValueError('Replacement receipt does not cover its bundle memory keys')
    # Maintained receipt validation checks IDs and source proofs; no writes or replay.
    from orchestration_lifecycle import _receipt, _native_capture
    checked = _receipt(root, corrected[0], project, brain, brain_lock_held=True)
    if not _native_capture(root, run, corrected[0], checked):
        raise ValueError('Replacement is not a verified native capture')
    if any(state != 'active' for state in checked['current_states']):
        raise ValueError('Retirement requires replacement memories that remain active')
    with brain._connection() as con:
        if con.execute("SELECT 1 FROM memories WHERE json_extract(source,'$.job_id')=? LIMIT 1", (failed,)).fetchone():
            raise ValueError('The failed import has Brain writes; preserve it for inspection')
    return {'job_id': failed, 'replacement_job_id': replacement, 'project_id': project,
            'run_id': run.name, 'run_directory': str(run), 'source_sha256': source_hash,
            'failure_receipt_sha256': digest(receipt), 'failure_journal_sha256': digest(journal),
            'replacement_source_sha256': corrected[5], 'replacement_receipt_sha256': digest(corrected[4]),
            'replacement_journal_sha256': digest(corrected[3]), 'validation_error': validation_error}


def verify_retirement(root, run, job, project, brain):
    """Read a disposition under the caller's coordinator/task locks; never credit it."""
    path = safe_path(root, _directory(root, job) / RECEIPT)
    if not path.exists():
        return None
    saved = read_json(path, root)
    if not isinstance(saved, dict) or saved.get('schema_version') != 1 or saved.get('status') != 'retired':
        raise ValueError('Invalid native capture retirement receipt')
    with file_lock(brain.lock):
        proof = _verify(root, run, job, saved.get('replacement_job_id'), project, brain)
    if any(saved.get(key) != value for key, value in proof.items()):
        raise ValueError('Retired capture evidence changed; preserve the disposition for inspection')
    text(saved.get('reviewer'), 'Retirement reviewer', 100)
    text(saved.get('note'), 'Retirement note', 1000, 20)
    if not saved.get('coordinator') or not saved.get('retired_at'):
        raise ValueError('Retirement lacks its reviewed owner and timestamp')
    return {'decision': 'retired', 'memory_required': False, 'contribution_credit': False,
            'replacement_job_id': proof['replacement_job_id'],
            'retirement_sha256': digest(saved)}


def retire_capture(root, run_id, job_id, *, replacement_job, source_sha256, owner, session, generation, reviewer, note):
    """Append an atomic disposition, preserving original task/journal/memory receipts."""
    root = Path(root).resolve()
    run = resolve_capture_run(root, run_id)
    coordinator = Coordinator(run)
    reviewer, note = text(reviewer, 'Reviewer', 100), text(note, 'Review note', 1000, 20)
    if len(note.split()) < 4:
        raise ValueError('Use a substantive retirement validation note')
    with file_lock(coordinator.lock), ExitStack() as locks:
        coordinator.require_owner(coordinator.read(), owner, session, generation)
        manifest = read_json(run / 'run.json', run)
        if (not isinstance(manifest, dict) or manifest.get('run_id') != run.name
                or manifest.get('schema_version') != 1 or not isinstance(manifest.get('tasks'), list)):
            raise ValueError('Manifest does not identify the exact selected run')
        project = scope(manifest.get('project_id'))
        for job in sorted({job_id, replacement_job}):
            directory = _directory(root, job)
            if not directory.is_dir():
                raise ValueError('Canonical import is unavailable')
            locks.enter_context(file_lock(safe_path(root, directory / 'review.lock')))
            locks.enter_context(file_lock(safe_path(root, directory / 'memory.lock')))
        brain = BrainStore(root, read_only=True)
        locks.enter_context(file_lock(brain.lock))
        proof = _verify(root, run, job_id, replacement_job, project, brain)
        if proof['source_sha256'] != source_sha256:
            raise ValueError('Canonical task source changed; reload before retiring')
        path = safe_path(root, _directory(root, job_id) / RECEIPT)
        saved = read_json(path, root) if path.exists() else None
        request = dict(proof, reviewer=reviewer, note=note)
        if saved is not None:
            if (not isinstance(saved, dict) or saved.get('schema_version') != 1 or saved.get('status') != 'retired'
                    or not saved.get('coordinator') or not saved.get('retired_at')
                    or any(saved.get(key) != value for key, value in request.items())):
                raise ValueError('Retirement request changed; preserve the original disposition')
            return saved
        saved = dict(request, schema_version=1, status='retired', retired_at=timestamp(), provider_calls=0,
                     coordinator={'owner': owner, 'session': session, 'generation': generation})
        coordinator.require_owner(coordinator.read(), owner, session, generation)
        # The cooperative locks serialize supported writers. Exclusive publication
        # also refuses an unexpected receipt appearing outside those writers.
        staging = path.with_name(path.name + '.' + uuid.uuid4().hex + '.tmp')
        try:
            write_json(staging, saved)
            os.link(staging, path)
        finally:
            staging.unlink(missing_ok=True)
        return saved
