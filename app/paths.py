"""Single location map for this installed, per-user application."""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
APP = ROOT / 'app'
CONFIG = ROOT / 'config'
STATE = ROOT / 'runtime'
WORKSPACES = STATE / 'workspaces'
TASKS = ROOT / 'runs/tasks'
VENDOR = ROOT / 'vendor/quota'

def ensure_directories():
    for path in (CONFIG, STATE, TASKS, WORKSPACES, WORKSPACES / 'google',
                 WORKSPACES / 'grok', WORKSPACES / 'claude', WORKSPACES / 'tasks'):
        path.mkdir(parents=True, exist_ok=True)
