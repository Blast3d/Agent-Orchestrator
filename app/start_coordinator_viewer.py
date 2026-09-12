"""Open or reuse the Orchestrator viewer without starting or attaching a model."""
import argparse
import json
from pathlib import Path
import webbrowser
from urllib.parse import quote

from coordinator_viewer import SERVICE, ViewerStore
from paths import ROOT
from start_brain_dashboard import open_dashboard


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, default=ROOT)
    parser.add_argument('--run', help='Exact run name or path within the selected workspace')
    parser.add_argument('--no-open', action='store_true')
    parser.add_argument('--bind-codex-session', help='Explicitly map an exact saved Codex conversation UUID to --run')
    args = parser.parse_args(argv)
    try:
        store = ViewerStore(args.root)
        run_id = None
        if args.run:
            path = Path(args.run)
            if path.is_absolute() or len(path.parts) > 1:
                resolved = (args.root / path).resolve()
                expected = store.run_path(resolved.name)
                if resolved != expected:
                    raise ValueError('The selected run must be inside this workspace.')
                run_id = resolved.name
            else:
                run_id = args.run
            store.state(run_id)
        if args.bind_codex_session:
            if not run_id:
                raise ValueError('Choose --run before binding a Codex conversation.')
            print(json.dumps(store.bind(run_id, 'codex', args.bind_codex_session)))
            return 0
        state = open_dashboard(args.root, False, service=SERVICE,
                               script='coordinator_viewer.py', state_name='coordinator-viewer')
        url = state['origin'] + ('/?run=' + quote(run_id) if run_id else '/')
        if not args.no_open:
            webbrowser.open(url)
        print(json.dumps(dict(state, url=url, model_calls=0)))
        return 0
    except (OSError, ValueError, RuntimeError) as error:
        print(json.dumps({'status': 'error', 'error': str(error) if isinstance(error, ValueError) else type(error).__name__}))
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
