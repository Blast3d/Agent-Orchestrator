"""Scoped file-tool routes: commands, stream audits and validation proofs per worker.

A scoped assignment edits a disposable worktree through file tools only. Each
route keeps its supplied-text hardening and adds exactly the file tools below;
a route runs scoped only while its recorded live validation still matches the
installed executables and these modules (fail closed otherwise).
"""
from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

from antigravity_progress import AntigravityProgress
from codex_progress import CodexProgress
from paths import CONFIG
from scoped_files import TOOLS
from task_store import write_json
from worker_progress import ClaudeProgress

APP = Path(__file__).resolve().parent
SERVER = APP / 'scoped_files_mcp.py'
AGY_HANDLER = APP / 'antigravity_scoped_tools.py'
PROOFS = CONFIG / 'scoped-proofs'
CLAUDE_EXE = Path.home() / 'AppData/Roaming/npm/node_modules/@anthropic-ai/claude-code/bin/claude.exe'
GROK_EXE = Path.home() / '.grok/bin/grok.exe'
ROUTES = ('claude', 'codex', 'grok', 'gemini')
MODULES = ('scoped_files.py', 'scoped_files_mcp.py', 'scoped_routes.py', 'worker_worktree.py',
           'antigravity_scoped_tools.py')
TOOL_NAMES = frozenset(tool['name'] for tool in TOOLS)
CLAUDE_TOOLS = ('Read', 'Write', 'Edit', 'Glob', 'Grep')
# AGY's own file tools; sed_file and notebook tools stay denied with everything else.
AGY_TOOLS = frozenset(('view_file', 'list_dir', 'find_by_name', 'grep_search', 'write_to_file',
                       'replace_file_content', 'multi_replace_file_content'))
REQUIRED_CHECKS = ('inside_read', 'inside_write', 'outside_read_denied', 'outside_write_denied',
                   'git_write_denied', 'config_write_denied', 'shell_denied', 'web_denied',
                   'foreign_tools_denied')
MAX_TURNS = '60'
PREFIX = ('You are a specialist worker reporting to the lead orchestrator. Your assigned folder is a '
          'disposable copy of the project, and your file tools work only inside it; paths are relative '
          'to that folder. You cannot run commands, use the web or delegate. Make the requested changes '
          'there, then answer with a short summary of what you changed and anything left undone. The '
          'lead reviews your changes before anything reaches the project.\n\n')
# Grok reads Claude, Cursor and Codex configuration unless these cells are off.
GROK_COMPAT_OFF = {f'GROK_{vendor}_{surface}_ENABLED': 'false'
                   for vendor in ('CLAUDE', 'CURSOR', 'CODEX')
                   for surface in ('HOOKS', 'MCPS', 'RULES', 'AGENTS', 'SKILLS', 'SESSIONS')}
GROK_COMPAT_OFF.update(GROK_MANAGED_MCPS_ENABLED='false', GROK_MANAGED_MCP_GATEWAY_TOOLS_ENABLED='false',
                       # Starts the job-local server without writing ~/.grok/trusted_folders.toml;
                       # the preflight then refuses any project hook or permission rule.
                       GROK_FOLDER_TRUST='0')
_CREATE_NO_WINDOW = subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0


class ScopedHeld(ValueError):
    """The scoped route cannot run; nothing was sent."""


class ScopedToolViolation(ValueError):
    """A worker used a tool outside its scoped allowlist; safe local cause only."""
    def __init__(self, cause):
        self.cause = cause
        super().__init__(cause)


def digest(path):
    with Path(path).open('rb') as source:
        return hashlib.file_digest(source, 'sha256').hexdigest()


def executable(worker):
    if worker == 'codex':
        import codex_worker
        return Path(codex_worker.executable() or 'codex-unavailable')
    if worker == 'gemini':
        from antigravity_boundary import EXE
        return EXE
    return {'claude': CLAUDE_EXE, 'grok': GROK_EXE}[worker]


def fingerprints(worker):
    paths = {name: APP / name for name in MODULES}
    paths.update(python=Path(sys.executable), executable=executable(worker))
    return {key: digest(path) for key, path in paths.items()}


def proof_path(worker):
    return PROOFS / f'{worker}.json'


def proof_error(worker):
    """Offline admission; a route without a current passing proof stays held."""
    if worker not in ROUTES:
        return f'{worker} has no scoped file-tool route.'
    try:
        proof = json.loads(proof_path(worker).read_text(encoding='utf-8'))
        if (proof.get('schema_version') != 1 or proof.get('status') != 'verified' or proof.get('worker') != worker
                or any(proof.get('checks', {}).get(check) is not True for check in REQUIRED_CHECKS)):
            return f'Scoped {worker} validation is missing or incomplete; run the scoped validation first.'
        if proof.get('fingerprints') != fingerprints(worker):
            return f'The {worker} executable or scoped tool modules changed; scoped validation is required again.'
    except (OSError, ValueError, TypeError, AttributeError, KeyError):
        return f'Scoped {worker} validation is unavailable; dispatch is held.'
    return None


def write_proof(worker, checks, evidence, extra=None):
    if any(checks.get(check) is not True for check in REQUIRED_CHECKS):
        raise ScopedHeld('Every scoped check must pass before a proof is written.')
    proof = {'schema_version': 1, 'status': 'verified', 'worker': worker,
             'verified_at': datetime.now(timezone.utc).isoformat(), 'checks': checks,
             'fingerprints': fingerprints(worker), 'evidence': evidence, **(extra or {})}
    PROOFS.mkdir(parents=True, exist_ok=True)
    write_json(proof_path(worker), proof)
    return proof


def server_command(tree, audit, read_only=False):
    return [sys.executable, str(SERVER), '--root', str(tree), '--audit', str(audit),
            *(['--read-only'] if read_only else [])]


def claude_command(model, effort):
    """Built-in file tools under --restricted; the worktree is the only working directory."""
    return [str(CLAUDE_EXE), '-p', '--restricted', '--safe-mode', '--no-chrome', '--strict-mcp-config',
            '--tools', ','.join(CLAUDE_TOOLS), '--permission-mode', 'acceptEdits',
            '--permission-prompts', 'none', '--output-format', 'stream-json', '--verbose',
            '--include-partial-messages', '--max-turns', MAX_TURNS, '--model', model, '--effort', effort,
            '--settings', '{"remoteControlAtStartup":false}']


def codex_command(path, work, tree, audit, model, effort):
    """Shell stays off; our MCP server is the only tool source."""
    import codex_worker
    base = codex_worker.command_for(path, work, model, effort)
    index = base.index('mcp_servers={}')
    base[index - 1:index + 1] = []
    server = server_command(tree, audit)
    settings = {'command': server[0], 'args': server[1:], 'startup_timeout_sec': 30,
                'tool_timeout_sec': 120}
    extra = []
    for key, value in settings.items():
        extra.extend(['-c', f'mcp_servers.files.{key}={json.dumps(value)}'])
    return base[:-1] + extra + [base[-1]]


def grok_prepare(work, tree, audit, prompt):
    """Write the job-local config naming only our server, and the prompt file."""
    work = Path(work)
    settings = work / '.grok'
    if settings.exists():
        raise ScopedHeld('Grok needs a new assignment folder.')
    settings.mkdir()
    server = server_command(tree, audit)
    (settings / 'config.toml').write_text(
        '[mcp_servers.files]\n'
        f'command = {json.dumps(server[0])}\n'
        f'args = {json.dumps(server[1:])}\n', encoding='utf-8')
    (work / 'task.txt').write_bytes((PREFIX + prompt).encode('utf-8'))
    return [str(GROK_EXE), '--no-auto-update', '--prompt-file', str(work / 'task.txt'),
            '--model', 'grok-4.6', '--reasoning-effort', 'medium', '--output-format', 'json',
            '--max-turns', MAX_TURNS, '--no-subagents', '--verbatim', '--no-memory', '--no-plan',
            '--disable-web-search', '--tools', 'read_file', '--disallowed-tools', 'read_file,Agent',
            '--permission-mode', 'dontAsk', '--allow', 'MCPTool(files__*)',
            # Built-in tools are already removed; Grok reaches MCP tools through its
            # search_tool/use_tool pair, which a Read deny rule would block.
            *[part for rule in ('Bash', 'Edit', 'Write', 'WebFetch') for part in ('--deny', rule)]]


def grok_environment(env):
    return {**env, **GROK_COMPAT_OFF}


def grok_inspection(work, env):
    """Zero-inference discovery: exactly our MCP server, no job-local hooks."""
    try:
        completed = subprocess.run([str(GROK_EXE), 'inspect', '--json'], cwd=str(work),
                                   env=grok_environment(env), capture_output=True, text=True,
                                   encoding='utf-8', timeout=60, creationflags=_CREATE_NO_WINDOW)
        payload = json.loads(completed.stdout)
        servers = [server for server in payload['mcpServers'] if not server.get('disabled')]
        hooks = payload['hooks']
        if (completed.returncode or [server['name'] for server in servers] != ['files']
                or Path(servers[0]['target']).resolve() != Path(sys.executable).resolve()
                or any(hook['source']['type'] != 'user' for hook in hooks)
                or payload['permissions'].get('loaded') != 0
                or payload.get('plugins')):
            raise ValueError()
    except (OSError, ValueError, KeyError, TypeError, IndexError, subprocess.TimeoutExpired) as exc:
        raise ScopedHeld('Grok discovered configuration other than the scoped file server.') from exc
    # User-level hooks cannot be switched off without editing user settings; pin them.
    return hashlib.sha256(json.dumps(hooks, sort_keys=True).encode('utf-8')).hexdigest()


def grok_preflight(work, env):
    digest_now = grok_inspection(work, env)
    try:
        expected = json.loads(proof_path('grok').read_text(encoding='utf-8')).get('user_hooks_sha256')
    except (OSError, ValueError):
        expected = None
    if digest_now != expected:
        raise ScopedHeld('Grok user hooks changed since scoped validation; validate again.')


def agy_hook_config(tree):
    command = f'{Path(sys.executable).as_posix()} {AGY_HANDLER.as_posix()} {Path(tree).as_posix()}'
    return {'orchestrator-scoped-files': {'enabled': True, 'PreToolUse': [
        {'matcher': '*', 'hooks': [{'type': 'command', 'command': command, 'timeout': 5}]}]}}


def gemini_command(prompt, work, tree, env, timeout_seconds=None):
    import antigravity_boundary as agy
    if any(char.isspace() for char in str(Path(tree).resolve())):
        raise ScopedHeld('Antigravity scoped worktree paths with spaces need validation.')
    return agy.command_for(PREFIX + prompt, work, env, timeout_seconds,
                           hook=agy_hook_config(Path(tree).resolve()), add_dir=Path(tree).resolve())


def _allowed_codex_item(item):
    return (item.get('type') == 'mcp_tool_call' and item.get('server') == 'files'
            and item.get('tool') in TOOL_NAMES)


class ScopedClaudeProgress(ClaudeProgress):
    def __init__(self):
        super().__init__()
        self.tool_calls = []

    def observe(self, event, elapsed_s):
        if isinstance(event, dict) and event.get('type') == 'assistant':
            message = event.get('message')
            for block in (message.get('content') if isinstance(message, dict) else None) or []:
                if isinstance(block, dict) and block.get('type') in ('tool_use', 'server_tool_use'):
                    if block.get('type') != 'tool_use' or block.get('name') not in CLAUDE_TOOLS:
                        raise ScopedToolViolation('claude_unscoped_tool')
                    self.tool_calls.append(block['name'])
        super().observe(event, elapsed_s)

    def snapshot(self):
        return dict(super().snapshot(), scoped_tool_calls=len(self.tool_calls))


class ScopedCodexProgress(CodexProgress):
    def __init__(self, expected_model=None):
        super().__init__(expected_model)
        self.tool_calls = []

    def _item(self, event, etype, elapsed):
        item = event.get('item')
        if isinstance(item, dict) and item.get('type') == 'mcp_tool_call':
            if not _allowed_codex_item(item):
                raise ScopedToolViolation('codex_unscoped_tool')
            if etype == 'item.completed':
                self.tool_calls.append(item['tool'])
            self._state = 'working'
            return True
        return super()._item(event, etype, elapsed)

    def snapshot(self):
        return dict(super().snapshot(), scoped_tool_calls=len(self.tool_calls))


class ScopedAntigravityProgress(AntigravityProgress):
    """AGY offers every tool and the pre-tool hook denies the rest, so a denied
    attempt is recorded; a tool outside the allowlist, or a file tool on a path the
    policy refuses, that completes stops the task."""
    def __init__(self, expected_model=None, root=None):
        super().__init__(expected_model=expected_model)
        self.root = root
        self.tool_calls = []
        self.denied_calls = []

    def _path_refused(self, name, info):
        """Recheck a completed file tool's paths; the hook should already have refused them."""
        from antigravity_scoped_tools import PATHS, WRITES
        from scoped_files import Scope, ScopeError
        parameters = info.get('parameters')
        if self.root is None or not isinstance(parameters, dict):
            return self.root is not None
        try:
            scope = Scope(self.root)
            for key in PATHS:
                if key in parameters:
                    scope.check_path(parameters[key], for_write=name in WRITES)
        except (ScopeError, OSError, TypeError):
            return True
        return False

    def observe(self, event, elapsed_s):
        body = event.get('step_update') if isinstance(event, dict) and event.get('event') == 'step_update' else None
        kind = body.get('step_type') if isinstance(body, dict) else None
        if kind in ('tool', 'subagent'):
            name = body.get('tool_name')
            state = body.get('state')
            if kind == 'tool':
                info = body.get('tool_info')
                malformed = not isinstance(info, dict) or info.get('name') != name or 'subagent_info' in body
            else:
                # A hook-denied invoke_subagent closes as an ERROR step with no subagents.
                info = body.get('subagent_info')
                malformed = state != 'ERROR' or not isinstance(info, dict) or info.get('subagents') != []
            if (malformed or not isinstance(name, str) or self.terminal_result is not None
                    or self._protocol_error is not None or state not in ('ACTIVE', 'DONE', 'ERROR')
                    or (name not in AGY_TOOLS and state == 'DONE')):
                raise ScopedToolViolation('antigravity_unscoped_tool')
            if kind == 'tool' and state == 'DONE' and self._path_refused(name, info):
                raise ScopedToolViolation('antigravity_path_refused')
            if not isinstance(elapsed_s, (int, float)) or (self._last_event is not None and elapsed_s < self._last_event):
                self._reject('antigravity_invalid_event_time')
            self._conversation_id(event, body)
            if state == 'DONE':
                self.tool_calls.append(name)
            elif state == 'ERROR':
                self.denied_calls.append(name)
            self._events += 1
            self._last_event = float(elapsed_s)
            return
        super().observe(event, elapsed_s)

    def snapshot(self):
        return dict(super().snapshot(), scoped_tool_calls=len(self.tool_calls),
                    scoped_denied_calls=len(self.denied_calls))


def prepare(worker, prompt, work, tree, env, *, model=None, effort='medium', timeout_seconds=None,
            validating=False):
    """Return the invocation for one scoped assignment (invoke_cloud keyword arguments)."""
    if not validating and (error := proof_error(worker)):
        raise ScopedHeld(error)
    work, tree = Path(work), Path(tree)
    audit = work / 'scoped-audit.ndjson'
    if worker == 'claude':
        return {'command': claude_command(model, effort), 'stdin': PREFIX + prompt, 'cwd': tree, 'env': env,
                'parser': ScopedClaudeProgress(), 'protocol': 'claude'}
    if worker == 'codex':
        import codex_worker
        path = codex_worker.executable()
        if not path:
            raise ScopedHeld('Codex is unavailable.')
        return {'command': codex_command(path, work, tree, audit, model, effort), 'stdin': PREFIX + prompt,
                'cwd': work, 'env': codex_worker.environment(env), 'parser': ScopedCodexProgress(model),
                'protocol': 'codex', 'expected_model': model}
    if worker == 'grok':
        command = grok_prepare(work, tree, audit, prompt)
        if not validating:
            grok_preflight(work, env)
        return {'command': command, 'stdin': None, 'cwd': work, 'env': grok_environment(env),
                'parser': None, 'protocol': 'claude'}
    if worker == 'gemini':
        from antigravity_boundary import MODEL, child_environment
        env = child_environment(env)
        return {'command': gemini_command(prompt, work, tree, env, timeout_seconds), 'stdin': None,
                'cwd': work, 'env': env, 'parser': ScopedAntigravityProgress(MODEL, tree.resolve()),
                'protocol': 'antigravity', 'expected_model': MODEL}
    raise ScopedHeld(f'{worker} has no scoped file-tool route.')
