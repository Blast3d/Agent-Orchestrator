"""Durable coordinator checkpoints and explicit or current-lead-initiated handoffs."""
import argparse
from copy import deepcopy
import hashlib
import json
import math
import os
from pathlib import Path
import uuid

from paths import ROOT, TASKS
from task_store import timestamp, write_json
from usage_guard import Guard, file_lock
from claude_models import select_model, require_model_allowed

OWNERS = {'astra': 'codex', 'fable': 'claude'}
LIST_FIELDS = ('completed', 'next_steps', 'decisions', 'constraints', 'authorization',
               'open_jobs', 'validation', 'artifacts')
MAX_BYTES = 512 * 1024


def advisory_usage(guard):
    policy = getattr(guard, 'policy', None)
    return isinstance(policy, dict) and policy.get('quota_admission_mode') == 'advisory'


def queue_advisory_refresh(guard, worker):
    """A collector launch is evidence, never an additional admission gate."""
    try:
        receipt = guard.request_refresh(worker)
        if isinstance(receipt, dict) and receipt.get('status') in ('queued', 'coalesced', 'error'):
            # Persist only bounded request metadata, never arbitrary reader output.
            result = {'status': receipt['status'], 'provider': worker}
            for field in ('request_id', 'requested_at'):
                value = receipt.get(field)
                if isinstance(value, str) and len(value) <= 128:
                    result[field] = value
            return result
    except Exception:
        pass
    return {'status': 'error', 'provider': worker,
            'reason': 'Usage collection could not be queued; cached admission still applies.'}


def read_object(path):
    if path.stat().st_size > MAX_BYTES:
        raise ValueError('Coordinator input exceeds the size limit')
    def unique(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError('Duplicate JSON field')
            result[key] = value
        return result
    value = json.loads(path.read_text(encoding='utf-8-sig'), object_pairs_hook=unique)
    if not isinstance(value, dict):
        raise ValueError('Expected a JSON object')
    return value


def validate_checkpoint(value):
    if not isinstance(value, dict) or not isinstance(value.get('objective'), str) or not value['objective'].strip():
        raise ValueError('Checkpoint needs an objective')
    for field in LIST_FIELDS:
        if not isinstance(value.get(field), list):
            raise ValueError('Checkpoint needs a list for ' + field)
    if len(json.dumps(value, allow_nan=False).encode('utf-8')) > MAX_BYTES // 2:
        raise ValueError('Checkpoint exceeds the size limit')
    return deepcopy(value)


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, allow_nan=False).encode('utf-8')).hexdigest()


def worker_key(owner, session):
    return digest({'owner': owner, 'session': session})


class Coordinator:
    def __init__(self, run):
        self.run = Path(run).resolve(strict=True)
        if not self.run.is_dir() or self.run.parent.name != '.orchestration':
            raise ValueError('Select an exact project .orchestration run directory')
        self.workspace = self.run.parent.parent
        self.path = self.run / 'coordinator.json'
        self.lock = self.run / 'coordinator.lock'
        for path in (self.path, self.lock, self.run / 'run.json'):
            if path.is_symlink() or path.resolve().parent != self.run:
                raise ValueError('Coordinator files must remain inside the selected run')

    def read(self):
        state = read_object(self.path)
        if (state.get('schema_version') != 1 or state.get('run_id') != self.run.name
                or state.get('workspace') != str(self.workspace)
                or type(state.get('generation')) is not int or state['generation'] < 1
                or state.get('status') not in ('active', 'handoff_ready')
                or state.get('owner') not in OWNERS
                or not isinstance(state.get('history'), list)
                or not isinstance(state.get('session'), str) or not state['session'].strip()):
            raise ValueError('Coordinator state is invalid; preserve it for recovery')
        validate_checkpoint(state.get('checkpoint'))
        workers = state.get('worker_sessions', {})
        if not isinstance(workers, dict):
            raise ValueError('Coordinator worker roles are invalid; preserve them for recovery')
        for key, worker in workers.items():
            if (not isinstance(worker, dict) or worker.get('owner') not in OWNERS
                    or not isinstance(worker.get('session'), str) or not worker['session'].strip()
                    or worker.get('role') != 'worker'
                    or key != worker_key(worker['owner'], worker['session'])):
                raise ValueError('Coordinator worker roles are invalid; preserve them for recovery')
        handoff = state.get('handoff', {})
        if 'outgoing_role' in handoff and (
                handoff['outgoing_role'] != 'worker' or handoff.get('from') not in OWNERS
                or not isinstance(handoff.get('from_session'), str) or not handoff['from_session'].strip()
                or handoff.get('leadership_relinquished') is not True):
            raise ValueError('Outgoing coordinator role is invalid; preserve it for recovery')
        if state.get('status') == 'handoff_ready':
            handoff = state.get('handoff')
            if (not isinstance(handoff, dict) or handoff.get('to') not in OWNERS
                    or not isinstance(handoff.get('id'), str)
                    or handoff.get('checkpoint_sha256') != digest(state['checkpoint'])):
                raise ValueError('Handoff state is invalid; preserve it for recovery')
        return state

    def save(self, state):
        state['updated_at'] = timestamp()
        if len(state['history']) > 50:
            archive = self.run / 'coordinator-history'
            if archive.is_symlink() or archive.resolve().parent != self.run:
                raise ValueError('History archive must remain inside the selected run')
            archive.mkdir(exist_ok=True)
            older = state['history'][:-50]
            write_json(archive / (digest(older) + '.json'), {'handoffs': older})
            state['history'] = state['history'][-50:]
        if len((json.dumps(state, indent=2) + '\n').replace('\n', os.linesep).encode('utf-8')) > MAX_BYTES:
            raise ValueError('Coordinator state exceeds its limit; preserve the previous record')
        write_json(self.path, state)
        return state

    def initialize(self, owner, session):
        if owner not in OWNERS or not session.strip():
            raise ValueError('Initialization needs a coordinator and session identity')
        with file_lock(self.lock):
            if self.path.exists():
                raise ValueError('Coordinator already initialized; inspect its status')
            manifest = read_object(self.run / 'run.json')
            checkpoint = {'objective': manifest.get('objective'), **{key: [] for key in LIST_FIELDS}}
            checkpoint['constraints'] = ['Preserve dirty work. Read the run brief and project instructions.',
                                         'No new billable routes, publishing or expanded source disclosure without authorization.']
            checkpoint['next_steps'] = ['Read run.json, brief.md and existing review decisions; reconcile progress before dispatch.']
            checkpoint['authorization'] = [manifest.get('authorization', {'status': 'unknown; recover from user context'})]
            checkpoint['open_jobs'] = deepcopy(manifest.get('tasks', []))
            validate_checkpoint(checkpoint)
            return self.save({'schema_version': 1, 'run_id': self.run.name, 'workspace': str(self.workspace),
                              'owner': owner, 'session': session.strip(), 'generation': 1, 'status': 'active',
                              'checkpoint': checkpoint, 'checkpoint_at': timestamp(), 'history': [],
                              'worker_sessions': {}})

    @staticmethod
    def require_owner(state, owner, session, generation):
        if (state['status'] != 'active' or state['owner'] != owner or state['session'] != session
                or state['generation'] != generation):
            raise ValueError('Stale coordinator identity or generation; inspect status before doing more work')

    def checkpoint(self, value, owner, session, generation):
        value = validate_checkpoint(value)
        with file_lock(self.lock):
            state = self.read()
            self.require_owner(state, owner, session, generation)
            state.update(checkpoint=value, checkpoint_at=timestamp(), generation=generation + 1)
            return self.save(state)

    def prepare(self, target, reason, generation, previous_lead_stopped=False, *, owner=None, session=None, yield_lead=False):
        if not previous_lead_stopped and not yield_lead:
            raise ValueError('Stop the previous coordinator before preparing a takeover')
        if target not in OWNERS or reason not in ('usage-limit', 'manual', 'near-zero'):
            raise ValueError('Select a supported coordinator and handoff reason')
        with file_lock(self.lock):
            state = self.read()
            if yield_lead:
                self.require_owner(state, owner, session, generation)
            if state['generation'] != generation:
                raise ValueError('Generation changed; inspect the current checkpoint before takeover')
            if state['status'] == 'handoff_ready':
                if state['handoff']['to'] == target and state['handoff']['reason'] == reason:
                    return state
                raise ValueError('A different handoff is already pending')
            if state['owner'] == target:
                raise ValueError('The requested coordinator already owns this run')
            handoff = {'id': uuid.uuid4().hex, 'from': state['owner'], 'to': target, 'reason': reason,
                       'from_session': state['session'], 'yielded_by_lead': yield_lead,
                       'outgoing_role': 'worker', 'leadership_relinquished': True,
                       'outgoing_session_stopped': bool(previous_lead_stopped and not yield_lead),
                       'evidence': 'current lead yielded its own session' if yield_lead else 'explicit operator attestation; not automatic quota detection',
                       'prepared_at': timestamp(), 'checkpoint_at': state['checkpoint_at'],
                       'checkpoint_sha256': digest(state['checkpoint']), 'previous_lead_stopped': True}
            if target == 'fable':
                handoff['receiving_model'] = select_model('coordinator')
            state.update(status='handoff_ready', generation=generation + 1, handoff=handoff)
            state['history'].append(deepcopy(handoff))
            return self.save(state)

    def claim(self, handoff_id, session, generation, guard=None):
        session = session.strip()
        if not session:
            raise ValueError('Claim needs the receiving session identity')
        def verify(state):
            handoff = state.get('handoff', {})
            if handoff.get('to') == 'fable':
                # Check the frozen handoff model, never silently change a
                # previously launched session to today's configured default.
                saved_model = handoff.get('launch', {}).get('model') or handoff.get('receiving_model') or 'claude-fable-5'
                require_model_allowed(saved_model)
            if (state['status'] == 'active' and handoff.get('id') == handoff_id
                    and state['owner'] == handoff.get('to') and state['session'] == session
                    and state['generation'] == generation + 1):
                return True
            if (state['status'] != 'handoff_ready' or state['generation'] != generation
                    or handoff.get('id') != handoff_id):
                raise ValueError('Handoff is stale or already claimed by another session')
            launch = handoff.get('launch', {})
            if launch and launch.get('session') != session:
                raise ValueError('This handoff is pinned to its launched receiving session')
            return False
        with file_lock(self.lock):
            state = self.read()
            if verify(state):
                return state
        # Quota readers have their own deadlines. Do not hold the run lock
        # during a provider read; revalidate identity and generation afterward.
        guard = guard or Guard()
        handoff = state['handoff']
        worker = OWNERS[handoff['to']]
        refresh_receipt = queue_advisory_refresh(guard, worker) if advisory_usage(guard) else None
        if refresh_receipt is None and not guard.refresh(worker).get(worker, {}).get('ok'):
            raise ValueError('Receiving coordinator quota refresh failed; handoff remains ready')
        launch = handoff.get('launch', {})
        if launch:
            from coordinator_transfer import reserved_claim_check
            quota = reserved_claim_check(guard, worker, launch)
        else:
            quota = guard.check(worker, 'small')
        if not quota.get('allowed'):
            raise ValueError('Receiving coordinator quota is held; handoff remains ready')
        with file_lock(self.lock):
            state = self.read()
            if verify(state):
                return state
            workers = state.setdefault('worker_sessions', {})
            if handoff.get('outgoing_role') == 'worker':
                outgoing = {'owner': handoff['from'], 'session': handoff['from_session'],
                            'role': 'worker', 'handoff_id': handoff_id,
                            'demoted_at_generation': generation + 1,
                            'session_stopped_at_handoff': handoff.get('outgoing_session_stopped', False)}
                workers[worker_key(outgoing['owner'], outgoing['session'])] = outgoing
            workers.pop(worker_key(handoff['to'], session), None)
            state.update(status='active', owner=handoff['to'], session=session,
                         generation=generation + 1, claimed_at=timestamp(), quota_at_claim=quota)
            if refresh_receipt is not None:
                state['quota_refresh_at_claim'] = refresh_receipt
            if launch:
                state['handoff']['launch']['claim_confirmed_at'] = state['claimed_at']
            return self.save(state)

    def role(self, owner, session):
        """Report session duties; this does not launch workers or grant assignments."""
        if owner not in OWNERS or not isinstance(session, str) or not session.strip():
            raise ValueError('Role lookup needs a supported owner and session identity')
        session = session.strip()
        state = self.read()
        active = state['status'] == 'active'
        lead = active and state['owner'] == owner and state['session'] == session
        handoff = state.get('handoff', {})
        outgoing = (handoff.get('outgoing_role') == 'worker'
                    and handoff.get('from') == owner and handoff.get('from_session') == session)
        worker = worker_key(owner, session) in state.get('worker_sessions', {})
        role = 'lead' if lead else ('worker' if worker or outgoing else 'unassigned')
        return {'run_id': state['run_id'], 'owner': owner, 'session': session,
                'role': role, 'status': 'active' if active else 'awaiting_claim',
                'lead_owner': state['owner'] if active else None,
                'lead_session': state['session'] if active else None,
                'generation': state['generation'], 'can_coordinate': lead,
                'may_continue_assigned_work': active and role == 'worker',
                'requires_current_lead_assignment': role == 'worker',
                'quota_checked': False, 'session_liveness_checked': False, 'model_calls': 0,
                'guidance': 'Workers keep bounded assigned work and report results to the current lead. '
                            'Reconcile existing assignments after claim. Provider limits still apply; '
                            'exhaustion pauses work, and quota reset never restores leadership.'}

    def readiness(self, threshold=5, guard=None):
        """Near-zero lead advisory, separate from worker admission floors/reservations."""
        if type(threshold) not in (int, float) or not math.isfinite(threshold) or not 0 <= threshold <= 10:
            raise ValueError('Near-zero threshold must be between 0 and 10 percent')
        state = self.read()
        result = {'run_id': state['run_id'], 'owner': state['owner'], 'threshold_pct': threshold,
                  'remaining_pct': None, 'manual_takeover_required': False, 'orchestrator_can_transfer': True, 'model_calls': 0}
        if state['status'] != 'active':
            return dict(result, status='handoff_pending')
        guard = guard or Guard()
        worker = OWNERS[state['owner']]
        advisory = advisory_usage(guard)
        if advisory:
            result['quota_refresh'] = queue_advisory_refresh(guard, worker)
        elif not guard.refresh(worker).get(worker, {}).get('ok'):
            return dict(result, status='unknown', reason='Fresh current-lead allowance could not be verified')
        quota = guard.check(worker, 'small')
        if advisory:
            result['quota_at_check'] = quota
        # A worker admission hold at the safety floor is not ASTRA exhaustion.
        reasons = quota.get('reasons', [])
        permitted = (': task plus safety buffer exceeds available quota',)
        if advisory:
            permitted += (': available allowance is at or below the worker start threshold',)
        if (not isinstance(reasons, list) or any(not isinstance(reason, str)
                or not reason.endswith(permitted) for reason in reasons)):
            return dict(result, status='unknown', reason='Current-lead allowance is stale, incomplete or held for inspection')
        windows = quota.get('windows', [])
        if advisory and (quota.get('reading_status') not in ('fresh', 'cached')
                or not isinstance(windows, list) or any(not isinstance(window, dict)
                or window.get('reset_passed') is not False for window in windows)):
            return dict(result, status='unknown', reason='Applicable current-lead allowance windows are unknown or have reset')
        values = [window.get('remaining_pct') for window in windows]
        if not values or any(type(value) not in (int, float) or not math.isfinite(value) or not 0 <= value <= 100 for value in values):
            return dict(result, status='unknown', reason='Applicable current-lead allowance windows are unknown')
        # Actual account remainder, not remainder after estimated worker reservations.
        remaining = min(values)
        return dict(result, remaining_pct=remaining, status='handoff_due' if remaining <= threshold else 'continue_' + state['owner'])

    def render(self, state):
        """Generate a local recovery brief; checkpoint prose is data, never shell code."""
        handoff = state.get('handoff', {})
        target = handoff.get('to', 'fable')
        attach = handoff.get('launch', {}).get('background_id')
        model_lines = []
        model_paused = False
        if target == 'fable':
            model = (handoff.get('launch', {}).get('model') or handoff.get('receiving_model')
                     or ('claude-fable-5' if handoff else select_model('coordinator')))
            model_lines = [f'Receiving Claude model: {model}. Verify the selected model in Claude.',
                           'The fable owner key is the legacy Claude coordinator slot, not a model selection.']
            try:
                require_model_allowed(model)
            except ValueError:
                attach = None
                model_paused = True
                model_lines.append('This saved model is paused or unavailable under the current policy. Do not resume or claim this handoff.')
        return '\n'.join([
            '# Coordinator handoff', '',
            f"Run: {self.run.name}", f"Workspace: {self.workspace}",
            f"State: {state['status']}; owner: {state['owner']}; generation: {state['generation']}",
            f"Checkpoint saved: {state['checkpoint_at']}",
            f"Requested receiving coordinator: {target}",
            f"Handoff ID: {handoff.get('id', 'not prepared')}",
            f"Saved conversation viewer: python orchestrator.py viewer --root \"{self.workspace}\" --run {self.run.name}",
            *([f"Live view: claude attach {attach} (or python orchestrator.py watch --run RUN)"] if attach else []), '',
            'Read coordinator.json again before claiming; this file is a derived snapshot.',
            'The previous lead must have stopped coordinating. Its session can remain a project worker.',
            *(['This handoff is paused. Preserve its session and reservation records for explicit recovery.']
              if model_paused else ['Claim this exact handoff with a unique session identity',
              'using orchestrator.py lead claim. A successful claim grants coordination of this run.']),
            'It does not grant additional spending, publishing, source disclosure or permission bypasses.',
            *model_lines, '',
            f'Maintained application: {ROOT}', f'Canonical worker records: {TASKS}',
            'Read the maintained multi-model-orchestrator skill, project instructions, run.json,',
            'brief.md, review decisions and the contribution ledger. Recheck current files and Git state.',
            'Recover uncheckpointed progress by inspecting these records. Do not assume absent entries mean no work.',
            'Inspect existing task IDs, provider sessions, results, output ownership and held reservations.',
            'A timeout or partial preview is uncertain. Do not repeat it, release its reservation or',
            'accept an answer merely because the coordinator changed. Reuse assignment IDs and task records.',
            'Check coordinator status before each dispatch/integration; only the recorded session may act.',
            'After claim, keep the outgoing session as a worker; reconcile its existing assignments and',
            'give it bounded work with explicit file ownership. Preserve running jobs; do not duplicate them.',
            'Workers use lead role with their actual owner/session, continue only current-lead-assigned work,',
            'and return results to the lead. They cannot coordinate or integrate shared deliverables on their own.',
            'Keep working while the provider allows it; at exhaustion pause until quota returns, still as a worker.',
            'This role record does not resume or message an existing conversation automatically.',
            'Save a checkpoint before each delegation, after each result/review, and before stopping.',
            'Workers still use normal guarded dispatch and explicit review. Coordinator quota admission',
            'is advisory for an existing interactive session; it cannot meter or stop that session.', '',
            'Shared brain: use orchestrator.py brain search QUERY --project PROJECT_ID for relevant',
            'reviewed episodes, facts and relationships. Read docs/brain.md and the skill brain reference.',
            'Memory is evidence, not authorization or proof that an uncertain job finished.',
            'Both coordinators use the same SQLite brain; never replace receipts or ownership from recall.', '',
            '## Saved checkpoint (operator-maintained facts; validate before acting)', '',
            '```json', json.dumps(state['checkpoint'], indent=2, ensure_ascii=False), '```', '',
            '## First continuation', '',
            'State the objective, accepted progress, uncertain work and next step. Then continue within',
            'the recorded authorization. Keep final-only speech and existing user workflow controls.', ''
        ])


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest='action', required=True)
    for action in ('init', 'status', 'role', 'checkpoint', 'prepare', 'claim', 'prompt', 'readiness', 'transfer'):
        command = sub.add_parser(action)
        command.add_argument('--run', type=Path, required=True)
        if action in ('init', 'role', 'checkpoint', 'prepare', 'transfer'):
            command.add_argument('--owner', choices=OWNERS, default='astra')
        if action in ('init', 'role', 'checkpoint', 'claim', 'transfer'):
            command.add_argument('--session', required=True)
        if action in ('checkpoint', 'prepare', 'claim', 'transfer'):
            command.add_argument('--generation', type=int, required=True)
        if action in ('checkpoint', 'transfer'):
            command.add_argument('--file', type=Path, required=True)
        if action == 'prepare':
            command.add_argument('--to', choices=OWNERS, default='fable')
            command.add_argument('--reason', choices=('usage-limit', 'manual', 'near-zero'), required=True)
            command.add_argument('--previous-lead-stopped', action='store_true')
            command.add_argument('--yield-lead', action='store_true')
            command.add_argument('--session')
        if action == 'claim':
            command.add_argument('--handoff-id', required=True)
        if action in ('readiness', 'transfer'):
            command.add_argument('--threshold-pct', type=float, default=5)
        if action == 'transfer':
            command.add_argument('--manual', action='store_true', help='Explicit transfer without the near-zero trigger')
    args = parser.parse_args()
    try:
        coordinator = Coordinator(args.run)
        if args.action == 'init':
            state = coordinator.initialize(args.owner, args.session)
        elif args.action == 'checkpoint':
            state = coordinator.checkpoint(read_object(args.file), args.owner, args.session, args.generation)
        elif args.action == 'prepare':
            state = coordinator.prepare(args.to, args.reason, args.generation, args.previous_lead_stopped,
                                        owner=args.owner, session=args.session, yield_lead=args.yield_lead)
        elif args.action == 'claim':
            state = coordinator.claim(args.handoff_id, args.session, args.generation)
        elif args.action == 'transfer':
            from coordinator_transfer import transfer
            state = transfer(coordinator, read_object(args.file), args.owner, args.session, args.generation,
                             threshold=args.threshold_pct, manual=args.manual)
        elif args.action == 'readiness':
            result = coordinator.readiness(args.threshold_pct)
            print(json.dumps(result))
            return 2 if result['status'] == 'unknown' else 0
        elif args.action == 'role':
            print(json.dumps(coordinator.role(args.owner, args.session)))
            return 0
        else:
            state = coordinator.read()
        if args.action == 'prompt':
            # Explicit export prints the context; routine status output omits private checkpoint text.
            print(coordinator.render(state))
        else:
            summary = {key: state.get(key) for key in ('run_id', 'owner', 'session', 'status', 'generation', 'checkpoint_at')}
            if state.get('handoff'):
                summary['handoff_id'] = state['handoff']['id']
                summary['next_owner'] = state['handoff']['to']
                summary['outgoing_role'] = state['handoff'].get('outgoing_role')
            summary['worker_sessions'] = list(state.get('worker_sessions', {}).values())
            launch = state.get('handoff', {}).get('launch')
            if launch:
                summary['launch'] = launch
            for key in ('transfer_reused', 'transfer_held', 'transfer_reason', 'transfer_readiness'):
                if key in state:
                    summary[key] = state[key]
            summary['model_calls'] = 'unknown' if launch else 0
            print(json.dumps(summary))
        return 0
    except (OSError, ValueError, KeyError, TypeError, RuntimeError) as exc:
        # Avoid printing user paths or malformed JSON contents into terminal recordings.
        message = str(exc) if type(exc) is ValueError else type(exc).__name__
        print(json.dumps({'ok': False, 'status': 'error', 'error': message, 'model_calls': 'unknown' if args.action == 'transfer' else 0}))
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
