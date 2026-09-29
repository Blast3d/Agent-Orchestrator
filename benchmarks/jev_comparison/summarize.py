"""Render the completed frozen experiment; no inference or memory writes."""
import argparse
from datetime import datetime, timezone
import html
import json
from pathlib import Path
import statistics


def read(path):
    return json.loads(Path(path).read_text(encoding='utf-8-sig'))


def render(run):
    raw=read(run/'results.json')
    if raw.get('status')!='completed' or len(raw.get('rows',[]))!=4:
        raise ValueError('Keep incomplete results separate; four completed conditions are required.')
    experiment=read(run/'experiment.json')
    rows=[]
    for result in raw['rows']:
        declaration=next(row for row in experiment['conditions'] if row['id']==result['id'])
        child=run.parent/declaration['run_id']
        call_id=result['calls'][0]
        call=read(child/'calls'/call_id/'record.json')
        canonical=read(child/'calls'/call_id/'result.json')
        provider=canonical.get('provider_result') or {}
        model_usage=canonical.get('modelUsage') or {}
        usage=canonical.get('usage') or {}
        costs=[v.get('costUSD') for v in model_usage.values()]
        estimate=(sum(costs) if costs and all(type(c) in (float,int) for c in costs)
                  else provider.get('total_cost_usd'))
        rows.append(dict(result,job_id=call['job_id'],child_run=child.name,
            base_prompt_sha256=call['base_prompt_sha256'],
            fresh_input_tokens=usage.get('input_tokens'),cache_created_tokens=usage.get('cache_creation_input_tokens'),
            cache_read_tokens=usage.get('cache_read_input_tokens'),thinking_tokens=usage.get('output_tokens_details',{}).get('thinking_tokens'),
            claude_cost_estimate_usd=estimate,claude_actual_charge_usd=None,
            model_usage=model_usage,context_chars=len(call.get('memory_context','')),
            memory_execution_requested=call.get('memory_execution_requested'),
            memory_context_sha256=call.get('memory_sha256')))
    groups={}
    for group,pattern in [('No memory','-cold-'),('Jev-assisted memory','-jev-')]:
        own=[r for r in rows if pattern in r['id']]
        groups[group]={'runs':len(own),'passed':sum(r['final_grade']['passed'] for r in own),
            'checks':sum(r['final_grade']['total'] for r in own),
            'mean_seconds':statistics.mean(r['wall_seconds'] for r in own),
            'range_seconds':[min(r['wall_seconds'] for r in own),max(r['wall_seconds'] for r in own)],
            'main_response_input_tokens':sum(r['normalized_usage']['input_tokens'] for r in own),
            'main_response_output_tokens':sum(r['normalized_usage']['output_tokens'] for r in own),
            'cache_read_tokens':sum(r['cache_read_tokens'] for r in own) if all(r['cache_read_tokens'] is not None for r in own) else None}
    result={'created_at':datetime.now(timezone.utc).isoformat(),'rows':rows,'groups':groups,
        'base_prompts_identical':len({r['base_prompt_sha256'] for r in rows})==1,
        'summary':raw['summary'],'limitations':raw['limits'],
        'notes':['Main response usage includes fresh/cache creation/cache read input. Auxiliary modelUsage is retained separately; do not add it to top-level usage blindly.',
            'Claude costs are provider list-price estimates, not verified additional subscription charges.',
            'Timing includes dispatch, retrieval, provider, cleanup and grading; setup and seed insertion excluded.',
            'Light local regression checks ran during the first cold request; provider load, caching and fixed order were not controlled.',
            'The test checks correctness and delivery. It cannot establish causal speed improvement or incremental Jev benefit.',
            'Base prompt fingerprints hash LF-normalized text; the four saved CRLF prompt files are also byte-identical.']}
    feedback_path=run/'review/memory-feedback.json'
    feedback_note=('Both accepted memory-enabled answers received evidence-bound neutral usefulness feedback after independent review. All four accepted outcomes were saved to their isolated Brain scopes.'
                   if feedback_path.is_file() else 'Usefulness feedback has not yet been recorded.')
    (run/'analysis.json').write_text(json.dumps(result,indent=2)+'\n',encoding='utf-8')
    lines=['# Medium test results: no memory versus Jev-assisted recall','',
        'Four fresh Claude Opus/medium sessions repaired the same scheduler. Each response was graded against the same 106 frozen checks. No ordinary Brain arm was run.','',
        '| Condition | Passed | Mean time | Observed range | Main input tokens | Main output tokens |',
        '| --- | ---: | ---: | ---: | ---: | ---: |']
    for name,g in groups.items():
        lines.append(f"| {name} | {g['passed']}/{g['checks']} | {g['mean_seconds']:.2f}s | {g['range_seconds'][0]:.2f}–{g['range_seconds'][1]:.2f}s | {g['main_response_input_tokens']:,} | {g['main_response_output_tokens']:,} |")
    lines += ['',f"Observed time change: {raw['summary']['observed_change_percent']:+.1f}%. Two runs per arm and a fixed cold-first order do not establish a reliable speed effect.",'',
        '| Individual run | Passed | Total seconds | Provider seconds | Recall ms | Jev decision | Supplied / claimed memories |',
        '| --- | ---: | ---: | ---: | ---: | --- | ---: |']
    table=[]
    for r in rows:
        j=r.get('jev'); decision=('applied' if j and j.get('applied') else j.get('status') if j else 'not called')
        values=[r['id'],f"{r['final_grade']['passed']}/{r['final_grade']['total']}",f"{r['wall_seconds']:.2f}",
            f"{r['provider_seconds']:.2f}" if r['provider_seconds'] is not None else 'Unknown',
            f"{r['memory_elapsed_ms']:.2f}" if r['memory_elapsed_ms'] is not None else 'Not called',decision,
            f"{len(r['memory_ids'])} / {len(r['memory_used_claims'])}"]
        table.append(values);lines.append('| '+' | '.join(values)+' |')
    lines += ['',f"Jev reported charge across its two test requests: ${raw['summary']['jev_reported_cost_usd']:.8f}. Claude's additional subscription charge is unknown.",'',
        'Six reviewed question-and-note memories per warm scope were inserted after both cold runs. All facts came from the public task contract; no completed solutions or hidden tests were seeded. Exact supplied IDs and context hashes remain in each call record.','',
        'Claims of memory use are self-reports. Equal correctness does not show a quality improvement. '+feedback_note,'',
        *['- '+s for s in result['limitations']+result['notes']]]
    (run/'results.md').write_text('\n'.join(lines)+'\n',encoding='utf-8')
    cards=''.join('<article><h2>'+html.escape(name)+'</h2><strong>'+f"{g['mean_seconds']:.1f}s"+'</strong><p>'+f"{g['passed']}/{g['checks']} checks across {g['runs']} runs"+'</p></article>' for name,g in groups.items())
    headers=['Run','Checks','Total seconds','Provider seconds','Recall ms','Jev','Supplied / claimed']
    tr='<tr>'+''.join('<th>'+h+'</th>' for h in headers)+'</tr>'
    tr+=''.join('<tr>'+''.join('<td>'+html.escape(v)+'</td>' for v in row)+'</tr>' for row in table)
    notes=''.join('<li>'+html.escape(s)+'</li>' for s in result['limitations']+result['notes']+[feedback_note])
    page='''<!doctype html><html lang="en"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Jev medium comparison</title><style>body{font:16px system-ui;margin:32px auto;max-width:1150px;padding:0 24px;background:#f4f7f4;color:#17342c}h1{font-size:32px}h2{font-size:18px}.cards{display:flex;gap:18px;flex-wrap:wrap}article{background:white;border:1px solid #cadbd2;border-radius:12px;padding:22px;min-width:280px;flex:1}strong{font-size:38px}table{border-collapse:collapse;width:100%;background:white}th,td{padding:12px;text-align:left;border-bottom:1px solid #dce6e0}.scroll{overflow-x:auto}.note{padding:16px;background:#fff0d3;border-radius:8px;margin:22px 0}li{margin:9px 0;line-height:1.5}summary{cursor:pointer;font-weight:600}</style>
<h1>No memory vs Jev-assisted memory</h1><p>Medium dependency scheduler · 106 checks per run · same requested Claude Opus model · two fresh sessions per condition</p><section class="cards">'''+cards+'''</section><p class="note">These are observed timings, not proof of a reliable speed improvement. Fixed order, cache warming and only two runs per arm limit conclusions.</p><div class="scroll"><table>'''+tr+'''</table></div><p>Supplied memories are observed input; claimed use is the model's self-report. Grading checks are correlated within each implementation.</p><details open><summary>Controls and interpretation</summary><ul>'''+notes+'''</ul></details><p>Raw evidence: <a href="analysis.json">analysis.json</a> · <a href="results.json">results.json</a> · <a href="results.md">written report</a></p></html>'''
    (run/'results.html').write_text(page,encoding='utf-8')
    print(json.dumps({'report':str(run/'results.html'),'groups':groups,'jev_applied':raw['summary']['jev_rerank_applied']}))
    return result


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--run',type=Path,required=True)
    render(parser.parse_args().run.resolve(strict=True))
