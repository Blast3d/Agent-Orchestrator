"""Render measured pilot results without inferring missing usage or charges."""
import argparse
from collections import defaultdict
import html
import json
from pathlib import Path
import statistics


def read(path):
    return json.loads(path.read_text(encoding='utf-8'))


def number(data, *keys):
    for key in keys:
        value=data.get(key)
        if type(value) in (int,float) and value>=0:return value
    return None


def usage(record):
    raw=record.get('usage') or {}
    inputs=number(raw,'input_tokens','inputTokens','prompt_eval_count')
    outputs=number(raw,'output_tokens','outputTokens','eval_count')
    cached=number(raw,'cached_input_tokens','cache_read_input_tokens','cachedInputTokens')
    if record['provider']=='Anthropic':
        parts=[number(raw,key) for key in ('input_tokens','cache_creation_input_tokens','cache_read_input_tokens')]
        inputs=sum(parts) if all(value is not None for value in parts) else None
    elif record['provider']=='xAI' and number(raw,'total_tokens') is not None:
        # This CLI reports cache separately. Reconcile against its explicit total;
        # do not assume another route has the same cache convention.
        reported_total=number(raw,'total_tokens')
        parts=[number(raw,key) for key in ('input_tokens','cache_creation_input_tokens','cache_read_input_tokens')]
        additive=sum(parts) if all(value is not None for value in parts) else None
        if outputs is not None and reported_total>=outputs:
            observed_input=reported_total-outputs
            inputs=observed_input if observed_input in (inputs,additive) else None
        else:
            inputs=None
    return {'input_tokens':inputs,'output_tokens':outputs,'cached_input_tokens':cached,
            'actual_models':record.get('actual_models',[]),
            'requested_model':record.get('requested_model'),
            'additional_subscription_charge_usd':None}


def total(rows,key):
    values=[row[key] for row in rows]
    return sum(values) if values and all(value is not None for value in values) else None


def disk_bytes(directory):
    return sum(path.stat().st_size for path in directory.rglob('*') if path.is_file())


def build(run):
    attempts=[];scored_calls=set();records={}
    for path in (run/'calls').glob('*/record.json'):records[path.parent.name]=read(path)
    for path in sorted((run/'attempts').glob('*/summary.json')):
        row=read(path);ids=row['calls'];scored_calls.update(ids)
        calls=[records[identifier] for identifier in ids]
        normalized=[dict(provider=record['provider'],**usage(record)) for record in calls]
        initial,final=calls[0],calls[-1]
        audits=calls[1:-1]
        row['call_path_seconds']=initial['elapsed_seconds']+final['elapsed_seconds']+max([r['elapsed_seconds'] for r in audits] or [0])
        row['input_tokens']=total(normalized,'input_tokens')
        row['output_tokens']=total(normalized,'output_tokens')
        row['provider_usage']={}
        for provider in sorted({call['provider'] for call in normalized}):
            selected=[call for call in normalized if call['provider']==provider]
            row['provider_usage'][provider]={'calls':len(selected),'input_tokens':total(selected,'input_tokens'),'output_tokens':total(selected,'output_tokens'),
                                           'cached_input_tokens':total(selected,'cached_input_tokens')}
        row['wall_time_comparable']=row.get('wall_time_comparable',True)
        attempts.append(row)
    grouped=[]
    for task in ('small','medium'):
        for condition in ('solo','team'):
            rows=[row for row in attempts if row['task']==task and row['condition']==condition]
            if not rows:continue
            provider_totals={}
            for provider in sorted({p for row in rows for p in row['provider_usage']}):
                measures=[row['provider_usage'][provider] for row in rows if provider in row['provider_usage']]
                provider_totals[provider]={'calls':sum(m['calls'] for m in measures),'input_tokens':total(measures,'input_tokens'),'output_tokens':total(measures,'output_tokens')}
            grouped.append({'task':task,'condition':condition,'attempts':len(rows),'initial_full_passes':sum(row['initial_all_passed'] for row in rows),
                            'final_full_passes':sum(row['final_all_passed'] for row in rows),
                            'median_call_path_seconds':statistics.median(row['call_path_seconds'] for row in rows),
                            'input_tokens':total(rows,'input_tokens'),'output_tokens':total(rows,'output_tokens'),'provider_usage':provider_totals})
    setup=[]
    for identifier,record in records.items():
        if identifier not in scored_calls:
            setup.append({'id':identifier,'status':record['status'],'provider':record.get('provider'),
                          'seconds':record.get('elapsed_seconds'),'usage':usage(record)})
    jobs={record['job_id'] for record in records.values() if record.get('job_id')}
    root=run.parents[1]
    sizes={'benchmark_source_bytes':disk_bytes(Path(__file__).parent),'trial_run_bytes':disk_bytes(run),
           'linked_hosted_task_bytes':sum(disk_bytes(root/'runs/tasks'/job) for job in jobs)}
    sizes['total_bytes']=sum(sizes.values())
    identities={provider:{'observed_models':sorted({model for identifier in scored_calls
                if records[identifier]['provider']==provider for model in records[identifier].get('actual_models',[])}),
                'requested_models':sorted({records[identifier]['requested_model'] for identifier in scored_calls
                if records[identifier]['provider']==provider and records[identifier].get('requested_model')})}
                for provider in sorted({records[identifier]['provider'] for identifier in scored_calls})}
    return {'status':'complete' if len(attempts)==8 else 'partial','attempts':attempts,'groups':grouped,'setup_or_unscored_calls':setup,
            'scored_model_calls':len(scored_calls),'disk':sizes,'model_identity':identities,
            'timing_definition':'Call-path time = initial OpenAI call + parallel audit maximum + final OpenAI call; includes each call startup/admission but excludes grading, outer checkpoints, and human setup pauses. Raw uninterrupted wall time is also retained.',
            'usage_definition':'OpenAI input already includes cached input. Anthropic input adds uncached, cache creation and cache-read components. xAI input is reconciled against reported total minus output; these CLI receipts count cache separately. Reasoning output is not added again. Cross-provider token counts use different tokenizers. Missing or inconsistent components remain unknown.',
            'limitations':['Two workloads with two repetitions are a pilot, not statistically independent evidence from 153 tasks.',
                          'Same implementation model and effort across conditions; team receives extra review compute. No matched-dollar-budget claim.',
                          'Fresh supplied-code calls do not simulate all aspects of long-lived agents or repository tools.',
                          'Controller conversation, fixture construction and evaluation development are outside scored token totals.',
                          'Historical setup capture/brief failures remain recorded, not silently rerun or counted as model-quality failures.',
                          'Call counts describe scored dispatches; provider-reported auxiliary model usage does not count as a separate worker.',
                          'Independent audit text and failing candidate behavior are reviewed by the lead before orchestration closeout.',
                          'Actual subscription charges and causal allowance savings are not established by token or reservation totals.']}


def display(value):
    return 'unknown' if value is None else f'{value:,.0f}'


def render(run,data):
    lines=['# Solo versus orchestration: compact pilot','',
           'Measured code repair and audit results. The team uses OpenAI + Claude for small work, and OpenAI + Claude + Grok for medium work.','',
           '| Workload | Condition | Initial full passes | Final full passes | Median call-path seconds | Input tokens | Output tokens |',
           '|---|---|---:|---:|---:|---:|---:|']
    for row in data['groups']:
        n=row['attempts']
        lines.append(f"| {row['task']} | {row['condition']} | {row['initial_full_passes']}/{n} | {row['final_full_passes']}/{n} | {row['median_call_path_seconds']:.1f} | {display(row['input_tokens'])} | {display(row['output_tokens'])} |")
    lines += ['',data['timing_definition'],'',data['usage_definition'],'','## Individual attempts','',
              '| Attempt | Before audit | After audit | Call-path seconds | Input | Output |','|---|---:|---:|---:|---:|---:|']
    for row in data['attempts']:
        lines.append(f"| [{row['id']}](attempts/{row['id']}/final.py) | {row['initial_passed']}/{row['total']} | {row['final_passed']}/{row['total']} | {row['call_path_seconds']:.1f} | {display(row['input_tokens'])} | {display(row['output_tokens'])} |")
    lines += ['','## Provider usage by condition','','| Workload | Condition | Provider | Calls | Input | Output |','|---|---|---|---:|---:|---:|']
    for group in data['groups']:
        for provider,row in group['provider_usage'].items():
            lines.append(f"| {group['task']} | {group['condition']} | {provider} | {row['calls']} | {display(row['input_tokens'])} | {display(row['output_tokens'])} |")
    lines += ['','## Model identity','']
    for provider,row in data['model_identity'].items():
        lines.append(f"- {provider}: observed {', '.join(row['observed_models']) or 'not exposed by CLI'}; requested {', '.join(row['requested_models']) or 'see canonical dispatcher task'}. Provider-reported model accounting may include auxiliary activity.")
    lines += ['','The contribution map preserves canonical raw token fields. This benchmark report additionally normalizes cache components as described above; use this report for trial usage comparisons.']
    lines += ['','## Setup records','']
    for row in data['setup_or_unscored_calls']:
        lines.append(f"- {row['id']}: {row['status']}; reported input {display(row['usage']['input_tokens'])}, output {display(row['usage']['output_tokens'])}.")
    lines += ['','## Limits and storage','']+['- '+item for item in data['limitations']]
    lines += ['',f"Measured artifacts: {data['disk']['total_bytes']/1024**2:.2f} MiB, including linked hosted task records. Disk measurement precedes report generation and final closeout metadata."]
    (run/'results.md').write_text('\n'.join(lines)+'\n',encoding='utf-8')
    rows=''
    for row in data['attempts']:
        rows+=f"<tr data-task='{row['task']}'><td><a href='attempts/{row['id']}/final.py'>{row['id']}</a></td><td>{row['initial_passed']}/{row['total']}</td><td>{row['final_passed']}/{row['total']}</td><td>{row['call_path_seconds']:.1f}s</td><td>{display(row['input_tokens'])}</td><td>{display(row['output_tokens'])}</td></tr>"
    cards=''
    maximum=max([r['median_call_path_seconds'] for r in data['groups']] or [1])
    for row in data['groups']:
        color='#5b9cff' if row['condition']=='solo' else '#d89a5f'
        cards+=f"<article><b>{row['task'].title()} · {row['condition'].title()}</b><p>{row['final_full_passes']}/{row['attempts']} complete passes</p><div class='track'><div style='width:{100*row['median_call_path_seconds']/maximum:.1f}%;background:{color}'></div></div><p>{row['median_call_path_seconds']:.1f}s median call-path time</p><small>{display(row['input_tokens'])} input · {display(row['output_tokens'])} output tokens</small></article>"
    page='''<!doctype html><html lang="en"><meta charset="utf-8"><meta name="viewport" content="width=device-width"><title>Solo versus orchestration pilot</title>
<style>body{font:16px system-ui;background:#111a26;color:#e8edf5;max-width:1050px;margin:40px auto;padding:0 20px;line-height:1.5}h1{font-size:30px}a{color:#8bbcff}.cards{display:grid;grid-template-columns:repeat(auto-fit,minmax(220px,1fr));gap:12px}article{background:#1c293a;border:1px solid #33465b;border-radius:10px;padding:18px}.track{height:10px;background:#344153;border-radius:5px}.track div{height:10px;border-radius:5px}table{width:100%;border-collapse:collapse;font-size:14px}th,td{text-align:left;padding:10px;border-bottom:1px solid #33465b}select{font:inherit;padding:6px}small,.muted{color:#b4c3d7}code{color:#b7d0ff}section{margin-top:28px}</style>
<h1>Solo versus orchestration</h1><p>Two compact synthetic workloads. Two repetitions. Initial repair, audit, one revision, and retest. No production app changes.</p>
<div class="cards">'''+cards+'''</div><section><h2>Attempts</h2><label>Show <select id="filter"><option value="all">All workloads</option><option>small</option><option>medium</option></select></label>
<table><thead><tr><th>Attempt / final code</th><th>Initial</th><th>Final</th><th>Call-path time</th><th>Input</th><th>Output</th></tr></thead><tbody>'''+rows+'''</tbody></table></section>
<section><h2>How to read this</h2><p>Small team: OpenAI + Claude. Medium team: OpenAI + Claude + Grok. Solo has the same implementation model, effort, public feedback, and revision opportunity. Auditors add compute.</p><p>'''+html.escape(data['timing_definition'])+'''</p><p>'''+html.escape(data['usage_definition'])+'''</p><p>These checks are correlated within two workloads. A clean sweep may mean the fixture is too easy. This pilot does not establish a universal winner or additional subscription charges.</p><p><a href="results.md">Detailed report</a> · <a href="results.json">Machine-readable measurements</a></p></section>
<script>document.getElementById('filter').addEventListener('change',e=>document.querySelectorAll('tbody tr').forEach(row=>row.hidden=e.target.value!=='all'&&row.dataset.task!==e.target.value));</script></html>'''
    (run/'results.html').write_text(page,encoding='utf-8')


def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--run',type=Path,required=True)
    args=parser.parse_args();run=args.run.resolve();data=build(run)
    (run/'results.json').write_text(json.dumps(data,indent=2)+'\n',encoding='utf-8')
    render(run,data)
    print(json.dumps({'status':data['status'],'attempts':len(data['attempts']),'groups':data['groups'],'disk':data['disk']}))


if __name__=='__main__':main()
