"""Project-scoped recommendations, explicit skill review and evidence of use."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import re
from threading import BoundedSemaphore, Lock, Thread
import uuid

from brain_store import digest, safe_path, scope
from jev_openrouter import evaluate, load_config
from jev_orchestration_decisions import build
from skill_catalog import catalog, load_selected
from task_store import TaskStore, timestamp, write_json
from usage_guard import file_lock

ID = re.compile(r'^[a-f0-9]{32}$')
HASH = re.compile(r'^[a-f0-9]{64}$')
MAX_PLANS = 500
MAX_PLAN_BYTES = 96 * 1024
_slots = BoundedSemaphore(2)
_running = set()
_running_lock = Lock()


def _text(value, label, maximum, minimum=1):
    if not isinstance(value, str) or not minimum <= len(value.strip()) <= maximum:
        raise ValueError(f'{label} must contain {minimum}..{maximum} characters.')
    return value.strip()


def _reviewer(reviewer, note):
    reviewer = _text(reviewer, 'Reviewer', 100)
    note = _text(note, 'Review evidence', 1000, 20)
    if len(note.split()) < 4:
        raise ValueError('Describe the evidence for this review.')
    return reviewer, note


def _directory(root):
    root = Path(root).resolve()
    directory = safe_path(root, root / 'runtime/skill-plans')
    directory.mkdir(parents=True, exist_ok=True)
    return directory


def _path(root, plan_id):
    if not isinstance(plan_id, str) or not ID.fullmatch(plan_id):
        raise ValueError('Choose an exact skill plan.')
    return safe_path(Path(root).resolve(), _directory(root) / (plan_id + '.json'))


def _save(root, plan):
    if len(json.dumps(plan).encode('utf-8')) > MAX_PLAN_BYTES:
        raise ValueError('Skill plan exceeds its storage limit.')
    write_json(_path(root, plan['id']), plan)


def get_plan(root, project_id, plan_id):
    path = _path(root, plan_id)
    if path.stat().st_size > MAX_PLAN_BYTES:
        raise ValueError('Skill plan exceeds its storage limit.')
    plan = json.loads(path.read_text(encoding='utf-8'))
    if (not isinstance(plan, dict) or plan.get('id') != plan_id
            or plan.get('project_id') != scope(project_id)):
        raise ValueError('That skill plan is unavailable in this project.')
    with _running_lock:
        if ((plan.get('execution') or {}).get('status') in ('starting', 'running')
                and plan_id not in _running):
            plan['execution'].update(status='uncertain', error='Dashboard restarted during execution; inspect the saved task before continuing.')
    return plan


def plans(root, project_id):
    project_id = scope(project_id)
    paths = list(_directory(root).glob('*.json'))
    if len(paths) > MAX_PLANS:
        raise ValueError('Skill plan storage needs review before it can be read.')
    result = []
    for path in paths:
        if path.stat().st_size > MAX_PLAN_BYTES:
            continue
        try:
            plan = json.loads(path.read_text(encoding='utf-8'))
        except (ValueError, OSError, UnicodeError):
            continue
        if (isinstance(plan, dict) and plan.get('project_id') == project_id
                and isinstance(plan.get('created_at'), str) and ID.fullmatch(str(plan.get('id', '')))):
            summary = {k: plan.get(k) for k in ('id', 'project_id', 'task', 'status', 'created_at',
                                               'manifest_sha256', 'recommendation', 'review', 'execution')}
            if summary.get('execution'):
                summary['execution'] = {k: summary['execution'].get(k) for k in ('status', 'job_id', 'run_id')}
            result.append(summary)
    return {'plans': sorted(result, key=lambda p: p['created_at'], reverse=True)[:30]}


def _choose(root, project_id, task, choices):
    state, questions = build('skills', {'task': task, 'catalogue': choices})
    receipt = evaluate(root, project_id, 'skills', state, questions)
    if not isinstance(receipt, dict):
        raise ValueError('JEV returned an invalid receipt.')
    telemetry = {k: receipt[k] for k in ('status', 'model', 'provider', 'provider_calls', 'http_status',
        'input_tokens', 'output_tokens', 'cost_usd', 'elapsed_ms', 'cache', 'reason') if k in receipt}
    if receipt.get('status') != 'ok':
        return None, telemetry, receipt.get('status', 'unavailable')
    answer = receipt.get('answers', {}).get('skills_selection', {})
    confidence = answer.get('confidence')
    available = {c['id'] for c in choices} | {'none'}
    value = answer.get('choice')
    if (answer.get('type') != 'choice' or value not in available
            or isinstance(confidence, bool) or not isinstance(confidence, (int, float))
            or not 0 <= confidence <= 1):
        return None, telemetry, 'invalid_response'
    telemetry.update(choice=value, confidence=confidence)
    threshold = max(.8, load_config(root).get('min_confidence', .8))
    if confidence < threshold:
        return None, telemetry, 'low_confidence'
    return (None if value == 'none' else value), telemetry, ('none' if value == 'none' else 'recommended')


def recommend(root, project_id, task, *, jev=False, run_id=None):
    project_id, task = scope(project_id), _text(task, 'Task', 4000)
    if type(jev) is not bool:
        raise ValueError('Choose whether to ask JEV.')
    if run_id is not None:
        from local_services import RUN_NAME
        if not isinstance(run_id, str) or not RUN_NAME.fullmatch(run_id):
            raise ValueError('Choose an exact orchestration run.')
        run = safe_path(Path(root).resolve(), Path(root).resolve() / '.orchestration' / run_id)
        manifest = json.loads((run / 'run.json').read_text(encoding='utf-8'))
        if manifest.get('project_id') != project_id:
            raise ValueError('The selected run belongs to a different project.')
    current = catalog(root, project_id)
    candidates = [p for p in current['packs'] if p['skills']]
    recommendation = {'status': 'manual', 'pack_id': None, 'skill_id': None}
    decisions = []
    if jev and candidates:
        pack_id, receipt, status = _choose(root, project_id, task, [
            {'id': p['id'], 'description': p['description'][:500]} for p in candidates])
        decisions.append(dict(receipt, stage='pack'))
        recommendation.update(status=status, pack_id=pack_id)
        if pack_id:
            selected = next(p for p in candidates if p['id'] == pack_id)
            words = set(re.findall(r'[a-z0-9]+', task.lower()))
            skills = sorted(selected['skills'], key=lambda s: (
                -len(words & set(re.findall(r'[a-z0-9]+', (s['name'] + ' ' + s['description']).lower()))), s['id']))[:20]
            skill_id, receipt, status = _choose(root, project_id, task, [
                {'id': s['id'], 'description': s['description'][:300]} for s in skills])
            decisions.append(dict(receipt, stage='skill', shortlist_ids=[s['id'] for s in skills]))
            recommendation.update(status=status, skill_id=skill_id)
    elif jev:
        recommendation['status'] = 'empty_catalog'
    # A slow recommendation cannot endorse a catalog changed during the call.
    if catalog(root, project_id)['manifest_sha256'] != current['manifest_sha256']:
        recommendation = {'status': 'catalog_changed', 'pack_id': None, 'skill_id': None}
    plan = {'schema_version': 1, 'id': uuid.uuid4().hex, 'project_id': project_id,
            'created_at': timestamp(), 'task': task, 'run_id': run_id, 'status': 'proposed',
            'manifest_sha256': current['manifest_sha256'], 'recommendation': recommendation,
            'decisions': decisions, 'review': None, 'context': None, 'execution': None}
    with file_lock(_directory(root) / 'catalog.lock'):
        if len(list(_directory(root).glob('*.json'))) >= MAX_PLANS:
            raise ValueError('Skill plan storage is full; archive reviewed plans before adding more.')
        _save(root, plan)
    return plan


def review(root, project_id, plan_id, skill_ids, expected_manifest_sha256, reviewer, note):
    reviewer, note = _reviewer(reviewer, note)
    with file_lock(_path(root, plan_id).with_suffix('.lock')):
        plan = get_plan(root, project_id, plan_id)
        if plan['execution']:
            raise ValueError('Create a new plan to change skills after execution is requested.')
        if expected_manifest_sha256 != plan['manifest_sha256']:
            raise ValueError('Catalog selection changed; reload it before review.')
        current = catalog(root, project_id)
        available = {s['id']: s for p in current['packs'] for s in p['skills']}
        if (not isinstance(skill_ids, list) or not 1 <= len(skill_ids) <= 3
                or any(not isinstance(i, str) or i not in available for i in skill_ids)
                or len(set(skill_ids)) != len(skill_ids)):
            raise ValueError('Choose one to three available skills.')
        hashes = {i: available[i]['sha256'] for i in skill_ids}
        loaded = load_selected(root, project_id, skill_ids, expected_manifest_sha256, hashes)
        plan.update(status='reviewed', context=loaded, review={
            'reviewer': reviewer, 'note': note, 'reviewed_at': timestamp(),
            'skill_ids': skill_ids, 'skill_hashes': hashes, 'context_sha256': loaded['sha256']})
        _save(root, plan)
        return plan


def delivery(root, project_id, plan_id):
    plan = get_plan(root, project_id, plan_id)
    if plan['status'] != 'reviewed' or not plan.get('review') or not plan.get('context'):
        raise ValueError('The lead must review and load skills before worker delivery.')
    review = plan['review']
    loaded = load_selected(root, project_id, review['skill_ids'], plan['manifest_sha256'], review['skill_hashes'])
    if loaded['sha256'] != review['context_sha256'] or loaded['text'] != plan['context']['text']:
        raise ValueError('Reviewed skill context changed; create and review a new plan.')
    return dict(loaded, plan_id=plan_id, project_id=scope(project_id), user_id='local', task=plan['task'],
                review=review, execution_requested=False)


def _canonical(root, project_id, job_id):
    store = TaskStore(Path(root) / 'runs/tasks')
    path = safe_path(Path(root).resolve(), store.directory(job_id) / 'result.json')
    if path.stat().st_size > 8 * 1024**2:
        raise ValueError('Task evidence is oversized.')
    result = json.loads(path.read_text(encoding='utf-8'))
    if result.get('job_id') != job_id or result.get('assignment_project_id') != scope(project_id):
        raise ValueError('That task is unavailable in this project.')
    return store, result


def use_evidence(root, project_id, job_id):
    project_id = scope(project_id)
    _, result = _canonical(root, project_id, job_id)
    context = result.get('skill_context')
    if (result.get('status') != 'accepted' or result.get('review_status') != 'accepted'
            or result.get('execution_status') != 'succeeded' or not result.get('finalized_at')
            or not isinstance(context, dict) or context.get('execution_requested') is not True
            or context.get('project_id') != project_id or context.get('user_id') != 'local'
            or not context.get('skills') or not isinstance(result.get('response'), str)):
        raise ValueError('Skill usefulness needs an accepted answer with recorded skill delivery.')
    text = context.get('text')
    if not isinstance(text, str) or hashlib.sha256(text.encode('utf-8')).hexdigest() != context.get('sha256'):
        raise ValueError('Saved skill delivery cannot be verified.')
    source_hash = digest({k: result.get(k) for k in ('job_id', 'assignment_project_id', 'response', 'finalized_at', 'review')})
    context_hash = digest(context)
    rating = result.get('skill_feedback')
    if rating and (rating.get('source_sha256') != source_hash or rating.get('context_sha256') != context_hash):
        rating = {'status': 'stale'}
    return {'job_id': job_id, 'source_sha256': source_hash, 'context_sha256': context_hash,
            'saved_context': text, 'accepted_answer': result['response'], 'feedback': rating,
            'skills': context['skills'], 'evidence_limit': 'Requested input is not proof of reading; usefulness is reviewer judgment.'}


def _remember_feedback(root, result, rating):
    from brain_store import BrainStore
    names = ', '.join(s['name'] for s in result['skill_context']['skills'])
    payload = {'project_id': result['assignment_project_id'], 'kind': 'episode',
        'title': ('Skill usefulness: ' + result['task'])[:160],
        'content': f'Supplied skills: {names}\nReviewer rated {rating["rating"]}: {rating["note"]}\nExplicit reviewer judgment, not a measured causal effect.',
        'tags': ['skills', 'skill-feedback', rating['rating']], 'importance': .55,
        'episode': {'problem': result['task'][:500], 'action': ('Supplied reviewed task-specific instructions: ' + names)[:1000],
                    'outcome': rating['note']},
        'source': {'type': 'task', 'job_id': result['job_id'], 'review_sha256': digest(result['review']),
                   'skill_feedback_sha256': digest(rating)}}
    store = BrainStore(root)
    memory = store.propose(payload)
    memory = store.approve(memory['id'], rating['reviewer'], rating['note'])
    return {'status': 'remembered', 'memory_id': memory['id']}


def feedback(root, project_id, job_id, rating, reviewer, note, expected_source_sha256, expected_context_sha256):
    reviewer, note = _reviewer(reviewer, note)
    if rating not in ('helped', 'neutral', 'harmful'):
        raise ValueError('Choose helped, neutral or harmful.')
    store = TaskStore(Path(root) / 'runs/tasks')
    with file_lock(store.directory(job_id) / 'review.lock'):
        evidence = use_evidence(root, project_id, job_id)
        if (expected_source_sha256 != evidence['source_sha256']
                or expected_context_sha256 != evidence['context_sha256']):
            raise ValueError('The answer or delivered skills changed; reload review evidence.')
        _, result = _canonical(root, project_id, job_id)
        previous = result.get('skill_feedback')
        candidate = {'schema_version': 1, 'status': 'reviewed', 'rating': rating, 'reviewer': reviewer,
                     'note': note, 'reviewed_at': timestamp(), 'source_sha256': evidence['source_sha256'],
                     'context_sha256': evidence['context_sha256']}
        if previous and all(previous.get(k) == candidate[k] for k in ('rating', 'reviewer', 'note', 'source_sha256', 'context_sha256')):
            return dict(previous, memory_outcome=result.get('skill_feedback_memory', {'status': 'not_recorded'}))
        if result.get('skill_feedback_memory', {}).get('status') == 'writing':
            raise ValueError('A previous feedback write is uncertain; inspect it before writing again.')
        result.update(skill_feedback=candidate, skill_feedback_memory={'status': 'writing'})
        store.save(job_id, result)
        try:
            outcome = _remember_feedback(root, result, candidate)
        except Exception as exc:
            # Do not repeat an uncertain write or infer successful memory from task acceptance.
            outcome = {'status': 'writing', 'error': type(exc).__name__, 'reason': 'Inspect Brain before retrying this write.'}
        result['skill_feedback_memory'] = outcome
        store.save(job_id, result)
        closeout = _close_hosted(root, result.get('run_id'), result) if result.get('run_id') else {'status': 'not_requested'}
        return dict(candidate, memory_outcome=outcome, closeout=closeout)


def execute(root, project_id, plan_id, worker):
    if worker not in ('claude', 'grok', 'codex'):
        raise ValueError('Choose an existing supplied-text worker route.')
    with file_lock(_path(root, plan_id).with_suffix('.lock')):
        plan = get_plan(root, project_id, plan_id)
        if plan.get('execution'):
            return plan  # A click/retry never relaunches a provider call.
        delivery(root, project_id, plan_id)
        if not _slots.acquire(blocking=False):
            raise ValueError('Two skill tasks are running; wait for an existing task to finish.')
        with _running_lock:
            _running.add(plan_id)
        plan['execution'] = {'status': 'starting', 'worker': worker, 'requested_at': timestamp()}
        try:
            _save(root, plan)
        except Exception:
            _release(plan_id)
            raise
    try:
        Thread(target=_execute, args=(Path(root).resolve(), project_id, plan_id, worker), daemon=True).start()
    except Exception:
        _release(plan_id)
        raise
    return plan


def _release(plan_id):
    with _running_lock:
        if plan_id in _running:
            _running.remove(plan_id)
            _slots.release()


def _execution_update(root, project_id, plan_id, **fields):
    with file_lock(_path(root, plan_id).with_suffix('.lock')):
        plan = get_plan(root, project_id, plan_id)
        plan['execution'].update(fields)
        _save(root, plan)
        return plan


def _execute(root, project_id, plan_id, worker):
    import subprocess
    import sys
    try:
        plan = get_plan(root, project_id, plan_id)
        from orchestration_lifecycle import start_run
        # A browser action cannot claim an existing coordinator identity. CLI/native
        # leads can attach a plan to their own exact run through --skill-plan.
        packet = start_run(workspace=root, name='skill-task', objective=plan['task'], query=plan['task'][:500], project=project_id,
                           no_memory=True, root=root)
        run = Path(packet['run'])
        manifest = json.loads((run / 'run.json').read_text(encoding='utf-8'))
        manifest.update(native_work=False, skill_plan_id=plan_id)
        write_json(run / 'run.json', manifest)
        brief = run / 'drafts' / ('skill-' + plan_id + '.md')
        output = run / 'drafts' / ('skill-' + plan_id + '.json')
        brief.write_text('# Objective\n' + plan['task'] + '\n\n# Inputs\nUse the reviewed skill context supplied by the dispatcher. '
            'You have supplied text only and cannot use tools or edit files.\n\n# Required output\n'
            'A concrete answer or proposed patch grounded in the supplied task and instructions.\n\n# Acceptance checks\n'
            'Identify assumptions and unavailable source evidence. Do not claim unperformed tool operations.\n', encoding='utf-8')
        plan = _execution_update(root, project_id, plan_id, status='running', run_id=run.name)
        command = [sys.executable, str(root / 'orchestrator.py'), 'run', worker, '--prompt-file', str(brief),
            '--output', str(output), '--task', plan['task'][:500], '--size', 'small', '--category', 'coding',
            '--project', project_id, '--assignment-id', 'skill-' + plan_id, '--run', str(run),
            '--skill-plan', plan_id, '--require-brief-check', '--no-auto-fallback', '--memory-depth', 'compact']
        if worker == 'claude':
            command += ['--claude-model', 'opus', '--claude-effort', 'low']
        completed = subprocess.run(command, cwd=root, capture_output=True, text=True, timeout=900)
        result = json.loads(output.read_text(encoding='utf-8')) if output.is_file() else None
        if not result:
            _execution_update(root, project_id, plan_id, status='uncertain', error='No canonical export; inspect the saved task before retrying.')
        else:
            status = ('finished' if result.get('execution_status') == 'succeeded' else
                      'uncertain' if result.get('execution_status') == 'uncertain' else 'failed')
            _execution_update(root, project_id, plan_id, status=status, job_id=result.get('job_id'), returncode=completed.returncode,
                response=result.get('response', '')[:12000], review_status=result.get('review_status'),
                error=result.get('reason') or result.get('error'), memory_outcome=result.get('memory_outcome'))
    except Exception as exc:
        _execution_update(root, project_id, plan_id, status='uncertain', error=type(exc).__name__ + ': inspect the saved run before retrying.')
    finally:
        _release(plan_id)


def accept(root, project_id, plan_id, reviewer, note):
    reviewer, note = _reviewer(reviewer, note)
    with file_lock(_path(root, plan_id).with_suffix('.lock')):
        plan = get_plan(root, project_id, plan_id)
        execution = plan.get('execution') or {}
        if execution.get('status') != 'finished' or not execution.get('job_id'):
            raise ValueError('A finished worker answer is required for acceptance.')
        store, result = _canonical(root, project_id, execution['job_id'])
        if (result.get('skill_context') or {}).get('plan_id') != plan_id:
            raise ValueError('The accepted answer belongs to a different skill plan.')
        if result.get('status') != 'accepted':
            result = store.review(result['job_id'], 'accepted', reviewer, note)
        from automatic_memory import record_accepted_outcome
        memory_outcome = record_accepted_outcome(store, result['job_id'])
        closeout = _close_hosted(root, execution.get('run_id'), result)
        execution.update(review_status=result['review_status'], memory_outcome=memory_outcome, closeout=closeout)
        _save(root, plan)
        return plan


def _close_hosted(root, run_id, result):
    """Close only the isolated hosted run this UI created, after explicit review."""
    if not isinstance(run_id, str):
        return {'status': 'not_requested'}
    root = Path(root).resolve()
    run = safe_path(root, root / '.orchestration' / run_id)
    manifest = json.loads((run / 'run.json').read_text(encoding='utf-8'))
    if (manifest.get('native_work') is not False
            or manifest.get('skill_plan_id') != result.get('skill_context', {}).get('plan_id')
            or result.get('run_id') != run_id):
        return {'status': 'not_requested'}
    from contribution_tasks import task_ledger
    from contributions import write_report
    ledger = task_ledger(result)
    ledger.update(scope_id=run_id, title=manifest['objective'],
        basis='The explicitly accepted supplied-text answer is authored by the observed worker. Reviewer judgment is separate; no effort is inferred from tokens.')
    ledger['work_items'][0].update(id=result['job_id'], allocations=[{
        'agent_id': 'worker:' + result['worker'], 'percent': 100,
        'evidence': 'Canonical answer and explicit lead review in runs/tasks/' + result['job_id']}])
    write_json(run / 'contributions-ledger.json', ledger)
    write_report(run, ledger)
    from orchestration_lifecycle import closeout_run
    from coordinator_handoff import Coordinator
    identity = Coordinator(run).read()
    return closeout_run(run, owner=identity['owner'], session=identity['session'], generation=identity['generation'], root=root)
