"""Read-only team sizing advice and bounded, content-free timing summaries.

This module never refreshes quota, reserves work, or starts a provider. Counts
are starting points for an explicit work breakdown, not measured optima.
"""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from copy import deepcopy
from datetime import datetime
import json
import math
from pathlib import Path
import statistics

from execution_limits import timeout_for_task
from paths import ROOT
from usage_guard import Guard, validate_policy


TASK_TYPES = {
    'simple': (0, 0, 'The lead handles a small command or edit directly.'),
    'debug': (1, 1, 'One diagnostic worker, then an independent check of the fix.'),
    'implementation': (2, 1, 'Separate file ownership for makers, then independent review.'),
    'research': (4, 1, 'Separate questions or sources, then a synthesis and evidence review.'),
    'audit': (6, 2, 'Separate audit areas, then two independent checks of the findings.'),
    'batch': (8, 1, 'Independent batches, then a review of outputs and failures.'),
}
PROVIDERS = ('codex', 'claude', 'grok', 'gemini', 'vscode-copilot')
STATUSES = frozenset(('pending', 'running', 'succeeded', 'held', 'uncertain', 'failed'))
METRICS = ('preparation_seconds', 'execution_seconds', 'postprocessing_seconds',
           'total_seconds', 'first_event_seconds', 'first_answer_seconds', 'queue_wait_seconds')
MAX_RECORD_BYTES = 512 * 1024
MAX_HISTORY_BYTES = 32 * 1024 * 1024
MAX_SCAN_ENTRIES = 5000
DEFAULT_HISTORY_LIMIT = 300


def _integer(value, name, minimum, maximum):
    if isinstance(value, bool) or not isinstance(value, int) or not minimum <= value <= maximum:
        raise ValueError(f'{name} must be an integer from {minimum} to {maximum}')
    return value


def _number(value):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    try:
        return float(value) if math.isfinite(value) and value >= 0 else None
    except OverflowError:
        return None


def _timestamp(value):
    if not isinstance(value, str):
        return None
    try:
        result = datetime.fromisoformat(value.replace('Z', '+00:00'))
        return result if result.tzinfo is not None else None
    except ValueError:
        return None


def _interval(start, end):
    start, end = _timestamp(start), _timestamp(end)
    if start is None or end is None:
        return None
    value = (end - start).total_seconds()
    return value if value >= 0 else None


def _json(path, limit):
    with Path(path).open('rb') as handle:
        raw = handle.read(limit + 1)
    if len(raw) > limit:
        raise ValueError('Saved metadata exceeds the reader limit')
    value = json.loads(raw.decode('utf-8-sig'))
    if not isinstance(value, dict):
        raise ValueError('Saved metadata must be an object')
    return value, len(raw)


def sanitize_record(record):
    """Retain only fixed labels and numeric timing; never emit task content."""
    worker = record.get('worker')
    worker = worker if worker in PROVIDERS else 'other'
    status = record.get('execution_status')
    status = status if status in STATUSES else 'unknown'
    imported = bool(record.get('imported_completed_artifact'))
    reconciled = bool(record.get('reconciled_at'))
    started = _timestamp(record.get('started_at')) is not None
    measured = status == 'succeeded' and started and not imported
    metrics = dict.fromkeys(METRICS)
    if measured:
        for key, first, last in (
            ('preparation_seconds', 'created_at', 'started_at'),
            ('execution_seconds', 'started_at', 'ended_at'),
            ('postprocessing_seconds', 'ended_at', 'finalized_at'),
            ('total_seconds', 'created_at', 'finalized_at'),
        ):
            # Manual reconciliation may replace finalization long after inference.
            if not reconciled or key not in ('postprocessing_seconds', 'total_seconds'):
                metrics[key] = _interval(record.get(first), record.get(last))
        progress = record.get('execution_progress')
        if isinstance(progress, dict):
            metrics['first_event_seconds'] = _number(progress.get('first_event_s'))
            metrics['first_answer_seconds'] = _number(progress.get('first_answer_s'))
        # No queue is inferred from creation -> start: that also includes quota
        # collection, memory retrieval and other preparation. Unknown stays null.
    return {'provider': worker, 'execution_status': status, 'imported': imported,
            'reconciled': reconciled, 'timed_success': measured, 'metrics': metrics}


def _summary(values):
    ordered = sorted(value for value in values if value is not None)
    if not ordered:
        return {'samples': 0, 'median': None, 'p90': None, 'maximum': None}
    return {'samples': len(ordered), 'median': round(statistics.median(ordered), 3),
            'p90': round(ordered[max(0, math.ceil(len(ordered) * .9) - 1)], 3),
            'maximum': round(ordered[-1], 3)}


def timing_history(tasks_root, limit=DEFAULT_HISTORY_LIMIT):
    """Read bounded canonical indexes, never response files or private logs."""
    _integer(limit, 'history limit', 0, 1000)
    groups = defaultdict(list)
    report = {'records_read': 0, 'records_skipped': 0, 'bytes_read': 0,
              'scan_truncated': False, 'history_truncated': False, 'by_provider': {}}
    if not limit:
        return report
    candidates = []
    try:
        for index, directory in enumerate(Path(tasks_root).iterdir()):
            if index >= MAX_SCAN_ENTRIES:
                report['scan_truncated'] = True
                break
            try:
                if directory.is_symlink() or not directory.is_dir():
                    continue
                path = directory / 'record.json'
                if path.is_symlink():
                    report['records_skipped'] += 1
                    continue
                stat = path.stat()
                candidates.append((stat.st_mtime_ns, path))
            except OSError:
                report['records_skipped'] += 1
    except FileNotFoundError:
        return report
    except OSError:
        report['records_skipped'] += 1
        return report
    candidates.sort(key=lambda item: (item[0], item[1].name), reverse=True)
    report['history_truncated'] = len(candidates) > limit
    for _, path in candidates[:limit]:
        room = min(MAX_RECORD_BYTES + 1, MAX_HISTORY_BYTES - report['bytes_read'])
        if room <= 0:
            report['history_truncated'] = True
            break
        try:
            with path.open('rb') as handle:
                raw = handle.read(room)
            report['bytes_read'] += len(raw)
            if len(raw) > MAX_RECORD_BYTES:
                raise ValueError('Task index exceeds reader limit')
            record = json.loads(raw.decode('utf-8-sig'))
            if not isinstance(record, dict):
                raise ValueError('Task index must be an object')
            safe = sanitize_record(record)
            groups[safe['provider']].append(safe)
            report['records_read'] += 1
        except (OSError, ValueError, TypeError, RecursionError):
            report['records_skipped'] += 1
    for provider, rows in sorted(groups.items()):
        report['by_provider'][provider] = {
            'records': len(rows),
            'execution_status_counts': dict(Counter(row['execution_status'] for row in rows)),
            'timed_successes': sum(row['timed_success'] for row in rows),
            'imported_records': sum(row['imported'] for row in rows),
            'reconciled_records': sum(row['reconciled'] for row in rows),
            'latency': {metric: _summary(row['metrics'][metric] for row in rows) for metric in METRICS},
        }
    report['interpretation'] = (
        'Durations describe successfully started tasks only; status counts include all indexed tasks. '
        'Preparation includes quota and other setup, not a measured queue. Imported artifacts have '
        'unknown execution duration. Reconciled finalization is excluded from total/postprocessing. '
        'Providers received different tasks: these samples do not rank model speed or prove an optimal team size.')
    return report


def quota_capacity(policy, data, providers=PROVIDERS, size='small', cap=4):
    """Simulate new allowance reservations on a copy; respect shared pools."""
    _integer(cap, 'cap', 1, 10)
    timeout_for_task(size)
    if not providers or any(worker not in PROVIDERS for worker in providers):
        raise ValueError('Select a supported hosted provider')
    providers = tuple(dict.fromkeys(providers))
    unknown = {'status': 'unknown', 'capacity': 0, 'allocations': [],
               'workers': {worker: {'status': 'unknown', 'eligible': False} for worker in providers}}
    try:
        validate_policy(policy)
        copied = deepcopy(data)
        if not isinstance(copied, dict) or not isinstance(copied.get('reservations'), dict):
            return unknown
        # evaluate is pure. Avoid Guard.__init__, state(), check(), or refresh(),
        # which create/write state even when a caller only wants advice.
        guard = object.__new__(Guard)
        guard.policy = policy

        def evaluate(worker):
            result = guard.evaluate(copied, worker, size)
            eligible = result['allowed'] and result['status'] == 'ready'
            reasons = result.get('reasons', [])
            status = result['status']
            if any('unknown' in reason or 'No applicable quota' in reason for reason in reasons):
                status = 'unknown'
            elif any('stale' in reason or 'reset boundary' in reason for reason in reasons):
                status = 'stale'
            elif any('refresh failed' in reason for reason in reasons):
                status = 'refresh_failed'
            return result, {'status': status, 'eligible': eligible,
                            'available_pct': min((w['available_pct'] for w in result['windows']), default=None),
                            'estimate_pct': result['estimate_pct']}

        workers = {worker: evaluate(worker)[1] for worker in providers}
        allocations = []
        while len(allocations) < cap:
            changed = False
            for worker in providers:
                result, info = evaluate(worker)
                if not info['eligible']:
                    continue
                pools = copied.get('worker_pools', {}).get(worker, policy['worker_pools'][worker])
                # This entry exists only inside a discarded copy. No reservation
                # token from the application is created or finished.
                copied['reservations']['team-advice-' + str(len(allocations))] = {
                    'worker': worker, 'estimate_pct': result['estimate_pct'],
                    'pools': pools, 'finished_at': None,
                }
                allocations.append(worker)
                changed = True
                if len(allocations) >= cap:
                    break
            if not changed:
                break
        uncertain = any(row['status'] in ('unknown', 'stale', 'refresh_failed') for row in workers.values())
        return {'status': 'ready' if allocations else 'unknown' if uncertain else 'held', 'capacity': len(allocations),
                'allocations': allocations, 'workers': workers}
    except (ValueError, KeyError, TypeError, AttributeError, OverflowError, RecursionError):
        return unknown


def recommend(task_type, independent_workstreams=1, cap=4, quota=None):
    if task_type not in TASK_TYPES:
        raise ValueError('Unknown task type')
    _integer(independent_workstreams, 'independent workstreams', 1, 1000)
    _integer(cap, 'cap', 1, 10)
    producers, reviewers, explanation = TASK_TYPES[task_type]
    producers = min(producers, independent_workstreams)
    peak = min(cap, max(producers, reviewers))
    capacity = 0 if quota is None else _integer(quota.get('capacity', 0), 'quota capacity', 0, 10)
    admitted = min(peak, capacity)
    status = 'solo' if not peak else 'held' if not admitted else 'limited' if admitted < peak else 'ready'
    return {
        'task_type': task_type, 'advisory_only': True, 'status': status,
        'lead_count': 1, 'independent_workstreams': independent_workstreams,
        'producer_assignments': producers, 'reviewer_assignments': reviewers,
        'total_worker_assignments': producers + reviewers,
        'total_roles_including_lead': 1 + producers + reviewers,
        'concurrent_worker_cap': cap, 'planned_peak_simultaneous_workers': peak,
        'quota_limited_peak_workers': admitted,
        'quota_state': quota.get('status', 'unknown') if quota else 'unknown',
        'first_wave_workers': min(producers, admitted),
        'planned_waves': math.ceil(producers / cap) + math.ceil(reviewers / cap),
        'explanation': explanation,
        'review_order': 'Review follows completed work; reviewers are included in worker totals, not added to the concurrent cap.',
        'next_step': ('The lead can complete this without new worker dispatch.' if not peak else
                      'Check the recorded admission reasons and choose an eligible provider.' if not admitted else
                      'The lead assigns bounded scopes; each dispatch checks the configured quota policy and route authorization.'),
    }


def build_report(root=ROOT, *, task_type='implementation', independent_workstreams=1,
                 cap=4, size='small', providers=PROVIDERS, history_limit=DEFAULT_HISTORY_LIMIT):
    root = Path(root)
    timeout_for_task(size)
    # Validate before any history work, including the simple no-worker route.
    recommend(task_type, independent_workstreams, cap)
    try:
        policy, _ = _json(root / 'runtime/policy.json', 128 * 1024)
        state, _ = _json(root / 'runtime/usage.json', 8 * 1024 * 1024)
    except (OSError, ValueError, TypeError, RecursionError):
        policy = state = None
    quota = quota_capacity(policy, state, providers, size, cap)
    return {'schema_version': 1,
            'plan': recommend(task_type, independent_workstreams, cap, quota),
            'quota': quota, 'task_size': size, 'execution_deadline_seconds': timeout_for_task(size),
            'history': timing_history(root / 'runs/tasks', history_limit),
            'limits': [
                'This command reads saved metadata only: no provider calls, new reservations or model launches.',
                'The default cap of 4 is a conservative planning choice, not an enforced global scheduler limit.',
                'Total assignments can run in waves; the lead is counted separately from workers.',
                'Allowance eligibility does not confirm model billing authorization or current route availability.',
                'A larger deadline prevents premature termination; it does not make a worker faster.',
            ]}


def render(report):
    plan = report['plan']
    lines = [f"Team advice: {plan['task_type']} ({plan['status']})",
             f"Lead: 1 | Producer assignments: {plan['producer_assignments']} | Review assignments: {plan['reviewer_assignments']}",
             f"Total roles: {plan['total_roles_including_lead']} | Peak simultaneous workers: {plan['planned_peak_simultaneous_workers']} | Cached quota permits up to: {plan['quota_limited_peak_workers']}",
             f"Quota evidence: {plan['quota_state']} (unknown is not a measured zero allowance)",
             plan['explanation'], plan['review_order'], plan['next_step'], '',
             'Historical successful task medians (seconds; unknown means unmeasured):',
             'Provider     Samples   Preparation   Execution   Postprocessing']
    for provider, row in report['history']['by_provider'].items():
        values = [row['latency'][key]['median'] for key in ('preparation_seconds', 'execution_seconds', 'postprocessing_seconds')]
        values = [('unknown' if value is None else f'{value:.2f}') for value in values]
        lines.append(f"{provider:<12} {row['timed_successes']:>7} {values[0]:>13} {values[1]:>11} {values[2]:>16}")
    lines.extend(['', report['history'].get('interpretation', 'Historical sampling disabled or no saved tasks.'), *report['limits']])
    return '\n'.join(lines)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--type', dest='task_type', choices=tuple(TASK_TYPES), default='implementation')
    parser.add_argument('--independent-workstreams', type=int, default=1,
                        help='Actually independent scopes; default 1. The lead and later reviewers are separate.')
    parser.add_argument('--cap', type=int, default=4, help='Planning cap for simultaneous workers, 1..10; lead excluded')
    parser.add_argument('--size', choices=('tiny', 'small', 'medium', 'large'), default='small')
    parser.add_argument('--provider', choices=PROVIDERS, action='append', help='Repeat to limit hosted quota pools considered')
    parser.add_argument('--history-limit', type=int, default=DEFAULT_HISTORY_LIMIT, help='Most recently modified task indexes, 0..1000')
    parser.add_argument('--json', action='store_true')
    args = parser.parse_args(argv)
    try:
        report = build_report(task_type=args.task_type, independent_workstreams=args.independent_workstreams,
                              cap=args.cap, size=args.size, providers=args.provider or PROVIDERS,
                              history_limit=args.history_limit)
    except ValueError as exc:
        parser.error(str(exc))
    print(json.dumps(report, indent=2) if args.json else render(report))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
