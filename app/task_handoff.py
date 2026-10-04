"""Continue confirmed quota-limited work through a frozen, guarded worker plan."""
from copy import copy
from datetime import datetime, timezone
import hashlib
import json
import math
from pathlib import Path
import re
import uuid

from task_store import timestamp, write_json, preflight_without_execution
from usage_guard import file_lock
from claude_models import select_model


WORKERS = frozenset(('claude', 'codex', 'grok', 'local-chat'))
BASIC_LOCAL_CATEGORIES = frozenset(('general', 'chat', 'formatting', 'extraction', 'summarization', 'classification'))
_ID = re.compile(r'[A-Za-z0-9][A-Za-z0-9._:-]{0,159}')
_JOB = re.compile(r'[a-f0-9]{32}')
_LOW = ': task plus safety buffer exceeds available quota'
_ADVISORY_LOW = ': available allowance is at or below the worker start threshold'
_ADVISORY_EMPTY = ': available allowance is exhausted after reservations'
_BLOCKED = ': provider rejected work; cooldown active'
_AUTOMATIC_WORKERS = frozenset(('claude', 'codex', 'grok'))


class HandoffHeld(ValueError):
    """A plan needs inspection or a deliberate new identity before proceeding."""


def _digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':'),
                                     allow_nan=False).encode('utf-8')).hexdigest()


def _finalized(value):
    try:
        return isinstance(value, str) and datetime.fromisoformat(value.replace('Z', '+00:00')).tzinfo is not None
    except ValueError:
        return False


def _number(value):
    return type(value) in (int, float) and math.isfinite(value) and 0 <= value <= 100


def apply_automatic_fallbacks(args, policy, *, project_default=None):
    """Apply the configured cloud-only route without replacing an explicit plan.

    Unscoped invocations get a fresh receipt identity, never a prompt-derived
    identity that could merge unrelated requests. The receipt exposes that
    identity for deliberate retries. Synthetic scope cannot enable memory reads.
    Calling this twice on the same arguments keeps their existing identity.
    """
    if (not isinstance(policy, dict) or policy.get('quota_admission_mode') != 'advisory'
            or getattr(args, 'no_auto_fallback', False)
            or getattr(args, 'fallback_worker', None)
            or getattr(args, 'worker', None) not in _AUTOMATIC_WORKERS):
        return args
    configured = policy.get('automatic_fallbacks', {})
    if not isinstance(configured, dict):
        return args
    candidates = configured.get(args.worker, [])
    if not isinstance(candidates, (list, tuple)):
        return args
    fallback = []
    for worker in candidates:
        if (isinstance(worker, str) and worker in _AUTOMATIC_WORKERS
                and worker != args.worker and worker not in fallback):
            fallback.append(worker)
    if not fallback:
        return args
    if type(getattr(args, 'no_memory', False)) is not bool:
        return args
    project = getattr(args, 'project', None)
    assignment = getattr(args, 'assignment_id', None)
    if not project and isinstance(project_default, str) and _ID.fullmatch(project_default):
        project = project_default
    # Preserve existing validation for invalid identity/recall requests. Automatic
    # routing is not permission to infer a missing explicit project or revision.
    if ((assignment and not project) or (getattr(args, 'revision_of', None) and not assignment)
            or (not project and getattr(args, 'memory_query', None) is not None)):
        return args
    if not project:
        args.project = 'unscoped-worker-dispatch'
        args.no_memory = True
    else:
        args.project = project
    args.automatic_assignment_id_added = not bool(assignment)
    if not assignment:
        args.assignment_id = 'auto-' + uuid.uuid4().hex
    args.fallback_worker = fallback
    args.automatic_fallbacks_applied = True
    return args


def prefer_alternate_decision(quota):
    """Defer low allowance only while an authorized frozen alternate remains."""
    if (quota.get('admission_mode') != 'advisory' or not quota.get('allowed')
            or quota.get('prefer_alternate') is not True or not _number(quota.get('threshold_pct'))):
        return None
    reasons = [w['id'] + _ADVISORY_LOW for w in quota.get('windows', [])
               if isinstance(w, dict) and isinstance(w.get('id'), str)
               and _number(w.get('available_pct')) and w['available_pct'] <= quota['threshold_pct']
               and w.get('reset_passed') is False]
    if not reasons or not _advisory_handoff_reason(quota, reasons, quota['windows']):
        return None
    decision = dict(quota, allowed=False, status='held', reasons=reasons, route_preference=True)
    decision.pop('reservation_id', None)
    return decision


def _advisory_handoff_reason(quota, reasons, windows):
    """Allow cached low readings, but never reinterpret unknown as exhausted."""
    known = {}
    for window in windows:
        if (not isinstance(window, dict) or not isinstance(window.get('id'), str)
                or not window['id'] or window['id'] in known):
            return None
        known[window['id']] = window
    threshold = quota.get('threshold_pct')
    for reason in reasons:
        if not isinstance(reason, str):
            return None
        ending = next((suffix for suffix in (_ADVISORY_LOW, _ADVISORY_EMPTY, _BLOCKED) if reason.endswith(suffix)), None)
        if ending is None:
            return None
        pool = reason[:-len(ending)]
        window = known.get(pool)
        if ending in (_ADVISORY_LOW, _ADVISORY_EMPTY):
            if (window is None or not _number(threshold) or not _number(window.get('remaining_pct'))
                    or not _number(window.get('available_pct'))
                    or window['available_pct'] > threshold
                    or not _finalized(window.get('observed_at'))
                    or window.get('reset_passed') is not False):
                return None
            if ending == _ADVISORY_EMPTY:
                reset = window.get('reset_at')
                known_period = (_finalized(reset) and
                                datetime.fromisoformat(reset.replace('Z', '+00:00')) > datetime.now(timezone.utc))
                pending = window.get('pending_pct', 0)
                if (window['available_pct'] != 0 or not (window.get('freshness') == 'fresh'
                        or (_number(pending) and pending > 0)
                        or (window['remaining_pct'] == 0 and known_period))):
                    return None
        else:
            cooldowns = quota.get('cooldown_active_pools', {})
            until = cooldowns.get(pool) if isinstance(cooldowns, dict) else None
            confirmed = (_finalized(until) and
                         datetime.fromisoformat(until.replace('Z', '+00:00')) > datetime.now(timezone.utc))
            if not confirmed:
                return None
    if any(reason.endswith(_ADVISORY_LOW) for reason in reasons):
        return f'The last known allowance is at or below the {threshold:g}% preference; trying an authorized alternate.'
    if any(reason.endswith(_ADVISORY_EMPTY) for reason in reasons):
        return 'No allowance is available after reservations; trying an authorized alternate.'
    return 'The provider has a confirmed active usage cooldown.'


def quota_handoff_reason(result):
    """Return a plain explanation only for a confirmed, safely ended quota stop."""
    if (not isinstance(result, dict) or not _finalized(result.get('finalized_at'))
            or not isinstance(result.get('job_id'), str) or not _JOB.fullmatch(result['job_id'])):
        return None
    if result.get('reservation_state') in ('held_for_reconciliation', 'cleanup_failed'):
        return None
    if result.get('reservation_id') and result.get('reservation_state') != 'finished_pending_fresh_quota':
        return None
    cleanup = result.get('cleanup_errors', [])
    if not isinstance(cleanup, list) or any(not isinstance(item, dict) or
            item.get('step') in ('canonical_save', 'finish', 'export_close') for item in cleanup):
        return None
    if result.get('status') == 'failed' and result.get('execution_status') == 'failed':
        if result.get('failure_kind') == 'quota_exhausted':
            return 'The worker exited with a confirmed usage-limit rejection.'
        return None
    reviewed_preflight = (result.get('status') == result.get('review_status') == 'rejected'
                          and preflight_without_execution(result))
    if ((result.get('status') != 'held' and not reviewed_preflight) or result.get('execution_status') != 'held'
            or result.get('started_at')):
        return None
    quota = result.get('quota_before')
    if not isinstance(quota, dict) or quota.get('allowed') is not False:
        return None
    reasons, windows = quota.get('reasons'), quota.get('windows')
    if not isinstance(reasons, list) or not reasons or not isinstance(windows, list):
        return None
    if quota.get('admission_mode') == 'advisory':
        return _advisory_handoff_reason(quota, reasons, windows)
    known = {}
    for window in windows:
        if (not isinstance(window, dict) or not isinstance(window.get('id'), str)
                or window['id'] in known or not _number(window.get('remaining_pct'))
                or not _number(window.get('available_pct'))):
            return None
        known[window['id']] = window
    for reason in reasons:
        if not isinstance(reason, str):
            return None
        ending = next((suffix for suffix in (_LOW, _BLOCKED) if reason.endswith(suffix)), None)
        # A low reading plus any stale/unknown/authentication failure is still held.
        if ending is None or reason[:-len(ending)] not in known:
            return None
    return 'The current allowance cannot safely cover this task.'


def _held(args, reason, chain=None):
    return {'status': 'held', 'execution_status': 'held', 'worker': args.worker,
            'reason': reason, 'cleanup_errors': [], 'export_status': 'unclaimed',
            'assignment_reused': False, 'handoff_blocked': True,
            'handoff_chain': chain or [], 'handoff_summary': reason}


def _configuration(args):
    fallback = getattr(args, 'fallback_worker', None) or []
    if not isinstance(fallback, (list, tuple)):
        raise HandoffHeld('Choose an ordered list of fallback workers.')
    order = [args.worker, *fallback]
    if len(order) > 3 or len(set(order)) != len(order) or any(worker not in WORKERS for worker in order):
        raise HandoffHeld('A handoff can use at most three different workers: Claude, Codex, Grok, or an explicitly chosen local worker.')
    category = getattr(args, 'category', 'general')
    if 'local-chat' in order and (args.size not in ('tiny', 'small') or not isinstance(category, str)
                                 or category.strip().lower() not in BASIC_LOCAL_CATEGORIES):
        raise HandoffHeld('The local worker is reserved for tiny or small basic tasks: chat, formatting, extraction, summarization, or classification. Choose a capable cloud worker for this handoff.')
    project, assignment = getattr(args, 'project', None), getattr(args, 'assignment_id', None)
    if any(not isinstance(value, str) or not _ID.fullmatch(value) for value in (project, assignment)):
        raise HandoffHeld('Give this handoff a project and assignment reference so repeated requests can find the same work.')
    brief = Path(args.prompt_file).read_bytes()
    try:
        claude_model = select_model(requested=getattr(args, 'claude_model', None)) if 'claude' in order else None
        if 'codex' in order:
            from codex_worker import select_codex_model
            codex_model = select_codex_model(getattr(args, 'codex_model', None))
    except ValueError as exc:
        raise HandoffHeld(str(exc)) from exc
    contract = {'workers': order, 'prompt_sha256': hashlib.sha256(brief).hexdigest(),
                'size': args.size, 'category': getattr(args, 'category', 'general'),
                'claude_model': claude_model,
                'claude_effort': getattr(args, 'claude_effort', 'medium') if 'claude' in order else None,
                'require_brief_check': getattr(args, 'require_brief_check', False),
                'revision_of': getattr(args, 'revision_of', None)}
    if 'codex' in order:
        # Only Codex plans carry these keys, so existing saved plans keep their identity.
        contract.update(codex_model=codex_model, codex_effort=getattr(args, 'codex_effort', 'medium'))
    from memory_usage import recall_plan, contract_fields
    contract.update(contract_fields(recall_plan(args)))
    if getattr(args,'timeout_seconds',None) is not None:
        from execution_limits import timeout_for_task
        contract['timeout_seconds']=timeout_for_task(args.size,args.timeout_seconds)
        if 'local-chat' in order:
            raise HandoffHeld('Hosted deadline overrides cannot be applied to a local-model fallback')
    if (contract['size'] not in ('tiny', 'small', 'medium', 'large')
            or not isinstance(contract['category'], str) or not contract['category'].strip()
            or type(contract['require_brief_check']) is not bool
            or (contract['revision_of'] is not None and
                (not isinstance(contract['revision_of'], str) or not _JOB.fullmatch(contract['revision_of'])))):
        raise HandoffHeld('The handoff settings need correction before a worker can start.')
    return project, assignment, contract, brief


def _child_key(workflow_id, contract_hash, step, worker):
    return 'handoff:' + workflow_id[:24] + ':' + _digest([workflow_id, contract_hash, step, worker])


def _object(pairs):
    value = {}
    for key, item in pairs:
        if key in value:
            raise HandoffHeld('The saved handoff plan contains conflicting fields; ask Codex to inspect it.')
        value[key] = item
    return value


def _read_plan(path):
    if path.is_symlink():
        raise HandoffHeld('The handoff plan was redirected; ask Codex to inspect it.')
    try:
        value = json.loads(path.read_text(encoding='utf-8'), object_pairs_hook=_object)
        if not isinstance(value, dict):
            raise ValueError('Not an object')
        return value
    except (OSError, ValueError, TypeError, RecursionError) as exc:
        raise HandoffHeld('The saved handoff plan cannot be read. Ask Codex to inspect it before trying again.') from exc


def _existing_child(store, project, workflow_id):
    prefix = 'handoff:' + workflow_id[:24] + ':'
    for path in Path(store.root).glob('*/record.json'):
        try:
            record = json.loads(path.read_text(encoding='utf-8'))
            if (isinstance(record, dict) and record.get('assignment_project_id') == project
                    and isinstance(record.get('assignment_id'), str)
                    and record['assignment_id'].startswith(prefix)):
                return True
        except (OSError, ValueError, TypeError):
            continue
    return False


def _validate_plan(plan, workflow_id, project, assignment, contract):
    if (type(plan.get('schema_version')) is not int or plan['schema_version'] != 1
            or plan.get('workflow_id') != workflow_id or plan.get('project') != project
            or plan.get('assignment_id') != assignment or plan.get('contract') != contract
            or plan.get('contract_sha256') != _digest(contract)):
        raise HandoffHeld('This assignment already has a different handoff plan or brief. Keep its original plan, or ask Codex to prepare a deliberate new assignment.')
    output = plan.get('original_output')
    steps = plan.get('steps')
    if (not isinstance(output, str) or not Path(output).is_absolute()
            or not isinstance(steps, list) or len(steps) > len(contract['workers'])):
        raise HandoffHeld('The saved handoff plan is incomplete; ask Codex to inspect it.')
    previous = contract['revision_of']
    for index, step in enumerate(steps):
        worker = contract['workers'][index]
        key = assignment if index == 0 else _child_key(workflow_id, plan['contract_sha256'], index, worker)
        if (not isinstance(step, dict) or step.get('worker') != worker or step.get('assignment_id') != key
                or step.get('revision_of') != previous or step.get('state') not in ('dispatching', 'returned', 'interrupted')):
            raise HandoffHeld('The saved handoff steps do not agree; ask Codex to inspect them.')
        job = step.get('job_id')
        if job is not None and (not isinstance(job, str) or not _JOB.fullmatch(job)):
            raise HandoffHeld('A saved handoff task reference is invalid; ask Codex to inspect it.')
        if index < len(steps) - 1 and job is None:
            raise HandoffHeld('A handoff predecessor is missing; ask Codex to inspect it.')
        previous = job


def _run_locked(args, dispatch_fn, store, path, snapshot, workflow_id, project, assignment, contract, brief):
    if path.exists():
        plan = _read_plan(path)
        _validate_plan(plan, workflow_id, project, assignment, contract)
    else:
        if _existing_child(store, project, workflow_id):
            raise HandoffHeld('A previous handoff exists but its plan is missing. Ask Codex to inspect the original work before starting anything else.')
        plan = {'schema_version': 1, 'workflow_id': workflow_id, 'project': project,
                'assignment_id': assignment, 'contract': contract, 'contract_sha256': _digest(contract),
                'original_output': str(Path(args.output).absolute()), 'created_at': timestamp(), 'steps': []}
        if snapshot.is_symlink():
            raise HandoffHeld('The saved handoff brief was redirected; ask Codex to inspect it.')
        if not snapshot.exists():
            # Immutable bytes close the gap between different workers reading a changing file.
            with snapshot.open('xb') as handle:
                handle.write(brief)
                handle.flush()
                import os
                os.fsync(handle.fileno())
        if hashlib.sha256(snapshot.read_bytes()).hexdigest() != contract['prompt_sha256']:
            raise HandoffHeld('The saved handoff brief does not match this request; ask Codex to inspect it.')
        write_json(path, plan)
    if snapshot.is_symlink() or not snapshot.is_file() or hashlib.sha256(snapshot.read_bytes()).hexdigest() != contract['prompt_sha256']:
        raise HandoffHeld('The frozen handoff brief is missing or changed. Ask Codex to inspect it before proceeding.')
    chain, predecessor, cause = [], contract['revision_of'], None
    final = None
    for index, worker in enumerate(contract['workers']):
        if snapshot.is_symlink() or hashlib.sha256(snapshot.read_bytes()).hexdigest() != contract['prompt_sha256']:
            return _held(args, 'The frozen handoff brief changed. Ask Codex to inspect it before another worker starts.', chain)
        key = assignment if index == 0 else _child_key(workflow_id, plan['contract_sha256'], index, worker)
        step_args = copy(args)
        step_args.worker, step_args.assignment_id = worker, key
        step_args.claude_model = contract['claude_model']
        if 'codex_model' in contract:
            # The frozen plan holds the resolved model; map it back to its identity.
            from lead_selection import LEADS
            step_args.codex_model = next(lead for lead, option in LEADS.items()
                                         if option['model'] == contract['codex_model'])
            step_args.codex_effort = contract['codex_effort']
        step_args.fallback_worker = []
        step_args.handoff_has_alternate = index < len(contract['workers']) - 1
        step_args.prompt_file = snapshot
        step_args.revision_of = predecessor
        step_args.handoff_from_job_id = predecessor if index else None
        step_args.handoff_reason = cause if index else None
        base = Path(plan['original_output'])
        step_args.output = base if index == 0 else base.with_name(base.stem + f'.handoff-{index}-{worker}' + (base.suffix or '.json'))
        if index == len(plan['steps']):
            plan['steps'].append({'worker': worker, 'assignment_id': key, 'revision_of': predecessor,
                                  'state': 'dispatching', 'job_id': None, 'created_at': timestamp()})
        step = plan['steps'][index]
        step['state'] = 'dispatching'
        write_json(path, plan)
        try:
            result = dispatch_fn(step_args, store=store)
        except Exception as exc:
            step.update(state='interrupted', error_type=type(exc).__name__)
            write_json(path, plan)
            return _held(args, 'A handoff step did not return safely. Ask Codex to check the original task before trying again.', chain)
        if not isinstance(result, dict):
            raise HandoffHeld('A handoff step returned unreadable task evidence; ask Codex to inspect it.')
        job_id = result.get('job_id')
        if step.get('job_id') is not None and job_id != step['job_id']:
            raise HandoffHeld('A handoff assignment returned a different task reference; ask Codex to inspect it.')
        if job_id is not None and (not isinstance(job_id, str) or not _JOB.fullmatch(job_id)):
            raise HandoffHeld('A handoff step returned an invalid task reference; ask Codex to inspect it.')
        next_reason = quota_handoff_reason(result)
        reason = result.get('reason') or result.get('error') or next_reason
        step.update(state='returned', job_id=job_id, status=result.get('status'),
                    execution_status=result.get('execution_status'), reason=reason if isinstance(reason, str) else None,
                    updated_at=timestamp())
        context=result.get('memory_context')
        if isinstance(context,dict):
            step['memory_context_sha256']=context.get('sha256')
            step['memory_revalidated']=True
            previous_hash=plan['steps'][index-1].get('memory_context_sha256') if index else None
            step['memory_context_changed']=bool(previous_hash and previous_hash!=context.get('sha256'))
        write_json(path, plan)
        chain.append({'job_id': job_id, 'worker': worker, 'status': result.get('status'),
                      'execution_status': result.get('execution_status'), 'reason': reason if isinstance(reason, str) else None})
        final = dict(result)
        if job_id is not None and result.get('prompt_sha256') != contract['prompt_sha256']:
            return _held(args, 'The task does not match the saved handoff brief. Ask Codex to inspect it before assigning another worker.', chain)
        if not next_reason or index == len(contract['workers']) - 1:
            break
        predecessor, cause = job_id, next_reason
    final['handoff_chain'] = chain
    final['handoff_plan_id'] = workflow_id
    final['handoff_summary'] = ('The saved answer is ready for review.' if final.get('status') == 'awaiting_review'
                                else 'The handoff stopped at the last saved task; inspect its recorded status before planning more work.')
    return final


def dispatch_with_handoff(args, *, dispatch_fn, store):
    """Use normal guarded dispatch for each distinct worker, with one durable plan."""
    if not getattr(args, 'fallback_worker', None):
        return dispatch_fn(args, store=store)
    try:
        project, assignment, contract, brief = _configuration(args)
        workflow_id = _digest([project, assignment])
        parent = Path(store.root).parent.resolve()
        root = Path(store.root).parent / 'handoffs'
        if root.is_symlink() or not root.resolve().is_relative_to(parent):
            raise HandoffHeld('The handoff folder was redirected; ask Codex to inspect it.')
        path, lock, snapshot = (root / (workflow_id + extension) for extension in ('.json', '.lock', '.brief.txt'))
        if any(item.is_symlink() for item in (path, lock, snapshot)):
            raise HandoffHeld('Saved handoff evidence was redirected; ask Codex to inspect it.')
        # Keep one owner across dispatch; a competing caller receives an immediate hold.
        with file_lock(lock, timeout=0):
            return _run_locked(args, dispatch_fn, store, path, snapshot, workflow_id, project, assignment, contract, brief)
    except TimeoutError:
        return _held(args, 'This assignment is already being handled. Wait for its saved result instead of starting another copy.')
    except HandoffHeld as exc:
        return _held(args, str(exc))
    except (OSError, ValueError, TypeError, KeyError, RecursionError):
        return _held(args, 'The handoff settings or saved evidence could not be read safely. Ask Codex to inspect them before trying again.')
