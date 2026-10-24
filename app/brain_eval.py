"""Hand-labelled Brain recall test: draft cases from real task queries, then A/B settings.

`extract` drafts cases from canonical tasks that recorded a memory query. A person
then fills each case's `expected_ids` with the memories that should rank first.
`run` scores every labelled case under a baseline and an optional candidate
configuration, and reports accuracy, latency and context size plus the cases
whose top answer flipped. A change to ranking should show a gain here before it
becomes a default.

Searches open the Brain read-only and record no recall traces. No Jev
(OpenRouter) request is made unless a configuration enables it AND --allow-jev
is given; those requests are billed and count toward the daily Jev limit.
"""
import argparse
from datetime import datetime, timezone
import json
import math
from pathlib import Path
import re
import sqlite3
import statistics
import sys
import time

from brain_store import BrainStore, safe_path, scope, text

SCHEMA_VERSION = 1
MAX_TASK_DIRS = 2000
MAX_TASK_BYTES = 8 * 1024**2
MAX_CASES = 500
MAX_CASES_BYTES = 4 * 1024**2
MAX_STARTUP_PACKETS = 500
MAX_STARTUP_BYTES = 1024**2
STRATEGIES = ('auto', 'keyword', 'graph', 'semantic')
DEPTHS = ('compact', 'balanced', 'deep')
# depth=case uses each case's recorded depth (compact when none was recorded).
DEFAULT_CONFIG = {'strategy': 'auto', 'depth': 'case', 'jev': 'off', 'shape': 'rubric'}
CONFIG_CHOICES = {'strategy': STRATEGIES, 'depth': DEPTHS + ('case',), 'jev': ('on', 'off'), 'shape': ('rubric', 'choice')}
_ID = re.compile(r'[a-f0-9]{32}')
INSTRUCTIONS = ('Fill expected_ids for each case with the memory IDs that should rank first, best first. '
                'Use the candidate titles, the dashboard or `brain get` to decide. Leave expected_ids empty '
                'to skip a case. Cases with no correct memory in the Brain should be skipped, not guessed.')


def _now():
    return datetime.now(timezone.utc).isoformat(timespec='seconds')


def _rating(result):
    try:
        from memory_usage import summary
        feedback = summary(result).get('feedback') or {}
    except (ValueError, TypeError, KeyError, AttributeError):
        return None
    return feedback.get('rating') if feedback.get('status') == 'reviewed' else None


def _titles(store, ids):
    """Titles for labelling help; unknown or forgotten IDs are simply omitted."""
    ids = [identifier for identifier in ids if _ID.fullmatch(identifier)]
    if not ids:
        return {}
    marks = ','.join('?' * len(ids))
    with store._connection() as con:
        return {row['id']: {'title': row['title'], 'kind': row['kind'], 'status': row['status']}
                for row in con.execute(f'SELECT id,title,kind,status FROM memories WHERE id IN ({marks})', ids)}


def _startup_cases(project_id, workspaces):
    """Recall queries saved in orchestration startup packets, newest first."""
    packets = []
    for workspace in workspaces:
        folder = Path(workspace) / '.orchestration'
        if folder.is_dir():
            packets += [path for path in folder.glob('*/startup-context.json') if path.is_file()]
    packets.sort(key=lambda path: path.stat().st_mtime, reverse=True)
    drafted = []
    for path in packets[:MAX_STARTUP_PACKETS]:
        try:
            if path.stat().st_size > MAX_STARTUP_BYTES:
                continue
            packet = json.loads(path.read_text(encoding='utf-8'))
        except (OSError, ValueError):
            continue
        memory = packet.get('project_memory') if isinstance(packet, dict) else None
        query = memory.get('query') if isinstance(memory, dict) else None
        if packet.get('project_id') != project_id or not isinstance(query, str) or not query.strip():
            continue
        results = memory.get('results') if isinstance(memory.get('results'), list) else []
        budget = (memory.get('retrieval') or {}).get('budget') if isinstance(memory.get('retrieval'), dict) else None
        drafted.append({'id': 'run-' + str(packet.get('run_id') or path.parent.name)[:90],
                        'project_id': project_id, 'query': query.strip()[:500],
                        'profile': memory.get('profile') if isinstance(memory.get('profile'), str) else 'general',
                        'depth': budget.get('depth') if isinstance(budget, dict) and budget.get('depth') in DEPTHS else 'balanced',
                        'source_run': str(path.parent), 'rating': None, 'finalized_at': packet.get('prepared_at'),
                        'supplied_ids': [row['id'] for row in results if isinstance(row, dict) and isinstance(row.get('id'), str)][:24],
                        'expected_ids': [], 'notes': ''})
    return drafted, len(packets)


def extract(root, project_id, *, limit=100, local_candidates=10, workspaces=()):
    """Draft unlabelled cases from canonical tasks and startup packets; rated-helpful tasks come first."""
    root = Path(root).resolve()
    project_id = scope(project_id)
    limit = max(1, min(int(limit), MAX_CASES))
    tasks = safe_path(root, root / 'runs/tasks')
    directories = sorted((entry for entry in tasks.iterdir() if entry.is_dir()) if tasks.is_dir() else [],
                         key=lambda entry: entry.stat().st_mtime, reverse=True)[:MAX_TASK_DIRS]
    drafted, scanned = [], 0
    for directory in directories:
        path = directory / 'result.json'
        try:
            if not path.is_file() or path.stat().st_size > MAX_TASK_BYTES:
                continue
            result = json.loads(path.read_text(encoding='utf-8'))
        except (OSError, ValueError):
            continue
        scanned += 1
        if not isinstance(result, dict) or result.get('assignment_project_id') != project_id:
            continue
        context = result.get('memory_context')
        query = context.get('query') if isinstance(context, dict) else None
        if not isinstance(query, str) or not query.strip():
            continue
        ids = [identifier for identifier in context.get('ids', []) if isinstance(identifier, str)][:24]
        budget = context.get('budget') if isinstance(context.get('budget'), dict) else {}
        drafted.append({'id': 'task-' + str(result.get('job_id', directory.name))[:32],
                        'project_id': project_id, 'query': query.strip()[:500],
                        'profile': context.get('profile') if isinstance(context.get('profile'), str) else 'general',
                        'depth': budget.get('depth') if budget.get('depth') in DEPTHS else 'compact',
                        'source_job_id': result.get('job_id'), 'rating': _rating(result),
                        'finalized_at': result.get('finalized_at'),
                        'supplied_ids': ids, 'expected_ids': [], 'notes': ''})
    startup, packets = _startup_cases(project_id, [root, *workspaces])
    drafted += startup
    # Rank before removing repeated queries, so a rated or newer copy is the one kept.
    order = {'helped': 0, 'harmful': 1, 'neutral': 2}
    drafted.sort(key=lambda case: (order.get(case['rating'], 3), -_timestamp(case.get('finalized_at')), case['id']))
    seen, cases = set(), []
    for case in drafted:
        key = ' '.join(case['query'].casefold().split())
        if key not in seen and len(cases) < limit:
            seen.add(key)
            cases.append(case)
    store = BrainStore(root, read_only=True)
    for case in cases:
        local = []
        if local_candidates:
            try:
                found = store.search(case['query'], project_id, strategy='keyword', record_trace=False,
                                     jev=False, limit=local_candidates, depth='deep')
                local = [row['id'] for row in found['results']][:local_candidates]
            except (ValueError, OSError, sqlite3.Error):
                local = []
        titles = _titles(store, list(dict.fromkeys(case['supplied_ids'] + local)))
        case['candidates'] = [dict(titles[identifier], id=identifier,
                                   supplied=identifier in case['supplied_ids'], local_rank=(
                                       local.index(identifier) + 1 if identifier in local else None))
                              for identifier in dict.fromkeys(case['supplied_ids'] + local) if identifier in titles]
    return {'schema_version': SCHEMA_VERSION, 'project_id': project_id, 'generated_at': _now(),
            'instructions': INSTRUCTIONS, 'scanned_tasks': scanned, 'scanned_startup_packets': packets, 'cases': cases}


def _timestamp(value):
    try:
        return datetime.fromisoformat(str(value).replace('Z', '+00:00')).timestamp()
    except ValueError:
        return 0.0


def parse_config(pairs):
    """Turn KEY=VALUE pairs into a validated retrieval configuration."""
    config = dict(DEFAULT_CONFIG)
    for pair in pairs or []:
        key, separator, value = pair.partition('=')
        if not separator or key not in CONFIG_CHOICES or value not in CONFIG_CHOICES[key]:
            raise ValueError('Use KEY=VALUE with ' + '; '.join(
                key + ' in ' + '|'.join(choices) for key, choices in CONFIG_CHOICES.items()))
        config[key] = value
    return config


def load_cases(path):
    path = Path(path)
    if path.stat().st_size > MAX_CASES_BYTES:
        raise ValueError('Case file exceeds 4 MiB')
    document = json.loads(path.read_text(encoding='utf-8-sig'))
    if (not isinstance(document, dict) or document.get('schema_version') != SCHEMA_VERSION
            or not isinstance(document.get('cases'), list) or len(document['cases']) > MAX_CASES):
        raise ValueError('Case file needs schema_version 1 and at most 500 cases')
    cases, seen = [], set()
    for raw in document['cases']:
        if not isinstance(raw, dict):
            raise ValueError('Each case must be an object')
        expected = raw.get('expected_ids') or []
        if not isinstance(expected, list) or any(not isinstance(item, str) or not _ID.fullmatch(item)
                                                for item in expected):
            raise ValueError('expected_ids must be a list of 32-character memory IDs')
        if not expected:
            continue
        identifier = text(str(raw.get('id') or ''), 'Case id', 100)
        if identifier in seen:
            raise ValueError('Case ids must be unique: ' + identifier)
        seen.add(identifier)
        cases.append({'id': identifier, 'project_id': scope(raw.get('project_id', document.get('project_id'))),
                      'query': text(raw.get('query'), 'Query', 500),
                      'profile': raw.get('profile') or 'general',
                      'depth': raw.get('depth') if raw.get('depth') in DEPTHS else None,
                      'expected_ids': list(dict.fromkeys(expected))})
    return cases


def _percentile(values, fraction):
    if not values:
        return None
    ordered = sorted(values)
    return ordered[min(len(ordered) - 1, max(0, math.ceil(fraction * len(ordered)) - 1))]


def score_case(case, found, elapsed_ms):
    ids = [row['id'] for row in found['results']]
    expected = case['expected_ids']
    rank = next((index + 1 for index, identifier in enumerate(ids) if identifier in expected), None)
    jev = (found.get('retrieval') or {}).get('jev') or {}
    return {'case_id': case['id'], 'rank': rank, 'hit_at_1': rank == 1, 'hit_at_3': rank is not None and rank <= 3,
            'found': rank is not None, 'recall': sum(1 for item in expected if item in ids) / len(expected),
            'returned_ids': ids, 'lookup_ms': found.get('lookup_ms'), 'elapsed_ms': round(elapsed_ms, 3),
            'context_chars': found.get('context_chars', len(found.get('context', ''))),
            'delivered': len(ids),
            'jev': {key: jev.get(key) for key in ('status', 'applied', 'shape', 'requested_shape',
                                                  'choice_confidence', 'provider_calls', 'cost_usd')} if jev else None}


def summarize(rows):
    count = len(rows)
    if not count:
        return {'cases': 0}
    latencies = [row['lookup_ms'] for row in rows if isinstance(row['lookup_ms'], (int, float))]
    contexts = [row['context_chars'] for row in rows]
    jev_rows = [row['jev'] for row in rows if row['jev']]
    calls = [item.get('provider_calls') for item in jev_rows]
    costs = [item.get('cost_usd') for item in jev_rows]
    return {'cases': count,
            'hit_at_1': round(sum(row['hit_at_1'] for row in rows) / count, 4),
            'hit_at_3': round(sum(row['hit_at_3'] for row in rows) / count, 4),
            'found_rate': round(sum(row['found'] for row in rows) / count, 4),
            'mrr': round(sum(1 / row['rank'] for row in rows if row['rank']) / count, 4),
            'recall': round(sum(row['recall'] for row in rows) / count, 4),
            'lookup_ms_p50': _percentile(latencies, .5), 'lookup_ms_p95': _percentile(latencies, .95),
            'context_chars_mean': round(statistics.fmean(contexts), 1),
            'context_chars_median': statistics.median(contexts),
            'delivered_mean': round(statistics.fmean(row['delivered'] for row in rows), 2),
            'jev_applied': sum(1 for item in jev_rows if item.get('applied')),
            'jev_choice_used': sum(1 for item in jev_rows if item.get('shape') == 'choice'),
            'jev_provider_calls': sum(calls) if calls and all(type(c) is int for c in calls) else (0 if not calls else None),
            'jev_cost_usd': (round(sum(costs), 6) if costs and all(isinstance(c, (int, float)) for c in costs)
                             else (0 if not costs else None))}


def compare(baseline_rows, candidate_rows, baseline_summary, candidate_summary):
    delta = {key: round(candidate_summary[key] - baseline_summary[key], 4)
             for key in ('hit_at_1', 'hit_at_3', 'found_rate', 'mrr', 'recall', 'context_chars_mean', 'delivered_mean')
             if isinstance(baseline_summary.get(key), (int, float)) and isinstance(candidate_summary.get(key), (int, float))}
    for key in ('lookup_ms_p50', 'lookup_ms_p95'):
        if isinstance(baseline_summary.get(key), (int, float)) and isinstance(candidate_summary.get(key), (int, float)):
            delta[key] = round(candidate_summary[key] - baseline_summary[key], 3)
    paired = {row['case_id']: row for row in baseline_rows}
    gained, lost = [], []
    for row in candidate_rows:
        before = paired.get(row['case_id'])
        if before and before['hit_at_1'] != row['hit_at_1']:
            (gained if row['hit_at_1'] else lost).append(
                {'case_id': row['case_id'], 'baseline_rank': before['rank'], 'candidate_rank': row['rank']})
    return {'delta': delta, 'hit_at_1_gained': gained, 'hit_at_1_lost': lost}


def run(root, cases, baseline, candidate=None, *, allow_jev=False, store=None, clock=time.perf_counter):
    """Score labelled cases; returns a report with per-case rows and summaries."""
    configs = [('baseline', baseline)] + ([('candidate', candidate)] if candidate else [])
    if any(config['jev'] == 'on' for _, config in configs) and not allow_jev:
        raise ValueError('A configuration enables Jev ranking, which makes billed OpenRouter requests; '
                         'repeat with --allow-jev to permit them.')
    if not cases:
        raise ValueError('No labelled cases: fill expected_ids in the case file first.')
    store = store or BrainStore(root, read_only=True)
    report = {'schema_version': SCHEMA_VERSION, 'generated_at': _now(), 'case_count': len(cases),
              'configs': dict(configs), 'runs': {},
              'notes': ['Searches were read-only and recorded no recall traces.',
                        'Jev results can come from its decision cache; repeat on another day to check stability.']}
    for name, config in configs:
        rows = []
        for case in cases:
            started = clock()
            found = store.search(case['query'], case['project_id'], strategy=config['strategy'],
                                 depth=(case['depth'] or 'compact') if config['depth'] == 'case' else config['depth'],
                                 profile=case['profile'],
                                 record_trace=False, jev=config['jev'] == 'on', jev_shape=config['shape'])
            rows.append(score_case(case, found, (clock() - started) * 1000))
        report['runs'][name] = {'summary': summarize(rows), 'cases': rows}
    if candidate:
        report['comparison'] = compare(report['runs']['baseline']['cases'], report['runs']['candidate']['cases'],
                                       report['runs']['baseline']['summary'], report['runs']['candidate']['summary'])
    return report


def markdown(report):
    names = list(report['runs'])
    keys = ('hit_at_1', 'hit_at_3', 'found_rate', 'mrr', 'recall', 'lookup_ms_p50', 'lookup_ms_p95',
            'context_chars_mean', 'delivered_mean', 'jev_applied', 'jev_choice_used', 'jev_provider_calls', 'jev_cost_usd')
    header = ['metric'] + names + (['delta'] if 'comparison' in report else [])
    lines = ['Brain recall test: ' + str(report['case_count']) + ' labelled cases', '',
             '| ' + ' | '.join(header) + ' |', '|' + '---|' * len(header)]
    for key in keys:
        cells = [key] + [str(report['runs'][name]['summary'].get(key)) for name in names]
        if 'comparison' in report:
            cells.append(str(report['comparison']['delta'].get(key, '')))
        lines.append('| ' + ' | '.join(cells) + ' |')
    if 'comparison' in report:
        comparison = report['comparison']
        lines += ['', 'Top answer gained in ' + str(len(comparison['hit_at_1_gained'])) + ' cases, lost in '
                  + str(len(comparison['hit_at_1_lost'])) + '.']
        for label, items in (('gained', comparison['hit_at_1_gained']), ('lost', comparison['hit_at_1_lost'])):
            for item in items[:20]:
                lines.append('- ' + label + ': ' + item['case_id'] + ' (rank ' + str(item['baseline_rank'])
                             + ' -> ' + str(item['candidate_rank']) + ')')
    return '\n'.join(lines)


def _write(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, ensure_ascii=False) + '\n', encoding='utf-8')
    return str(path)


def main(argv=None):
    from paths import ROOT
    parser = argparse.ArgumentParser(prog='brain eval', description=__doc__.splitlines()[0])
    parser.add_argument('--root', type=Path, default=ROOT)
    commands = parser.add_subparsers(dest='command', required=True)
    draft = commands.add_parser('extract', help='Draft unlabelled cases from canonical task queries')
    draft.add_argument('--project', required=True)
    draft.add_argument('--limit', type=int, default=100)
    draft.add_argument('--output', type=Path)
    draft.add_argument('--workspace', type=Path, action='append', default=[],
                       help='Extra project root whose .orchestration startup packets hold recall queries')
    score = commands.add_parser('run', help='Score labelled cases under one or two configurations')
    score.add_argument('--cases', type=Path, required=True)
    score.add_argument('--baseline', nargs='*', default=[], metavar='KEY=VALUE',
                       help='strategy=auto|keyword|graph|semantic depth=case|compact|balanced|deep jev=on|off shape=rubric|choice')
    score.add_argument('--candidate', nargs='*', default=None, metavar='KEY=VALUE',
                       help='Same keys; unset keys fall back to the defaults, not the baseline')
    score.add_argument('--allow-jev', action='store_true', help='Permit billed Jev (OpenRouter) requests')
    score.add_argument('--output', type=Path)
    args = parser.parse_args(argv)
    try:
        stamp = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')
        if args.command == 'extract':
            document = extract(args.root, args.project, limit=args.limit, workspaces=args.workspace)
            path = _write(args.output or args.root / 'output' / ('brain-recall-cases-' + document['project_id'] + '.json'),
                          document)
            print(json.dumps({'status': 'drafted', 'path': path, 'cases': len(document['cases']),
                              'scanned_tasks': document['scanned_tasks'],
                              'scanned_startup_packets': document['scanned_startup_packets'], 'next': INSTRUCTIONS}))
            return 0
        baseline = parse_config(args.baseline)
        candidate = parse_config(args.candidate) if args.candidate is not None else None
        report = run(args.root, load_cases(args.cases), baseline, candidate, allow_jev=args.allow_jev)
        path = _write(args.output or args.root / 'output' / ('brain-recall-eval-' + stamp + '.json'), report)
        print(markdown(report))
        print('\nReport: ' + path)
        return 0
    except (ValueError, OSError, sqlite3.Error) as exc:
        print(json.dumps({'status': 'error', 'error': str(exc)}))
        return 2


if __name__ == '__main__':
    sys.exit(main())
