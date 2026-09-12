"""Project-scoped assignment receipts; identity claims never invoke a worker."""
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import re
import uuid

from task_store import timestamp, write_json
from usage_guard import file_lock


class AssignmentConflict(ValueError):
    """An explicit assignment identity already represents different work."""


class AssignmentIncomplete(RuntimeError):
    """Saved identity evidence needs inspection; automatic redispatch is unsafe."""


_ID = re.compile(r'[A-Za-z0-9][A-Za-z0-9._:-]{0,159}')
_JOB = re.compile(r'[a-f0-9]{32}')
_SHA = re.compile(r'[a-f0-9]{64}')
_CONTRACT_KEYS = {'worker', 'prompt_sha256', 'size', 'category', 'claude_model',
                  'claude_effort', 'require_brief_check'}
_ASSIGNMENT_FIELDS = {'assignment_project_id', 'assignment_id',
                      'assignment_contract_sha256', 'assignment_intent_id', 'revision_of'}
_TASK_FIELDS = {'job_id', 'schema_version', 'created_at', 'started_at', 'ended_at',
                'status', 'execution_status', 'review_status', 'canonical_result'}


def _identifier(value, label):
    if not isinstance(value, str) or not _ID.fullmatch(value):
        raise ValueError(label + ' must be a 1-160 character identifier, not a path')
    return value


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError('Duplicate JSON key')
        result[key] = value
    return result


def _read_json(path):
    try:
        if path.is_symlink():
            raise ValueError('Linked evidence is not supported')
        data = json.loads(path.read_text(encoding='utf-8'), object_pairs_hook=_unique_object)
        if not isinstance(data, dict):
            raise ValueError('Expected a JSON object')
        return data
    except (OSError, ValueError, TypeError) as exc:
        raise AssignmentIncomplete('Assignment evidence is missing or invalid; inspect it before retrying') from exc


def _contract(value):
    if not isinstance(value, dict) or not _CONTRACT_KEYS <= set(value) or set(value)-_CONTRACT_KEYS-{'memory_query','memory_policy','timeout_seconds'}:
        raise ValueError('Assignment contract must contain declared identity fields and supported optional settings')
    if 'timeout_seconds' in value:
        from execution_limits import timeout_for_task
        timeout_for_task(value['size'],value['timeout_seconds'])
        if value['timeout_seconds'] is None:
            raise ValueError('Explicit timeout identity must be an integer')
    if 'memory_query' in value and (not isinstance(value['memory_query'], str) or not 1 <= len(value['memory_query'].strip()) <= 500):
        raise ValueError('Memory query identity must contain 1 to 500 characters')
    if 'memory_policy' in value:
        from memory_usage import POLICIES
        if value['memory_policy'] not in POLICIES:
            raise ValueError('Memory policy identity is invalid')
        if (value['memory_policy'] in ('task_label', 'explicit')) != ('memory_query' in value):
            raise ValueError('Memory policy and query identity must agree')
    if not isinstance(value['worker'], str) or not value['worker'].strip():
        raise ValueError('Assignment contract requires a worker')
    if not isinstance(value['prompt_sha256'], str) or not _SHA.fullmatch(value['prompt_sha256']):
        raise ValueError('Assignment contract requires a SHA-256 prompt fingerprint')
    if value['size'] not in ('tiny', 'small', 'medium', 'large'):
        raise ValueError('Assignment contract has an invalid task size')
    if not isinstance(value['category'], str) or not value['category'].strip():
        raise ValueError('Assignment contract requires a category')
    if type(value['require_brief_check']) is not bool:
        raise ValueError('Assignment brief-check setting must be true or false')
    for key in ('claude_model', 'claude_effort'):
        item = value[key]
        if value['worker'] == 'claude':
            if not isinstance(item, str) or not item.strip():
                raise ValueError('Claude assignment requires its model and effort')
        elif item is not None:
            raise ValueError('Non-Claude assignments must have null Claude settings')
    normalized = deepcopy(value)
    encoded = json.dumps(normalized, sort_keys=True, separators=(',', ':'), allow_nan=False)
    return normalized, hashlib.sha256(encoded.encode('utf-8')).hexdigest()


class AssignmentReceipts:
    """Claim once under a project lock, then return the same canonical task.

    A durable intent precedes task creation. If either task creation or receipt
    completion is interrupted, the intent remains and later calls fail closed.
    This helper never retries, finishes reservations, or changes review decisions.
    """

    def __init__(self, store):
        self.store = store
        self.root = Path(store.root).parent / 'assignments'

    def _locations(self, project_id):
        parent = Path(self.store.root).parent.resolve()
        if not self.root.resolve().is_relative_to(parent):
            raise AssignmentIncomplete('Assignment directory resolves outside the task store parent')
        digest = hashlib.sha256(project_id.encode('utf-8')).hexdigest()
        path = self.root / (digest + '.json')
        lock = self.root / (digest + '.lock')
        if path.is_symlink() or lock.is_symlink():
            raise AssignmentIncomplete('Linked assignment evidence is not supported')
        return path, lock

    def _existing_assignment(self, project_id, assignment_id=None):
        # Detect a deleted index/entry without treating an unrelated old malformed
        # task as a new identity. Intent records are never reconstructed by guessing.
        for path in Path(self.store.root).glob('*/record.json'):
            try:
                record = _read_json(path)
            except AssignmentIncomplete:
                continue
            if (record.get('assignment_project_id') == project_id and
                    (assignment_id is None or record.get('assignment_id') == assignment_id)):
                return True
        return False

    def _index(self, path, project_id):
        if not path.exists():
            if self._existing_assignment(project_id):
                raise AssignmentIncomplete('Assignment index is missing for recorded work; inspect it before retrying')
            return {'schema_version': 1, 'project_id': project_id, 'assignments': {}}
        data = _read_json(path)
        if (type(data.get('schema_version')) is not int or data['schema_version'] != 1 or
                data.get('project_id') != project_id or not isinstance(data.get('assignments'), dict)):
            raise AssignmentIncomplete('Assignment index does not match its project')
        jobs = set()
        for key, entry in data['assignments'].items():
            try:
                _identifier(key, 'Saved assignment')
                if not isinstance(entry, dict) or entry.get('assignment_id') != key:
                    raise ValueError('Assignment identity mismatch')
                contract, digest = _contract(entry.get('contract'))
                if digest != entry.get('contract_sha256'):
                    raise ValueError('Assignment fingerprint mismatch')
                if not isinstance(entry.get('intent_id'), str) or not _JOB.fullmatch(entry['intent_id']):
                    raise ValueError('Missing intent identity')
                if entry.get('state') not in ('intent', 'claimed'):
                    raise ValueError('Unknown receipt state')
                job = entry.get('job_id')
                if entry['state'] == 'intent' and job is not None:
                    raise ValueError('Pending intent has a task identity')
                if entry['state'] == 'claimed':
                    if not isinstance(job, str) or not _JOB.fullmatch(job) or job in jobs:
                        raise ValueError('Invalid or duplicated task identity')
                    jobs.add(job)
                revision = entry.get('revision_of')
                if revision is not None and (not isinstance(revision, str) or not _JOB.fullmatch(revision)):
                    raise ValueError('Invalid predecessor identity')
                if revision is not None and revision == job:
                    raise ValueError('Self-referencing revision')
                if not isinstance(entry.get('created_at'), str) or not entry['created_at']:
                    raise ValueError('Missing intent timestamp')
            except (ValueError, TypeError, KeyError) as exc:
                raise AssignmentIncomplete('Assignment index contains invalid identity evidence') from exc
        return data

    def _task(self, project_id, assignment_id, entry):
        directory = self.store.directory(entry['job_id'])
        if directory.is_symlink() or not directory.resolve().is_relative_to(Path(self.store.root).resolve()):
            raise AssignmentIncomplete('Assignment task resolves outside the task store')
        record = _read_json(directory / 'record.json')
        result_path = directory / 'result.json'
        if result_path.exists():
            current = _read_json(result_path)
        elif (record.get('status') == 'preparing' and record.get('execution_status') == 'pending'
                and not record.get('started_at') and not record.get('finalized_at')):
            current = record
        else:
            raise AssignmentIncomplete('Canonical assignment result is missing; inspect the original task')
        expected = {'job_id': entry['job_id'], 'assignment_project_id': project_id,
                    'assignment_id': assignment_id, 'assignment_contract_sha256': entry['contract_sha256'],
                    'assignment_intent_id': entry['intent_id'], 'revision_of': entry.get('revision_of')}
        for document in (record, current):
            if any(document.get(key) != value for key, value in expected.items()):
                raise AssignmentIncomplete('Canonical task does not match its assignment receipt')
            if any(document.get(key) != entry['contract'][key]
                   for key in ('worker', 'prompt_sha256', 'size', 'category')):
                raise AssignmentIncomplete('Canonical task contract does not match its assignment receipt')
            if document.get('canonical_result') != str(directory / 'result.json'):
                raise AssignmentIncomplete('Canonical result location does not match its task')
            if (document.get('status') not in ('preparing', 'running', 'awaiting_review', 'accepted',
                                               'rejected', 'held', 'failed', 'recovery_required')
                    or document.get('execution_status') not in ('pending', 'running', 'succeeded',
                                                               'held', 'failed', 'uncertain')
                    or document.get('review_status') not in ('pending', 'accepted', 'rejected')):
                raise AssignmentIncomplete('Canonical task lifecycle evidence is invalid')
            if (document['status'] in ('accepted', 'rejected') and
                    (document['execution_status'] != 'succeeded' or not document.get('finalized_at')
                     or document['review_status'] != document['status'])):
                raise AssignmentIncomplete('Canonical review evidence is inconsistent')
            errors = document.get('cleanup_errors', [])
            if not isinstance(errors, list) or any(not isinstance(item, dict) for item in errors):
                raise AssignmentIncomplete('Canonical cleanup evidence is invalid')
            for field, key in (('requested_model', 'claude_model'), ('requested_effort', 'claude_effort'),
                               ('require_brief_check', 'require_brief_check')):
                if field in document and document[field] != entry['contract'][key]:
                    raise AssignmentIncomplete('Canonical worker settings do not match the assignment contract')
        return current

    def _predecessor(self, index, revision_of, new_key):
        previous = [(key, entry) for key, entry in index['assignments'].items()
                    if entry.get('job_id') == revision_of]
        if len(previous) != 1 or previous[0][0] == new_key:
            raise AssignmentConflict('Revision must name an existing different assignment in the same project')
        key, entry = previous[0]
        if entry['state'] != 'claimed':
            raise AssignmentIncomplete('Predecessor assignment is incomplete')
        task = self._task(index['project_id'], key, entry)
        if (not task.get('finalized_at') or task.get('execution_status') not in ('succeeded', 'failed', 'held')
                or task.get('status') not in ('awaiting_review', 'accepted', 'rejected', 'failed', 'held')
                or task.get('reservation_state') in ('held_for_reconciliation', 'cleanup_failed')
                or (task.get('reservation_id') and task.get('reservation_state') != 'finished_pending_fresh_quota')
                or any(x.get('step') in ('finish', 'canonical_save') for x in task.get('cleanup_errors', []))):
            raise AssignmentIncomplete('Predecessor execution or reservation is unresolved; a revision cannot start')

    def claim(self, project_id, assignment_id, contract, metadata, revision_of=None):
        """Return (current canonical task, reused); only a new claim creates a task."""
        _identifier(project_id, 'Project')
        _identifier(assignment_id, 'Assignment')
        normalized, digest = _contract(contract)
        if revision_of is not None and (not isinstance(revision_of, str) or not _JOB.fullmatch(revision_of)):
            raise ValueError('Revision must reference a canonical task identifier')
        if not isinstance(metadata, dict) or (_ASSIGNMENT_FIELDS | _TASK_FIELDS).intersection(metadata):
            raise ValueError('Task metadata must not supply controlled identity or lifecycle fields')
        if any(metadata.get(key) != normalized[key] for key in ('worker', 'prompt_sha256', 'size', 'category')):
            raise ValueError('Task metadata must match the assignment contract')
        try:
            metadata = json.loads(json.dumps(metadata, allow_nan=False))
        except (ValueError, TypeError) as exc:
            raise ValueError('Task metadata must contain finite JSON values') from exc
        path, lock = self._locations(project_id)
        with file_lock(lock):
            index = self._index(path, project_id)
            entry = index['assignments'].get(assignment_id)
            if entry is not None:
                if entry['contract'] != normalized or entry.get('revision_of') != revision_of:
                    raise AssignmentConflict('Assignment key already represents a different contract; use an explicit new key')
                if entry['state'] != 'claimed':
                    raise AssignmentIncomplete('Assignment creation was interrupted; inspect its intent before retrying')
                return self._task(project_id, assignment_id, entry), True
            if self._existing_assignment(project_id, assignment_id):
                raise AssignmentIncomplete('Assignment receipt is missing for an existing task')
            if revision_of is not None:
                self._predecessor(index, revision_of, assignment_id)
            entry = {'assignment_id': assignment_id, 'state': 'intent', 'intent_id': uuid.uuid4().hex,
                     'contract': normalized, 'contract_sha256': digest, 'revision_of': revision_of,
                     'job_id': None, 'created_at': timestamp()}
            index['assignments'][assignment_id] = entry
            try:
                write_json(path, index)
                record = self.store.create(**metadata, assignment_project_id=project_id,
                    assignment_id=assignment_id, assignment_contract_sha256=digest,
                    assignment_intent_id=entry['intent_id'], revision_of=revision_of)
                entry.update(state='claimed', job_id=record['job_id'])
                write_json(path, index)
            except Exception as exc:
                # Never roll back an intent: task creation may already have happened.
                raise AssignmentIncomplete('Assignment creation did not finish safely; inspect the saved intent') from exc
            return record, False
