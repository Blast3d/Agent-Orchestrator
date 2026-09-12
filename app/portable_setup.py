"""Create only missing per-installation settings; no logins or global changes."""
import argparse
import copy
import json
from pathlib import Path

from paths import ROOT
from usage_guard import DEFAULT_POLICY, validate_policy


def initialize(root=ROOT, home=None):
    root = Path(root).resolve()
    home = Path.home() if home is None else Path(home)
    policy = copy.deepcopy(DEFAULT_POLICY)
    policy.update(quota_admission_mode='advisory', worker_start_threshold_pct=20,
                  floor_pct=0, automatic_fallbacks={})
    validate_policy(policy)
    workers = {
        'schema_version': 1, 'orchestrator': 'Codex',
        'policy': {'automatic_billable_fallback': False,
                   'prefer_local_or_included_quota': True,
                   'recheck_readiness_before_dispatch': True,
                   'paused_claude_model_families': ['fable']},
        'application_root': str(root), 'task_directory': str(root / 'runs/tasks'),
        'skill_source_directory': str(root / 'skills/multi-model-orchestrator'),
        'workers': [
            {'id': 'claude', 'provider': 'Anthropic', 'tool': 'claude-code',
             'requested_model': 'opus', 'effort': 'medium', 'quota_worker': 'claude',
             'executable': str(home / 'AppData/Roaming/npm/node_modules/@anthropic-ai/claude-code/bin/claude.exe'),
             'status': 'Install and authenticate separately; readiness unverified'},
            {'id': 'grok', 'provider': 'xAI', 'tool': 'grok-build',
             'requested_model': 'grok-4.6', 'quota_worker': 'grok',
             'executable': str(home / '.grok/bin/grok.exe'),
             'status': 'Install and authenticate separately; readiness unverified'},
            {'id': 'vscode-copilot', 'tool': 'vscode-language-model-api',
             'quota_worker': 'vscode-copilot',
             'status': 'Install bundled VSIX, enable bridge and choose a model in VS Code'}],
        'quota_guard': {'script': str(root / 'app/usage_guard.py'),
                        'dispatch_script': str(root / 'app/dispatch_worker.py'),
                        'state_directory': str(root / 'runtime'),
                        'required_before_hosted_delegation': True},
    }
    # This optional route remains unconfigured: bootstrap never starts local models.
    local = {'executable': '', 'models_directory': '', 'chat_model': '',
             'base_model': '', 'environment': {'OLLAMA_HOST': '127.0.0.1:11434',
                                              'OLLAMA_NO_CLOUD': '1'}}
    created, preserved = [], []
    for relative, data in (('config/workers.json', workers),
                           ('config/ollama-profile.json', local),
                           ('runtime/policy.json', policy)):
        destination = root / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        try:
            with destination.open('x', encoding='utf-8') as stream:
                stream.write(json.dumps(data, indent=2) + '\n')
            created.append(relative)
        except FileExistsError:
            preserved.append(relative)
    for relative in ('runs/tasks', 'runtime/workspaces/claude', 'runtime/workspaces/grok',
                     'runtime/workspaces/google', 'runtime/workspaces/tasks'):
        (root / relative).mkdir(parents=True, exist_ok=True)
    return {'created': created, 'preserved': preserved, 'model_calls': 0,
            'global_settings_changed': False}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--quiet', action='store_true')
    args = parser.parse_args()
    result = initialize()
    if not args.quiet:
        print(json.dumps(result, indent=2))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
