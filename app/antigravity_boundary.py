"""Verified, fail-closed AGY supplied-text route; this is not an OS sandbox."""
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys

from paths import CONFIG
from task_store import write_json

MODEL = 'gemini-3.8-flash-medium'
VALIDATION = CONFIG / 'antigravity-boundary.json'
HANDLER = Path(__file__).with_name('antigravity_deny_tools.py')
EXE = Path.home() / 'AppData/Local/agy/bin/agy.exe'
REQUIRED_CHECKS = ('mcp_denied', 'timeout_denied', 'files_denied', 'shell_denied',
                   'web_denied', 'delegation_denied', 'messaging_denied', 'scheduling_denied')


class BoundaryHeld(ValueError):
    pass


def digest(path):
    with Path(path).open('rb') as source:
        return hashlib.file_digest(source, 'sha256').hexdigest()


def boundary_error():
    """Offline admission. Never create a validation record from a worker answer."""
    try:
        proof = json.loads(VALIDATION.read_text(encoding='utf-8'))
        if (type(proof.get('schema_version')) is not int or proof['schema_version'] != 1 or proof.get('status') != 'verified'
                or proof.get('model') != MODEL
                or any(proof.get('checks', {}).get(key) is not True for key in REQUIRED_CHECKS)):
            return 'Antigravity tool-boundary verification is missing or incomplete.'
        for key, path in (('executable_sha256', EXE), ('handler_sha256', HANDLER),
                          ('python_sha256', Path(sys.executable))):
            if proof.get(key) != digest(path):
                return 'Antigravity executable or permission handler changed; revalidation is required.'
        for path in (HANDLER, Path(sys.executable)):
            # This Windows handler invocation was verified without shell quoting.
            if any(char.isspace() for char in str(path)):
                return 'Antigravity permission-handler paths with spaces need validation.'
    except (OSError, ValueError, TypeError, AttributeError):
        return 'Antigravity tool-boundary verification is unavailable; dispatch is held.'
    return None


def child_environment(env):
    env = env.copy()
    env['AGY_CLI_DISABLE_AUTO_UPDATE'] = 'true'
    for name in ('GOOGLE_GEMINI_BASE_URL', 'GOOGLE_CLOUD_PROJECT', 'GCLOUD_PROJECT',
                 'CLOUDSDK_CORE_PROJECT'):
        env.pop(name, None)
    return env


def hook_config():
    command = Path(sys.executable).as_posix() + ' ' + HANDLER.as_posix()
    return {'orchestrator-no-tools': {'enabled': True, 'PreToolUse': [
        {'matcher': '*', 'hooks': [{'type': 'command', 'command': command, 'timeout': 5}]}]}}


def local_command_payload(payload, name):
    counters = ('input_tokens', 'output_tokens', 'thinking_tokens', 'cache_read_tokens', 'total_tokens')
    try:
        usage = payload['usage']
        if (payload.get('status') != 'SUCCESS' or type(payload.get('num_turns')) is not int
                or payload['num_turns'] != 0 or payload.get('conversation_id') != ''
                or type(payload.get('duration_seconds')) not in (int, float)
                or payload['duration_seconds'] != 0 or payload['command']['name'] != name
                or not isinstance(usage, dict) or any(key not in usage for key in counters)
                or any(type(value) not in (int, float) or value != 0 for value in usage.values())):
            raise ValueError()
    except (KeyError, TypeError, AttributeError, ValueError) as exc:
        raise BoundaryHeld('Antigravity did not confirm a local zero-usage inspection.') from exc


def validate_discovery(payload, hook_path):
    local_command_payload(payload, 'hooks')
    try:
        hooks = payload['command']['data']['hooks']
        if not isinstance(hooks, list) or len(hooks) != 1:
            raise ValueError()
        expected = hook_config()['orchestrator-no-tools']['PreToolUse'][0]['hooks'][0]
        entry = hooks[0]
        if (entry.get('name') != 'orchestrator-no-tools' or entry.get('enabled') is not True
                or Path(entry.get('source', '')).resolve() != hook_path.resolve()
                or entry.get('actions') != [{'event': 'PreToolUse', 'matcher': '*',
                    'type': 'command', 'command': expected['command'], 'timeout_seconds': 5}]):
            raise ValueError()
    except (KeyError, TypeError, ValueError, AttributeError, OSError) as exc:
        raise BoundaryHeld('Antigravity did not load the exact verified permission hook.') from exc


def configuration_hashes():
    base = Path.home() / '.gemini'
    return {name: digest(base / name) if (base / name).exists() else None for name in
            ('antigravity-cli/settings.json', 'config/config.json', 'config/mcp_config.json')}


def verify_prepared(work):
    try:
        work = Path(work)
        preflight = json.loads((work / 'antigravity-preflight.json').read_text(encoding='utf-8'))
        error = boundary_error()
        if (error or preflight.get('verified') is not True or preflight.get('model') != MODEL
                or preflight.get('configuration_hashes') != configuration_hashes()
                or preflight.get('validation_sha256') != digest(VALIDATION)
                or preflight.get('executable_sha256') != digest(EXE)
                or preflight.get('handler_sha256') != digest(HANDLER)
                or preflight.get('hook_sha256') != digest(work / '.agents/hooks.json')
                or json.loads((work / '.agents/hooks.json').read_text(encoding='utf-8')) != hook_config()):
            raise BoundaryHeld(error or 'Antigravity settings changed after permission preflight.')
    except (OSError, ValueError, TypeError, AttributeError) as exc:
        if isinstance(exc, BoundaryHeld):
            raise
        raise BoundaryHeld('Antigravity prepared permission evidence is unavailable.') from exc


def prepare(work, env):
    error = boundary_error()
    if error:
        raise BoundaryHeld(error)
    work = Path(work).resolve()
    settings = work / '.agents'
    if settings.exists():
        raise BoundaryHeld('Antigravity needs a new isolated assignment folder.')
    settings.mkdir()
    hook_path = settings / 'hooks.json'
    write_json(hook_path, hook_config())
    # Do not add a real connector or initialize a newly configured global server.
    global_mcp = Path.home() / '.gemini/config/mcp_config.json'
    try:
        mcp = json.loads(global_mcp.read_text(encoding='utf-8')) if global_mcp.exists() else {}
        if not isinstance(mcp, dict) or not isinstance(mcp.get('mcpServers', {}), dict) or mcp.get('mcpServers', {}):
            raise BoundaryHeld('Antigravity global MCP configuration changed; review this route first.')
    except (OSError, ValueError) as exc:
        raise BoundaryHeld('Antigravity global MCP configuration needs review.') from exc
    config_before = configuration_hashes()
    common = [str(EXE), '--add-dir', str(work), '--mode', 'plan', '--sandbox']
    effective = {}
    for command in ('hooks', 'config'):
        response = subprocess.run(common + ['-p', '/' + command, '--output-format', 'json',
            '--print-timeout', '30s'], cwd=work, env=child_environment(env),
            capture_output=True, text=True, encoding='utf-8', timeout=40,
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0)
        try:
            payload = json.loads(response.stdout)
            if response.returncode:
                raise ValueError()
            local_command_payload(payload, command)
            if command == 'hooks':
                validate_discovery(payload, hook_path)
            else:
                effective = payload['command']['data']['config']
                if (effective.get('useG1Credits') is not False
                        or effective.get('toolPermission') != 'request-review'
                        or effective.get('customModelsConfig') or effective.get('modelProvider')):
                    raise BoundaryHeld('Antigravity account or permission settings differ from the verified route.')
        except (ValueError, TypeError, KeyError, AttributeError) as exc:
            if isinstance(exc, BoundaryHeld):
                raise
            raise BoundaryHeld('Antigravity permission preflight could not be verified.') from exc
    # Check again after the CLI commands; disable auto-update only in this child.
    error = boundary_error()
    try:
        changed = (json.loads(hook_path.read_text(encoding='utf-8')) != hook_config()
                   or config_before != configuration_hashes())
    except (OSError, ValueError) as exc:
        raise BoundaryHeld('Antigravity permission settings became unreadable.') from exc
    if error or changed:
        raise BoundaryHeld(error or 'Antigravity permission settings changed during preflight.')
    write_json(work / 'antigravity-preflight.json', {
        'verified': True, 'model': MODEL, 'hook_sha256': digest(hook_path),
        'configuration_hashes': config_before, 'validation_sha256': digest(VALIDATION),
        'handler_sha256': digest(HANDLER), 'executable_sha256': digest(EXE),
        'credit_overage': False, 'permission_mode': effective['toolPermission'],
        'scope': 'supplied text only; all agent tool calls denied'})
    return common


def command_for(prompt, work, env):
    if len(prompt.encode('utf-8')) > 12000:
        raise BoundaryHeld('Antigravity supplied-text brief exceeds 12 KB; split the assignment.')
    common = prepare(work, env)
    return common + ['-p', prompt, '--output-format', 'stream-json', '--disable-slash-commands',
        '--model', MODEL, '--print-timeout', '90s', '--log-file', str(Path(work) / 'private-agy.log')]
