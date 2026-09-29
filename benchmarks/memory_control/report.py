"""Report observed discovery, delivery, quality, latency and usage separately."""
import argparse
from collections import Counter
import html
import importlib.util
import json
from pathlib import Path

HERE=Path(__file__).resolve().parent
module_spec=importlib.util.spec_from_file_location('prior_pilot_accounting',HERE.parent/'solo_vs_team/report.py')
accounting=importlib.util.module_from_spec(module_spec)
module_spec.loader.exec_module(accounting)


def read(path):return json.loads(path.read_text(encoding='utf-8'))


def total(rows,key):
    values=[row[key] for row in rows]
    return sum(values) if values and all(value is not None for value in values) else None


def build(run):
    calls={p.parent.name:read(p) for p in (run/'calls').glob('*/record.json')}
    correction=read(run/'review/corrected-grades.json') if (run/'review/corrected-grades.json').exists() else None
    trials=[]
    for identifier in ('solo-cold','team-cold','solo-warm','team-warm'):
        path=run/'trials'/identifier/'summary.json'
        if not path.exists():continue
        raw=read(path)
        first=correction['trials'][identifier]['initial'] if correction else raw['initial_grade']
        last=correction['trials'][identifier]['final'] if correction else raw['final_grade']
        records=[calls[name] for name in raw['calls']]
        usage=[dict(provider=record['provider'],**accounting.usage(record)) for record in records]
        operations=[op for actor in raw['actors'] for op in actor['operations']]
        counts=Counter(op['action'].get('op','invalid') for op in operations)
        claimed=[];discovery_calls={}
        for record in records:
            try:
                value=record['response'].strip()
                if value.startswith('```'):value='\n'.join(value.splitlines()[1:-1])
                parsed=json.loads(value)
                if parsed.get('actions'):
                    discovery_calls[record['call_id']]=record['elapsed_seconds']
                for note in parsed.get('memory_used',[]):
                    claimed.append({'call_id':record['call_id'],'id':note.get('id'),'use':note.get('use'),
                                    'id_was_delivered':note.get('id') in record.get('memory_ids',[])})
            except (ValueError,TypeError,AttributeError):
                pass
        provider_usage={}
        for provider in sorted({record['provider'] for record in records}):
            selected=[row for row in usage if row['provider']==provider]
            provider_usage[provider]={'calls':len(selected),'input_tokens':total(selected,'input_tokens'),
                                      'output_tokens':total(selected,'output_tokens'),'cached_input_tokens':total(selected,'cached_input_tokens')}
        trials.append(dict(id=identifier,team=raw['team'],memory=raw['memory'],
                           initial_passed=first['passed'],final_passed=last['passed'],
                           raw_initial_passed=raw['initial_grade']['passed'],raw_final_passed=raw['final_grade']['passed'],
                           grade_correction_applied=bool(correction),total=last['total'],all_passed=last['all_passed'],
                           complete_submission=raw['complete_submission'],wall_seconds=raw['wall_seconds'],
                           call_path_seconds=raw['call_path_seconds'],wall_time_comparable=raw['wall_time_comparable'],
                           cumulative_provider_seconds=sum(record['elapsed_seconds'] for record in records),
                           model_calls=len(records),searches=counts['search'],listings=counts['list'],reads=counts['read'],
                           discovery_model_calls=len(discovery_calls),
                           actor_discovery=[{'actor':actor['name'],'provider':actor['provider'],
                                             'calls':sum(call in discovery_calls for call in actor['calls']),
                                             'provider_seconds':sum(discovery_calls.get(call,0) for call in actor['calls'])}
                                            for actor in raw['actors']],
                           file_operation_errors=sum(not op['ok'] for op in operations),
                           memory_ids=sorted({identifier for record in records for identifier in record.get('memory_ids',[])}),
                           calls_with_memory=sum(bool(record.get('memory_ids')) for record in records),
                           memory_use_claims=claimed,protocol_errors=[error for actor in raw['actors'] for error in actor['errors']],
                           input_tokens=total(usage,'input_tokens'),output_tokens=total(usage,'output_tokens'),provider_usage=provider_usage,
                           actor_file_actions=[{'actor':a['name'],'provider':a['provider'],'operations':a['operations']} for a in raw['actors']]))
    comparisons=[]
    for arm in ('solo','team'):
        cold=next((row for row in trials if row['id']==arm+'-cold'),None)
        warm=next((row for row in trials if row['id']==arm+'-warm'),None)
        if cold and warm:
            comparisons.append({'arm':arm,'search_difference_cold_minus_warm':cold['searches']-warm['searches'],
                                'listing_difference_cold_minus_warm':cold['listings']-warm['listings'],
                                'discovery_call_difference_cold_minus_warm':cold['discovery_model_calls']-warm['discovery_model_calls'],
                                'model_call_difference_cold_minus_warm':cold['model_calls']-warm['model_calls'],
                                'warm_to_cold_call_path_ratio':warm['call_path_seconds']/cold['call_path_seconds'],
                                'warm_to_cold_wall_ratio':warm['wall_seconds']/cold['wall_seconds'] if cold['wall_time_comparable'] and warm['wall_time_comparable'] else None,
                                'initial_pass_difference_warm_minus_cold':warm['initial_passed']-cold['initial_passed'],
                                'final_pass_difference_warm_minus_cold':warm['final_passed']-cold['final_passed']})
    job_ids={row['job_id'] for row in calls.values() if row.get('job_id')}
    source_bytes=accounting.disk_bytes(HERE)
    run_bytes=accounting.disk_bytes(run)
    hosted_bytes=sum(accounting.disk_bytes(run.parents[1]/'runs/tasks'/job) for job in job_ids)
    return {'status':'complete' if len(trials)==4 else 'partial','trials':trials,'comparisons':comparisons,
            'actual_model_calls':sum(call.get('status')=='succeeded' for call in calls.values()),
            'calls_in_completed_trials':sum(row['model_calls'] for row in trials),
            'call_status_counts':dict(Counter(call.get('status','unknown') for call in calls.values())),
            'grade_correction':{key:correction[key] for key in ('reason','performed_at','provider_calls')} if correction else None,
            'model_identity':{provider:{'observed':sorted({model for call in calls.values() if call.get('provider')==provider for model in call.get('actual_models',[])}),
                                      'requested':sorted({call['requested_model'] for call in calls.values() if call.get('provider')==provider and call.get('requested_model')})}
                              for provider in sorted({call['provider'] for call in calls.values()})},
            'storage':{'source_bytes':source_bytes,'run_bytes':run_bytes,'hosted_bytes':hosted_bytes,'total_bytes':source_bytes+run_bytes+hosted_bytes},
            'limits':['One repetition per condition on one constructed project; 280 checks are correlated cases, not independent tasks.',
                      'Cold-first order was requested. Provider load and prefix caching can also change later-run latency; ratios are observations, not isolated causal memory-speed estimates.',
                      'All workers use the same controller-mediated simulated file interface and fresh model invocations carrying their prior conversation. This is not a native PowerShell or persistent coding-agent benchmark.',
                      'The same relevant prior facts exist in public fixture history and approved Brain notes. Memory aids navigation and one subproblem; no full solution or hidden grade is injected.',
                      'The memory seed set was frozen before any cold inference and inserted after both cold trials. Contestant answer review/capture was deferred until all scored trials finished.',
                      'Retrieved and supplied memory is verified from context receipts. Claimed use is self-report; behavioral differences and grades are shown separately.',
                      'OpenAI input includes cached input; Anthropic cache components are additive; xAI observed totals reconcile cache separately. Reasoning output is already included. Different provider tokenizers and subscription accounting prevent dollar-saving claims.',
                      'Setup fixture-author/controller model usage is outside scored calls and unavailable here. Actual OpenAI model identity is not exposed by this CLI; requested identity is recorded.',
                      'Call counts mean scored worker/CLI invocations, not independently verified underlying model requests. Anthropic observed identities include Opus and an auxiliary Haiku model.',
                      'Contribution maps preserve canonical raw token fields; this report additionally normalizes cache components. Storage precedes final closeout metadata.']}


def display(value):return 'unknown' if value is None else f'{value:,.0f}'


def render(run,data):
    lines=['# Brain memory: four-condition pilot','',
           'Real OpenAI, Claude and Grok implementation with simulated file tools and real Brain retrieval. Both cold runs preceded memory insertion.','',
           '| Condition | Initial | Final | Wall seconds | Call-path seconds | Searches | Listings | Reads | Worker calls |',
           '|---|---:|---:|---:|---:|---:|---:|---:|---:|']
    rows=''
    for row in data['trials']:
        label=row['id'];score=f"{row['final_passed']}/{row['total']}"
        lines.append(f"| [{label}](trials/{label}/summary.json) | {row['initial_passed']}/{row['total']} | {score} | {row['wall_seconds']:.1f} | {row['call_path_seconds']:.1f} | {row['searches']} | {row['listings']} | {row['reads']} | {row['model_calls']} |")
        rows+=f"<tr data-arm='{'team' if row['team'] else 'solo'}'><td><a href='trials/{label}/summary.json'>{label}</a></td><td>{row['initial_passed']}/{row['total']}</td><td>{score}</td><td>{row['wall_seconds']:.1f}s</td><td>{row['searches']} / {row['listings']}</td><td>{row['reads']}</td><td>{row['model_calls']}</td><td>{len(row['memory_ids'])}</td></tr>"
    correction_note=''
    if data['grade_correction']:
        correction_note=('Correctness was rechecked after all model calls with the documented pure builtin divmod available. '
                         'The frozen grader omitted it, falsely scoring both solo initial answers 254/280; corrected initial answers pass 280/280. '
                         'Original grades, feedback, timing and revisions are preserved. No corrected feedback was sent to models. '
                         'The incorrect public feedback affected the solo revision process; corrected scores cannot retrospectively correct measured timing.')
        lines+=['',correction_note]
    if any(not row['wall_time_comparable'] for row in data['trials']):
        lines+=['','Interrupted/resumed wall times are not comparable; use recorded call-path time and inspect flags in results.json.']
    lines+=['','Wall time includes model calls, controller checkpoints, file operations, public tests and final grading. Call-path sums the slowest call in each concurrent wave. Cumulative provider time is retained separately in JSON.','',
            '## Provider usage','','| Condition | Provider | Calls | Logical input | Cached input | Output |','|---|---|---:|---:|---:|---:|']
    for row in data['trials']:
        for provider,measure in row['provider_usage'].items():
            lines.append(f"| {row['id']} | {provider} | {measure['calls']} | {display(measure['input_tokens'])} | {display(measure['cached_input_tokens'])} | {display(measure['output_tokens'])} |")
    lines+=['','## Discovery calls','','These are model calls requesting file operations; durations include the model request and its memory lookup, not just filesystem I/O. Concurrent worker durations must not be added as elapsed time.','','| Condition | Worker | Discovery calls | Provider seconds |','|---|---|---:|---:|']
    for row in data['trials']:
        for actor in row['actor_discovery']:
            lines.append(f"| {row['id']} | {actor['actor']} | {actor['calls']} | {actor['provider_seconds']:.1f} |")
    lines+=['','## Verified delivery and reported use','']
    for row in data['trials']:
        lines.append(f"- {row['id']}: {len(row['memory_ids'])} unique delivered memories; {row['calls_with_memory']}/{row['model_calls']} calls received memory; {len(row['memory_use_claims'])} per-call use claims. Full claims and actual file actions are in results.json.")
    lines+=['','## Limits','']+['- '+limit for limit in data['limits']]
    lines+=['',f"Measured source, run and hosted records: {data['storage']['total_bytes']/1024**2:.2f} MiB."]
    (run/'results.md').write_text('\n'.join(lines)+'\n',encoding='utf-8')
    limits=''.join('<li>'+html.escape(limit)+'</li>' for limit in data['limits'])
    page='''<!doctype html><html lang="en"><meta charset="utf-8"><meta name="viewport" content="width=device-width"><title>Brain memory pilot</title>
<style>body{font:16px system-ui;line-height:1.6;max-width:1180px;margin:35px auto;padding:0 20px;background:#121b28;color:#e9eef7}a{color:#87b9ff}table{width:100%;border-collapse:collapse;font-size:14px}th,td{text-align:left;padding:12px;border-bottom:1px solid #34465e}section{margin:25px 0;padding:20px;background:#1b293b;border-radius:12px}select{font:inherit;padding:6px}h1{font-size:30px}.muted{color:#bbc9dc}li{margin:7px 0}.table{overflow:auto}</style>
<h1>Does project memory help?</h1><p>One compact synthetic project. Solo OpenAI and an OpenAI + Claude + Grok team. Both cold runs first, then four prewritten memories inserted into the real Brain.</p>
<section><h2>Measured runs</h2><label>Show <select id="filter"><option value="all">All runs</option><option value="solo">Solo</option><option value="team">Team</option></select></label>
<div class="table"><table><thead><tr><th>Run / evidence</th><th>Initial</th><th>Final</th><th>Wall time</th><th>Search / list</th><th>Reads</th><th>Calls</th><th>Memories</th></tr></thead><tbody>'''+rows+'''</tbody></table></div></section>
<p>'''+html.escape(correction_note)+'''</p><p><a href="results.md">Detailed measurements</a> · <a href="results.json">Full data and file actions</a> · <a href="review/seed-bundle.json">Exact injected memories</a> · <a href="review/experiment-lock.json">Frozen experiment controls</a></p>
<section><h2>What this can establish</h2><p>Search actions and delivered memory IDs are observed. Reported memory use is the model's own account. Scores are independently graded. Cold-first ordering, caching and provider load limit causal timing claims.</p><ul>'''+limits+'''</ul></section>
<script>document.getElementById('filter').addEventListener('change',e=>document.querySelectorAll('tbody tr').forEach(r=>r.hidden=e.target.value!=='all'&&r.dataset.arm!==e.target.value));</script></html>'''
    (run/'results.html').write_text(page,encoding='utf-8')


def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--run',type=Path,required=True)
    args=parser.parse_args();run=args.run.resolve();data=build(run)
    (run/'results.json').write_text(json.dumps(data,indent=2)+'\n',encoding='utf-8')
    render(run,data)
    print(json.dumps({'status':data['status'],'trials':[{key:row[key] for key in ('id','initial_passed','final_passed','total','wall_seconds','call_path_seconds','searches','listings','reads','model_calls','input_tokens','output_tokens')} for row in data['trials']],
                      'comparisons':data['comparisons'],'storage':data['storage']}))


if __name__=='__main__':main()
