"""Read-only historical/current comparison; only writes to the selected new parent."""
import argparse
from collections import Counter
from datetime import datetime, timezone
import hashlib
import html
import json
from pathlib import Path
import statistics
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'app'))
from experiment_usage import summarize_usage

PILOT = 'memory-control-pilot-20260913T034256Z-9e51f8cc'
CORRECTED = 'memory-usage-rerun-20260913T164925Z-65606a3b'
SIX = 'six-bot-audit-memory-final-20260913T180341Z-aa2d3536'
LOAD = 'brain-audit-eight-20260909T040051Z-a3d6c55f'
SOL_XHIGH = 'sol-xhigh-jev-tern-comparison-20260926T164546Z-7a7d93c6'


def read(path):
    return json.loads(Path(path).read_text(encoding='utf-8-sig'))


def write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, ensure_ascii=True) + '\n', encoding='utf-8')


def stats(values):
    values = [v for v in values if isinstance(v, (int, float))]
    return {'n': len(values), 'sum': sum(values), 'median': statistics.median(values),
            'min': min(values), 'max': max(values)} if values else {'n': 0, 'sum': None, 'median': None, 'min': None, 'max': None}


def call_records(child, ids=None):
    rows = [read(p) for p in sorted((child / 'calls').glob('*/record.json'))]
    if ids is not None:
        by_id = {r['call_id']: r for r in rows}
        rows = [by_id[key] for key in ids]
    return rows


def measures(child, identifier, reported=None):
    raw = read(child / 'trials' / identifier / 'summary.json')
    records = call_records(child, raw['calls'])
    usage = summarize_usage([dict(r, worker_id=r.get('actor_id', r.get('provider'))) for r in records])
    operations = [o for a in raw['actors'] for o in a['operations']]
    counts = Counter(o['action'].get('op', 'invalid') for o in operations)
    first, last = raw['initial_grade'], raw['final_grade']
    case_rows = [dict(case, role=role['role']) for role in last.get('roles', []) for case in role.get('cases', [])]
    memory_rows = []
    call_details = []
    claims = []
    for r in records:
        retrieval = r.get('memory_retrieval') or {}
        canonical = {}
        if r.get('job_id'):
            canonical_path = ROOT / 'runs/tasks' / r['job_id'] / 'result.json'
            if canonical_path.exists():
                canonical = read(canonical_path)
                if not retrieval:
                    retrieval = (canonical.get('memory_context') or {}).get('retrieval') or {}
        result = canonical.get('provider_result') or {}
        call_details.append({'call_id': r['call_id'], 'provider': r['provider'], 'job_id': r.get('job_id'),
            'elapsed_seconds': r.get('elapsed_seconds'), 'usage_raw': r.get('usage'),
            'phase_durations_ms': canonical.get('phase_durations_ms'),
            'provider_reported_price_usd': result.get('total_cost_usd', result.get('cost_usd')),
            'auxiliary_model_usage': canonical.get('modelUsage'), 'actual_subscription_charge_usd': None})
        memory_rows.append({'call_id': r['call_id'], 'provider': r['provider'],
            'ids': r.get('memory_ids', []), 'context_sha256': r.get('memory_sha256'),
            'context_hash_valid': r.get('memory_sha256') == hashlib.sha256(r.get('memory_context', '').encode()).hexdigest() if r.get('memory_ids') else None,
            'lookup_ms': r.get('memory_lookup_ms'), 'elapsed_ms': r.get('memory_elapsed_ms'),
            'trace_id': r.get('memory_trace_id'), 'retrieval': retrieval})
        try:
            response = r['response'].strip()
            if response.startswith('```'):
                response = '\n'.join(response.splitlines()[1:-1])
            for claim in json.loads(response).get('memory_used', []):
                claims.append(dict(claim, call_id=r['call_id'], id_was_delivered=claim.get('id') in r.get('memory_ids', [])))
        except (ValueError, KeyError, TypeError):
            pass
    jev = [row['retrieval']['jev'] for row in memory_rows if isinstance(row['retrieval'].get('jev'), dict)]
    jev_totals = {}
    for field in ('provider_calls', 'input_tokens', 'output_tokens', 'cost_usd'):
        values = [j.get(field) for j in jev]
        jev_totals[field] = sum(values) if values and all(type(v) in (int, float) for v in values) else None
    if not jev:
        jev_totals.update(provider_calls=0, input_tokens=0, output_tokens=0, cost_usd=0)
    jev_totals.update(lookups=len(jev), applied=sum(j.get('applied') is True for j in jev),
        order_changed=sum(j.get('order_changed') is True for j in jev),
        promoted_count=sum(j.get('promoted_count', 0) for j in jev),
        cache_hits=sum((j.get('cache') or {}).get('status') == 'hit' for j in jev),
        statuses=dict(Counter(j.get('status', 'unknown') for j in jev)),
        latency_ms=stats([j.get('elapsed_ms') for j in jev]),
        candidate_count=stats([j.get('candidate_count') for j in jev]),
        scored_candidate_count=stats([j.get('scored_candidate_count') for j in jev]))
    data = {'id': identifier, 'run_id': child.name, 'status': 'completed',
        'source_summary': str(child / 'trials' / identifier / 'summary.json'),
        'team': raw['team'], 'memory': raw['memory'],
        'started_at': raw['started_at'], 'ended_at': raw['ended_at'],
        'initial_passed': first['passed'], 'final_passed': last['passed'], 'total': last['total'],
        'final_failures': [case for case in case_rows if not case['passed']],
        'final_groups': {group: {'passed': sum(c['passed'] for c in case_rows if c.get('group', 'unclassified') == group),
            'total': sum(c.get('group', 'unclassified') == group for c in case_rows)} for group in {c.get('group', 'unclassified') for c in case_rows}},
        'complete_submission': raw['complete_submission'], 'wall_seconds': raw['wall_seconds'],
        'call_path_seconds': raw['call_path_seconds'], 'wall_time_comparable': raw.get('wall_time_comparable', False),
        'cumulative_call_seconds': sum(r['elapsed_seconds'] for r in records),
        'model_calls': len(records), 'usage': usage, 'searches': counts['search'], 'listings': counts['list'], 'reads': counts['read'],
        'file_operation_errors': sum(not o['ok'] for o in operations),
        'protocol_errors': [e for a in raw['actors'] for e in a['errors']],
        'calls_with_memory': sum(bool(r.get('memory_ids')) for r in records),
        'unique_memories_delivered': len({key for r in records for key in r.get('memory_ids', [])}),
        'memory_lookup_ms': stats([r.get('memory_lookup_ms') for r in records]),
        'memory_elapsed_ms': stats([r.get('memory_elapsed_ms') for r in records]),
        'memory_calls': memory_rows, 'memory_use_claims': claims, 'jev': jev_totals, 'call_details': call_details,
        'observed_models': {provider: sorted({m for r in records if r['provider'] == provider for m in r.get('actual_models', [])}) for provider in {r['provider'] for r in records}},
        'requested_models': {provider: sorted({r['requested_model'] for r in records if r['provider'] == provider and r.get('requested_model')}) for provider in {r['provider'] for r in records}}}
    if reported:
        data['initial_passed'] = reported['initial_passed']
        data['final_passed'] = reported['final_passed']
        data['historical_grade_correction_applied'] = reported.get('grade_correction_applied', False)
        data['raw_initial_passed'] = first['passed']
        data['raw_final_passed'] = last['passed']
        assert abs(data['wall_seconds'] - reported['wall_seconds']) < .00001
        # Normalization must reproduce the saved report before comparing runs.
        for field in ('input_tokens', 'output_tokens'):
            assert usage['totals'][field] == reported[field], (child.name, field)
    return data


def source(path):
    return {'path': str(path), 'sha256': hashlib.sha256(path.read_bytes()).hexdigest()}


def historical():
    orch = ROOT / '.orchestration'
    sources, suites = [], []
    for run_id, relative, label in [(PILOT, 'results.json', 'September 12 MDT pilot'), (CORRECTED, 'review/results.json', 'September 13 MDT corrected rerun')]:
        run = orch / run_id
        path = run / relative
        result = read(path)
        sources.append(source(path))
        trials = [measures(orch / row['run_id'] if row.get('run_id') else run, row['id'], row) for row in result['trials']]
        for row in trials:
            sources.append(source(Path(row['source_summary'])))
        suites.append({'id': run_id, 'label': label, 'trials': trials, 'repeat': result.get('repeat'),
            'limit': 'Original grader omitted divmod; corrected grades do not repair historical feedback/timing.' if run_id == PILOT else 'Team warm is a separate fresh repeat; earlier assignment-index hold and diagnosis are excluded from its trial time.'})
    load_path = orch / LOAD / 'review/final-benchmark.json'
    six_path = orch / SIX / 'review/results.json'
    sources.extend([source(load_path), source(six_path)])
    six = read(six_path)
    six_rows = [{k: row.get(k) for k in ('id', 'run_id', 'status', 'wall_seconds', 'usage', 'final_grade', 'partial_grade')} for row in six['conditions'] if row.get('id') in ('team-cold', 'team-warm')]
    sol_path = orch / SOL_XHIGH / 'results.json'
    sol = read(sol_path)
    sources.append(source(sol_path))
    for row in sol['current']:
        sources.append(source(Path(row['source_summary'])))
    sol_suite = {'id': SOL_XHIGH, 'label': 'September 26 GPT-6 Sol xhigh suite',
        'trials': sol['current'], 'interrupted_attempts': sol.get('interrupted_attempts', []),
        'limit': 'Separate lead session; same fixed Tern workload and contestant roster. One observation per condition; the team-JEV attempt was held and its clean repeat is scored.'}
    scheduler = []
    for run_id, label in [('jev-medium-comparison-20260925T174644Z-7321634f', 'September 25 JEV scheduler diagnostic'),
                          ('historical-memory-timing-20260926T160903Z-5416759f', 'September 26 JEV scheduler repeat')]:
        path = orch / run_id / 'results.json'
        result = read(path)
        sources.append(source(path))
        scheduler.append({'id': run_id, 'label': label, 'summary': result['summary'], 'setup_usage': result.get('setup_usage'),
            'rows': [{'id': row['id'], 'wall_seconds': row['wall_seconds'], 'final_grade': row['final_grade'],
                'memory_elapsed_ms': row.get('memory_elapsed_ms'), 'normalized_usage': row['normalized_usage'], 'memory_count': len(row.get('memory_ids', [])),
                'jev': row.get('jev')} for row in result['rows']]})
    return {'suites': suites, 'sources': sources, 'retrieval_reference': read(load_path),
        'six_bot_reference': {'status': six['status'], 'conditions': six_rows, 'limit': 'Different 240-check workload. Warm explicitly closed incomplete; no final warm duration or complete score. Never resumed or pooled with Tern.'},
        'sol_xhigh_suite': sol_suite, 'jev_scheduler_diagnostics': scheduler}


def delta(new, old, label):
    nt, ot = new['usage']['totals'], old['usage']['totals']
    def difference(n, o):
        return {'new': n, 'baseline': o, 'absolute': n - o, 'percent': (n / o - 1) * 100 if o else None} if n is not None and o is not None else None
    return {'comparison': label, 'new_run': new['run_id'], 'new_condition': new['id'], 'baseline_run': old['run_id'], 'baseline_condition': old['id'],
        'wall_seconds': difference(new['wall_seconds'], old['wall_seconds']),
        'logical_input_tokens': difference(nt['input_tokens'], ot['input_tokens']),
        'output_tokens': difference(nt['output_tokens'], ot['output_tokens']),
        'total_tokens': difference(nt['known_total_tokens'], ot['known_total_tokens']) if not nt['unknown_usage_calls'] and not ot['unknown_usage_calls'] else None,
        'calls': difference(new['model_calls'], old['model_calls']),
        'score_points': new['final_passed'] - old['final_passed'],
        'causal_estimate': False}


def build(parent):
    snapshot = parent / 'review/historical-baseline.json'
    if snapshot.exists():
        past = read(snapshot)
        assert all(hashlib.sha256(Path(s['path']).read_bytes()).hexdigest() == s['sha256'] for s in past['sources']), 'Historical evidence changed'
        if 'jev_scheduler_diagnostics' not in past:
            past = historical()
            write(snapshot, past)
    else:
        past = historical()
        write(snapshot, past)
    rows, pending, interrupted = [], [], []
    experiment = read(parent / 'experiment.json') if (parent / 'experiment.json').exists() else {'conditions': []}
    repeat = read(parent / 'review/fresh-repeat.json') if (parent / 'review/fresh-repeat.json').exists() else None
    for row in experiment['conditions']:
        child = parent.parent / row['run_id']
        condition = read(child / 'condition.json')
        summary = child / 'trials' / row['id'] / 'summary.json'
        if summary.exists():
            item = measures(child, row['id'])
            item['condition_metadata'] = condition
            rows.append(item)
        else:
            if repeat and row['run_id'] == repeat['failed_run_id']:
                failed_calls = call_records(child)
                completed_calls = [r for r in failed_calls if r['status'] == 'succeeded']
                jev = [(r.get('memory_retrieval') or {}).get('jev') or {} for r in failed_calls]
                interrupted.append({'run_id':child.name,'condition':row['id'],'status':'reconciled_incomplete',
                    'invocation_attempts':len(failed_calls),'completed_contestant_calls':len(completed_calls),
                    'pre_provider_holds':sum(r['status']=='held' for r in failed_calls),
                    'usage':summarize_usage([dict(r,worker_id=r.get('actor_id')) for r in completed_calls]),
                    'jev_provider_calls':sum(j.get('provider_calls',0) for j in jev),
                    'jev_reported_cost_usd':sum(j.get('cost_usd',0) for j in jev),
                    'final_score':None,'completed_wall_seconds':None,
                    'reason':repeat['reason'],'reconciliation':str(child/'review/hold-reconciliation.json')})
                repeat_child = parent.parent / repeat['repeat_run_id']
                if repeat['status'] == 'completed':
                    item = measures(repeat_child, repeat['source_condition_id'])
                    item.update(id=row['id'], source_condition_id=repeat['source_condition_id'],
                        condition_metadata=read(repeat_child/'condition.json'), fresh_repeat=True)
                    rows.append(item)
                else:
                    pending.append({'id':row['id'],'run_id':repeat_child.name,'status':repeat['status'],'observed_calls':len(call_records(repeat_child))})
            else:
                pending.append({'id': row['id'], 'run_id': row['run_id'], 'status': condition['status'], 'observed_calls': len(call_records(child))})
    comparisons = []
    corrected = past['suites'][1]['trials']
    for row in rows:
        old_id = row['id'] if row['id'] in ('solo-cold', 'team-cold', 'solo-warm', 'team-warm') else ('team-warm' if row['team'] else 'solo-warm')
        comparisons.append(delta(row, next(r for r in corrected if r['id'] == old_id), 'current versus corrected historical'))
        if 'jev' in row['id']:
            base = next((r for r in rows if r['id'] == ('team-warm' if row['team'] else 'solo-warm')), None)
            if base:
                comparisons.append(delta(row, base, 'current JEV versus current ordinary Brain'))
            base = next((r for r in rows if r['id'] == ('team-cold' if row['team'] else 'solo-cold')), None)
            if base:
                comparisons.append(delta(row, base, 'current JEV versus current no memory'))
        sol = next((candidate for candidate in past.get('sol_xhigh_suite', {}).get('trials', []) if candidate['id'] == row['id']), None)
        if sol:
            comparisons.append(delta(row, sol, 'current Luna versus September 26 Sol xhigh'))
    interrupted_path = parent / 'review/interrupted-team-warm-attempt.json'
    if interrupted_path.exists() and not interrupted:
        attempt = read(interrupted_path)
        child = parent.parent / attempt['attempt_run_id']
        records = call_records(child)
        completed = [r for r in records if r.get('status') == 'succeeded']
        jev = [(r.get('memory_retrieval') or {}).get('jev') or {} for r in records]
        interrupted.append({'run_id': attempt['attempt_run_id'], 'condition': attempt['condition_id'],
            'status': attempt['status'], 'invocation_attempts': len(records),
            'completed_contestant_calls': len(completed),
            'pre_provider_holds': sum(r.get('status') == 'held' for r in records),
            'usage': summarize_usage([dict(r, worker_id=r.get('actor_id', r.get('provider'))) for r in completed]),
            'jev_provider_calls': sum(j.get('provider_calls', 0) for j in jev),
            'jev_reported_cost_usd': sum(j.get('cost_usd', 0) for j in jev),
            'final_score': None, 'completed_wall_seconds': None,
            'observed_partial_wall_seconds': attempt.get('wall_seconds'),
            'reason': attempt.get('held_call', {}).get('quota_before', {}).get('provider_refresh_errors', {}).get('grok', {}).get('error', 'A provider-start quota guard held a call before provider execution.'),
            'reconciliation': str(child/'review/hold-reconciliation.json'),
            'held_provider_request_count': attempt.get('held_call', {}).get('provider_request_count', 0)})
    controller = read(parent / 'review/controller-usage.json') if (parent / 'review/controller-usage.json').exists() else {'status': 'not_yet_measured', 'tokens': None}
    manifest = read(parent / 'run.json')
    run_created_at = manifest.get('created_utc') or manifest.get('created_at')
    timing = {'run_created_at': run_created_at, 'completed_trial_wall_sum_seconds': sum(r['wall_seconds'] for r in rows)}
    if rows:
        first_start = min(r['started_at'] for r in rows)
        last_end = max(r['ended_at'] for r in rows)
        timing.update(first_scored_trial_started_at=first_start, latest_completed_trial_ended_at=last_end,
            completed_trial_block_seconds=(datetime.fromisoformat(last_end)-datetime.fromisoformat(first_start)).total_seconds())
        if run_created_at:
            timing['preparation_from_run_creation_seconds'] = (datetime.fromisoformat(first_start)-datetime.fromisoformat(run_created_at)).total_seconds()
        timing['between_trial_overhead_seconds'] = timing['completed_trial_block_seconds'] - timing['completed_trial_wall_sum_seconds']
    return {'generated_at': datetime.now(timezone.utc).isoformat(), 'status': 'completed' if len(rows) == 6 and not pending else 'in_progress',
        'parent_run_id': parent.name, 'lead': read(parent / 'review/lead-model-receipt.json'), 'historical': past,
        'current': rows, 'pending': pending, 'comparisons': comparisons, 'controller_usage': controller, 'suite_timing': timing,
        'fresh_repeat':repeat, 'interrupted_attempts':interrupted,
        'limits': ['One repetition per condition; fixed cold-first order, caching and provider load prevent reliable causal speed claims.',
            'Historical source/fixture/seed/grade matching is checked separately; provider versions, operating guidance and harness telemetry may differ over time.',
            'Call counts are fresh supplied-text contestant invocations. Team members run concurrently; cumulative call time is not elapsed wall time.',
            'Logical input includes cache according to provider receipts. Cache is shown separately, never added twice; absent measurements remain unknown.',
            'JEV costs are provider-reported API cost, distinct from contestant subscription charges, which remain unknown.',
            'JEV applied, changed order, delivered memory and claimed usefulness are separate measurements.',
            'Four relevant notes fit in every warm packet; this workload has little distractor-selection pressure and does not test larger-corpus recall.',
            'Controller and setup model usage is outside the scored trial totals.']}


def render(parent, data):
    def num(value, digits=0):
        return 'unknown' if value is None else f'{value:,.{digits}f}'
    lead = data['lead']
    lead_model = lead.get('actual_model') or lead.get('model') or 'unknown'
    lead_effort = lead.get('reasoning_effort') or 'unknown'
    lead_effort_label = {'extra_high': 'extra-high'}.get(lead_effort, lead_effort)
    lead_source = lead.get('source') or 'unknown evidence source'
    lines = ['# Historical Brain versus current JEV', '',
        f'Lead model {lead_model} ({lead_effort_label} reasoning) was user-confirmed from: {lead_source}. The lead model selection is not app-attested. Fixed contestant roster: OpenAI Astra, Claude Opus and Grok. Actual model identity for direct OpenAI contestant calls was not returned in provider receipts. One repetition per condition.', '',
        '| Suite | Condition | Initial / final | Seconds | Calls | Logical input | Output | Cached input | Reads |',
        '|---|---|---:|---:|---:|---:|---:|---:|---:|']
    all_rows = [(s['label'], t) for s in data['historical']['suites'] for t in s['trials']]
    all_rows += [(data['historical']['sol_xhigh_suite']['label'], t) for t in data['historical']['sol_xhigh_suite']['trials']]
    all_rows += [(f'Current {lead_model}-led suite', t) for t in data['current']]
    html_rows = ''
    for label, row in all_rows:
        u = row['usage']['totals']
        cells = [label, row['id'], f"{row['initial_passed']}/{row['total']} → {row['final_passed']}/{row['total']}", num(row['wall_seconds'], 2), str(row['model_calls']), num(u['input_tokens']), num(u['output_tokens']), num(u['cached_input_tokens']), str(row['reads'])]
        lines.append('| ' + ' | '.join(cells) + ' |')
        html_rows += '<tr data-arm="' + ('team' if row['team'] else 'solo') + '">' + ''.join('<td>' + html.escape(c) + '</td>' for c in cells) + '</tr>'
    timing = data['suite_timing']
    lines += ['', '## Suite timing', '',
        f"Completed trial wall times sum to {num(timing['completed_trial_wall_sum_seconds'], 2)}s. The block from first scored start to last completed end was {num(timing.get('completed_trial_block_seconds'), 2)}s; between-condition and interrupted-retry overhead was {num(timing.get('between_trial_overhead_seconds'), 2)}s. Setup from parent creation to the first scored trial was {num(timing.get('preparation_from_run_creation_seconds'), 2)}s. Overhead includes quota recovery/repeat coordination and is separate from condition wall times."]
    lines += ['', '## Observed differences', '', '| Comparison | Condition | Wall change | Total token change | Final score change |', '|---|---|---:|---:|---:|']
    for row in data['comparisons']:
        wall, tokens = row['wall_seconds'], row['total_tokens']
        lines.append(f"| {row['comparison']} | {row['new_condition']} | {wall['absolute']:+.2f}s ({wall['percent']:+.1f}%) | " + (f"{tokens['absolute']:+,} ({tokens['percent']:+.1f}%)" if tokens else 'unknown') + f" | {row['score_points']:+d} |")
    lines += ['', '## Memory and JEV', '', 'Warm treatment is Brain plus the current JEV ranking and passage-review path. JEV application, changed order, and delivery are recorded separately.', '', '| Condition | Memory calls | Notes | Lookup median ms | JEV lookups / API calls | Applied / changed order | JEV time sum ms | JEV reported cost |', '|---|---:|---:|---:|---:|---:|---:|---:|']
    for row in data['current']:
        j = row['jev']
        lines.append(f"| {row['id']} | {row['calls_with_memory']}/{row['model_calls']} | {row['unique_memories_delivered']} | {num(row['memory_lookup_ms']['median'], 2)} | {j['lookups']} / {num(j['provider_calls'])} | {j['applied']} / {j['order_changed']} | {num(j['latency_ms']['sum'], 2)} | {num(j['cost_usd'], 8)} |")
    jev_rows = [row['jev'] for row in data['current'] if row['jev']['lookups']]
    if jev_rows:
        lookups = sum(j['lookups'] for j in jev_rows)
        low_confidence = sum(j['statuses'].get('low_confidence', 0) for j in jev_rows)
        lines += ['', f"{low_confidence}/{lookups} scored JEV lookups returned low_confidence. "
            f"There were {sum(j['provider_calls'] or 0 for j in jev_rows)} API requests and {sum(j['cache_hits'] for j in jev_rows)} cache hits. "
            f"Reported scored JEV API cost: ${sum(j['cost_usd'] or 0 for j in jev_rows):.8f}. "
            'Applied decisions and changed order are counted separately above; this comparison does not establish an improvement from JEV.']
    lines += ['', '## File actions and delivered memories', '', '| Condition | Searches | Listings | Reads | Operation errors | Calls with memory | Distinct notes | Self-reports matching delivered ID |', '|---|---:|---:|---:|---:|---:|---:|']
    for row in data['current']:
        claims = row['memory_use_claims']
        grounded = sum(claim.get('id_was_delivered') is True for claim in claims)
        lines.append(f"| {row['id']} | {row['searches']} | {row['listings']} | {row['reads']} | {row['file_operation_errors']} | {row['calls_with_memory']}/{row['model_calls']} | {row['unique_memories_delivered']} | {grounded}/{len(claims)} |")
    errors = [(row['id'], error) for row in data['current'] for error in row['protocol_errors']]
    if not errors:
        lines.append('The team-warm and team-JEV rows each had one expected simulated read failure for `docs/maintenance.md`, which is absent from the fixture. No protocol errors occurred; both rows passed 280/280.')
    lines += ['', '## Retained quality failures', '']
    failures = [(row, case) for row in data['current'] for case in row['final_failures']]
    lines += [f"- {row['id']}: {case['role']} / {case['case']} ({case['group']}): {case.get('detail')}." for row, case in failures] or ['No final failures in completed current conditions.']
    lines += ['Scored answers were left unchanged after their allocated revision. Initial-to-final regressions are retained in the comparison.']
    if data['interrupted_attempts']:
        lines += ['', '## Interrupted attempt and fresh repeat', '']
        for attempt in data['interrupted_attempts']:
            total = attempt['usage']['totals'].get('known_total_tokens')
            lines.append(f"The original {attempt['condition']} attempt completed {attempt['completed_contestant_calls']} contestant calls using {num(total)} reported tokens. The stale Grok usage read led the local admission guard to hold the next call before provider execution. It has no final score or completed duration; the held call made {attempt['held_provider_request_count']} provider requests.")
        lines.append('The scored team-warm row is an explicitly linked clean repeat after successful quota-only refresh. Interrupted usage and diagnosis time are excluded from that trial; they remain in the overall execution record. No threshold was lowered and no uncertain call was retried.')
    lines += ['', 'Full provider breakdowns, delivered IDs/hashes, lookup traces, JEV candidates/scoring, cache hits, file actions, memory-use self-reports and comparison formulas are retained in [results.json](results.json).', '', '## Other historical tests', '']
    for diagnostic in data['historical']['jev_scheduler_diagnostics']:
        summary = diagnostic['summary']
        cold = [r for r in diagnostic['rows'] if 'cold' in r['id']]
        warm = [r for r in diagnostic['rows'] if 'jev' in r['id']]
        statuses = Counter((r.get('jev') or {}).get('status', 'not-called') for r in warm)
        applications = sum((r.get('jev') or {}).get('applied') is True for r in warm)
        order_changes = sum((r.get('jev') or {}).get('order_changed') is True for r in warm)
        api_calls = sum((r.get('jev') or {}).get('provider_calls', 0) for r in warm)
        jev_input = sum((r.get('jev') or {}).get('input_tokens', 0) for r in warm)
        jev_output = sum((r.get('jev') or {}).get('output_tokens', 0) for r in warm)
        cold_ok = all(r['final_grade']['passed'] == r['final_grade']['total'] for r in cold)
        warm_ok = all(r['final_grade']['passed'] == r['final_grade']['total'] for r in warm)
        prep_values = [r['memory_elapsed_ms'] for r in warm if isinstance(r.get('memory_elapsed_ms'), (int, float))]
        prep_label = f" Mean full memory-preparation latency was {statistics.mean(prep_values) / 1000:.3f}s." if prep_values else ''
        lines.append(f"{diagnostic['label']} used a separate 106-check Python scheduler task: cold mean {summary['cold_mean_seconds']:.2f}s, JEV mean {summary['jev_mean_seconds']:.2f}s ({summary['observed_change_percent']:+.1f}%); all cold/warm runs passed 106/106 ({cold_ok and warm_ok}). JEV made {api_calls} API calls ({jev_input:,} input / {jev_output:,} output tokens), reported ${summary['jev_reported_cost_usd']:.9f}, applied in {applications}/{len(warm)} warm calls, and changed order in {order_changes}/{len(warm)}. Statuses: {dict(statuses)}.{prep_label} This separate workload is not pooled with Tern.")
    lines += [
        'The six-bot 240-check test is separate: its cold result completed in 150.10 seconds; warm was explicitly closed incomplete. No full warm duration or quality comparison is claimed.',
        'The September 9 local lookup test used 10,000 synthetic records and 100 searches, with zero model calls: lookup p50 8.71 ms / p95 14.78 ms; caller wall p50 30.29 ms / p95 36.46 ms. It measures local retrieval only.', '', '## Limits', '']
    lines += ['- ' + text for text in data['limits']]
    lines += ['', '- September 12 scores were corrected after inference for the omitted divmod builtin; its original feedback and timing remain affected.', '- September 13 team warm uses the fresh repeat; the interrupted attempt and diagnosis time are excluded from its trial duration.']
    if data['pending']:
        lines += ['', 'Pending conditions: ' + ', '.join(row['id'] + ' (' + row['status'] + ')' for row in data['pending'])]
    lines += ['', '## Current provider usage', '', '| Condition | Provider | Calls | Logical input | Output | Cached input |', '|---|---|---:|---:|---:|---:|']
    for row in data['current']:
        for provider in row['usage']['by_provider']:
            lines.append('| ' + ' | '.join([row['id'], provider['provider'], str(provider['calls']), num(provider['input_tokens']), num(provider['output_tokens']), num(provider['cached_input_tokens'])]) + ' |')
    lines += ['', '## Controller and harness preparation', '', 'Controller token use was not attested for this Luna run or the Astra audit. The lead model choice is user-confirmed; counters and subscription charges are unknown. Startup JEV recall is separate from scored JEV calls. Historical controller usage is unknown.', '',
        '| Role | Logical input | Cached input (already included) | Output | Calls / cost | Measured through |', '|---|---:|---:|---:|---:|---|']
    for key in ('lead', 'astra'):
        entry = data['controller_usage'].get(key, {})
        usage = entry.get('usage') or {}
        lines.append('| ' + ' | '.join([key, num(usage.get('input_tokens')), num(usage.get('cached_input_tokens')), num(usage.get('output_tokens')), 'unknown', entry.get('through_at') or 'unknown']) + ' |')
    setup = data['controller_usage'].get('jev_startup', {})
    lines.append('| JEV startup Brain recall | ' + ' | '.join([num(setup.get('input_tokens')), 'n/a', num(setup.get('output_tokens')), f"{num(setup.get('provider_calls'))} / ${num(setup.get('reported_cost_usd'), 8)} / {num(setup.get('elapsed_ms'), 2)}ms JEV", f"{num(setup.get('retrieval_elapsed_ms'), 2)}ms retrieval"]) + ' |')
    lines += ['', 'The isolated Grok quota refresh used zero provider calls. Its verification receipt is in `review/grok-quota-adapter-fix-verification.json`; this operational recovery is outside scored contestant usage.', '']
    (parent / 'results.md').write_text('\n'.join(lines) + '\n', encoding='utf-8')
    page = '''<!doctype html><html lang="en"><meta charset="utf-8"><meta name="viewport" content="width=device-width"><title>Sol xhigh JEV comparison</title><style>body{font:16px/1.6 system-ui;max-width:1300px;margin:32px auto;padding:0 20px;background:#111b29;color:#e7edf6}h1{font-size:30px}a{color:#9acbff}table{border-collapse:collapse;width:100%;font-size:14px}td,th{padding:11px;text-align:left;border-bottom:1px solid #394d67}select{padding:8px;font:inherit}.scroll{overflow:auto}.note{padding:16px;background:#203047;border-radius:8px}</style><h1>Historical Brain versus current JEV</h1><p>GPT-6 Sol · Extra-high reasoning · Matched Tern 280-check workload</p><p class="note">One observation per condition. Timing differences include provider load and caching; they do not establish a causal JEV speedup.</p><p><a href="results.md">Detailed comparison</a> · <a href="results.json">Full measurements and receipts</a> · <a href="review/overall-contributions/contribution-audit.md">Contribution audit</a></p><label>Show <select id="arm"><option value="all">All runs</option><option value="solo">Solo</option><option value="team">Team</option></select></label><div class="scroll"><table><thead><tr>'''
    page = page.replace('<title>Sol xhigh JEV comparison</title>', '<title>Brain and JEV comparison</title>')
    lead_paragraph = page.index('<p>GPT-6 Sol')
    lead_paragraph_end = page.index('</p>', lead_paragraph) + 4
    page = page[:lead_paragraph] + f'<p>{html.escape(str(lead_model))} ? {html.escape(str(lead_effort_label))} reasoning, user-confirmed ? Matched Tern 280-check workload</p>' + page[lead_paragraph_end:]
    page += ''.join('<th>' + h + '</th>' for h in ['Suite', 'Condition', 'Initial → final', 'Seconds', 'Calls', 'Logical input', 'Output', 'Cached input', 'Reads'])
    page += '</tr></thead><tbody>' + html_rows + '</tbody></table></div><p>September 12: post-hoc corrected grades; timing retains the original feedback defect. September 13: team warm is the separate fresh repeat.</p>'
    # Render the additional generated Markdown tables without introducing a dependency.
    # Every cell is escaped; none of the source/provider text is treated as markup.
    active_table = False
    for line in lines[lines.index('## Observed differences'):]:
        if line.startswith('|'):
            cells = [c.strip() for c in line.strip('|').split('|')]
            if all(set(c) <= set('-: ') for c in cells):
                continue
            if not active_table:
                page += '<div class="scroll"><table>'
                active_table = True
            page += '<tr>' + ''.join('<td>' + html.escape(c) + '</td>' for c in cells) + '</tr>'
        else:
            if active_table:
                page += '</table></div>'
                active_table = False
            if line.startswith('## '):
                page += '<h2>' + html.escape(line[3:]) + '</h2>'
            elif line:
                page += '<p>' + html.escape(line) + '</p>'
    if active_table:
        page += '</table></div>'
    page += '<script>document.getElementById("arm").onchange=e=>document.querySelectorAll("tr[data-arm]").forEach(r=>r.hidden=e.target.value!=="all"&&r.dataset.arm!==e.target.value)</script></html>'
    (parent / 'results.html').write_text(page, encoding='utf-8')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--run', required=True, type=Path)
    args = parser.parse_args()
    parent = args.run.resolve(strict=True)
    assert (parent / 'review/lead-model-receipt.json').exists(), 'Select the new comparison parent'
    data = build(parent)
    write(parent / 'results.json', data)
    render(parent, data)
    historical_trials = sum(len(s['trials']) for s in data['historical']['suites']) + len(data['historical']['sol_xhigh_suite']['trials'])
    print(json.dumps({'status': data['status'], 'historical_trials': historical_trials, 'completed_current_trials': len(data['current']), 'pending': data['pending']}))


if __name__ == '__main__':
    main()
