"""Start, inspect, chat with, or unload the dedicated local Ollama worker."""
import argparse
import json
import os
from pathlib import Path
import subprocess
import time
import urllib.error
import urllib.request

from paths import STATE as ROOT, CONFIG, ensure_directories
ensure_directories()
PROFILE = json.loads((CONFIG / 'ollama-profile.json').read_text(encoding='utf-8'))
BASE_URL = 'http://127.0.0.1:11434'


def request(route, body=None, timeout=5):
    payload = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(BASE_URL + '/api/' + route, data=payload, headers={'Content-Type': 'application/json'})
    with urllib.request.urlopen(req, timeout=timeout) as response:
        return json.load(response)


def child_environment():
    env = os.environ.copy()
    env.update(PROFILE['environment'])
    return env


def start():
    try:
        return {'running': True, 'already_running': True, 'version': request('version')['version']}
    except (urllib.error.URLError, TimeoutError, OSError):
        pass
    exe = Path(PROFILE['executable'])
    if not exe.is_file() or not Path(PROFILE['models_directory']).is_dir():
        raise RuntimeError('The configured runtime or SSD model folder is unavailable.')
    with (ROOT / 'ollama-server.log').open('ab') as log:
        process = subprocess.Popen([str(exe), 'serve'], cwd=str(exe.parent), env=child_environment(), stdin=subprocess.DEVNULL, stdout=log, stderr=log, creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0)
    for _ in range(30):
        if process.poll() is not None:
            raise RuntimeError('Ollama exited during startup; inspect its saved local log.')
        try:
            version = request('version')['version']
            (ROOT / 'ollama-server-state.json').write_text(json.dumps({'pid': process.pid, 'version': version, 'started': time.time()}), encoding='utf-8')
            return {'running': True, 'already_running': False, 'version': version}
        except (urllib.error.URLError, TimeoutError, OSError):
            time.sleep(0.5)
    raise RuntimeError('Ollama has not answered yet; inspect the saved log before starting again.')


def status():
    return {'version': request('version')['version'], 'installed_models': [m['name'] for m in request('tags').get('models', [])], 'loaded_models': [{k: m[k] for k in ('name', 'size', 'size_vram', 'context_length') if k in m} for m in request('ps').get('models', [])]}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=('start', 'status', 'chat', 'unload'))
    args = parser.parse_args()
    try:
        if args.action == 'start':
            print(json.dumps(start()))
        elif args.action == 'status':
            print(json.dumps(status()))
        elif args.action == 'chat':
            start()
            return subprocess.call([PROFILE['executable'], 'run', PROFILE['chat_model'], '--think=false'], env=child_environment())
        else:
            models = request('ps').get('models', [])
            for model in models:
                request('generate', {'model': model['name'], 'keep_alive': 0}, timeout=30)
            print(json.dumps({'unloaded_ollama_models': len(models), 'dictation_service_changed': False}))
        return 0
    except (OSError, urllib.error.URLError, RuntimeError, ValueError, KeyError) as exc:
        print(json.dumps({'error': str(exc) if isinstance(exc, RuntimeError) else type(exc).__name__, 'help': 'Check that the X drive is available and inspect the local setup log.'}))
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
