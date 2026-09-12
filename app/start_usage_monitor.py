"""Start one hidden usage monitor; register the same helper for Windows sign-in."""
import argparse
import json
import os
from pathlib import Path
import subprocess
import sys
import time

from paths import APP, ensure_directories
SCRIPT = APP / 'usage_guard.py'
sys.path.insert(0, str(SCRIPT.parent))
from usage_guard import Guard, file_lock

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--register-startup', action='store_true')
    parser.add_argument('--restart', action='store_true')
    args = parser.parse_args()
    ensure_directories()
    guard = Guard()
    pythonw = Path(sys.executable).with_name('pythonw.exe')
    executable = str(pythonw if pythonw.exists() else Path(sys.executable))
    if args.register_startup:
        import winreg
        command = subprocess.list2cmdline([executable, str(Path(__file__).resolve())])
        with winreg.CreateKey(winreg.HKEY_CURRENT_USER, r'Software\Microsoft\Windows\CurrentVersion\Run') as key:
            winreg.SetValueEx(key, 'CodexModelUsageMonitor', 0, winreg.REG_SZ, command)
    with file_lock(guard.root / 'launch.lock'):
        if args.restart:
            (guard.root / 'monitor.stop').touch()
            try:
                with file_lock(guard.root / 'monitor.lock', timeout=55):
                    pass
            except TimeoutError:
                print(json.dumps({'monitor_started': False, 'stop_requested': True,
                                  'reason': 'Current quota read is still ending; run monitor again after it exits.'}))
                return 2
        try:
            with file_lock(guard.root / 'monitor.lock', timeout=.1):
                pass
        except TimeoutError:
            print(json.dumps({'already_running': True}))
            return 0
        with (guard.root / 'monitor.log').open('ab') as log:
            process = subprocess.Popen([executable, str(SCRIPT), 'monitor', '--interval', '300'],
                stdin=subprocess.DEVNULL, stdout=log, stderr=log,
                creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0)
        for _ in range(50):
            if process.poll() is not None:
                print(json.dumps({'monitor_started': False, 'reason': 'Monitor exited; inspect runtime/monitor.log'}))
                return 1
            try:
                state = json.loads((guard.root / 'monitor-state.json').read_text(encoding='utf-8'))
                if state.get('pid') == process.pid:
                    print(json.dumps({'monitor_started': True, 'pid': process.pid, 'startup_registered': args.register_startup}))
                    return 0
            except (OSError, ValueError):
                pass
            time.sleep(.1)
        print(json.dumps({'monitor_started': False, 'pid': process.pid, 'reason': 'Startup acknowledgement not received; inspect before retrying.'}))
        return 2

if __name__ == '__main__':
    raise SystemExit(main())
