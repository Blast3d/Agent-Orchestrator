"""Extract a release and verify its CMD launcher and isolated local services."""
import argparse
import json
import os
from pathlib import Path
import subprocess
import sys
import time
import urllib.request

from package_app import extract_checked


def smoke(archive, destination):
    destination = Path(destination).resolve()
    destination.mkdir(parents=True, exist_ok=False)
    extract_checked(archive, destination)
    root = destination / 'Agent-Orchestrator'
    python = root / 'python/python.exe'
    env = dict(os.environ)
    env['PATH'] = str(Path(os.environ['SystemRoot']) / 'System32')
    env.pop('PYTHONPATH', None)
    env.pop('PYTHONHOME', None)
    creationflags = subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0
    def run(args, **kwargs):
        return subprocess.run(args, cwd=destination, env=env, capture_output=True,
                              text=True, encoding='utf-8', timeout=45,
                              creationflags=creationflags, **kwargs)
    checks = []
    result = run([str(python), str(root / 'scripts/check_package.py')])
    report = json.loads(result.stdout)
    checks.append({'check': 'package-manifest-and-runtime', 'ok': result.returncode == 0 and report['ok'],
                   'component_checks': len(report['checks'])})
    # PowerShell invokes the actual CMD wrapper from outside the extracted root.
    powershell = Path(os.environ['SystemRoot']) / 'System32/WindowsPowerShell/v1.0/powershell.exe'
    quoted_launcher = str(root / 'Orchestrator.cmd').replace("'", "''")
    result = run([str(powershell), '-NoProfile', '-Command',
                  "& '" + quoted_launcher + "' --help; exit $LASTEXITCODE"])
    checks.append({'check': 'cmd-without-system-python-on-path',
                   'ok': result.returncode == 0 and 'Commands:' in result.stdout})
    result = run([str(python), '-c',
                  'import sys,winpty,pyte,wcwidth,json; print(json.dumps([sys.executable,winpty.__file__,pyte.__file__,wcwidth.__file__]))'])
    locations = json.loads(result.stdout)
    checks.append({'check': 'all-imports-inside-extracted-tree',
                   'ok': all(Path(p).resolve().is_relative_to(root) for p in locations)})
    processes, origins = [], []
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    try:
        for index in range(2):
            state = root / f'runtime/smoke-service-{index}.json'
            process = subprocess.Popen([str(python), str(root / 'app/brain_dashboard.py'),
                                        '--root', str(root), '--port', '0', '--state-file', str(state)],
                                       cwd=destination, env=env, stdout=subprocess.DEVNULL,
                                       stderr=subprocess.DEVNULL, stdin=subprocess.DEVNULL,
                                       creationflags=creationflags)
            processes.append(process)
            deadline = time.monotonic() + 20
            while not state.is_file() and process.poll() is None and time.monotonic() < deadline:
                time.sleep(0.1)
            data = json.loads(state.read_text(encoding='utf-8'))
            origin = data['origin']
            if not origin.startswith('http://127.0.0.1:'):
                raise ValueError('Dashboard did not bind to loopback')
            with opener.open(origin + '/health', timeout=5) as response:
                health = json.load(response)
            with opener.open(origin + '/', timeout=5) as response:
                page = response.read(2 * 1024 * 1024)
            checks.append({'check': 'loopback-dashboard-' + str(index),
                           'ok': health['instance_id'] == data['instance_id'] and b'<html' in page.lower()})
            origins.append(origin)
        checks.append({'check': 'concurrent-services-distinct-ports', 'ok': len(set(origins)) == 2})
    finally:
        for process in processes:
            if process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=5)
    # Verify modified, even invalid user settings are never silently repaired.
    config = root / 'config/workers.json'
    saved = config.read_bytes()
    try:
        config.write_bytes(b'user settings preservation smoke')
        result = run([str(python), str(root / 'app/portable_setup.py'), '--quiet'])
        checks.append({'check': 'existing-settings-preserved',
                       'ok': result.returncode == 0 and config.read_bytes() == b'user settings preservation smoke'})
    finally:
        config.write_bytes(saved)
    return {'ok': all(item['ok'] for item in checks), 'checks': checks,
            'model_calls': 0, 'system_python_on_path': False, 'test_services_stopped': True,
            'limits': ['Same Windows host; no clean-machine VM, provider calls or visible UI interaction.']}


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('archive', type=Path)
    parser.add_argument('destination', type=Path)
    args = parser.parse_args()
    result = smoke(args.archive, args.destination)
    print(json.dumps(result, indent=2))
    raise SystemExit(0 if result['ok'] else 1)
