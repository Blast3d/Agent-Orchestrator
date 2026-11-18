"""Own the desktop workspace until Electron closes its stdin, or reuse the CLI service."""
import argparse
import json
import os
from pathlib import Path
import sys
import threading

from brain_store import safe_path
from coordinator_viewer import SERVICE, ViewerServer
from start_brain_dashboard import running_state
from usage_guard import file_lock, write_json


def serve(root):
    root = Path(root).resolve()
    state_path = safe_path(root, root / 'runtime/coordinator-viewer.json')
    lock = safe_path(root, root / 'runtime/coordinator-viewer-launch.lock')
    state_path.parent.mkdir(parents=True, exist_ok=True)
    server = None
    try:
        with file_lock(lock):
            state = running_state(state_path, SERVICE)
            if state is None:
                server = ViewerServer(root)
                state = dict(service=SERVICE, version=1, pid=os.getpid(),
                             origin=server.origin, instance_id=server.instance_id)
                write_json(state_path, state)
        print(json.dumps(dict(state, owned=server is not None)), flush=True)
        if server is not None:
            def stop_on_disconnect():
                sys.stdin.buffer.read(1)
                server.shutdown()
            threading.Thread(target=stop_on_disconnect, daemon=True).start()
            server.serve_forever(poll_interval=.1)
    finally:
        if server is not None:
            server.server_close()
            with file_lock(lock):
                if state_path.exists() and json.loads(state_path.read_text('utf-8')).get('instance_id') == server.instance_id:
                    state_path.unlink()


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, required=True)
    serve(parser.parse_args().root)
