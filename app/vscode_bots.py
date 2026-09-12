"""Discover local VS Code chat bridges without reading conversations or credentials."""
from __future__ import annotations
import argparse
import json
from pathlib import Path
import re
import urllib.error
import urllib.request

ENDPOINTS = Path.home() / '.agent-orchestrator/vscode-bots'
IDENTIFIER = re.compile(r'[a-f0-9]{8}(?:-[a-f0-9]{4}){3}-[a-f0-9]{12}')


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise ValueError('VS Code bridge redirects are not permitted.')


def endpoint(path):
    path = Path(path)
    with path.open('rb') as handle:
        raw = handle.read(2049)
    if len(raw) > 2048:
        raise ValueError('Invalid VS Code bridge metadata.')
    data = json.loads(raw)
    if (not isinstance(data, dict) or data.get('version') != 1
            or not isinstance(data.get('port'), int) or isinstance(data['port'], bool)
            or not 1 <= data['port'] <= 65535
            or not isinstance(data.get('token'), str) or not re.fullmatch(r'[a-f0-9]{64}', data['token'])
            or not isinstance(data.get('instance'), str) or not IDENTIFIER.fullmatch(data['instance'])):
        raise ValueError('Invalid VS Code bridge metadata.')
    return data


def request(data, route, body=None, timeout=1):
    # Disable environment proxies and redirects so the bearer stays on loopback.
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect())
    headers = {'Authorization': 'Bearer ' + data['token']}
    if body is not None:
        headers['Content-Type'] = 'application/json'
        body = json.dumps(body, ensure_ascii=False).encode('utf-8')
    req = urllib.request.Request(f"http://127.0.0.1:{data['port']}" + route, body, headers)
    return opener.open(req, timeout=timeout)


def discover(directory=None):
    directory = Path(directory or ENDPOINTS)
    bridges = []
    # Prefer newest endpoint receipts; stale files from a closed editor are harmless.
    def modified(path):
        try:
            return path.stat().st_mtime
        except OSError:
            return 0
    paths = sorted(directory.glob('*.json'), key=modified, reverse=True)[:8]
    for path in paths:
        try:
            data = endpoint(path)
            with request(data, '/status', timeout=0.5) as response:
                raw = response.read(8193)
            if len(raw) > 8192:
                continue
            status = json.loads(raw)
            if status.get('service') != 'vscode-orchestrator-bots' or status.get('version') != 1:
                continue
            model = status.get('model')
            if model is not None and (not isinstance(model, dict) or not isinstance(model.get('id'), str)
                                      or not 1 <= len(model['id']) <= 200 or model.get('vendor') != 'copilot'):
                continue
            bridges.append({'instance': data['instance'], 'endpoint': str(path),
                'enabled': status.get('enabled') is True and model is not None,
                'busy': status.get('busy') is True, 'model': model})
        except (OSError, ValueError, TypeError, AttributeError):
            continue
    return bridges


def select_bridge(directory=None):
    enabled = [row for row in discover(directory) if row['enabled']]
    if not enabled:
        raise ValueError('In VS Code, run Agent Orchestrator: Enable VS Code Bots and select a Copilot model.')
    if len(enabled) != 1:
        raise ValueError('More than one VS Code bot bridge is enabled. Disable bots in the other VS Code windows.')
    if enabled[0]['busy']:
        raise ValueError('The VS Code bot is already working. Wait for its task to finish.')
    return enabled[0]


def status(directory=None):
    bridges = discover(directory)
    enabled = [row for row in bridges if row['enabled']]
    return {'integrations': [
        {'id': 'codex', 'name': 'Codex in VS Code', 'route': 'native Codex workers',
         'quota_pool': 'codex', 'readiness': 'checked by the active Codex coordinator'},
        {'id': 'claude', 'name': 'Claude Code in VS Code', 'route': 'existing guarded Claude dispatcher',
         'quota_pool': 'claude', 'readiness': 'checked by the Claude dispatcher'},
        {'id': 'vscode-copilot', 'name': 'Copilot in VS Code', 'route': 'VS Code Language Model API',
         'quota_pool': 'copilot-account', 'readiness': 'ready' if len(enabled) == 1 and not enabled[0]['busy'] else
         'busy' if len(enabled) == 1 else 'choose one VS Code window' if len(enabled) > 1 else 'enable in VS Code',
         'models': [row['model'] for row in enabled], 'allowance': 'unknown; shared with VS Code Copilot chat'}],
        'live_model_request_performed': False}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.parse_args()
    print(json.dumps(status(), indent=2))


if __name__ == '__main__':
    main()
