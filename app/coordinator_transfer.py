"""One-shot, current-lead-initiated Claude launch with durable intent before spawn."""
from copy import deepcopy
import json
import os
from pathlib import Path
import re
import subprocess
import uuid

from coordinator_handoff import advisory_usage, digest, queue_advisory_refresh, read_object, validate_checkpoint
from paths import ROOT
from task_store import timestamp
from usage_guard import Guard, file_lock
from claude_models import select_model, require_model_allowed

CLAUDE = Path.home() / 'AppData/Roaming/npm/node_modules/@anthropic-ai/claude-code/bin/claude.exe'
HIDDEN = subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0
BACKGROUND_ID = re.compile(r'\b[0-9a-f]{8}\b')


def additional_directories(coordinator):
    """Grant only existing application, recorded project and installed skill folders."""
    record = read_object(coordinator.run / 'run.json')
    home = Path.home().resolve()
    candidates = [ROOT, home / '.claude/skills/multi-model-orchestrator']
    candidates.extend(record[field] for field in ('source_workspace', 'implementation_workspace')
                      if isinstance(record.get(field), str) and record[field].strip())
    # The launch cwd already covers the run. Never infer grants from checkpoint
    # prose or widen a missing project path to an existing parent directory.
    covered = [coordinator.workspace.resolve()]
    directories = []
    for value in candidates:
        try:
            path = Path(value)
            if not path.is_absolute():
                continue
            path = path.resolve(strict=True)
            if (not path.is_dir() or path == home or path in home.parents
                    or path == Path(path.anchor)):
                continue
        except (OSError, RuntimeError, ValueError):
            continue
        if any(path.is_relative_to(parent) for parent in covered):
            continue
        covered.append(path)
        directories.append(str(path))
    return directories


def launch_command(coordinator, session, handoff_id, executable=None):
    model = select_model('coordinator')
    executable = Path(executable) if executable else CLAUDE
    if not executable.is_file():
        raise ValueError('The configured Claude executable is unavailable; lead was not yielded')
    # Only this receiving session edits the existing checkout. A new worktree
    # would omit the outgoing lead's uncommitted files. Auto reviews routine
    # actions; explicit approval rules still apply. Settings are session-only.
    settings = json.dumps({'worktree': {'bgIsolation': 'none'}, 'remoteControlAtStartup': False,
                           'permissions': {'additionalDirectories': additional_directories(coordinator)}})
    instructions = {
        'role': 'Claude receiving coordinator, not a specialist worker',
        'requested_model': model,
        'coordinator_owner_key': 'fable',
        'run_directory': str(coordinator.run), 'handoff_id': handoff_id,
        'receiving_session': session, 'application': str(ROOT / 'orchestrator.py'),
        'steps': [
            'The outgoing lead has explicitly yielded this run under the user-authorized 5-percent handoff workflow.',
            'Read coordinator.json and verify this handoff ID and launch session. Do not start a second session.',
            'The fable owner key is the legacy Claude coordinator slot, not the selected model. Use the saved launch model and current model-pause policy; never resume a paused model.',
            'Read the installed multi-model-orchestrator skill and its coordinator-handoff reference. The manual-only /orchestrator-takeover skill remains a separate user recovery command; do not invoke it.',
            'Run lead claim for this exact run, handoff ID, current generation and receiving session before coordinating.',
            'If claim fails, stop; do not edit project files, dispatch workers, release reservations or invent ownership.',
            'After claim, read the saved checkpoint and original run/task records, reconcile uncertain jobs and continue the authorized objective.',
            'The outgoing coordinator remains a project worker. Reconcile its in-flight assignments, preserve file ownership, and give it bounded work while its provider allows. It reports results to you and cannot independently dispatch or integrate shared work.',
            'Use lead role with each session identity to confirm duties. Exhaustion pauses that worker; quota reset never returns leadership. The role ledger does not automatically message or resume the outgoing conversation.',
            'No new source disclosure, paid routes, commits, pushes, merges or permission bypasses are authorized by this transfer.',
            'Save checkpoints and check owner/session before each dispatch or integration. Preserve dirty files and existing worker ownership.',
            'Use the user-authorized Auto permission mode for routine actions within the recorded scope. Honor explicit ask/deny rules and any remaining permission prompts. Report final results and update the same run ledger.',
        ],
    }
    label = 'Claude ' + model.removeprefix('claude-').split('-')[0].title()
    return [str(executable), '--bg', '--model', model, '--session-id', session,
            '--name', label + ' coordinator ' + handoff_id[:8], '--permission-mode', 'auto',
            '--settings', settings, json.dumps(instructions, ensure_ascii=False)]


def list_agents(executable, workspace, env):
    """Machine-readable session listing from the installed CLI; no model calls."""
    completed = subprocess.run([str(executable), 'agents', '--json'], cwd=workspace, env=env,
                               capture_output=True, text=True, encoding='utf-8', errors='replace',
                               timeout=20, creationflags=HIDDEN)
    try:
        listing = json.loads(completed.stdout)
    except ValueError:
        return []
    return listing if isinstance(listing, list) else []


def background_id(stdout, listing, name):
    """Resolve the daemon id `claude attach` takes: the named listing row, else the id --bg printed."""
    rows = [row for row in listing if isinstance(row, dict) and row.get('kind') == 'background'
            and row.get('name') == name and row.get('id')]
    if rows:
        return max(rows, key=lambda row: row.get('startedAt') or 0)['id']
    found = BACKGROUND_ID.search(stdout or '')
    return found.group(0) if found else None


def open_viewer(executable, identifier, workspace, env):
    """Show the receiving session in its own console. Detaching or closing it leaves the session running."""
    if os.name != 'nt':
        return 'unavailable'
    subprocess.Popen([str(executable), 'attach', identifier], cwd=workspace, env=env,
                     creationflags=subprocess.CREATE_NEW_CONSOLE)
    return 'opened'


def launch_fable(command, workspace, *, run=subprocess.run, agents=list_agents, viewer=open_viewer):
    # Retain the import name for existing callers; model selection is explicit.
    require_model_allowed(command[command.index('--model') + 1])
    from dispatch_worker import worker_environment
    env = worker_environment()
    completed = run(command, cwd=workspace, env=env, capture_output=True, text=True,
                    encoding='utf-8', errors='replace', timeout=45, creationflags=HIDDEN)
    # Do not print provider stdout, paths or prompts. CLI exit is submission
    # evidence only; the receiving session's successful claim confirms takeover.
    outcome = {'status': 'submitted' if completed.returncode == 0 else 'uncertain',
               'exit_code': completed.returncode}
    if completed.returncode != 0:
        return outcome
    # --bg returns before the session shows anything, and its permission prompts
    # wait invisibly. Attach a visible console so the user sees the switchover and
    # can answer them; a viewer problem never changes the submission receipt.
    try:
        executable, name = command[0], command[command.index('--name') + 1]
        identifier = background_id(completed.stdout, agents(executable, workspace, env), name)
        outcome['background_id'] = identifier
        outcome['viewer'] = viewer(executable, identifier, workspace, env) if identifier else 'unresolved'
    except (OSError, ValueError, subprocess.SubprocessError) as exc:
        outcome['viewer'] = 'failed:' + type(exc).__name__
    return outcome


def transfer(coordinator, checkpoint, owner, session, generation, *, guard=None,
             launcher=None, executable=None, threshold=5, manual=False):
    checkpoint = validate_checkpoint(checkpoint)
    session = session.strip()
    request_id = digest({'run': coordinator.run.name, 'owner': owner, 'session': session,
                         'generation': generation, 'checkpoint': checkpoint,
                         'threshold': threshold, 'manual': manual})
    launcher = launcher or launch_fable
    # Serialize the admission/intent for this run. No model is invoked while
    # this lock is held. A crash after intent must never cause a launch retry.
    with file_lock(coordinator.lock):
        state = coordinator.read()
        previous = state.get('handoff', {}).get('launch', {})
        if previous.get('request_id') == request_id:
            return dict(state, transfer_reused=True)
        coordinator.require_owner(state, owner, session, generation)
        guard = guard or Guard()
        if owner == 'fable':
            raise ValueError('Claude already owns this run; select a different ready coordinator using prepare --yield-lead')
        if not manual:
            readiness = coordinator.readiness(threshold, guard)
            if readiness['status'] != 'handoff_due':
                return dict(state, transfer_held=True, transfer_readiness=readiness)
        else:
            readiness = {'status': 'explicit_manual_transfer', 'threshold_pct': threshold}
        handoff_id = uuid.uuid4().hex
        receiving_session = str(uuid.uuid4())
        command = launch_command(coordinator, receiving_session, handoff_id, executable)
        model = command[command.index('--model') + 1]
        name = command[command.index('--name') + 1]
        refresh_receipt = queue_advisory_refresh(guard, 'claude') if advisory_usage(guard) else None
        if refresh_receipt is None and not guard.refresh('claude').get('claude', {}).get('ok'):
            return dict(state, transfer_held=True, transfer_reason='Receiving quota refresh failed')
        admission = guard.check('claude', 'small', reserve=True,
                                task='Coordinator handoff ' + handoff_id)
        if not admission.get('allowed') or not admission.get('reservation_id'):
            held = dict(state, transfer_held=True, transfer_reason='Receiving coordinator allowance is held')
            if refresh_receipt is not None:
                held.update(transfer_quota_refresh=refresh_receipt, transfer_quota=admission)
            return held
        handoff = {'id': handoff_id, 'from': owner, 'from_session': session, 'to': 'fable',
                   'receiving_model': model,
                   'reason': 'manual' if manual else 'near-zero', 'evidence': 'current lead yielded its own session',
                   'prepared_at': timestamp(), 'checkpoint_at': timestamp(),
                   'checkpoint_sha256': digest(checkpoint), 'previous_lead_stopped': True,
                   'outgoing_role': 'worker', 'leadership_relinquished': True,
                   'outgoing_session_stopped': False,
                   'yielded_by_lead': True, 'readiness': readiness,
                   'launch': {'request_id': request_id, 'session': receiving_session,
                              'model': model, 'name': name, 'status': 'launching',
                              'reservation_id': admission['reservation_id'], 'intent_at': timestamp()}}
        if refresh_receipt is not None:
            handoff['launch'].update(quota_refresh=refresh_receipt, quota_at_launch=admission)
        state.update(checkpoint=deepcopy(checkpoint), checkpoint_at=handoff['checkpoint_at'],
                     handoff=handoff, status='handoff_ready', generation=generation + 1)
        state['history'].append(deepcopy(handoff))
        try:
            coordinator.save(state)
        except BaseException:
            # No process start was attempted. Only this admission may be marked
            # failed; every prior/uncertain job remains reserved.
            try:
                guard.finish(admission['reservation_id'], 'failed')
            except Exception:
                pass  # Preserve an unresolved admission for explicit recovery.
            raise
    try:
        outcome = launcher(command, coordinator.workspace)
        if not isinstance(outcome, dict) or outcome.get('status') not in ('submitted', 'uncertain'):
            outcome = {'status': 'uncertain', 'error': 'Unrecognized launch result'}
    except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as exc:
        outcome = {'status': 'uncertain', 'error': type(exc).__name__}
    with file_lock(coordinator.lock):
        current = coordinator.read()
        if current.get('handoff', {}).get('id') != handoff_id:
            # A very fast receiver may already have completed and yielded again.
            # Do not overwrite its newer handoff with this launch result.
            return dict(current, previous_launch_outcome=outcome)
        current['handoff']['launch'].update(outcome, observed_at=timestamp())
        coordinator.save(current)
        return current


def reserved_claim_check(guard, worker, launch):
    """Evaluate admission without charging the already-held lead reservation twice."""
    token = launch.get('reservation_id')
    with guard.state() as data:
        reservation = data.get('reservations', {}).get(token)
        if not reservation or reservation.get('worker') != worker or reservation.get('finished_at'):
            raise ValueError('Launch reservation is missing or finished; inspect the pending handoff')
        evaluation = deepcopy(data)
        evaluation['reservations'].pop(token)
        return guard.evaluate(evaluation, worker, 'small')
