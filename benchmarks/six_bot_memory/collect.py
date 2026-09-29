"""Collect immutable run evidence into a transparent comparison, without inference."""
import argparse
from datetime import datetime
import json
from pathlib import Path
import sys
from prepare import ROOT, group, read, write, now, declaration
from experiment_view import ExperimentStore
from experiment_usage import normalize_usage, summarize_usage
sys.path.insert(0,str(Path(__file__).resolve().parent))
from run_experiment import Experiment

def collect(parent):
    if (parent/'review/source-link.json').exists():
        if read(parent/'review/source-link.json').get('final_warm'):
            from warm_final import FinalWarm
            experiment=FinalWarm(parent)
        elif read(parent/'review/source-link.json').get('repeat_warm'):
            from warm_repeat import FreshWarm
            experiment=FreshWarm(parent)
        else:
            from clean_pair import FreshPair
            experiment=FreshPair(parent)
        preparation=experiment.source
    else:
        experiment=Experiment(parent)
        preparation=experiment
    experiment.initialize()
    assert all(preparation.group.status(x)=='completed' for x in ('authoring','audit'))
    assert all(experiment.group.status(x)=='completed' for x in ('team-cold','team-warm'))
    viewer=ExperimentStore(ROOT);conditions=[];details={}
    for identifier in ('authoring','audit','team-cold','team-warm'):
        source=(preparation if identifier in ('authoring','audit') else
                experiment.cold_source if identifier=='team-cold' and hasattr(experiment,'cold_source') else experiment)
        child=source.group.child(identifier)
        summary=read(child/'trials'/identifier/'summary.json')
        records=[read(p) for p in sorted((child/'calls').glob('*/record.json'))]
        detail=viewer.detail(source.parent.name,identifier)
        details[identifier]=detail
        real=[r for r in records if r.get('provider_calls')!=0]
        assert all(r['status']=='succeeded' for r in real)
        workers=[]
        for member in declaration():
            own=[dict(r,worker_id=r['actor_id']) for r in records if r['actor_id']==member['id']]
            use=summarize_usage([r for r in own if r.get('provider_calls')!=0])
            operations=next((a.get('operations',[]) for a in summary.get('actors',[]) if a['name']==member['id']),[])
            workers.append(dict(member,calls=len(own),provider_invocations=sum(r.get('provider_calls')!=0 for r in own),
              seconds=sum(r.get('elapsed_seconds') or 0 for r in own),usage=use['totals'],
              reads=sum(o['action']['op']=='read' for o in operations),listings=sum(o['action']['op']=='list' for o in operations),
              searches=sum(o['action']['op']=='search' for o in operations),
              memory_counts=[len(r.get('memory_ids',[])) for r in own],
              actual_models=sorted({m for r in own for m in r.get('actual_models',[])})))
        rows=dict(id=identifier,run_id=child.name,label=detail['label'],wall_seconds=summary.get('wall_seconds'),
          wall_time_comparable=summary.get('wall_time_comparable',True),call_path_seconds=summary.get('call_path_seconds'),
          provider_invocations=len(real),recorded_calls=len(records),
          usage=summarize_usage([dict(r,worker_id=r['actor_id']) for r in real]),
          initial=detail['grades']['initial'],final=detail['grades']['final'],workers=workers,
          started_at=summary['started_at'],ended_at=summary['ended_at'])
        conditions.append(rows)
    cold=next(r for r in conditions if r['id']=='team-cold');warm=next(r for r in conditions if r['id']=='team-warm')
    audit=next(r for r in conditions if r['id']=='audit')
    insertion=read(experiment.group.child('team-warm')/'review/seed-insertion.json')
    seed_ids=set(insertion['memory_outcome']['memory_ids'])
    assert len(seed_ids)==6 and audit['ended_at']<cold['started_at']<cold['ended_at']<insertion['inserted_at']<warm['started_at']
    for identifier in ('team-cold','team-warm'):
        child=experiment.group.child(identifier)
        records=[read(p) for p in (child/'calls').glob('*/record.json')]
        assert {r['actor_id'] for r in records}=={r['id'] for r in declaration()}
        for r in records:
            assert set(r.get('memory_ids',[]))==(seed_ids if identifier=='team-warm' else set())
            if identifier=='team-cold':assert not r.get('memory_context')
            if r['provider']=='OpenAI':assert not r.get('tool_events')
        assert all(not a['errors'] for a in read(child/'trials'/identifier/'summary.json')['actors'])
    total=lambda row:row['usage']['totals']['known_total_tokens']
    interrupted=[]
    failed_runs=[preparation.group.child('team-cold')]
    if hasattr(experiment,'cold_source'):
        failed_runs.append(experiment.cold_source.group.child('team-warm'))
    link=read(parent/'review/source-link.json') if (parent/'review/source-link.json').exists() else {}
    if link.get('final_warm'):
        from warm_repeat import FreshWarm
        failed_runs.append(FreshWarm(ROOT/'.orchestration'/link['prior_warm_parent']).group.child('team-warm'))
    for run in failed_runs:
        records=[read(p) for p in (run/'calls').glob('*/record.json')]
        real=[dict(r,worker_id=r['actor_id']) for r in records if r.get('provider_calls')!=0]
        interrupted.append(dict(run_id=run.name,provider_invocations=len(real),
          pre_provider_holds=len(records)-len(real),usage=summarize_usage(real),status='incomplete_excluded_from_pair'))
    admissions=[read(p) for p in (experiment.group.child('team-warm')/'review').glob('wave-admission-*.json')]
    delta=dict(warm_time_change_pct=(warm['wall_seconds']/cold['wall_seconds']-1)*100,
      warm_token_change_pct=(total(warm)/total(cold)-1)*100)
    data=dict(status='completed',collected_at=now(),parent=parent.name,conditions=conditions,comparison=delta,
      audit_before_cold=True,cold_before_seed=True,exact_six_memories_delivered=True,roster=declaration(),
      limits=read(experiment.lock)['limits']+(['User-approved admission threshold differed: cold 20 percent, warm temporarily 10 percent; restored afterward. Claude quota parsing was corrected before the final warm repeat. This adds operational confounding to the fixed-order comparison.'] if hasattr(experiment,'cold_source') else []),controller_usage={'status':'unknown','included':False},
      preparation_parent=preparation.parent.name,interrupted_attempts=interrupted,warm_admission_checks=admissions,
      warm_admission_pause_seconds=sum(max(0,len(a['attempts'])-1)*60 for a in admissions),
      warm_admission_total_seconds=sum(a['elapsed_seconds'] for a in admissions),
      preparation_recovery='One proven zero-provider Windows file-write failure; affected author completed in an explicitly recorded follow-up. Three outer JSON quotes in the other Grok author response were mechanically escaped, preserving original response. Audit corrected interval test-data brackets and strengthened two allocation cases before freeze. The first cold attempt was interrupted by two Claude quota holds; its ten completed calls remain separately visible and excluded from this clean pair. Two completed pre-reboot handoff reservations were reconciled with evidence; uncertain worker reservations stayed held. Both clean conditions refresh actual allowances before every wave.')
    if link.get('final_warm'):
        data['limits'].append('Final warm execution checks admission for both Claude slots before each wave. Any collector or admission wait is included in its elapsed time; interrupted attempts are separate and add real usage overhead.')
    write(parent/'review/results.json',data)
    origin=read(ROOT/'runtime/coordinator-viewer.json')['origin']
    lines=['# Six-bot medium memory comparison','',
      'Six logical workers: two OpenAI Astra, two Claude Opus, two Grok. Same identities and roles authored and cross-audited the fixture, then used fresh sessions for cold and warm repairs. No helper agents were authorized. Provider-internal auxiliary model usage is retained separately in reported model metadata.','',
      'The 52 KB synthetic Harbor fixture has six modules and 240 fixed checks. Audited reference: 240/240; buggy start: 196/240. All six audits finished before the frozen no-memory run. Six short audit facts were inserted into the warm Brain only after cold completed; the same facts were discoverable in both runs under docs/maintenance.md. No full reference code or hidden expected answers entered scored prompts.','',
      '| Execution | Wall time | Provider calls | Input tokens | Output tokens | Total tokens | Initial checks | Final checks |',
      '|---|---:|---:|---:|---:|---:|---:|---:|']
    for r in (cold,warm):
        u=r['usage']['totals'];lines.append(f"| {r['label']} | {r['wall_seconds']:.2f} s | {r['provider_invocations']} | {u['input_tokens']:,} | {u['output_tokens']:,} | {u['known_total_tokens']:,} | {r['initial']['passed']}/240 | {r['final']['passed']}/240 |")
    lines+=['',f"Warm time changed {delta['warm_time_change_pct']:+.1f}%; reported tokens changed {delta['warm_token_change_pct']:+.1f}%. These are observations from one fixed-order pair, not a causal estimate or evidence for a universal optimal bot count.",'']
    lines += [f"Warm wall time includes {data['warm_admission_pause_seconds']:.0f} seconds of recorded admission pauses. Its allowance collection plus admission checks took {data['warm_admission_total_seconds']:.2f} seconds in total. The sum of the longest worker call in each wave was {cold['call_path_seconds']:.2f} seconds cold and {warm['call_path_seconds']:.2f} seconds warm; this is a separate dispatch metric, not full elapsed time.",'']
    for r in (cold,warm):
        lines += [f"## {r['label']}",'',
          f"[Open this isolated dashboard execution]({origin}/experiments?run={read(ROOT/'.orchestration'/r['run_id']/'condition.json')['parent_run_id']}&condition={r['id']})",'',
          '| Worker | Provider | Role | Calls | Call seconds (summed) | Total tokens | List / search / read |',
          '|---|---|---|---:|---:|---:|---:|']
        for w in r['workers']:
            lines.append(f"| {w['id']} | {w['provider']} | {w['role']} | {w['provider_invocations']} | {w['seconds']:.2f} | {w['usage']['known_total_tokens']:,} | {w['listings']} / {w['searches']} / {w['reads']} |")
        lines+=['','Final normal/edge sections: '+', '.join(f"{g['name']} {g['passed']}/{g['total']}" for g in r['final']['groups'])+'.','']
    lines+=['## Preparation accounting','',
      'Creation and cross-audit are separate dashboard conditions and excluded from repair totals. The local pre-dispatch failure remains visible; preparation wall time was resumed and is not comparable. Root controller usage is unknown, not zero.','']
    for r in conditions[:2]:
        u=r['usage']['totals'];lines.append(f"- {r['label']}: {r['provider_invocations']} completed provider invocations, {u['known_total_tokens']:,} measured tokens"+(f", {r['wall_seconds']:.2f} seconds." if r['wall_seconds'] is not None else "; resumed preparation time not compared."))
    lines+=['','### Interrupted attempts','',
      'These attempts produced no complete score. Their usage is real overhead and is excluded from the completed pair above.','',
      '| Run | Completed provider calls | Holds before provider | Reported tokens |',
      '|---|---:|---:|---:|']
    for r in interrupted:
        lines.append(f"| {r['run_id']} | {r['provider_invocations']} | {r['pre_provider_holds']} | {r['usage']['totals']['known_total_tokens']:,} |")
    lines+=['',data['preparation_recovery'],'','## Limits','']+['- '+s for s in data['limits']]+[
      '- Call seconds per worker sum its sequential invocations; summing across workers is not elapsed team time.',
      '- Usage counts include normalized input and output, including reported cache-related input. They do not establish billing or subscription cost.',
      '- Memory delivery and a worker citing it are observable; internal reading or reliance is not directly observable.',
      '- Cold and warm are independent bounded repair attempts. Creation/audit histories were not passed to their fresh sessions.',
      '']
    (parent/'results.md').write_text('\n'.join(lines),encoding='utf-8')
    print(json.dumps({'comparison':delta,'conditions':[{'id':r['id'],'seconds':r['wall_seconds'],'tokens':r['usage']['totals']['known_total_tokens'],'final':r['final']['passed']} for r in conditions]}),flush=True)
    return data

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--run',type=Path,required=True)
    collect(p.parse_args().run.resolve(strict=True))
