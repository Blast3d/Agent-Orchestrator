"""Notice and explicitly repair a stale or missing task index without mutating canonical results."""
import argparse
from datetime import datetime
import hashlib
from itertools import islice
import json
import os
from pathlib import Path
import re
import stat
import sys
import uuid

from paths import TASKS
from task_store import timestamp, write_json
from task_index import project_task_index, task_index_bytes
from usage_guard import file_lock

_JOB = re.compile(r'[a-f0-9]{32}')
_SHA = re.compile(r'[a-f0-9]{64}')
_RESULT_LIMIT = 4 * 1024 * 1024
_INDEX_LIMIT = 1 * 1024 * 1024
_STATE_FIELDS = ('status', 'execution_status', 'review_status')
_REVIEW_FIELDS = ('reviewer', 'note', 'reviewed_at')
_VALID_STATUS = {
    'preparing', 'pending', 'running', 'awaiting_review', 'accepted', 'rejected',
    'held', 'failed', 'recovery_required',
}
_VALID_EXECUTION = {'pending', 'running', 'succeeded', 'held', 'failed', 'uncertain'}
_VALID_REVIEW = {'pending', 'accepted', 'rejected'}
_IDENTITY_FIELDS = ('schema_version', 'job_id', 'worker', 'created_at', 'canonical_result',
                    'assignment_project_id', 'assignment_id', 'assignment_contract_sha256',
                    'assignment_intent_id', 'revision_of', 'prompt_sha256')
_AUDIT_LIMIT = 128


class SummaryRepairError(ValueError):
    """Safe, descriptive refusal for inspect or repair."""


def _job_id(value):
    if not isinstance(value, str) or not _JOB.fullmatch(value):
        raise SummaryRepairError('Task identifier must be 32 lowercase hexadecimal characters')
    return value


def _sha256_bytes(data):
    return hashlib.sha256(data).hexdigest()


def _is_reparse(path):
    try:
        st = path.lstat()
    except OSError as exc:
        raise SummaryRepairError('Path could not be inspected: ' + type(exc).__name__) from exc
    if path.is_symlink():
        return True
    return bool(getattr(st, 'st_file_attributes', 0) & getattr(stat, 'FILE_ATTRIBUTE_REPARSE_POINT', 0x400))


def _contained(path, root):
    try:
        resolved = path.resolve()
        base = root.resolve()
    except OSError as exc:
        raise SummaryRepairError('Path could not be resolved: ' + type(exc).__name__) from exc
    if not resolved.is_relative_to(base):
        raise SummaryRepairError('Path escapes the task store')
    return resolved


def _reject_redirected(path):
    current = Path(path)
    seen = set()
    while True:
        ident = str(current)
        if ident in seen:
            raise SummaryRepairError('Path cycle is not supported')
        seen.add(ident)
        if _is_reparse(current):
            raise SummaryRepairError('Symlink, junction, or reparse path is not supported')
        if current.parent == current:
            break
        current = current.parent


def _task_paths(job_id, tasks_root):
    root = Path(tasks_root).absolute()
    _reject_redirected(root)
    folder = root / job_id
    _contained(folder, root)
    _reject_redirected(folder.parent)
    if folder.exists():
        _reject_redirected(folder)
        if not folder.is_dir() or folder.is_symlink():
            raise SummaryRepairError('Task location is not a regular directory')
        _contained(folder, root)
    record = folder / 'record.json'
    result = folder / 'result.json'
    lock = folder / 'review.lock'
    for path in (record, result, lock):
        if os.path.lexists(path):
            _reject_redirected(path)
            _contained(path, folder if folder.exists() else root)
            if path.is_dir():
                raise SummaryRepairError(path.name + ' is a directory where a file is required')
    return folder, record, result, lock


def _read_bounded(path, limit, label):
    if not os.path.lexists(path):
        return None
    if path.is_dir():
        raise SummaryRepairError(label + ' is a directory where a file is required')
    _reject_redirected(path)
    before = path.stat()
    if not stat.S_ISREG(before.st_mode):
        raise SummaryRepairError(label + ' is not a regular file')
    size = before.st_size
    if size > limit:
        raise SummaryRepairError(label + ' exceeds the bounded read limit')
    with path.open('rb') as handle:
        if not os.path.samestat(before, os.fstat(handle.fileno())):
            raise SummaryRepairError(label + ' changed while opening')
        data = handle.read(limit + 1)
        after = os.fstat(handle.fileno())
    _reject_redirected(path)
    current = path.stat()
    if (not os.path.samestat(after, current) or before.st_mtime_ns != after.st_mtime_ns
            or after.st_mtime_ns != current.st_mtime_ns or before.st_size != after.st_size):
        raise SummaryRepairError(label + ' changed while reading')
    if len(data) > limit:
        raise SummaryRepairError(label + ' exceeds the bounded read limit')
    return data


def _parse_object(raw, label, required_job=None):
    if raw is None:
        return None
    try:
        text = raw.decode('utf-8-sig')
        def unique(pairs):
            data = {}
            for key, value in pairs:
                if key in data:
                    raise ValueError('Duplicate JSON key')
                data[key] = value
            return data
        def invalid_constant(value):
            raise ValueError('Nonfinite JSON constant')
        data = json.loads(text, object_pairs_hook=unique, parse_constant=invalid_constant)
        json.dumps(data, allow_nan=False)  # Also reject overflow floats such as 1e999.
    except (UnicodeDecodeError, ValueError, TypeError, RecursionError) as exc:
        raise SummaryRepairError(label + ' is malformed JSON') from exc
    if not isinstance(data, dict):
        raise SummaryRepairError(label + ' must be a JSON object')
    job = data.get('job_id')
    if required_job is not None and job != required_job:
        raise SummaryRepairError(label + ' job identity does not match the folder')
    if 'status' in data and not isinstance(data.get('status'), str):
        raise SummaryRepairError(label + ' status is invalid')
    return data


def _review_tuple(data):
    review = data.get('review') if isinstance(data, dict) else None
    if not isinstance(review, dict):
        return None
    return tuple(review.get(key) for key in _REVIEW_FIELDS)


def _canonical_state(data):
    return tuple(data.get(key) for key in _STATE_FIELDS) + (_review_tuple(data), data.get('finalized_at'))


def _coherent(result):
    status = result.get('status')
    execution = result.get('execution_status')
    review = result.get('review_status')
    if (not isinstance(status, str) or not isinstance(execution, str) or not isinstance(review, str)
            or status not in _VALID_STATUS or execution not in _VALID_EXECUTION or review not in _VALID_REVIEW):
        return False
    # Dispatcher save is not protected by review.lock. Only finalized tasks are
    # eligible; a running task must finish instead of racing a summary repair.
    def dated(value):
        try:
            return isinstance(value, str) and datetime.fromisoformat(value.replace('Z', '+00:00')).tzinfo is not None
        except (ValueError, OverflowError):
            return False
    if not dated(result.get('finalized_at')):
        return False
    if status in ('accepted', 'rejected'):
        if execution != 'succeeded' or review != status or not result.get('finalized_at'):
            return False
        review_obj = result.get('review')
        if not isinstance(review_obj, dict):
            return False
        if not isinstance(review_obj.get('reviewer'), str) or not review_obj.get('reviewer').strip():
            return False
        if not isinstance(review_obj.get('note'), str) or not review_obj.get('note').strip():
            return False
        return dated(review_obj.get('reviewed_at'))
    expected = {'awaiting_review': 'succeeded', 'held': 'held', 'failed': 'failed',
                'recovery_required': 'uncertain'}
    if expected.get(status) != execution or review != 'pending' or result.get('review') is not None:
        return False
    return True


def _validate_identity(result, record, result_path):
    if (type(result.get('schema_version')) is not int or result['schema_version'] != 1
            or not isinstance(result.get('canonical_result'), str)
            or not Path(result['canonical_result']).is_absolute()
            or Path(result['canonical_result']) != result_path):
        raise SummaryRepairError('Canonical source identity does not match this task location')
    if record is not None and any(record.get(key) != result.get(key) for key in _IDENTITY_FIELDS):
        raise SummaryRepairError('Task index has conflicting source or assignment identity')


def _project(result):
    return project_task_index(result)


def _projection_bytes(result):
    # The larger legacy read limit permits repair; every replacement must fit
    # the current dashboard's small-index bound and the normal writer projection.
    try:
        return task_index_bytes(_project(result))
    except ValueError as exc:
        raise SummaryRepairError(str(exc)) from exc


def summary_notices(record, result):
    """Pure notices for Inbox: index disagreement, missing index, or audit error. No payloads."""
    notices = []
    if result is not None and not isinstance(result, dict):
        return ['The saved answer could not be compared with its summary.']
    if record is not None and not isinstance(record, dict):
        notices.append('The saved task summary could not be read.')
        record = None
    if result is None:
        return notices
    if record is None:
        notices.append('The small task index is missing; the saved answer is still the canonical record.')
    else:
        mismatches = []
        for key in _STATE_FIELDS:
            if record.get(key) != result.get(key):
                mismatches.append(key.replace('_', ' '))
        record_review = _review_tuple(record)
        result_review = _review_tuple(result)
        if record_review != result_review:
            mismatches.append('review')
        if record.get('finalized_at') != result.get('finalized_at'):
            mismatches.append('finalized time')
        if record.get('job_id') != result.get('job_id') and record.get('job_id') is not None:
            mismatches.append('task identity')
        if mismatches:
            notices.append(
                'The small task index disagrees with the canonical saved answer on: '
                + ', '.join(mismatches) + '. The saved answer remains authoritative.'
            )
        elif record != _project(result):
            notices.append('The small task index has stale summary fields; the saved answer remains authoritative.')
    audit = result.get('contribution_audit') if isinstance(result.get('contribution_audit'), dict) else None
    if audit and audit.get('status') == 'error':
        notices.append('Contribution audit recorded an error; inspect the original task before treating follow-up as complete.')
    return notices


def _stat_fingerprint(path):
    if not path.exists():
        return None
    st = path.stat()
    return {'size': st.st_size, 'mtime_ns': st.st_mtime_ns, 'ino': st.st_ino, 'dev': st.st_dev}


def _audit_state(folder):
    audit_dir = folder / 'summary-repair-audit'
    if not os.path.lexists(audit_dir):
        return {'unresolved': [], 'completed': [], 'needs_review': False}
    _reject_redirected(audit_dir)
    if not audit_dir.is_dir():
        raise SummaryRepairError('Repair audit location is not a directory')
    entries = list(islice(audit_dir.iterdir(), _AUDIT_LIMIT + 1))
    if len(entries) > _AUDIT_LIMIT:
        return {'unresolved': [], 'completed': [], 'needs_review': True, 'notice': 'Repair history exceeds the inspection bound; inspect private evidence.'}
    intents, completed = {}, {}
    try:
        for path in entries:
            if path.suffix != '.json':
                continue
            item = _parse_object(_read_bounded(path, 65536, 'Repair audit'), 'Repair audit', folder.name)
            if item is None or item.get('phase') not in ('intent', 'applied', 'index_write_failed'):
                raise SummaryRepairError('Repair audit is malformed')
            if item['phase'] == 'intent':
                intents[path.name] = item
            elif item['phase'] == 'applied':
                completed[item.get('intent_file')] = item
        unresolved = [name for name, item in intents.items() if name not in completed
                      or any(completed[name].get(key) != item.get(key)
                             for key in ('result_sha256', 'projected_index_sha256', 'prior_index_sha256'))
                      or completed[name].get('index_sha256_after') != item.get('projected_index_sha256')]
        invalid = any(name not in intents for name in completed)
        return {'unresolved': unresolved, 'completed': list(completed), 'needs_review': bool(unresolved) or invalid}
    except (OSError, ValueError, TypeError):
        return {'unresolved': [], 'completed': [], 'needs_review': True, 'notice': 'Repair history is incomplete or unreadable; inspect private evidence.'}


def inspect_summary(job_id, *, tasks_root=TASKS):
    job_id = _job_id(job_id)
    folder, record_path, result_path, _lock = _task_paths(job_id, tasks_root)
    if not folder.exists():
        raise SummaryRepairError('Task folder is missing')
    raw_result = _read_bounded(result_path, _RESULT_LIMIT, 'Canonical result')
    if raw_result is None:
        raise SummaryRepairError('Canonical result is missing or unreadable')
    result = _parse_object(raw_result, 'Canonical result', job_id)
    if not _coherent(result):
        raise SummaryRepairError('Canonical lifecycle evidence is not coherent enough to project a summary')
    raw_record = _read_bounded(record_path, _INDEX_LIMIT, 'Task index')
    record = None
    index_status = 'missing'
    if raw_record is not None:
        try:
            record = _parse_object(raw_record, 'Task index', job_id)
            index_status = 'present'
        except SummaryRepairError:
            raise SummaryRepairError('Task index is malformed or has a conflicting identity; inspect it rather than guessing')
    projected = _project(result)
    _validate_identity(result, record, result_path)
    projected_bytes = _projection_bytes(result)
    result_sha = _sha256_bytes(raw_result)
    index_sha = _sha256_bytes(raw_record) if raw_record is not None else None
    projected_sha = _sha256_bytes(projected_bytes)
    matching = record == projected
    notices = summary_notices(record, result)
    history = _audit_state(folder)
    if history['needs_review']:
        notices.append('A prior repair has incomplete evidence; a matching index alone does not prove that repair completed.')
    action = 'no_op' if matching else 'repair_index'
    return {
        'schema_version': 1,
        'job_id': job_id,
        'action': action,
        'index_status': index_status,
        'index_matches_projection': matching,
        'expected_result_sha256': result_sha,
        'result_sha256': result_sha,
        'index_sha256': index_sha,
        'projected_index_sha256': projected_sha,
        'canonical_status': result.get('status'),
        'canonical_execution_status': result.get('execution_status'),
        'canonical_review_status': result.get('review_status'),
        'notices': notices,
        'repair_applied': None if history['needs_review'] else bool(history['completed']),
        'repair_history': history,
        'needs_review': action == 'repair_index' or history['needs_review'],
    }


def _validate_reviewer_note(reviewer, note):
    if not isinstance(reviewer, str) or not reviewer.strip():
        raise SummaryRepairError('Repair needs a named reviewer')
    if not isinstance(note, str) or len(note.strip()) < 20 or len(note.split()) < 4:
        raise SummaryRepairError('Repair needs a substantive validation note')
    if len(reviewer) > 160 or len(note) > 4000:
        raise SummaryRepairError('Repair reviewer or note exceeds the evidence size limit')
    return reviewer.strip(), note.strip()


def _write_audit(folder, payload):
    audit_dir = folder / 'summary-repair-audit'
    audit_dir.mkdir(parents=True, exist_ok=True)
    _reject_redirected(audit_dir)
    name = timestamp().replace(':', '').replace('+', '_') + '-' + uuid.uuid4().hex[:8] + '.json'
    path = audit_dir / name
    write_json(path, payload)
    return str(path.name)


def repair_summary(job_id, *, expected_result_sha256, reviewer, note, tasks_root=TASKS):
    job_id = _job_id(job_id)
    if not isinstance(expected_result_sha256, str) or not _SHA.fullmatch(expected_result_sha256):
        raise SummaryRepairError('expected_result_sha256 must be a 64-character SHA-256 hex digest from inspect')
    reviewer, note = _validate_reviewer_note(reviewer, note)
    folder, record_path, result_path, lock_path = _task_paths(job_id, tasks_root)
    if not folder.exists():
        raise SummaryRepairError('Task folder is missing')
    with file_lock(lock_path):
        _reject_redirected(result_path)
        _reject_redirected(folder)
        raw_result = _read_bounded(result_path, _RESULT_LIMIT, 'Canonical result')
        if raw_result is None:
            raise SummaryRepairError('Canonical result is missing or unreadable')
        current_sha = _sha256_bytes(raw_result)
        if current_sha != expected_result_sha256:
            raise SummaryRepairError('Canonical result hash does not match the inspect digest; source changed, refusing repair')
        result = _parse_object(raw_result, 'Canonical result', job_id)
        if not _coherent(result):
            raise SummaryRepairError('Canonical lifecycle evidence is not coherent enough to project a summary')
        result_stat = _stat_fingerprint(result_path)
        raw_record = _read_bounded(record_path, _INDEX_LIMIT, 'Task index')
        record = None
        if raw_record is not None:
            record = _parse_object(raw_record, 'Task index', job_id)
        _validate_identity(result, record, result_path)
        record_stat = _stat_fingerprint(record_path)
        projected = _project(result)
        projected_bytes = _projection_bytes(result)
        history = _audit_state(folder)
        if history['needs_review']:
            raise SummaryRepairError('Prior repair evidence is unresolved; inspect it before another repair')
        if record == projected:
            return {
                'schema_version': 1,
                'job_id': job_id,
                'status': 'no_op',
                'applied': False,
                'reason': 'Index already matches the canonical projection',
                'result_sha256': current_sha,
                'index_sha256': _sha256_bytes(raw_record),
                'notices': summary_notices(record, result),
            }
        intent = {
            'schema_version': 1,
            'phase': 'intent',
            'job_id': job_id,
            'reviewer': reviewer,
            'note': note,
            'result_sha256': current_sha,
            'prior_index_sha256': _sha256_bytes(raw_record) if raw_record is not None else None,
            'projected_index_sha256': _sha256_bytes(projected_bytes),
            'applied_at': None,
        }
        try:
            intent_name = _write_audit(folder, intent)
        except OSError as exc:
            raise SummaryRepairError('Repair intent evidence could not be written: ' + type(exc).__name__) from exc
        still = _read_bounded(result_path, _RESULT_LIMIT, 'Canonical result')
        if still != raw_result or _stat_fingerprint(result_path) != result_stat:
            raise SummaryRepairError('Canonical result changed after inspect; refusing replacement')
        if (_read_bounded(record_path, _INDEX_LIMIT, 'Task index') != raw_record
                or _stat_fingerprint(record_path) != record_stat):
            raise SummaryRepairError('Task index changed while repair was being prepared; refusing replacement')
        _reject_redirected(folder)
        if record_path.exists():
            _reject_redirected(record_path)
            if record_path.is_dir():
                raise SummaryRepairError('Task index is a directory where a file is required')
        try:
            write_json(record_path, projected)
        except OSError as exc:
            failure = dict(intent, phase='index_write_failed', error=type(exc).__name__, intent_file=intent_name)
            try:
                _write_audit(folder, failure)
            except OSError:
                pass
            raise SummaryRepairError('Index replacement failed after intent was recorded; inspect summary-repair-audit') from exc
        if record_path.is_symlink() or _is_reparse(record_path):
            raise SummaryRepairError('Index file was redirected during replacement')
        written = _read_bounded(record_path, _INDEX_LIMIT, 'Task index')
        if written != projected_bytes:
            raise SummaryRepairError('Index replacement could not be verified; inspect the retained repair intent')
        final_result = _read_bounded(result_path, _RESULT_LIMIT, 'Canonical result')
        if final_result != raw_result:
            raise SummaryRepairError('Canonical result bytes changed during repair')
        success = dict(
            intent,
            phase='applied',
            applied_at=timestamp(),
            intent_file=intent_name,
            result_sha256_after=_sha256_bytes(final_result),
            index_sha256_after=_sha256_bytes(written) if written is not None else None,
        )
        try:
            success_name = _write_audit(folder, success)
        except OSError as exc:
            raise SummaryRepairError(
                'Index was replaced but success evidence could not be written: ' + type(exc).__name__
                + '; inspect summary-repair-audit intent ' + intent_name
            ) from exc
        return {
            'schema_version': 1,
            'job_id': job_id,
            'status': 'repaired',
            'applied': True,
            'result_sha256': current_sha,
            'index_sha256': _sha256_bytes(written) if written is not None else None,
            'intent_evidence': intent_name,
            'success_evidence': success_name,
            'notices': summary_notices(_parse_object(written, 'Task index', job_id), result),
        }


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest='command', required=True)
    inspect_p = sub.add_parser('inspect', help='Plan a summary repair without writing')
    inspect_p.add_argument('job_id')
    inspect_p.add_argument('--tasks-root', type=Path, default=None)
    repair_p = sub.add_parser('repair', help='Rebuild the small index from the canonical result')
    repair_p.add_argument('job_id')
    repair_p.add_argument('--expected-result-sha256', required=True)
    repair_p.add_argument('--reviewer', required=True)
    repair_p.add_argument('--note', required=True)
    repair_p.add_argument('--tasks-root', type=Path, default=None)
    args = parser.parse_args(argv)
    root = args.tasks_root if args.tasks_root is not None else TASKS
    try:
        if args.command == 'inspect':
            payload = inspect_summary(args.job_id, tasks_root=root)
        else:
            payload = repair_summary(
                args.job_id,
                expected_result_sha256=args.expected_result_sha256,
                reviewer=args.reviewer,
                note=args.note,
                tasks_root=root,
            )
    except (SummaryRepairError, OSError, TimeoutError) as exc:
        message = str(exc) if isinstance(exc, SummaryRepairError) else 'Repair evidence could not be accessed safely: ' + type(exc).__name__
        print(json.dumps({'ok': False, 'error': message}))
        return 1
    print(json.dumps(payload))
    return 0


if __name__ == '__main__':
    raise SystemExit(main(sys.argv[1:]))
