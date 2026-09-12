"""Reopen the visible console of a permitted Claude coordinator; no model calls."""
import argparse
import json
from pathlib import Path
import subprocess

from coordinator_transfer import CLAUDE, list_agents
from claude_models import require_model_allowed
from paths import ROOT

PREFIXES = ('Claude Opus coordinator ', 'Fable coordinator ')


def session_allowed(row):
    model = row.get('model')
    name = str(row.get('name', ''))
    if not model:
        model = ('opus' if name.startswith(PREFIXES[0])
                 else 'claude-fable-5' if name.startswith(PREFIXES[1]) else None)
    if model:
        try:
            require_model_allowed(model)
        except ValueError:
            return False
    return True


def select_session(listing, preferred=None):
    """An exact selection never falls through to an unrelated coordinator."""
    rows = [row for row in listing if isinstance(row, dict) and row.get('kind') == 'background' and row.get('id')]
    for row in rows:
        if preferred and row['id'] == preferred:
            return row if session_allowed(row) else None
    if preferred is not None:
        return None
    named = [row for row in rows if str(row.get('name', '')).startswith(PREFIXES) and session_allowed(row)]
    def started(row):
        try:
            return float(row.get('startedAt') or 0)
        except (ValueError, TypeError):
            return 0
    return max(named, key=started) if named else None


def recorded_id(run):
    from coordinator_handoff import Coordinator
    return Coordinator(run).read().get('handoff', {}).get('launch', {}).get('background_id')


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--run', type=Path, help='Attach only the coordinator session recorded in this run')
    parser.add_argument('--list', action='store_true', help='Print background sessions without attaching')
    args = parser.parse_args(argv)
    try:
        from dispatch_worker import worker_environment
        env = worker_environment()
        workspace = ROOT
        if args.run:
            from coordinator_handoff import Coordinator
            workspace = Coordinator(args.run).workspace
        listing = list_agents(CLAUDE, workspace, env)
        preferred = recorded_id(args.run) if args.run else None
        if args.list:
            print(json.dumps([{key: row.get(key) for key in ('id', 'name', 'status', 'waitingFor', 'startedAt')}
                              for row in listing if isinstance(row, dict) and row.get('kind') == 'background'], indent=2))
            return 0
        if args.run:
            from coordinator_viewer import ViewerStore
            store = ViewerStore(workspace)
            state, _ = store.state(args.run.resolve().name)
            if args.run.resolve() != store.run_path(state['run_id']):
                raise ValueError('Run must belong to this workspace.')
            provider = store.provider(state, force=True)
            if not provider['can_attach']:
                raise ValueError('This exact coordinator console is unavailable. Use orchestrator.py viewer for saved history.')
            preferred = provider['background_id']
            listing = store.listing()['rows']
        row = select_session(listing, preferred)
        if not row:
            print(json.dumps({'ok': False, 'error': 'No running permitted Claude coordinator session to attach', 'model_calls': 0}))
            return 2
        # Attach in this console. Ctrl+Z detaches; the daemon keeps the session running either way.
        return subprocess.call([str(CLAUDE), 'attach', row['id']], cwd=workspace, env=env)
    except (OSError, ValueError, KeyError, TypeError, subprocess.SubprocessError) as exc:
        message = str(exc) if type(exc) is ValueError else type(exc).__name__
        print(json.dumps({'ok': False, 'error': message, 'model_calls': 0}))
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
