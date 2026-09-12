"""Open a verified existing Codex conversation without starting a model turn.

The installed VS Code extension handles /local/:conversationId URI navigation.
Capability detection checks that local implementation instead of assuming every
extension release supports this route. No prompt or transcript is sent to a CLI.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import re
import subprocess
import sys

UUID = re.compile(r'[a-fA-F0-9]{8}(?:-[a-fA-F0-9]{4}){3}-[a-fA-F0-9]{12}')
MAX_EXTENSION_BYTES = 16 * 1024 * 1024


def _read(path, maximum):
    with Path(path).open('rb') as stream:
        value = stream.read(maximum + 1)
    if len(value) > maximum:
        raise ValueError('Installed extension metadata exceeds the inspection limit.')
    return value.decode('utf-8')


def codex_support(home=None, *, platform=None):
    """Find the active local extension and a fixed, installed Windows launcher."""
    home = Path(home or Path.home()).resolve()
    unavailable = {'available': False, 'reason': 'Opening an exact Codex conversation is not supported by this installation.'}
    if (platform or sys.platform) != 'win32':
        return unavailable
    base = home / '.vscode/extensions'
    try:
        installed = json.loads(_read(base / 'extensions.json', 2 * 1024 * 1024))
        matches = [row for row in installed if isinstance(row, dict)
                   and row.get('identifier', {}).get('id') == 'openai.chatgpt']
        if len(matches) != 1:
            return dict(unavailable, reason='The active Codex extension could not be confirmed. Open the original Codex conversation manually.')
        location = matches[0].get('relativeLocation')
        if not isinstance(location, str) or not re.fullmatch(r'openai\.chatgpt-[A-Za-z0-9._-]+', location):
            return unavailable
        extension = (base / location).resolve()
        if not extension.is_relative_to(base.resolve()):
            return unavailable
        package = json.loads(_read(extension / 'package.json', 64 * 1024))
        if (package.get('publisher'), package.get('name')) != ('openai', 'chatgpt'):
            return unavailable
        if 'onUri' not in package.get('activationEvents', []):
            return unavailable
        implementation = _read(extension / 'out/extension.js', MAX_EXTENSION_BYTES)
        # These literal operations are present in the installed extension's URI
        # handler and local conversation route. A changed implementation fails
        # closed until its exact navigation capability is reviewed again.
        required = ('registerUriHandler', 'async handleUri(', 'navigateToRoute(',
                    '"/local"', '/:conversationId')
        if not all(marker in implementation for marker in required):
            return dict(unavailable, reason='The installed Codex extension does not expose a verified conversation link. Open it manually in VS Code.')
        candidates = [home / 'AppData/Local/Programs/Microsoft VS Code/Code.exe']
        for environment in ('ProgramFiles', 'ProgramFiles(x86)'):
            if os.environ.get(environment):
                candidates.append(Path(os.environ[environment]) / 'Microsoft VS Code/Code.exe')
        launcher = next((path for path in candidates if path.is_file()), None)
        if launcher is None:
            return dict(unavailable, reason='VS Code was not found at a supported installation path. Open the original Codex conversation manually.')
        return {'available': True, 'reason': 'Open this exact saved conversation in the Codex sidebar in VS Code.',
                'launcher': str(launcher.resolve()), 'extension_version': package.get('version')}
    except (OSError, ValueError, TypeError, AttributeError):
        return unavailable


def transcript_source(path, session_id):
    """Read only session identity/source metadata, never conversation content."""
    if not isinstance(session_id, str) or not UUID.fullmatch(session_id) or path is None:
        return None
    try:
        with Path(path).open('rb') as stream:
            line = stream.readline(65537)
        if len(line) > 65536:
            return None
        row = json.loads(line)
        payload = row.get('payload', {})
        if row.get('type') != 'session_meta' or payload.get('id') != session_id:
            return None
        return payload.get('source') if isinstance(payload.get('source'), str) else None
    except (OSError, ValueError, AttributeError):
        return None


def codex_interaction(session_id, source, support, *, history_available=False):
    action = {'kind': 'codex_conversation', 'label': 'Open Codex conversation',
              'available': False, 'reason': '', 'session_id': session_id, 'background_id': None}
    if not isinstance(session_id, str) or not UUID.fullmatch(session_id):
        action['reason'] = 'This run has no exact Codex conversation binding. Bind its original conversation before opening it.'
    elif not history_available:
        action['reason'] = 'The bound Codex conversation is not available in saved history. Open the original conversation manually.'
    elif source != 'vscode':
        action['reason'] = 'This conversation was not started in VS Code. Continue in its original Codex application.'
    elif not support.get('available'):
        action['reason'] = support.get('reason') or 'The installed Codex conversation opener is unavailable.'
    else:
        action.update(available=True, reason=support['reason'])
    return action


def open_codex_conversation(session_id, support, *, launch=None):
    if not isinstance(session_id, str) or not UUID.fullmatch(session_id):
        raise ValueError('Opening Codex requires an exact conversation UUID.')
    if not support.get('available') or not support.get('launcher'):
        raise ValueError('The installed Codex conversation opener is unavailable.')
    launcher = Path(support['launcher'])
    if launcher.name.lower() != 'code.exe' or not launcher.is_file():
        raise ValueError('The verified VS Code launcher is no longer available.')
    uri = 'vscode://openai.chatgpt/local/' + session_id
    environment = os.environ.copy()
    # Codex extension tools inherit Electron's Node mode. Leaving it set makes
    # Code.exe reject --open-url without delivering anything to the editor.
    environment.pop('ELECTRON_RUN_AS_NODE', None)
    process = (launch or subprocess.Popen)([str(launcher), '--open-url', uri],
                                          stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                                          stderr=subprocess.DEVNULL, env=environment,
                                          creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
    try:
        status = process.wait(timeout=1)
    except subprocess.TimeoutExpired:
        # A newly opened editor may remain running. Never terminate it.
        status = None
    if status not in (None, 0):
        raise ValueError('VS Code could not accept the conversation link. Open the original conversation manually or retry after restarting VS Code.')
    return 'opened'
