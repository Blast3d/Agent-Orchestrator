"""Small dispatch phase receipts and scoped metadata for the coordinator viewer."""
from datetime import datetime, timedelta, timezone
import json
import math
import itertools
from pathlib import Path
import re
import time

from task_store import timestamp, write_json
from task_progress_view import _bounded_read, _read_progress, _contained_file

PHASES = frozenset(('preparing', 'memory_lookup', 'quota_refresh', 'quota_reservation',
    'provider_execution', 'saving_result', 'quota_reconciliation', 'exporting',
    'awaiting_review', 'accepted', 'rejected', 'held', 'failed', 'recovery_required'))
TERMINAL = frozenset(('awaiting_review', 'accepted', 'rejected', 'held', 'failed', 'recovery_required'))
STATUSES = TERMINAL | frozenset(('preparing', 'running'))
JOB = re.compile(r'[0-9a-f]{32}')
RUN = re.compile(r'[A-Za-z0-9][A-Za-z0-9._-]{0,199}')


def _instant(value):
    try:
        parsed = datetime.fromisoformat(value.replace('Z', '+00:00'))
        return parsed.astimezone(timezone.utc) if parsed.tzinfo else None
    except (AttributeError, TypeError, ValueError, OverflowError):
        return None


def _number(value):
    try:
        return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value) and value >= 0
    except OverflowError:
        return False


def _json(path, root, maximum=512 * 1024, budget=None):
    try:
        if budget is not None:
            if budget[0] <= 0:
                return {}
            maximum = min(maximum, budget[0])
        raw, oversized, error = _bounded_read(path, maximum, root)
        if budget is not None:
            budget[0] -= (len(raw) if raw is not None else 0) + int(oversized)
        if error or oversized or raw is None:
            return {}
        value = json.loads(raw.decode('utf-8-sig'))
        return value if isinstance(value, dict) else {}
    except (OSError, ValueError, TypeError, RecursionError):
        return {}


class PhaseTracker:
    """Replace one bounded receipt per phase, never a chat/event log."""
    def __init__(self, directory, clock=time.monotonic):
        self.path = Path(directory) / 'activity.json'
        self.clock = clock
        self.previous = None
        self.entered = None
        self.durations = {}
        self.error = None

    def set(self, phase):
        if phase not in PHASES:
            raise ValueError('Unknown dispatcher phase')
        tick = self.clock()
        if self.previous is not None:
            self.durations[self.previous] = round(self.durations.get(self.previous, 0) + max(0, tick - self.entered) * 1000, 3)
        self.previous, self.entered = phase, tick
        receipt = {'schema_version': 1, 'job_id': self.path.parent.name,
            'phase': phase, 'phase_started_at': timestamp(), 'updated_at': timestamp(),
            'phase_durations_ms': dict(self.durations)}
        try:
            write_json(self.path, receipt)
        except OSError as exc:
            # Observability failure must not change execution or quota cleanup.
            self.error = type(exc).__name__
        return receipt


def snapshot(root, run_id, project_ids=None, *, home=None):
    """Read exact run/project tasks without prompt, answer, path or account data."""
    root = Path(root)
    checked = datetime.now(timezone.utc)
    output = {'tasks': [], 'checked_at': checked.isoformat(), 'task_count': 0, 'truncated': False}
    if not isinstance(run_id, str) or not RUN.fullmatch(run_id):
        return output
    run = root / '.orchestration' / run_id
    manifest = _json(run / 'run.json', root)
    if not manifest:
        return output
    coordinator = _json(run / 'coordinator.json', root)
    explicit = set()
    jobs = list(manifest.get('tasks', [])) if isinstance(manifest.get('tasks'), list) else []
    checkpoint = coordinator.get('checkpoint', {})
    if isinstance(checkpoint, dict) and isinstance(checkpoint.get('open_jobs'), list):
        jobs += checkpoint['open_jobs']
    for job in jobs:
        identifier = job.get('job_id') if isinstance(job, dict) else job
        if isinstance(identifier, str) and JOB.fullmatch(identifier):
            explicit.add(identifier)
    projects = {run_id}
    for project in (project_ids or []):
        if isinstance(project, str) and 0 < len(project) <= 200:
            projects.add(project)
    for key in ('memory_project_id', 'project_id'):
        value = manifest.get(key)
        if isinstance(value, str) and 0 < len(value) <= 200:
            projects.add(value)
    folders = root / 'runs' / 'tasks'
    try:
        candidates = list(itertools.islice(folders.iterdir(), 2001))
    except OSError:
        candidates = []
    output['scan_limited'] = len(candidates) > 2000
    budget = [8 * 1024**2]
    candidates = [folders / identifier for identifier in sorted(explicit)] + [p for p in candidates if p.name not in explicit]
    for folder in candidates[:2000]:
        if budget[0] <= 0:
            output['scan_limited'] = True
            break
        if not JOB.fullmatch(folder.name):
            continue
        # Filter on the small local index first. Only scoped rows need their
        # canonical source checked; never open every worker answer on each poll.
        record = _json(folder / 'record.json', root, 128 * 1024, budget)
        if folder.name not in explicit and (record.get('job_id') != folder.name or
                record.get('assignment_project_id') not in projects):
            continue
        # The canonical source wins; an index cannot override an existing result.
        source, problem = _contained_file(folders, folder.name, 'result.json')
        data = _json(source, root, 512 * 1024, budget) if source else record if problem == 'missing' else {}
        if data.get('job_id') != folder.name or (folder.name not in explicit and data.get('assignment_project_id') not in projects):
            continue
        status = data.get('status') if data.get('status') in STATUSES else 'unknown'
        activity = _json(folder / 'activity.json', root, 16384, budget)
        if activity.get('job_id') != folder.name:
            activity = {}
        phase = activity.get('phase') if activity.get('phase') in PHASES else 'unknown'
        # A review happens after the dispatch receipt; don't leave an accepted task awaiting review.
        if status in ('accepted', 'rejected') or (not activity and status in TERMINAL):
            phase = status
        durations = activity.get('phase_durations_ms', {})
        durations = {k: v for k, v in durations.items() if k in PHASES and _number(v)} if isinstance(durations, dict) else {}
        started = _instant(data.get('started_at'))
        created = _instant(data.get('created_at'))
        finalized = _instant(data.get('finalized_at'))
        elapsed = max(0, ((finalized or checked) - created).total_seconds()) if created else None
        timeout = data.get('timeout_seconds')
        deadline = None
        if started and _number(timeout) and 0 < timeout <= 86400:
            deadline = (started + timedelta(seconds=timeout)).isoformat()
        progress = _read_progress(folder.name, root / 'runtime/workspaces', [])
        updates = [_instant(activity.get('updated_at')), _instant(progress.get('saved_at'))]
        updated = max((value for value in updates if value), default=None)
        memory = _json(folder / 'memory-outcome.json', root, 16384, budget)
        reason = {'held': 'Admission is held; inspect the task review for the recorded reason.',
            'recovery_required': 'Execution needs reconciliation; its reservation remains held.',
            'failed': 'Task failed; inspect its canonical task record.',
            'awaiting_review': 'Answer is saved; a named reviewer must check it.',
            'accepted': 'Review accepted.', 'rejected': 'Review rejected.'}.get(status)
        from memory_usage import summary as memory_use_summary
        use = memory_use_summary(data)
        use['feedback'].pop('note',None)
        use['feedback'].pop('reviewer',None)
        output['tasks'].append({'job_id': folder.name,
            'memory_use':use,
            'provider': data.get('worker') if data.get('worker') in ('codex', 'claude', 'grok', 'gemini', 'local-chat', 'vscode-copilot') else 'unknown',
            'status': status, 'phase': phase,
            'phase_started_at': activity.get('phase_started_at') if _instant(activity.get('phase_started_at')) else None,
            'updated_at': updated.isoformat() if updated else None,
            'started_at': started.isoformat() if started else None,
            'deadline_at': deadline, 'elapsed_seconds': round(elapsed, 1) if elapsed is not None else None,
            'reason': reason, 'phase_durations_ms': durations, 'progress': progress,
            'memory_status': memory.get('status') if memory.get('status') in ('remembered', 'skipped', 'error', 'writing') else None})
    native = {}
    for job in jobs:
        name = job.get('agent') if isinstance(job, dict) else None
        if isinstance(name, str) and re.fullmatch(r'[A-Za-z][A-Za-z0-9_-]{0,99}', name):
            native[name] = job
    native_report = {'agents': {}, 'status': 'unavailable', 'reason': 'No Codex parent session is linked to this run.'}
    if native:
        binding = _json(run / 'viewer-session.json', root, 16384)
        parent = manifest.get('native_parent_session_id')
        if not parent and (binding.get('provider') == 'codex' and binding.get('owner') == coordinator.get('owner')
                           and binding.get('session') == coordinator.get('session')):
            parent = binding.get('session_id')
        if parent:
            end = manifest.get('completed_utc') or manifest.get('completed_at')
            if not end and manifest.get('status') == 'completed':
                end = coordinator.get('checkpoint_at')
            try:
                from native_activity import snapshot as native_snapshot
                native_report = native_snapshot(Path(home or Path.home()), parent, list(native),
                    run_started_at=manifest.get('created_utc'), run_ended_at=end)
            except Exception:
                native_report = {'agents': {}, 'status': 'unavailable',
                    'reason': 'Saved Codex activity could not be read. The coordinator checkpoint is still shown.'}
        output['native_activity_status'] = native_report.get('status')
    for name, job in native.items():
        status = job.get('status') if job.get('status') in ('running', 'completed', 'failed', 'planned') else 'unknown'
        saved = native_report.get('agents', {}).get(name, {})
        native_started = _instant(saved.get('started_at'))
        native_updated = _instant(saved.get('updated_at'))
        checkpoint_updated = _instant(coordinator.get('checkpoint_at'))
        native_elapsed = saved.get('elapsed_seconds')
        last_turn = saved.get('last_turn_duration_seconds')
        linked = saved.get('source') == 'codex_local_metadata'
        native_reason = saved.get('reason') or native_report.get('reason') or 'State reported by the coordinator.'
        if linked:
            event_notes = {'idle': 'Latest saved turn completed.', 'active': 'Latest saved event: turn started.',
                'interrupted': 'Latest saved turn was interrupted.'}
            native_reason = 'State reported by the coordinator. ' + event_notes.get(saved.get('lifecycle_state'), 'Timing comes from the saved session index.')
        else:
            native_reason = 'State reported by the coordinator. ' + native_reason
        output['tasks'].append({'job_id': 'native:' + name, 'provider': 'codex', 'status': status,
            'phase': status if linked else 'recorded_' + status, 'phase_started_at': None,
            'updated_at': (native_updated or checkpoint_updated).isoformat() if native_updated or checkpoint_updated else None,
            'updated_label': 'Saved activity' if native_updated else 'Coordinator checkpoint' if checkpoint_updated else None,
            'started_at': native_started.isoformat() if native_started else None,
            'deadline_at': None, 'elapsed_seconds': native_elapsed if _number(native_elapsed) else None,
            'elapsed_label': saved.get('elapsed_label') if linked else None,
            'last_turn_duration_seconds': last_turn if _number(last_turn) else None,
            'reason': native_reason, 'activity_source': 'codex_local_metadata' if linked else 'coordinator_checkpoint',
            'activity_error': None if linked else native_reason,
            'phase_durations_ms': {}, 'progress': {}, 'memory_status': None})
    output['tasks'].sort(key=lambda row: (row['status'] in TERMINAL or row['status'] == 'completed', row['started_at'] or '', row['job_id']))
    output['task_count'] = len(output['tasks'])
    output['truncated'] = len(output['tasks']) > 100 or output['scan_limited']
    output['tasks'] = output['tasks'][:100]
    return output
