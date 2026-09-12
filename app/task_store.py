"""Durable task evidence and explicit review, separate from model execution."""
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import uuid

from usage_guard import file_lock
from task_index import project_task_index, task_index_bytes


def write_json(path, value):
    temporary = path.with_name(path.name + '.' + uuid.uuid4().hex + '.tmp')
    try:
        with temporary.open('x', encoding='utf-8') as handle:
            json.dump(value, handle, indent=2)
            handle.write('\n')
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def timestamp():
    return datetime.now(timezone.utc).isoformat()


class OutputClaim:
    """Reserve an export path exclusively and detect replacement while a job runs."""
    def __init__(self, path, job_id):
        self.path = Path(path).absolute()
        self.handle = self.path.open('x+', encoding='utf-8')
        try:
            self.identity = os.fstat(self.handle.fileno())
            json.dump({'job_id': job_id, 'status': 'reserved_output'}, self.handle)
            self.handle.flush()
            os.fsync(self.handle.fileno())
        except BaseException:
            self.handle.close()
            raise

    def write(self, value):
        if self.path.is_symlink():
            raise OSError('Export path was replaced by a link')
        current = self.path.stat()
        if not os.path.samestat(self.identity, current):
            raise OSError('Export path was replaced while the task ran')
        self.handle.seek(0)
        self.handle.truncate()
        json.dump(value, self.handle, indent=2)
        self.handle.write('\n')
        self.handle.flush()
        os.fsync(self.handle.fileno())

    def close(self):
        self.handle.close()


class TaskStore:
    def __init__(self, root):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)

    def directory(self, job_id):
        if not re.fullmatch(r'[a-f0-9]{32}', job_id):
            raise ValueError('Invalid task identifier')
        return self.root / job_id

    def create(self, **metadata):
        job_id = uuid.uuid4().hex
        directory = self.directory(job_id)
        record = dict(metadata, schema_version=1, job_id=job_id, created_at=timestamp(),
                      started_at=None, ended_at=None, status='preparing', execution_status='pending',
                      review_status='pending', canonical_result=str(directory / 'result.json'))
        index = project_task_index(record)
        task_index_bytes(index)
        directory.mkdir()
        write_json(directory / 'record.json', index)
        return record

    def save(self, job_id, result):
        directory = self.directory(job_id)
        if result.get('finalized_at'):
            try:
                from contribution_tasks import write_task_audit
                result['contribution_audit'] = write_task_audit(directory, result)
            except Exception as exc:
                # An audit failure must not erase the answer or prevent quota cleanup.
                result['contribution_audit'] = {'status': 'error', 'error': type(exc).__name__}
        write_json(directory / 'result.json', result)
        # Preserve the canonical answer even if an unexpected large index is rejected.
        record = project_task_index(result)
        task_index_bytes(record)
        write_json(directory / 'record.json', record)

    def audit(self, job_id, ledger=None):
        directory = self.directory(job_id)
        with file_lock(directory / 'review.lock'):
            result = json.loads((directory / 'result.json').read_text(encoding='utf-8'))
            if not result.get('finalized_at'):
                raise ValueError('Task execution must finalize before its contribution audit')
            if ledger is not None:
                from contributions import build_report
                from contribution_tasks import task_ledger
                result['contribution_ledger'] = ledger
                build_report(task_ledger(result))
            self.save(job_id, result)
            if result['contribution_audit']['status'] == 'error':
                raise RuntimeError('Contribution audit could not be saved; inspect the task result')
            return result

    def review(self, job_id, decision, reviewer, note, contributions=None, *, curated_payload=None):
        if decision not in ('accepted', 'rejected'):
            raise ValueError('Review decision must be accepted or rejected')
        if not reviewer.strip() or len(note.strip()) < 20 or len(note.split()) < 4:
            raise ValueError('Review needs a named reviewer and a substantive validation note')
        if curated_payload is not None and decision != 'accepted':
            raise ValueError('Only an accepted task can become a remembered solution')
        directory = self.directory(job_id)
        with file_lock(directory / 'review.lock'):
            result = json.loads((directory / 'result.json').read_text(encoding='utf-8'))
            if (result.get('execution_status') != 'succeeded' or result.get('status') != 'awaiting_review'
                    or not result.get('finalized_at')):
                raise ValueError('Only an unreviewed, successfully executed answer can be reviewed')
            result.update(status=decision, review_status=decision,
                          review={'reviewer': reviewer.strip(), 'note': note.strip(), 'reviewed_at': timestamp()})
            if contributions is not None:
                from contributions import build_report
                from contribution_tasks import task_ledger
                result['contribution_ledger'] = contributions
                build_report(task_ledger(result))
            self.save(job_id, result)
        if decision == 'accepted':
            try:
                from automatic_memory import record_accepted_outcome
                result['memory_outcome'] = record_accepted_outcome(self, job_id, curated_payload=curated_payload)
            except Exception as exc:
                result['memory_outcome'] = {'job_id': job_id, 'status': 'error',
                    'error': type(exc).__name__, 'retryable': True}
        return result
