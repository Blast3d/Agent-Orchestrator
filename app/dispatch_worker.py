"""Run a bounded supplied-text task with quota admission and durable review state."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import urllib.error

from paths import ROOT, TASKS, WORKSPACES, ensure_directories
from task_store import OutputClaim, TaskStore, timestamp
from usage_guard import Guard
from execution_limits import timeout_for_task
from worker_execution import WorkerInterrupted, invoke_cloud
from antigravity_boundary import boundary_error, BoundaryHeld
from storage_budget import StorageBudget, StorageLimitError
from task_activity import PhaseTracker
from memory_usage import recall_plan, contract_fields
from claude_models import select_model

PROVIDERS = {'gemini': 'antigravity', 'grok': 'grok', 'claude': 'claude', 'vscode-copilot': 'vscode-copilot'}
PREFIX = ('You are a specialist worker reporting to Codex. Use only the supplied brief. '
          'Return your answer; do not use tools, read files, or delegate.\n\n')


def advisory_mode(guard):
    return isinstance(guard.policy, dict) and guard.policy.get('quota_admission_mode') == 'advisory'


def queue_usage_refresh(guard, provider, result, stage):
    # Collector launch failures are measurement warnings, never task failures.
    if provider == 'vscode-copilot':
        result.setdefault('background_quota_refresh', {})[stage] = {
            'status': 'unavailable', 'reason': 'VS Code does not expose account allowance through the model API.'}
        return
    try:
        receipt = guard.request_refresh(provider)
    except Exception as exc:
        receipt = {'status': 'error', 'error': type(exc).__name__}
    result.setdefault('background_quota_refresh', {})[stage] = receipt


def confirmed_quota_rejection(completed):
    """Recognize terminal quota errors, not numbers or quoted worker answers."""
    evidence = [completed.stderr or '']
    try:
        payload = json.loads(completed.stdout)
        if completed.returncode == 0 and (not isinstance(payload, dict) or not (
                payload.get('is_error') is True or payload.get('status') in ('error', 'failed'))):
            return False
        if isinstance(payload, dict):
            error = payload.get('error')
            # A current structured error is authoritative. Earlier stderr retry
            # messages cannot turn an authentication or other final failure into
            # a quota rejection and authorize another worker.
            if payload.get('type') == 'result' or isinstance(error, (dict, str)):
                evidence = []
            if isinstance(error, dict):
                if error.get('code') == 429 or error.get('status') == 429:
                    return True
                evidence.extend(str(error.get(key, '')) for key in ('code', 'status', 'type', 'message'))
            elif isinstance(error, str):
                evidence.append(error)
            if payload.get('is_error') is True and isinstance(payload.get('errors'), list):
                evidence.extend(item for item in payload['errors'] if isinstance(item, str))
    except (ValueError, TypeError):
        if completed.returncode == 0:
            return False
    pattern = (r'^\s*429\s+(?:rate[ _-]limit|too many requests)\b|'
               r'\b(?:HTTP(?:/\d(?:\.\d)?)?\s*[:=]?\s*429|API Error\s*:\s*429|'
               r'status(?: code)?\s*[:=]?\s*429)\b|'
               r'\brate[ _-]limit(?:[ _-](?:exceeded|reached|error))\b|'
               r'\bquota[ _-](?:exceeded|exhausted)\b|\bresource_exhausted\b|\btoo many requests\b')
    return any(re.search(pattern, text, flags=re.I) for text in evidence)


def worker_environment():
    env = os.environ.copy()
    exact = {'XAI_API_KEY', 'GROK_API_KEY', 'GEMINI_API_KEY', 'GOOGLE_API_KEY',
             'GOOGLE_GENAI_USE_VERTEXAI', 'GOOGLE_APPLICATION_CREDENTIALS',
             'CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC'}
    for name in list(env):
        if name in exact or name.startswith(('ANTHROPIC_', 'CLAUDE_CODE_USE_')):
            env.pop(name, None)
    env['DISABLE_AUTOUPDATER'] = '1'
    return env


def cloud_command(worker, prompt, work, claude_model=None, claude_effort='medium'):
    if worker == 'vscode-copilot':
        from vscode_bots import select_bridge
        try:
            bridge = select_bridge()
        except ValueError as exc:
            raise BoundaryHeld(str(exc)) from exc
        supplied = PREFIX + prompt
        if len(supplied.encode('utf-8')) > 32768:
            raise BoundaryHeld('The VS Code brief exceeds 32 KB; split it into smaller tasks.')
        return ([sys.executable, str(Path(__file__).with_name('vscode_worker.py')),
                 '--endpoint', bridge['endpoint'], '--model-id', bridge['model']['id'],
                 '--output-format', 'stream-json'], supplied)
    if worker == 'gemini':
        from antigravity_boundary import command_for
        return command_for(PREFIX + prompt, work, worker_environment()), None
    if worker == 'grok':
        task_file = work / 'task.txt'
        task_file.write_text(PREFIX + prompt, encoding='utf-8')
        command = [str(Path.home() / '.grok/bin/grok.exe'), '--no-auto-update',
                 '--prompt-file', str(task_file), '--model', 'grok-4.6',
                 '--reasoning-effort', 'low',
                 '--output-format', 'json', '--max-turns', '2', '--no-subagents',
                 '--verbatim', '--no-memory', '--no-plan', '--disable-web-search',
                 '--tools', 'read_file', '--disallowed-tools', 'read_file,Agent',
                 '--permission-mode', 'dontAsk']
        # Empty --tools is not an empty capability set in the installed CLI.
        # Select a nonempty allowlist, remove its sole tool, and deny retained
        # MCP meta-tools explicitly. dontAsk alone auto-allows some reads.
        for rule in ('Read', 'Grep', 'Bash', 'Edit', 'Write', 'WebFetch', 'MCPTool'):
            command.extend(['--deny', rule])
        return command, None
    claude_model = select_model(requested=claude_model)
    executable = Path.home() / 'AppData/Roaming/npm/node_modules/@anthropic-ai/claude-code/bin/claude.exe'
    return ([str(executable), '-p', '--safe-mode', '--no-chrome', '--strict-mcp-config',
             '--tools=', '--permission-mode', 'dontAsk', '--output-format', 'stream-json',
             '--verbose', '--include-partial-messages',
             '--max-turns', '2', '--model', claude_model, '--effort', claude_effort,
             '--settings', '{"remoteControlAtStartup":false}'], PREFIX + prompt)


def local_request(prompt):
    import manage_local
    manage_local.start()
    try:
        return manage_local.request('chat', {'model': 'codex-chat-light', 'think': False,
            'stream': False, 'keep_alive': '2m', 'messages': [{'role': 'user', 'content': prompt}],
            'options': {'num_ctx': 4096, 'num_predict': 512, 'num_thread': 4}}, timeout=120)
    except urllib.error.HTTPError as exc:
        raise RuntimeError(f'Local Ollama rejected the request with HTTP {exc.code}') from exc
    except OSError as exc:
        # urllib wraps connection failures/timeouts in URLError. The server may
        # already be generating, so losing the connection is not completion.
        raise WorkerInterrupted('local_transport_error') from exc


def dispatch(args, *, guard_factory=Guard, store=None, workspaces=None):
    """Return a summary; successful execution always requires a separate review."""
    claude_model = None
    if args.worker == 'claude':
        try:
            claude_model = select_model(requested=getattr(args, 'claude_model', None))
        except ValueError as exc:
            return dict(status='held', execution_status='held', worker=args.worker,
                        reason=str(exc), cleanup_errors=[], export_status='unclaimed',
                        reservation_id=None, assignment_reused=False)
    override=getattr(args,'timeout_seconds',None)
    effective_timeout=timeout_for_task(args.size,override)
    if args.worker=='local-chat' and override is not None:
        raise ValueError('The hosted-worker deadline override does not apply to local models')
    ensure_directories()
    store = store or TaskStore(TASKS)
    workspaces = Path(workspaces or WORKSPACES)
    from brief_check import inspect_brief
    if args.prompt_file.stat().st_size > 256 * 1024:
        raise ValueError('Worker brief exceeds 256 KiB; provide a bounded source excerpt')
    raw_prompt = args.prompt_file.read_bytes()
    try:
        prompt = raw_prompt.decode('utf-8-sig')
        brief_report = inspect_brief(prompt, args.worker)
        encoding_error = False
    except UnicodeDecodeError:
        prompt = ''
        encoding_error = True
        brief_report = inspect_brief('', args.worker)
        brief_report['errors'].append('The brief must be saved as UTF-8 text.')
    require_brief = getattr(args, 'require_brief_check', False)
    metadata = dict(worker=args.worker, task=args.task, size=args.size,
                    category=getattr(args, 'category', 'general'),
                    prompt_sha256=hashlib.sha256(raw_prompt).hexdigest(),
                    prompt_bytes=len(raw_prompt), requested_output=str(args.output.absolute()),
                    brief_check=brief_report, require_brief_check=require_brief)
    metadata.update(requested_timeout_seconds=override,timeout_policy='explicit' if override is not None else 'size-default')
    if getattr(args, 'handoff_from_job_id', None):
        metadata.update(handoff_from_job_id=args.handoff_from_job_id,
                        handoff_reason=getattr(args, 'handoff_reason', 'Confirmed allowance limit'))
    project_id = getattr(args, 'project', None)
    assignment_id = getattr(args, 'assignment_id', None)
    revision_of = getattr(args, 'revision_of', None)
    memory_plan = recall_plan(args)
    memory_query = memory_plan['query']
    metadata.update(memory_lookup_requested=memory_plan['enabled'],
                    memory_policy=memory_plan['policy'], memory_user_id=memory_plan['user_id'])
    if bool(project_id) != bool(assignment_id) or (revision_of and not assignment_id):
        raise ValueError('Use --project and --assignment-id together; revisions need both.')
    if assignment_id:
        from assignment_receipts import AssignmentReceipts, AssignmentConflict, AssignmentIncomplete
        contract = {key: metadata[key] for key in ('worker', 'prompt_sha256', 'size', 'category')}
        contract.update(claude_model=claude_model,
                        claude_effort=getattr(args, 'claude_effort', 'medium') if args.worker == 'claude' else None,
                        require_brief_check=require_brief)
        contract.update(contract_fields(memory_plan))
        if override is not None:
            contract['timeout_seconds']=override
        try:
            record, reused = AssignmentReceipts(store).claim(project_id, assignment_id, contract, metadata, revision_of)
        except (AssignmentConflict, AssignmentIncomplete) as exc:
            return dict(status='held', execution_status='held', worker=args.worker,
                        reason=str(exc), assignment_reused=False, export_status='unclaimed',
                        cleanup_errors=[], assignment_id=assignment_id, assignment_project_id=project_id)
        if reused:
            return dict(record, assignment_reused=True,
                        repeated_requested_output=str(args.output.absolute()))
    else:
        record = store.create(**metadata)
    job_id = record['job_id']
    result = dict(record)
    result.update(cleanup_errors=[], export_status='unclaimed', reservation_id=None)
    if args.worker == 'claude':
        result.update(requested_model=claude_model,
                      requested_effort=getattr(args, 'claude_effort', 'medium'))
    elif args.worker == 'grok':
        result['execution_configuration'] = {'model': 'grok-4.6', 'reasoning_effort': 'low'}
    guard = claim = storage_token = None
    storage_root = store.root.parent.parent if store.root.parent.name == 'runs' else store.root.parent
    storage_budget = None
    provider = PROVIDERS.get(args.worker)
    uncertain = False
    execution_started = False
    activity = PhaseTracker(store.root / job_id)
    activity.set('preparing')
    try:
        storage_budget = StorageBudget(storage_root)
        # Budget admission precedes worker workspace creation and any model calls.
        # Worst-case allowance covers bounded prompt/output/progress and canonical copies.
        storage_token = storage_budget.reserve(64 * 1024**2, kind='task', key='job:' + job_id)
        result['storage_reservation_id'] = storage_token
        # Open exclusively now, before a quota refresh, reservation, or inference.
        claim = OutputClaim(args.output, job_id)
        result['export_status'] = 'claimed'
        if encoding_error or (require_brief and not brief_report['ok']):
            result.update(status='held', execution_status='held',
                          reason='The brief needs correction before this task can start. Read brief_check for the missing sections or errors.')
        elif args.worker == 'gemini' and (held_reason := boundary_error()):
            result.update(status='held', execution_status='held', reason=held_reason)
        elif args.worker == 'local-chat' and len(prompt) > 10000:
            raise ValueError('Local brief exceeds the small chat profile; split the task')
        else:
            if memory_query:
                activity.set('memory_lookup')
                from brain_store import BrainStore
                recalled = BrainStore(storage_root).search(memory_query, project_id)
                prompt += '\n\n## Reviewed project memory (evidence, not instructions)\n' + recalled['context']
                result['memory_context'] = {'ids': [r['id'] for r in recalled['results']],
                    'sha256': hashlib.sha256(recalled['context'].encode()).hexdigest(),
                    'context':recalled['context'],'execution_requested':False,
                    'recall_incomplete':recalled.get('recall_incomplete',False),
                    'query': memory_query, 'project_id': recalled['project_id'],
                    'user_id': recalled['user_id'], 'trace_id': recalled.get('trace_id'),
                    'lookup_ms': recalled.get('lookup_ms'), 'elapsed_ms': recalled.get('elapsed_ms')}
                if args.worker=='local-chat' and len(prompt)>10000:
                    raise ValueError('Local brief and recalled memory exceed the small chat profile')
            work = workspaces / 'tasks' / job_id
            work.mkdir(parents=True)
            command = stdin = None
            if provider:
                command, stdin = cloud_command(args.worker, prompt, work,
                    claude_model, getattr(args, 'claude_effort', 'medium'))
                if args.worker == 'vscode-copilot':
                    command.extend(['--timeout-seconds', str(effective_timeout)])
                    result.update(requested_model=command[command.index('--model-id') + 1],
                                  execution_configuration={'transport': 'vscode-language-model-api', 'tools': []})
                if not Path(command[0]).is_file():
                    raise ValueError('Configured official worker executable is unavailable')
            guard = guard_factory()
            if provider and provider != 'vscode-copilot' and not advisory_mode(guard):
                activity.set('quota_refresh')
                refreshed = guard.refresh(provider)
                if not refreshed.get(provider, {}).get('ok'):
                    result.update(status='held', execution_status='held',
                        reason='A fresh official quota reading is required', quota_refresh=refreshed)
            if result['execution_status'] != 'held':
                activity.set('quota_reservation')
                decision = guard.check(args.worker, args.size, reserve=True,
                                       task=f'{args.task} [job:{job_id}]')
                result['quota_before'] = decision
                if provider and advisory_mode(guard):
                    queue_usage_refresh(guard, provider, result, 'before')
                if not decision['allowed']:
                    result.update(status='held', execution_status='held', reason='Quota admission refused')
                else:
                    result['reservation_id'] = decision['reservation_id']
                    if args.worker == 'gemini':
                        from antigravity_boundary import verify_prepared
                        verify_prepared(work)
                    result.update(reservation_id=decision['reservation_id'], status='running',
                                  execution_status='running', started_at=timestamp())
                    if provider:
                        result.update(timeout_seconds=effective_timeout,
                                      execution_progress_path=str(work / 'execution-progress.json'),
                                      partial_response_path=str(work / 'partial-response.txt'))
                    store.save(job_id, result)
                    activity.set('provider_execution')
                    execution_started = True
                    if args.worker == 'local-chat':
                        if 'memory_context' in result:result['memory_context']['execution_requested']=True
                        payload = local_request(prompt)
                        result['provider_result'] = payload
                        response = payload['message']['content']
                        result.update(model=payload.get('model'), usage={key: payload.get(key)
                            for key in ('prompt_eval_count', 'eval_count')})
                    else:
                        env = worker_environment()
                        options = {}
                        if args.worker == 'gemini':
                            from antigravity_boundary import child_environment, MODEL
                            env = child_environment(env)
                            options = {'protocol': 'antigravity', 'expected_model': MODEL}
                        if 'memory_context' in result:result['memory_context']['execution_requested']=True
                        completed = invoke_cloud(command, stdin, work, env,
                                                 timeout_seconds=result['timeout_seconds'], **options)
                        result['process_pid'] = getattr(completed, 'process_pid', None)
                        result['execution_progress'] = getattr(completed, 'progress', None)
                        if completed.returncode:
                            if (args.worker in ('claude', 'vscode-copilot') and result['execution_progress'] is not None
                                  and not result['execution_progress'].get('terminal_received')):
                                raise WorkerInterrupted('missing_terminal_result', True,
                                                        result['process_pid'], result['execution_progress'])
                            if confirmed_quota_rejection(completed):
                                guard.block(args.worker)
                                result['failure_kind'] = 'quota_exhausted'
                            raise RuntimeError(f'Worker exited {completed.returncode}; inspect the recorded failure kind and handoff receipt')
                        payload = json.loads(completed.stdout)
                        if not isinstance(payload, dict):
                            raise ValueError('Worker result must be a JSON object')
                        payload.pop('thought', None)
                        result['provider_result'] = payload
                        result.update(usage=payload.get('usage'), modelUsage=payload.get('modelUsage'),
                                      model=payload.get('model'))
                        if payload.get('is_error') or str(payload.get('status', '')).lower() in ('error', 'failed'):
                            if confirmed_quota_rejection(completed):
                                guard.block(args.worker)
                                result['failure_kind'] = 'quota_exhausted'
                            raise RuntimeError('Worker reported an unsuccessful task')
                        response = payload.get('text') or payload.get('response') or payload.get('result')
                    if not isinstance(response, str) or not response.strip():
                        raise ValueError('Worker returned no answer')
                    result.update(status='awaiting_review', execution_status='succeeded',
                                  review_status='pending', response=response)
    except (BoundaryHeld, StorageLimitError) as exc:
        result.update(status='held', execution_status='held', reason=str(exc))
    except WorkerInterrupted as exc:
        uncertain = True
        result.update(status='recovery_required', execution_status='uncertain', error=exc.cause,
                      local_process_terminated=exc.process_terminated,
                      process_pid=exc.process_pid,
                      execution_progress=exc.progress,
                      recovery_note='Confirm provider execution stopped before manually finishing this reservation.')
    except (KeyboardInterrupt, subprocess.TimeoutExpired, TimeoutError) as exc:
        uncertain = execution_started
        result.update(status='recovery_required' if uncertain else 'failed',
                      execution_status='uncertain' if uncertain else 'failed', error=type(exc).__name__)
        if uncertain:
            result['recovery_note'] = 'Provider execution may continue; reservation remains active pending reconciliation.'
    except Exception as exc:
        result.update(status='failed', execution_status='failed',
                      error=str(exc) if isinstance(exc, (ValueError, RuntimeError)) else type(exc).__name__)
    finally:
        result['ended_at'] = timestamp()
        activity.set('saving_result')
        # Canonical output is independent of the user-selected export destination.
        def persist():
            try:
                store.save(job_id, result)
            except Exception as exc:
                result['cleanup_errors'].append({'step': 'canonical_save', 'error': type(exc).__name__})
        persist()
        if guard is not None:
            activity.set('quota_reconciliation')
            actions = []
            token = result.get('reservation_id')
            if token and not uncertain:
                reported = {'usage': result.get('usage'), 'modelUsage': result.get('modelUsage')}
                actions.append(('finish', lambda: guard.finish(token, result['execution_status'], reported)))
                if provider:
                    if advisory_mode(guard):
                        actions.append(('refresh', lambda: queue_usage_refresh(guard, provider, result, 'after')))
                    elif provider != 'vscode-copilot':
                        actions.append(('refresh', lambda: guard.refresh(provider)))
            actions.extend([('dashboard', guard.dashboard), ('alerts', guard.alerts)])
            for label, action in actions:
                try:
                    action()
                except Exception as exc:
                    result['cleanup_errors'].append({'step': label, 'error': type(exc).__name__})
        if result.get('reservation_id'):
            result['reservation_state'] = ('held_for_reconciliation' if uncertain else
                'cleanup_failed' if any(e['step'] == 'finish' for e in result['cleanup_errors'])
                else 'finished_pending_fresh_quota')
        if claim is not None:
            activity.set('exporting')
            try:
                result['export_status'] = 'written'
                claim.write(result)
            except Exception as exc:
                result['export_status'] = 'failed'
                result['export_error'] = type(exc).__name__
            finally:
                try:
                    claim.close()
                except Exception as exc:
                    result['cleanup_errors'].append({'step': 'export_close', 'error': type(exc).__name__})
        if storage_token:
            # Unknown provider execution can still create recovery evidence; retain its slot.
            if uncertain:
                result['storage_reservation_state'] = 'held_for_reconciliation'
            else:
                try:
                    storage_budget.release(storage_token)
                    result['storage_reservation_state'] = 'released'
                except Exception as exc:
                    result['cleanup_errors'].append({'step':'storage_release','error':type(exc).__name__})
            persist()
        receipt = activity.set(result['status'] if result['status'] in ('awaiting_review', 'held', 'failed', 'recovery_required') else 'failed')
        result['phase_durations_ms'] = receipt['phase_durations_ms']
        if activity.error:
            result['cleanup_errors'].append({'step': 'activity_save', 'error': activity.error})
        # Publish review eligibility only once every dispatcher write is done.
        # Otherwise a reviewer can accept a task while a late save restores pending.
        result['finalized_at'] = timestamp()
        persist()
    return result


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    if argv and argv[0] == 'review':
        parser = argparse.ArgumentParser(description='Review a completed worker answer; this never runs a model.')
        parser.add_argument('action')
        parser.add_argument('job_id')
        parser.add_argument('--decision', choices=['accepted', 'rejected'], required=True)
        parser.add_argument('--reviewer', required=True)
        parser.add_argument('--note', required=True)
        parser.add_argument('--contributions-file', type=Path, help='Reviewed work allocation ledger; usage comes from the saved task')
        parser.add_argument('--remember-file', type=Path, help='Use this curated memory instead of the automatic compact review episode')
        args = parser.parse_args(argv)
        allocation = json.loads(args.contributions_file.read_text(encoding='utf-8')) if args.contributions_file else None
        payload = None
        if args.remember_file:
            if args.remember_file.stat().st_size > 16384:
                raise ValueError('Memory candidate exceeds 16 KiB')
            payload = json.loads(args.remember_file.read_text(encoding='utf-8-sig'))
            if not isinstance(payload, dict):
                raise ValueError('Curated memory must be an object')
        reviewed = TaskStore(TASKS).review(args.job_id, args.decision, args.reviewer, args.note,
                                         contributions=allocation, curated_payload=payload)
        outcome = reviewed.get('memory_outcome') or {}
        memory_error = outcome.get('error') if outcome.get('status') == 'error' else None
        dashboard_error = None
        try:
            Guard().dashboard()
        except Exception as exc:
            dashboard_error = type(exc).__name__
        print(json.dumps({'job_id': args.job_id, 'status': reviewed['status'], 'dashboard_error': dashboard_error,
                          'contribution_audit': reviewed.get('contribution_audit'),
                          'remembered_memory_id': outcome.get('memory_id'), 'memory_error': memory_error,
                          'memory_outcome': outcome or None}))
        return 2 if memory_error else 0
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('worker', choices=['gemini', 'grok', 'claude', 'local-chat', 'vscode-copilot'])
    parser.add_argument('--prompt-file', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True, help='New JSON file; claimed before any model call')
    parser.add_argument('--size', choices=['tiny', 'small', 'medium', 'large'], default='small')
    parser.add_argument('--task', required=True)
    parser.add_argument('--category', default='general', help='Work category, e.g. coding, research, design, narration or review')
    parser.add_argument('--claude-model', choices=['sonnet', 'opus', 'haiku', 'fable', 'claude-fable-5'],
                        default=None, help='Override the configured Claude worker model; paused families remain held')
    parser.add_argument('--claude-effort', choices=['low', 'medium', 'high'], default='medium')
    parser.add_argument('--require-brief-check', action='store_true', help='Hold incomplete briefs before reserving allowance or calling a worker')
    parser.add_argument('--project', help='Stable project identifier, used with --assignment-id')
    parser.add_argument('--assignment-id', help='Stable assignment key; repeated requests reuse the original task')
    parser.add_argument('--revision-of', help='Previous job ID for a deliberate revision under a new assignment key')
    memory = parser.add_mutually_exclusive_group()
    memory.add_argument('--memory-query', help='Override task-label recall with this bounded query in --project')
    memory.add_argument('--no-memory', action='store_true', help='Disable default reviewed-memory recall for this assignment')
    parser.add_argument('--timeout-seconds',type=int,help='Explicit hosted-worker deadline, 30..1800 seconds; changes assignment identity')
    parser.add_argument('--fallback-worker', action='append', choices=['claude', 'grok', 'local-chat'], default=[],
                        help='Suitable approved alternate for confirmed quota limits; repeat to set the order')
    parser.add_argument('--no-auto-fallback', action='store_true',
                        help='Disable configured alternates for a task with narrower provider scope')
    from task_handoff import apply_automatic_fallbacks, dispatch_with_handoff
    args = parser.parse_args(argv)
    project_default = None
    project_file = ROOT / '.orchestration' / 'project.json'
    if Path.cwd().resolve() == ROOT.resolve() and project_file.is_file() and project_file.stat().st_size <= 16384:
        project_default = json.loads(project_file.read_text(encoding='utf-8')).get('project_id')
    apply_automatic_fallbacks(args, Guard().policy, project_default=project_default)
    result = dispatch_with_handoff(args, dispatch_fn=dispatch, store=TaskStore(TASKS))
    summary = {key: result.get(key) for key in ('job_id', 'status', 'execution_status',
        'review_status', 'worker', 'canonical_result', 'requested_output', 'export_status',
        'reservation_id', 'reservation_state', 'reason', 'error', 'cleanup_errors', 'contribution_audit',
        'assignment_id', 'assignment_project_id', 'assignment_reused', 'repeated_requested_output', 'brief_check',
        'handoff_chain', 'handoff_from_job_id', 'handoff_reason', 'failure_kind')}
    if result.get('brief_check'):
        summary['brief_check'] = {key: result['brief_check'].get(key)
                                  for key in ('ok', 'missing', 'errors', 'warnings', 'character_count')}
    print(json.dumps(summary))
    if result.get('assignment_reused'):
        return 0
    if result['status'] == 'held':
        return 2
    critical = any(e['step'] in ('canonical_save', 'finish', 'export_close') for e in result['cleanup_errors'])
    return 0 if result['status'] == 'awaiting_review' and result['export_status'] == 'written' and not critical else 1


if __name__ == '__main__':
    try:
        raise SystemExit(main())
    except (OSError, ValueError, KeyError) as exc:
        print(json.dumps({'status': 'failed', 'error': str(exc)}))
        raise SystemExit(1)
