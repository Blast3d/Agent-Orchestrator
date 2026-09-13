"""Read-only installation checks; no credentials or inference."""
import importlib.util
import json
import sqlite3
from pathlib import Path
import sys

from paths import ROOT, APP, CONFIG, STATE, VENDOR

def diagnose():
    checks = []
    def add(name, ok, detail):
        checks.append({'check': name, 'ok': bool(ok), 'detail': detail})
    add('python', sys.version_info >= (3, 11), 'Requires Python 3.11 or newer')
    connection=sqlite3.connect(':memory:')
    try:
        connection.execute('CREATE VIRTUAL TABLE brain_check USING fts5(content)')
        connection.execute("INSERT INTO brain_check(brain_check,rank) VALUES('secure-delete',1)")
        add('brain:sqlite',sqlite3.sqlite_version_info >= (3,42,0),'SQLite 3.42+ with FTS5 secure deletion; no model or database server required')
    except sqlite3.Error:
        add('brain:sqlite',False,'SQLite FTS5 with secure deletion is required')
    finally:connection.close()
    for relative in ('orchestrator.py', 'config/workers.json', 'config/ollama-profile.json',
                     'app/dispatch_worker.py', 'app/usage_guard.py', 'app/quota_tui.py', 'app/quota_codex.py',
                     'app/quota_admission.py', 'app/background_usage.py', 'app/usage_report.py',
                     'app/task_inbox.py', 'app/assets/task-inbox.html', 'app/assignment_receipts.py',
                     'app/brief_check.py', 'app/task_handoff.py', 'app/coordinator_handoff.py', 'app/coordinator_transfer.py',
                     'app/watch_coordinator.py', 'Watch Fable Coordinator.cmd', 'app/local_services.py',
                     'app/coordinator_viewer.py', 'app/coordinator_viewer_page.py',
                     'app/coordinator_interaction.py', 'app/native_activity.py',
                     'app/start_coordinator_viewer.py', 'Open Orchestrator Viewer.cmd',
                     'docs/orchestrator-viewer.md',
                     'app/execution_limits.py', 'app/worker_progress.py', 'app/worker_execution.py',
                     'app/system_map.py','app/assets/system-map-template.html','app/assets/system-map-data.json','Open System Map.cmd',
                     'app/memory_bundle.py','app/memory_usage.py','app/project_memory_usage.py',
                     'app/task_activity.py', 'app/automatic_memory.py', 'app/team_planner.py', 'docs/team-sizing.md',
                     'app/orchestration_context.py', 'app/orchestration_lifecycle.py',
                     'app/assets/orchestration-context.md', 'docs/startup-and-closeout.md',
                     'app/antigravity_boundary.py', 'app/antigravity_deny_tools.py', 'app/antigravity_progress.py',
                     'app/brain_store.py','app/brain_cli.py','app/brain_dashboard.py','app/brain_interface.py',
                     'app/storage_budget.py','app/start_brain_dashboard.py','Open Brain Dashboard.cmd',
                     'runtime/policy.json', 'skills/multi-model-orchestrator/SKILL.md'):
        add('file:' + relative, (ROOT / relative).is_file(), 'Required application component')
    sys.path.insert(0, str(VENDOR))
    for module in ('winpty', 'pyte', 'wcwidth'):
        try:
            __import__(module)
            add('dependency:' + module, True, 'Import succeeded')
        except (ImportError, OSError):
            add('dependency:' + module, False, 'Quota terminal reader dependency unavailable')
    try:
        workers = json.loads((CONFIG / 'workers.json').read_text(encoding='utf-8'))
        discovery = json.loads((Path.home() / '.codex/model-workers.json').read_text(encoding='utf-8'))
        add('global-discovery', workers == discovery, 'Global worker registry must match the maintained configuration')
        for worker in workers['workers']:
            if worker.get('executable'):
                add('worker:' + worker['id'], Path(worker['executable']).is_file(), 'Executable exists; login, quota and task capability are separate checks')
        add('no-billable-fallback', workers['policy']['automatic_billable_fallback'] is False, 'Included or local routes only')
    except (OSError, ValueError, KeyError):
        add('registry', False, 'Registry unavailable or invalid')
    try:
        # Validate policy without creating directories or changing runtime state.
        from usage_guard import validate_policy
        validate_policy(json.loads((STATE / 'policy.json').read_text(encoding='utf-8')))
        add('quota-policy', True, 'Thresholds, estimates and pool identities validated')
    except (OSError, ValueError, ImportError, KeyError, TypeError):
        add('quota-policy', False, 'Policy unavailable or invalid')
    source = ROOT / 'skills/multi-model-orchestrator'
    installed = Path.home() / '.codex/skills/multi-model-orchestrator'
    for file in source.rglob('*'):
        if file.is_file() and '__pycache__' not in file.parts:
            relative = file.relative_to(source)
            target = installed / relative
            add('global-skill:' + relative.as_posix(), target.is_file() and target.read_bytes() == file.read_bytes(), 'Global skill forwards to the maintained app')
    for skill_name in ('multi-model-orchestrator', 'orchestrator-takeover'):
        for file in (ROOT / 'skills' / skill_name).rglob('*'):
            if file.is_file() and '__pycache__' not in file.parts:
                relative = file.relative_to(ROOT / 'skills' / skill_name)
                target = Path.home() / '.claude/skills' / skill_name / relative
                add('claude-skill:' + skill_name + '/' + relative.as_posix(),
                    target.is_file() and target.read_bytes() == file.read_bytes(),
                    'Claude discovers the same lead rules and explicit takeover command')
    for provider, path in (('codex', Path.home() / '.codex/AGENTS.md'),
                           ('claude', Path.home() / '.claude/CLAUDE.md')):
        try:
            instructions = path.read_text(encoding='utf-8')
            installed_hook = ('<!-- agent-orchestrator-startup -->' in instructions
                              and str(ROOT / 'orchestrator.py') in instructions)
        except (OSError, UnicodeError):
            installed_hook = False
        add(provider + '-startup-instructions', installed_hook,
            'Global instructions load orchestration for substantial work; run scripts/install_global.py to synchronize')
    return {'ok': all(c['ok'] for c in checks), 'checks': checks,
            'limits': ['Offline installation checks do not prove current authentication, remaining quota or task quality.']}
