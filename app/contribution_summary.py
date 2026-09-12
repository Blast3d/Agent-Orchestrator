"""Who did what, and how much: per-run and cross-run summaries from saved evidence.

Reads existing contribution-audit.json, run.json and runs/tasks/*/record.json only.
No model calls. Prompts, responses and evidence text never enter the output.
Missing token measurements stay null; the coordinator's own work is not metered.
"""
import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import re

from paths import ROOT

RUN_NAME = re.compile(r'[A-Za-z0-9][A-Za-z0-9._-]{0,199}')
JOB_ID = re.compile(r'[0-9a-f]{32}')
AGENT_FIELDS = ('id', 'name', 'provider', 'model', 'accepted_work_pct', 'delegations', 'delegation_pct',
                'input_tokens', 'output_tokens', 'known_tokens', 'known_token_share_pct')
TASK_FIELDS = ('job_id', 'worker', 'task', 'category', 'status', 'review_status', 'execution_status',
               'size', 'created_at', 'finalized_at', 'assignment_project_id')
NOTES = ['Accepted-work shares are the reviewer\'s recorded estimates, not measured effort or time.',
         'Token counts come only from dispatched workers whose provider reported them; unknown stays unknown.',
         'The coordinator\'s own conversation (ASTRA or Fable) is not metered, so its tokens are always unknown.',
         'Cost is only what a provider CLI reported for a task (Claude modelUsage.costUSD); it is not a bill.']


def _read(path, limit=4 * 1024 * 1024):
    try:
        if path.stat().st_size > limit:
            return None
        value = json.loads(path.read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return None
    return value if isinstance(value, dict) else None


def _count(value):
    return value if isinstance(value, int) and not isinstance(value, bool) and value >= 0 else None


def _add(total, value):
    return total if value is None else (total or 0) + value


def _stamp(path):
    try:
        return datetime.fromtimestamp(path.stat().st_mtime, timezone.utc).isoformat()
    except OSError:
        return None


def _model(record):
    for key in ('model', 'actual_model', 'requested_model'):
        if isinstance(record.get(key), str) and record[key].strip():
            return record[key].strip()
    usage = record.get('modelUsage')
    if isinstance(usage, dict) and usage:
        return ', '.join(sorted(str(key) for key in usage))
    return 'unknown'


def _cost(record):
    """Provider-reported spend for one task; None when nothing was reported."""
    usage = record.get('modelUsage')
    entries = usage.values() if isinstance(usage, dict) else []
    known = [entry['costUSD'] for entry in entries if isinstance(entry, dict)
             and isinstance(entry.get('costUSD'), (int, float)) and not isinstance(entry['costUSD'], bool) and entry['costUSD'] >= 0]
    return round(sum(known), 6) if known else None


def task_row(record, task_dir):
    row = {key: record.get(key) for key in TASK_FIELDS}
    usage = record.get('usage') if isinstance(record.get('usage'), dict) else {}
    row.update(input_tokens=_count(usage.get('input_tokens')), output_tokens=_count(usage.get('output_tokens')),
               cost_usd=_cost(record), model=_model(record), audit=(task_dir / 'contribution-audit.md').is_file())
    return row


def task_usage(rows):
    """Totals over linked canonical task records; unknown stays unknown."""
    usage = {'tasks': len(rows), 'tasks_with_tokens': 0, 'input_tokens': None, 'output_tokens': None, 'cost_usd': None}
    for row in rows:
        if row['input_tokens'] is not None or row['output_tokens'] is not None:
            usage['tasks_with_tokens'] += 1
        for key in ('input_tokens', 'output_tokens', 'cost_usd'):
            usage[key] = _add(usage[key], row[key])
    return usage


def _run_dir(root, run_id):
    if not isinstance(run_id, str) or not RUN_NAME.fullmatch(run_id):
        raise ValueError('Choose a run inside this workspace.')
    run = root / '.orchestration' / run_id
    if not run.is_dir() or run.resolve().parent != (root / '.orchestration').resolve():
        raise ValueError('Choose a run inside this workspace.')
    return run


def _records(root):
    for path in sorted((root / 'runs/tasks').glob('*/record.json')):
        if JOB_ID.fullmatch(path.parent.name):
            record = _read(path)
            if record:
                yield path.parent, record


def run_tasks(root, run_id, manifest, audit):
    """Dispatched tasks linked to the run by manifest job id, audit activity, or assignment scope."""
    ids = []
    for entry in manifest.get('tasks') or []:
        if isinstance(entry, dict) and isinstance(entry.get('job_id'), str) and JOB_ID.fullmatch(entry['job_id']):
            ids.append(entry['job_id'])
    for event in (audit or {}).get('activity') or []:
        if isinstance(event, dict) and isinstance(event.get('task_id'), str) and JOB_ID.fullmatch(event['task_id']):
            ids.append(event['task_id'])
    rows, seen = [], set()
    for task_dir, record in _records(root):
        job = task_dir.name
        if job in ids or record.get('assignment_project_id') == run_id:
            if job not in seen:
                seen.add(job)
                rows.append(task_row(record, task_dir))
    rows.sort(key=lambda row: str(row.get('created_at') or ''))
    return rows


def run_summary(root, run_id):
    root = Path(root).resolve()
    run = _run_dir(root, run_id)
    manifest = _read(run / 'run.json') or {}
    audit = _read(run / 'contribution-audit.json')
    summary = {'run_id': run_id, 'title': manifest.get('display_name') or (audit or {}).get('title') or run_id,
               'run_status': manifest.get('status'), 'updated_at': _stamp(run / 'contribution-audit.json') or _stamp(run / 'run.json'),
               'audit': None, 'agents': [], 'categories': [], 'tasks': run_tasks(root, run_id, manifest, audit), 'notes': NOTES}
    summary['task_usage'] = task_usage(summary['tasks'])
    if audit:
        usage = audit.get('usage') if isinstance(audit.get('usage'), dict) else {}
        coverage = usage.get('token_coverage') if isinstance(usage.get('token_coverage'), dict) else {}
        summary['audit'] = {'status': audit.get('status'), 'attribution_complete': bool(audit.get('attribution_complete')),
                            'unattributed_pct': audit.get('unattributed_pct'),
                            'usage': {key: usage.get(key) for key in ('delegations', 'input_tokens', 'output_tokens', 'known_tokens')},
                            'token_coverage': coverage.get('status')}
        summary['agents'] = [{key: agent.get(key) for key in AGENT_FIELDS}
                             for agent in audit.get('by_agent') or [] if isinstance(agent, dict)]
        summary['categories'] = [{'category': group.get('category'), 'unattributed_pct': group.get('unattributed_pct'),
                                  'by_agent': [{'id': agent.get('id'), 'accepted_work_pct': agent.get('accepted_work_pct')}
                                               for agent in group.get('by_agent') or [] if isinstance(agent, dict)]}
                                 for group in audit.get('by_category') or [] if isinstance(group, dict)]
    return summary


def by_worker(root):
    """Cross-run totals per dispatched worker identity, from canonical task records."""
    totals = {}
    for task_dir, record in _records(Path(root).resolve()):
        row = task_row(record, task_dir)
        worker = str(row['worker'] or 'unknown')
        total = totals.setdefault(worker, {'worker': worker, 'tasks': 0, 'by_status': {}, 'tasks_with_tokens': 0,
                                           'input_tokens': None, 'output_tokens': None, 'cost_usd': None, 'models': {}})
        total['tasks'] += 1
        status = str(row['status'] or 'unknown')
        total['by_status'][status] = total['by_status'].get(status, 0) + 1
        if row['input_tokens'] is not None or row['output_tokens'] is not None:
            total['tasks_with_tokens'] += 1
        total['input_tokens'] = _add(total['input_tokens'], row['input_tokens'])
        total['output_tokens'] = _add(total['output_tokens'], row['output_tokens'])
        total['cost_usd'] = _add(total['cost_usd'], row['cost_usd'])
        total['models'][row['model']] = total['models'].get(row['model'], 0) + 1
    return sorted(totals.values(), key=lambda total: (-total['tasks'], total['worker']))


def overview(root):
    root = Path(root).resolve()
    runs = []
    for path in sorted((root / '.orchestration').iterdir()) if (root / '.orchestration').is_dir() else []:
        if path.is_dir() and RUN_NAME.fullmatch(path.name) and ((path / 'run.json').is_file() or (path / 'contribution-audit.json').is_file()):
            summary = run_summary(root, path.name)
            audit = summary['audit'] or {}
            runs.append({'run_id': summary['run_id'], 'title': summary['title'], 'run_status': summary['run_status'],
                         'updated_at': summary['updated_at'], 'audit_status': audit.get('status') or 'missing',
                         'attribution_complete': audit.get('attribution_complete', False), 'usage': audit.get('usage'),
                         'agents': [{key: agent.get(key) for key in ('name', 'provider', 'accepted_work_pct', 'delegations', 'input_tokens', 'output_tokens')}
                                    for agent in summary['agents']],
                         'tasks': len(summary['tasks']), 'task_usage': summary['task_usage']})
    runs.sort(key=lambda run: (run['updated_at'] or '', run['run_id']), reverse=True)
    return {'schema_version': 1, 'generated_at': datetime.now(timezone.utc).isoformat(),
            'runs': runs, 'by_worker': by_worker(root), 'notes': NOTES}


def _num(value):
    return 'Unknown' if value is None else f'{value:,}' if isinstance(value, int) else str(value)


def _pct(value):
    return 'pending' if value is None else f'{value:.1f}%'


def _usd(value):
    return 'Unknown' if value is None else f'${value:,.4f}'


def render_markdown(data):
    lines = []
    if 'runs' in data:
        lines += ['# Who did what, and how much', '', '| Run | Status | Audit | Agents (accepted share) | Delegations | Tokens in / out | Task cost |', '|---|---|---|---|---|---|---|']
        for run in data['runs']:
            agents = '; '.join(f"{agent['name']} {_pct(agent['accepted_work_pct'])}" for agent in run['agents']) or 'no audit'
            usage = run.get('usage') or {}
            lines.append(f"| {run['title']} | {run.get('run_status') or '?'} | {run['audit_status']} | {agents} | {_num(usage.get('delegations'))} | {_num(usage.get('input_tokens'))} / {_num(usage.get('output_tokens'))} | {_usd(run['task_usage']['cost_usd'])} |")
        lines += ['', '## Dispatched work per worker (all runs)', '', '| Worker | Tasks | By status | Tasks with tokens | Tokens in / out | Cost (reported) | Models |', '|---|---|---|---|---|---|---|']
        for total in data['by_worker']:
            statuses = ', '.join(f'{key} {value}' for key, value in sorted(total['by_status'].items()))
            models = ', '.join(f'{key} x{value}' for key, value in sorted(total['models'].items(), key=lambda item: -item[1]))
            lines.append(f"| {total['worker']} | {total['tasks']} | {statuses} | {total['tasks_with_tokens']} | {_num(total['input_tokens'])} / {_num(total['output_tokens'])} | {_usd(total['cost_usd'])} | {models} |")
    else:
        lines += [f"# {data['title']}", '', f"Run `{data['run_id']}` - status {data.get('run_status') or '?'} - audit {(data['audit'] or {}).get('status') or 'missing'}", '']
        if data['agents']:
            lines += ['| Agent | Provider | Accepted share | Delegations | Tokens in / out | Known-token share |', '|---|---|---|---|---|---|']
            for agent in data['agents']:
                lines.append(f"| {agent['name']} | {agent.get('provider') or '?'} | {_pct(agent['accepted_work_pct'])} | {_num(agent['delegations'])} | {_num(agent['input_tokens'])} / {_num(agent['output_tokens'])} | {_pct(agent['known_token_share_pct'])} |")
        if data['tasks']:
            lines += ['', '| Task | Worker | Model | Status | Tokens in / out | Cost |', '|---|---|---|---|---|---|']
            for task in data['tasks']:
                lines.append(f"| {task.get('task') or task['job_id'][:8]} | {task.get('worker')} | {task['model']} | {task.get('status')} | {_num(task['input_tokens'])} / {_num(task['output_tokens'])} | {_usd(task['cost_usd'])} |")
            usage = data['task_usage']
            lines.append(f"| **Total** | {usage['tasks']} tasks | | {usage['tasks_with_tokens']} with tokens | {_num(usage['input_tokens'])} / {_num(usage['output_tokens'])} | {_usd(usage['cost_usd'])} |")
    lines += ['', *('- ' + note for note in data['notes']), '']
    return '\n'.join(lines)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, default=ROOT)
    parser.add_argument('--run', help='One run name for its agents and tasks; omit for every run plus per-worker totals')
    parser.add_argument('--markdown', action='store_true', help='Print a readable table instead of JSON')
    args = parser.parse_args(argv)
    try:
        data = run_summary(args.root, args.run) if args.run else overview(args.root)
    except (OSError, ValueError) as exc:
        print(json.dumps({'ok': False, 'error': str(exc) if isinstance(exc, ValueError) else type(exc).__name__, 'model_calls': 0}))
        return 2
    print(render_markdown(data) if args.markdown else json.dumps(data, ensure_ascii=False, indent=2))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
