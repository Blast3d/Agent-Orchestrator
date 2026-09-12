"""Bounded memory-request provenance and explicit reviewer usefulness ratings.

Preparation and requested execution are observable; a provider reading or using
evidence is not. Ratings are reviewer judgments, never inferred from success.
"""
import hashlib
import json
import math
from pathlib import Path
import re

from brain_store import digest, safe_path, scope, text
from task_store import timestamp
from usage_guard import file_lock


RATINGS = ('helped', 'neutral', 'harmful')
POLICIES = ('task_label', 'explicit', 'disabled', 'unscoped')
MAX_CANONICAL_BYTES = 8 * 1024**2
MAX_CONTEXT_CHARS = 64000
_ID = re.compile(r'[a-f0-9]{32}')
_SHA = re.compile(r'[a-f0-9]{64}')


def recall_plan(args):
    """Freeze a project-scoped recall choice without inspecting the brief."""
    query = getattr(args, 'memory_query', None)
    disabled = getattr(args, 'no_memory', False)
    project = getattr(args, 'project', None)
    if type(disabled) is not bool:
        raise ValueError('The no-memory setting must be true or false')
    if query is not None:
        query = text(query, 'Memory query', 500)
    if disabled and query is not None:
        raise ValueError('Choose --memory-query or --no-memory, not both')
    if query is not None and not project:
        raise ValueError('Memory retrieval needs an explicit --project scope')
    if disabled:
        policy, query = 'disabled', None
    elif query is not None:
        policy = 'explicit'
    elif project:
        task = getattr(args, 'task', None)
        if not isinstance(task, str) or not task.strip():
            raise ValueError('Automatic memory retrieval needs a task label')
        query = text(' '.join(task.split())[:500], 'Task memory query', 500)
        policy = 'task_label'
    else:
        policy, query = 'unscoped', None
    return {'policy': policy, 'query': query, 'enabled': query is not None,
            'project_id': project, 'user_id': 'local'}


def contract_fields(plan):
    fields = {'memory_policy': plan['policy']}
    if plan['enabled']:
        fields['memory_query'] = plan['query']
    return fields


def _number(value):
    return value if type(value) in (int, float) and math.isfinite(value) and value >= 0 else None


def _identifier(value, expression):
    return value if isinstance(value, str) and expression.fullmatch(value) else None


def _scope(value):
    try:
        return scope(value)
    except (ValueError, TypeError):
        return None


def _context(result):
    context = result.get('memory_context')
    if not isinstance(context, dict):
        return None, 'Memory lookup details were not recorded.'
    ids = context.get('ids')
    if (not isinstance(ids, list) or len(ids) > 200 or
            any(_identifier(item, _ID) is None for item in ids) or len(ids) != len(set(ids))):
        return None, 'Saved memory identifiers cannot be verified.'
    content = context.get('context')
    if not isinstance(content, str) or len(content) > MAX_CONTEXT_CHARS:
        return None, 'Saved memory context cannot be verified.'
    sha = _identifier(context.get('sha256'), _SHA)
    if sha is None or hashlib.sha256(content.encode('utf-8')).hexdigest() != sha:
        return None, 'Saved memory content no longer matches its fingerprint.'
    project = _scope(context.get('project_id'))
    if project is None or project != result.get('assignment_project_id'):
        return None, 'Saved memory scope does not match the task project.'
    # Older dispatch records predate explicit user scope; preserve the unknown.
    user = _scope(context.get('user_id')) if 'user_id' in context else None
    if 'user_id' in context and user is None:
        return None, 'Saved memory user scope cannot be verified.'
    if user is not None and result.get('memory_user_id', user) != user:
        return None, 'Saved memory user scope does not match the task request.'
    if type(context.get('execution_requested')) is not bool:
        return None, 'Memory execution-request state was not recorded.'
    return {'ids': ids, 'sha256': sha, 'project_id': project, 'user_id': user,
            'trace_id': _identifier(context.get('trace_id'), _ID),
            'lookup_ms': _number(context.get('lookup_ms')),
            'elapsed_ms': _number(context.get('elapsed_ms')),
            'execution_requested': context['execution_requested'],
            'recall_incomplete': context.get('recall_incomplete') is True}, None


def _accepted(result):
    review = result.get('review')
    from brain_store import date
    try:
        finalized = date(result.get('finalized_at'))
    except (ValueError, TypeError):
        return False
    return (result.get('status') == result.get('review_status') == 'accepted' and
            result.get('execution_status') == 'succeeded' and finalized is not None and
            isinstance(result.get('response'), str) and bool(result['response'].strip()) and
            isinstance(review, dict) and isinstance(review.get('reviewer'), str) and
            bool(review['reviewer'].strip()) and isinstance(review.get('note'), str) and
            len(review['note'].strip()) >= 20 and len(review['note'].split()) >= 4)


def _bindings(result, context):
    # Exclude feedback, automatic-memory receipts and contribution bookkeeping.
    # Their later writes must not invalidate the original accepted answer proof.
    source_hash = digest({key: result.get(key) for key in (
        'job_id', 'assignment_project_id', 'response', 'finalized_at', 'review',
        'task', 'category', 'assignment_id')})
    context_hash = digest({key: context.get(key) for key in (
        'ids', 'sha256', 'project_id', 'user_id', 'trace_id', 'execution_requested')})
    return source_hash, context_hash


def _rating_fields(feedback):
    if (not isinstance(feedback, dict) or type(feedback.get('schema_version')) is not int or
            feedback.get('schema_version') != 1 or
            feedback.get('rating') not in RATINGS):
        raise ValueError('Memory usefulness feedback is invalid')
    reviewer = text(feedback.get('reviewer'), 'Feedback reviewer', 100)
    note = text(feedback.get('note'), 'Feedback note', 1000, 20)
    if len(note.split()) < 4:
        raise ValueError('Feedback needs a substantive reviewer note')
    at = text(feedback.get('reviewed_at'), 'Feedback timestamp', 50)
    from brain_store import date
    date(at)
    return {'rating': feedback['rating'], 'reviewer': reviewer, 'note': note, 'reviewed_at': at}


def _feedback(result, context):
    feedback = result.get('memory_feedback')
    if feedback is None:
        return {'status': 'not_evaluated', 'rating': None,
                'reason': 'A reviewer has not evaluated memory usefulness.'}
    try:
        fields = _rating_fields(feedback)
    except (ValueError, TypeError):
        return {'status': 'invalid', 'rating': None,
                'reason': 'Saved usefulness feedback cannot be verified.'}
    if (context is None or not context['ids'] or not context['execution_requested'] or
            context['user_id'] is None or not _accepted(result)):
        return {'status': 'stale', 'rating': None,
                'reason': 'The accepted source or requested memory context is no longer valid.'}
    source_hash, context_hash = _bindings(result, context)
    if (feedback.get('job_id') != result.get('job_id') or
            feedback.get('source_sha256') != source_hash or
            feedback.get('context_binding_sha256') != context_hash):
        return {'status': 'stale', 'rating': None,
                'reason': 'The source, review or memory context changed after this rating.'}
    return dict(fields, status='reviewed', reason='Explicit reviewer judgment; not a causal measurement.')


def summary(result):
    """Return public request stages without prompts, queries or memory content."""
    result = result if isinstance(result, dict) else {}
    context, problem = _context(result)
    base = {'schema_version': 1, 'stage': 'not_recorded', 'label': 'Memory use not recorded',
            'reason': 'This task has no recorded memory request telemetry.',
            'project_id': _scope(result.get('assignment_project_id')), 'user_id': None,
            'memory_ids': [], 'memory_count': 0, 'context_sha256': None,
            'trace_id': None, 'lookup_ms': None, 'elapsed_ms': None,
            'execution_requested': None, 'recall_incomplete': False,
            'provider_read': 'not_observable', 'feedback': _feedback(result, context),
            'memory_policy': result.get('memory_policy') if result.get('memory_policy') in POLICIES else None}
    if 'memory_context' not in result:
        requested = result.get('memory_lookup_requested')
        if requested is False:
            base.update(stage='not_requested', label='Memory lookup not requested',
                        reason='Memory was explicitly disabled or the task has no project scope.')
        elif requested is True:
            base.update(stage='lookup_missing', label='Lookup did not complete',
                        reason='Memory was requested, but no completed lookup was recorded.')
        return base
    if context is None:
        base.update(stage='invalid_context', label='Memory context needs inspection', reason=problem)
        return base
    base.update(project_id=context['project_id'], user_id=context['user_id'],
                memory_ids=context['ids'], memory_count=len(context['ids']),
                context_sha256=context['sha256'], trace_id=context['trace_id'],
                lookup_ms=context['lookup_ms'], elapsed_ms=context['elapsed_ms'],
                execution_requested=context['execution_requested'], recall_incomplete=context['recall_incomplete'])
    if not context['ids']:
        base.update(stage='empty', label='No memories matched',
                    reason='The scoped lookup returned no memories for this task.')
    elif context['execution_requested']:
        base.update(stage='execution_requested', label='Memory included in requested input',
                    reason='Execution was requested with this context; provider reading and usefulness are not inferred.')
    else:
        base.update(stage='prepared', label='Memory prepared; execution not requested',
                    reason='Context was assembled, but this task did not request worker execution.')
    return base


def review_evidence(root, project_id, job_id):
    """Read the exact accepted answer and requested context for a local review.

    No task store is created, no memory is searched and no model is started.
    The returned fingerprints let a later rating reject changed source evidence.
    """
    project_id = scope(project_id)
    if _identifier(job_id, _ID) is None:
        raise ValueError('Choose an exact task identifier')
    root = Path(root).absolute()
    from task_progress_view import _bounded_read
    raw, oversized, error = _bounded_read(root / 'runs/tasks' / job_id / 'result.json',
                                           MAX_CANONICAL_BYTES, root)
    if raw is None or oversized or error:
        raise ValueError('Task evidence could not be read safely within its size limit')
    try:
        result = json.loads(raw.decode('utf-8-sig'))
    except (UnicodeError, ValueError, TypeError, RecursionError):
        raise ValueError('Task evidence is missing or invalid') from None
    if (not isinstance(result, dict) or result.get('job_id') != job_id or
            result.get('assignment_project_id') != project_id):
        raise ValueError('That task is not available in this project')
    if not _accepted(result):
        raise ValueError('Memory review needs a finalized accepted task and its review')
    context, problem = _context(result)
    if context is None:
        raise ValueError(problem)
    if not context['ids'] or not context['execution_requested']:
        raise ValueError('Memory review needs nonempty context in requested worker input')
    if context['user_id'] != 'local' or result.get('memory_user_id', 'local') != 'local':
        raise ValueError('That task memory is not available for this user')
    source_hash, context_hash = _bindings(result, context)
    answer, saved_context = result['response'], result['memory_context']['context']
    from project_memory_usage import _label
    return {'job_id': job_id, 'project_id': project_id, 'task': _label(result.get('task')),
            'memory': summary(result), 'accepted_answer': answer[:12000],
            'saved_context': saved_context[:8000],
            'accepted_answer_truncated': len(answer) > 12000,
            'saved_context_truncated': len(saved_context) > 8000,
            'accepted_answer_chars': len(answer), 'saved_context_chars': len(saved_context),
            'source_sha256': source_hash, 'context_binding_sha256': context_hash}


def record_feedback(task_store, job_id, rating, reviewer, note, *, expected_project_id=None,
                    expected_user_id=None, expected_source_sha256=None, expected_context_binding_sha256=None):
    """Save one bounded rating against the current accepted answer and context.

    This shares the canonical review lock but leaves response and review intact.
    Repeating an identical rating is idempotent. A current rating may be revised;
    stale feedback is preserved for inspection instead of silently rebound.
    """
    candidate = {'schema_version': 1, 'job_id': job_id, 'rating': rating,
                 'reviewer': reviewer, 'note': note, 'reviewed_at': timestamp()}
    fields = _rating_fields(candidate)
    candidate.update(fields)
    expected_project_id = scope(expected_project_id) if expected_project_id is not None else None
    expected_user_id = scope(expected_user_id, 'User') if expected_user_id is not None else None
    expected = (expected_source_sha256, expected_context_binding_sha256)
    if any(value is not None for value in expected) and any(_identifier(value, _SHA) is None for value in expected):
        raise ValueError('Provide both fingerprints from the displayed review evidence')
    root = Path(task_store.root).absolute()
    directory = safe_path(root, task_store.directory(job_id))
    with file_lock(safe_path(root, directory / 'review.lock')):
        path = safe_path(root, directory / 'result.json')
        from task_progress_view import _bounded_read
        raw, oversized, error = _bounded_read(path, MAX_CANONICAL_BYTES, root)
        if raw is None or oversized or error:
            raise ValueError('Canonical source task could not be read safely within its size limit')
        result = json.loads(raw.decode('utf-8-sig'))
        if not isinstance(result, dict) or result.get('job_id') != job_id:
            raise ValueError('Canonical source task identifier does not match')
        if expected_project_id is not None and result.get('assignment_project_id') != expected_project_id:
            raise ValueError('That task is not available in this project')
        if not _accepted(result):
            raise ValueError('Usefulness feedback requires a finalized accepted task and its review')
        context, problem = _context(result)
        if context is None:
            raise ValueError(problem)
        if not context['ids'] or not context['execution_requested']:
            raise ValueError('Usefulness feedback requires nonempty memory in requested worker input')
        if context['user_id'] is None:
            raise ValueError('Usefulness feedback requires a recorded memory user scope')
        if expected_user_id is not None and context['user_id'] != expected_user_id:
            raise ValueError('That task memory is not available for this user')
        source_hash, context_hash = _bindings(result, context)
        if expected_source_sha256 is not None and expected != (source_hash, context_hash):
            raise ValueError('The displayed review evidence changed; reopen it before rating memory')
        previous = result.get('memory_feedback')
        if previous is not None and _feedback(result, context)['status'] != 'reviewed':
            raise ValueError('Previous feedback no longer matches this task; preserve it for inspection')
        candidate.update(source_sha256=source_hash, context_binding_sha256=context_hash)
        if previous and all(previous.get(key) == candidate[key] for key in candidate if key != 'reviewed_at'):
            return summary(result)
        result['memory_feedback'] = candidate
        task_store.save(job_id, result)
        return summary(result)
