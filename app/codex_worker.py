"""Supplied-text Codex worker (ASTRA or Sol) through the official `codex exec`.

The worker answers a brief and uses no tools: shell, code-mode host, MCP, apps,
plugins, skills, memories, sub-agents and web search are switched off for the
process only (the user's config is never changed), the sandbox is read-only and
the working folder is the job's empty workspace. It runs only on a ChatGPT plan
sign-in, the included Codex allowance, never on per-call API billing.
"""
import os
import re
import subprocess

from lead_selection import CODEX_LEADS, LEADS

EFFORTS = ('low', 'medium', 'high')
_DISABLED_FEATURES = (
    'shell_tool', 'unified_exec', 'shell_snapshot', 'code_mode_host', 'apps', 'plugins', 'remote_plugin',
    'multi_agent', 'multi_agent_v2', 'memories', 'browser_use', 'browser_use_external', 'computer_use',
    'in_app_browser', 'image_generation', 'view_image', 'skill_search', 'skill_mcp_dependency_install',
    'tool_suggest', 'goals', 'hooks', 'sleep_tool', 'workspace_dependencies')
_OVERRIDES = (
    'approval_policy="never"', 'forced_login_method="chatgpt"', 'web_search="disabled"', 'mcp_servers={}',
    'analytics.enabled=false', 'project_doc_max_bytes=0', 'include_environment_context=false',
    'include_permissions_instructions=false', 'include_apps_instructions=false',
    'memories.use_memories=false', 'memories.generate_memories=false',
    'skills.include_instructions=false', 'skills.bundled.enabled=false',
    'tools.experimental_request_user_input.enabled=false', 'tools.update_plan.enabled=false')
# Credentials that would switch Codex from the plan sign-in to API billing.
_BILLING_ENV = ('OPENAI_API_KEY', 'CODEX_API_KEY', 'OPENAI_BASE_URL', 'OPENAI_ORG_ID', 'OPENAI_PROJECT_ID')
_PLAN_SIGNIN = re.compile(r'\blogged in using chatgpt\b', re.I)


def select_codex_model(requested=None):
    """astra or sol; defaults to the Codex lead chosen on the lead switch."""
    if requested is None:
        from lead_selection import describe
        requested = describe()['codex_lead']
    if requested not in CODEX_LEADS:
        raise ValueError('Choose astra or sol for the Codex worker')
    return LEADS[requested]['model']


def executable():
    from quota_codex import QuotaError, find_codex
    try:
        return find_codex()
    except QuotaError:
        return None


def environment(env):
    env = dict(env)
    for name in _BILLING_ENV:
        env.pop(name, None)
    return env


def plan_signin(path, env=None):
    """True only for a ChatGPT plan sign-in; the status text is never stored."""
    try:
        completed = subprocess.run([path, 'login', 'status'], capture_output=True, text=True,
                                   encoding='utf-8', errors='replace', timeout=30,
                                   env=environment(env if env is not None else os.environ),
                                   creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0)
    except (OSError, subprocess.TimeoutExpired):
        return False
    return completed.returncode == 0 and bool(_PLAN_SIGNIN.search(completed.stdout + completed.stderr))


def command_for(path, work, model, effort='medium'):
    if effort not in EFFORTS:
        raise ValueError('Choose low, medium or high Codex effort')
    command = [path, 'exec', '--json', '--ephemeral', '--skip-git-repo-check', '--ignore-user-config',
               '--ignore-rules', '--color', 'never', '--sandbox', 'read-only', '--cd', str(work),
               '--model', model, '-c', f'model_reasoning_effort="{effort}"']
    for override in _OVERRIDES:
        command.extend(['-c', override])
    for feature in _DISABLED_FEATURES:
        command.extend(['--disable', feature])
    # The brief arrives on stdin, never on the command line.
    command.append('-')
    return command


def configuration(model, effort):
    return {'transport': 'codex exec --json', 'model': model, 'reasoning_effort': effort,
            'sandbox': 'read-only', 'workspace': 'empty job folder', 'auth': 'ChatGPT plan sign-in',
            'tools': [], 'disabled': 'shell, code mode, MCP, apps, plugins, skills, memories, sub-agents, web search'}
