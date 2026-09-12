"""Check the extracted application without contacting providers or global setup."""
import hashlib
import importlib
import json
from pathlib import Path
import sqlite3
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / 'app'), str(ROOT / 'vendor/quota')]


def check(root=ROOT):
    root = Path(root).resolve()
    checks = []
    def add(name, ok):
        checks.append({'check': name, 'ok': bool(ok)})
    from portable_setup import initialize
    from usage_guard import validate_policy
    setup = initialize(root)
    add('python-3.11+', sys.version_info >= (3, 11))
    for module in ('winpty', 'pyte', 'wcwidth'):
        try:
            importlib.import_module(module)
            add('import:' + module, True)
        except (ImportError, OSError):
            add('import:' + module, False)
    connection = sqlite3.connect(':memory:')
    try:
        connection.execute('CREATE VIRTUAL TABLE smoke USING fts5(content)')
        connection.execute("INSERT INTO smoke(smoke,rank) VALUES('secure-delete',1)")
        add('sqlite-fts5-secure-delete', sqlite3.sqlite_version_info >= (3, 42))
    finally:
        connection.close()
    validate_policy(json.loads((root / 'runtime/policy.json').read_text(encoding='utf-8')))
    add('quota-policy', True)
    for command in ([str(root / 'orchestrator.py'), '--help'],
                    ['-c', 'import sqlite3, ssl, winpty, pyte, wcwidth; print("ok")']):
        child = subprocess.run([sys.executable, *command], cwd=root,
                               capture_output=True, timeout=30)
        add('subprocess:' + command[-1][:40], child.returncode == 0)
    manifest = root / 'PACKAGE-MANIFEST.json'
    if manifest.is_file():
        data = json.loads(manifest.read_text(encoding='utf-8'))
        for relative, digest in data['files'].items():
            file = (root / relative).resolve()
            if not file.is_relative_to(root):
                raise ValueError('Manifest path escapes package')
            add('sha256:' + relative, file.is_file() and hashlib.sha256(file.read_bytes()).hexdigest() == digest)
    return {'ok': all(item['ok'] for item in checks), 'checks': checks, 'setup': setup,
            'model_calls': 0, 'limits': ['Provider login and inference, global skill installation and live UI are separate checks.']}


if __name__ == '__main__':
    result = check()
    print(json.dumps(result, indent=2))
    raise SystemExit(0 if result['ok'] else 1)
