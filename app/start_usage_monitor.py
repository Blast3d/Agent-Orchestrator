"""Manually start, stop or inspect one hidden usage monitor."""
import argparse
import json
import os
from pathlib import Path
import subprocess
import sys
import threading
import time

from paths import APP, STATE
from usage_guard import file_lock

SCRIPT = APP / 'usage_guard.py'
STARTUP_NAME = 'CodexModelUsageMonitor'
STARTUP_KEY = r'Software\Microsoft\Windows\CurrentVersion\Run'


def startup_registered():
    if os.name != 'nt':
        return False
    import winreg
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, STARTUP_KEY) as key:
            winreg.QueryValueEx(key, STARTUP_NAME)
        return True
    except FileNotFoundError:
        return False


def configure_startup(enabled):
    if os.name != 'nt':
        raise ValueError('Windows startup settings are available only on Windows.')
    import winreg
    with winreg.CreateKey(winreg.HKEY_CURRENT_USER, STARTUP_KEY) as key:
        if enabled:
            command = subprocess.list2cmdline([python_executable(), str(Path(__file__).resolve())])
            winreg.SetValueEx(key, STARTUP_NAME, 0, winreg.REG_SZ, command)
        else:
            try:
                winreg.DeleteValue(key, STARTUP_NAME)
            except FileNotFoundError:
                pass


def python_executable():
    pythonw = Path(sys.executable).with_name('pythonw.exe')
    return str(pythonw if pythonw.exists() else Path(sys.executable))


def monitor_status(root=STATE):
    """The lifetime lock proves liveness; old PID receipts never mean On."""
    root = Path(root)
    # Serialize readers so one status probe cannot mistake another probe's
    # momentary lifetime-lock acquisition for a running monitor.
    with file_lock(root / 'monitor-status.lock', timeout=1):
        running = False
        try:
            with file_lock(root / 'monitor.lock', timeout=0):
                pass
        except TimeoutError:
            running = True
        stopping = running and (root / 'monitor.stop').exists()
    return {'status': 'stopping' if stopping else 'on' if running else 'off',
            'running': running, 'startup_registered': startup_registered()}


def _start(root):
    current = monitor_status(root)
    if current['status'] == 'stopping':
        raise ValueError('The current quota check is stopping. Wait for Off before turning it on again.')
    if current['running']:
        return dict(current, already_running=True)
    with (root / 'monitor.log').open('ab') as log:
        process = subprocess.Popen(
            [python_executable(), str(SCRIPT), '--root', str(root), 'monitor', '--interval', '300'],
            stdin=subprocess.DEVNULL, stdout=log, stderr=log,
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0)
    for _ in range(50):
        if process.poll() is not None:
            raise ValueError('Monitor exited; inspect runtime/monitor.log.')
        try:
            state = json.loads((root / 'monitor-state.json').read_text(encoding='utf-8'))
            if state.get('pid') == process.pid:
                current = monitor_status(root)
                if current['status'] == 'on':
                    threading.Thread(target=process.wait, daemon=True).start()
                    return dict(current, monitor_started=True, pid=process.pid)
        except (OSError, ValueError):
            pass
        time.sleep(.1)
    raise ValueError('Startup acknowledgement was not received. Inspect runtime/monitor.log before retrying.')


def set_monitor_enabled(enabled, root=STATE):
    if type(enabled) is not bool:
        raise ValueError('Choose On or Off for the usage monitor.')
    root = Path(root)
    with file_lock(root / 'launch.lock'):
        if enabled:
            return _start(root)
        (root / 'monitor.stop').touch()
        return dict(monitor_status(root), stop_requested=True)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, default=STATE)
    action = parser.add_mutually_exclusive_group()
    action.add_argument('--status', action='store_true')
    action.add_argument('--stop', action='store_true')
    action.add_argument('--restart', action='store_true')
    action.add_argument('--register-startup', action='store_true')
    action.add_argument('--unregister-startup', action='store_true')
    args = parser.parse_args(argv)
    try:
        if args.register_startup or args.unregister_startup:
            configure_startup(args.register_startup)
            result = monitor_status(args.root)
        elif args.status:
            result = monitor_status(args.root)
        elif args.stop:
            result = set_monitor_enabled(False, args.root)
        else:
            if args.restart:
                set_monitor_enabled(False, args.root)
                with file_lock(args.root / 'monitor.lock', timeout=55):
                    pass
            result = set_monitor_enabled(True, args.root)
        print(json.dumps(result))
        return 0
    except (OSError, ValueError, TimeoutError) as error:
        print(json.dumps({'status': 'error', 'error': str(error)}))
        return 2

if __name__ == '__main__':
    raise SystemExit(main())
