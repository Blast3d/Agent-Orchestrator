"""Registry of this workspace's loopback pages so each can link to, and start, the others."""
from pathlib import Path
import json
import re
import time

SERVICES = {
    'brain': {'title': 'Memory', 'service': 'orchestrator-brain-dashboard',
              'script': 'brain_dashboard.py', 'state': 'brain-dashboard'},
    'viewer': {'title': 'Orchestrator', 'service': 'orchestrator-session-viewer',
               'script': 'coordinator_viewer.py', 'state': 'coordinator-viewer'},
}
RUN_NAME = re.compile(r'[A-Za-z0-9][A-Za-z0-9._-]{0,199}')


def memory_projects(root, run_id, manifest):
    """Resolve explicit run scope and canonical task scopes, never by a display name."""
    from task_progress_view import _safe_canonical
    root = Path(root).resolve()
    projects = []
    def add(value):
        if isinstance(value, str) and value.strip() and len(value) <= 160 and value not in projects:
            projects.append(value)
    add(manifest.get('memory_project_id'))
    add(manifest.get('project_id'))
    for task in (manifest.get('tasks') or [])[:200]:
        identifier = task.get('job_id') if isinstance(task, dict) else None
        if not isinstance(identifier, str) or not re.fullmatch(r'[a-f0-9]{32}', identifier):
            continue
        folder = root / 'runs/tasks' / identifier
        canonical, record, problem = _safe_canonical(folder, identifier)
        source = canonical if canonical is not None else record if problem == 'index_only' else None
        if source:
            add(source.get('assignment_project_id'))
    return projects or [run_id]


def links(root, current, project=None, run=None, cache=None):
    """Sibling pages with their live origin, or None when not running. Health-checked; never launches."""
    from start_brain_dashboard import running_state
    root = Path(root).resolve()
    rows = []
    for key, entry in SERVICES.items():
        row = {'id': key, 'title': entry['title'], 'current': key == current, 'origin': None}
        if key != current:
            path = root / 'runtime' / (entry['state'] + '.json')
            try:
                revision = path.stat().st_mtime_ns
            except OSError:
                revision = None
            saved = (cache or {}).get(key)
            if saved and saved[0] == revision and time.monotonic() - saved[1] < 30:
                state = saved[2]
            else:
                state = running_state(path, entry['service'])
                if cache is not None:
                    cache[key] = (revision, time.monotonic(), state)
            row['origin'] = state['origin'] if state else None
            # A memory project named after a run deep-links the viewer to that run.
            if key == 'viewer' and project and RUN_NAME.fullmatch(project) \
                    and (root / '.orchestration' / project).resolve().is_relative_to(root / '.orchestration') \
                    and (root / '.orchestration' / project / 'coordinator.json').is_file():
                row['run'] = project
            if key == 'viewer':
                if project:
                    row['project'] = project
                if run and RUN_NAME.fullmatch(run):
                    path = (root / '.orchestration' / run).resolve()
                    if path.is_relative_to(root / '.orchestration') and (path / 'coordinator.json').is_file():
                        try:
                            with (path / 'run.json').open('rb') as stream:
                                data = stream.read(65537)
                            manifest = json.loads(data) if len(data) <= 65536 else {}
                            if (isinstance(manifest, dict) and manifest.get('run_id') == run
                                    and (not project or project in memory_projects(root, run, manifest))):
                                row['run'] = run
                        except (OSError, ValueError):
                            pass
        rows.append(row)
    return rows


def ensure(root, target):
    """Start the sibling page if needed and return its origin. Opens no browser and calls no model."""
    from start_brain_dashboard import open_dashboard
    entry = SERVICES.get(target)
    if not entry:
        raise ValueError('Unknown page.')
    try:
        state = open_dashboard(root, False, service=entry['service'], script=entry['script'], state_name=entry['state'])
    except RuntimeError as exc:
        raise ValueError(str(exc)) from None
    return {'id': target, 'title': entry['title'], 'origin': state['origin'], 'reused': state['reused']}
