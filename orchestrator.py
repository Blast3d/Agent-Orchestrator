"""One entry point for the local model orchestrator."""
import json
from pathlib import Path
import runpy
import sys
import webbrowser

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / 'app'))

def main():
    actions = {'status': ('usage_guard.py', 'status'), 'refresh': ('usage_guard.py', 'refresh'),
               'start': ('orchestration_lifecycle.py', 'start'), 'closeout': ('orchestration_lifecycle.py', 'closeout'),
               'run': ('dispatch_worker.py', None), 'review': ('dispatch_worker.py', 'review'),
               'monitor': ('start_usage_monitor.py', None), 'stop-monitor': ('start_usage_monitor.py', '--stop'),
               'local': ('manage_local.py', None), 'inventory': ('inventory_agents.py', None),
               'contributions': ('contribution_cli.py', None), 'visuals': ('project_visuals.py', None),
               'inbox': ('task_inbox.py', None), 'brief-check': ('brief_check.py', None),
               'summary': ('task_summary_repair.py', None), 'lead': ('coordinator_handoff.py', None),
               'brain': ('brain_cli.py', None), 'watch': ('watch_coordinator.py', None),
               'team': ('team_planner.py', None), 'remember': ('automatic_memory.py', None),
               'vscode-bots': ('vscode_bots.py', None),
               'viewer': ('start_coordinator_viewer.py', None), 'map': ('system_map.py', None)}
    if len(sys.argv) < 2 or sys.argv[1] in ('-h', '--help'):
        print('Agent Orchestrator: ASTRA leads by default; configured Claude Opus can continue through an explicit handoff.\n'
              'Commands: start, closeout, brain, lead, viewer, watch, team, remember, map, doctor, status, refresh, dashboard, inbox, summary, visuals, brief-check, run, review, contributions, tasks, monitor, stop-monitor, local, inventory, vscode-bots\n'
              'Examples:\n  python orchestrator.py doctor\n  python orchestrator.py refresh --provider claude\n'
              '  python orchestrator.py run claude --prompt-file brief.txt --output answer.json --task review --size small\n'
              '  python orchestrator.py review JOB_ID --decision accepted --reviewer Codex --note "Verified against source and tests."')
        return 0
    action = sys.argv[1]
    if action == 'doctor':
        from doctor import diagnose
        report = diagnose()
        print(json.dumps(report, indent=2))
        return 0 if report['ok'] else 1
    if action == 'tasks':
        from paths import TASKS
        records = []
        for path in sorted(TASKS.glob('*/record.json')):
            try:
                job = json.loads(path.read_text(encoding='utf-8'))
                records.append({key: job.get(key) for key in ('job_id', 'task', 'worker', 'status', 'execution_status', 'review_status', 'created_at')})
            except (ValueError, OSError):
                records.append({'job_id': path.parent.name, 'status': 'unreadable'})
        print(json.dumps({'tasks': records}, indent=2))
        return 0
    if action == 'dashboard':
        from usage_guard import Guard
        guard = Guard()
        guard.dashboard()
        webbrowser.open((guard.root / 'usage-dashboard.html').as_uri())
        return 0
    if action not in actions:
        raise ValueError('Unknown action; use --help')
    script, subcommand = actions[action]
    sys.argv = [str(ROOT / 'app' / script)] + ([subcommand] if subcommand else []) + sys.argv[2:]
    runpy.run_path(sys.argv[0], run_name='__main__')
    return 0

if __name__ == '__main__':
    raise SystemExit(main())
