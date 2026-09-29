"""Verify capture outcomes and close the completed and explicitly abandoned runs."""
import argparse
import copy
import json
from pathlib import Path
import sys
from prepare import ROOT,read,write,now,group
from contribution_tasks import task_ledger
from contributions import write_report
from coordinator_handoff import Coordinator
from orchestration_lifecycle import start_run,closeout_run
from memory_bundle import capture_run
from task_store import TaskStore
from usage_guard import file_lock
from experiment_usage import normalize_usage
from clean_pair import FreshPair,ORIGINAL
from warm_repeat import FreshWarm,COLD_PARENT

LEAD=dict(id='lead-astra',name='ASTRA controller',provider='OpenAI',model='unknown')
def item(key,label,agent,note,weight=1,category='coding'):
    return dict(id=key,label=label,category=category,status='accepted',weight=weight,
      allocations=[dict(agent_id=agent,percent=100,evidence=note)])
def event(key,agent,kind,task,usage=None,models=None):
    usage=usage or {}
    return dict(id=key,agent_id=agent,kind=kind,status='succeeded',task_id=task,
      input_tokens=usage.get('input_tokens'),output_tokens=usage.get('output_tokens'),actual_models=models or [])

def finish(run,ledger,validation,knowledge,evidence):
    write(run/'review/validation.json',validation)
    write(run/'contributions-ledger.json',ledger)
    report=write_report(run,ledger);assert report['attribution_complete']
    manifest=read(run/'run.json')
    manifest.update(status='in_progress',validation=[dict(status='passed',evidence='review/validation.json')],
      final_artifacts=list(dict.fromkeys(manifest.get('final_artifacts',[])+['review/validation.json','contribution-audit.md'])))
    write(run/'run.json',manifest)
    coordinator=Coordinator(run);state=coordinator.read();checkpoint=state['checkpoint']
    checkpoint.update(objective=manifest['objective'],completed=['Evidence reviewed; all linked task outcomes reconciled. Every experiment attempt has ended; incomplete conditions were explicitly abandoned before acceptance capture.'],
      next_steps=[],open_jobs=[],validation=['review/validation.json'],artifacts=manifest['final_artifacts'])
    state=coordinator.checkpoint(checkpoint,state['owner'],state['session'],state['generation'])
    identity={k:state[k] for k in ('owner','session','generation')}
    packet=start_run(run=run,no_memory=True,**identity)
    assert packet['operating_context']['sha256']=='2a5bf163a138151fad053933a27638b927923d4e6f9da1b14f11f296871e023c'
    bundle=dict(capture_id='six-bot-final-reviewed-evidence',project_id=manifest['project_id'],evidence=evidence,
      memories=[dict(key='six-worker-measurement',kind='fact',title=manifest.get('display_name',run.name)+' - reviewed outcome',
        content=knowledge,tags=['Harbor','benchmark','six-workers','reviewed-result'],importance=.5)],relations=[])
    write(run/'review/final-capture-bundle.json',bundle)
    captured=capture_run(ROOT,run.name,bundle,reviewer='ASTRA',note='Verified original calls, audit artifacts, scoped memory delivery, separate dashboard accounting and available checks after all attempts ended. Completed and explicitly incomplete results remain distinguished.',**identity)
    assert captured['memory_outcome']['status']=='remembered'
    closed=closeout_run(run,root=ROOT,**identity)
    print(json.dumps(dict(run=run.name,capture='remembered',status=closed['status'],
      held=[r for r in closed['checks'] if r['status']!='passed'])),flush=True)
    assert closed['status']=='completed',closed

def finalize(parent):
    link=read(parent/'review/source-link.json')
    if link.get('final_warm'):
        from warm_final import FinalWarm
        experiment=FinalWarm(parent)
    else:
        experiment=FreshWarm(parent) if link.get('repeat_warm') else FreshPair(parent)
    data=read(parent/'review/results.json')
    assert data['status'] in ('completed','incomplete_closed') and data['exact_six_memories_delivered'] and data['cold_before_seed']
    incomplete=data['status']=='incomplete_closed'
    if incomplete:
        closure=read(parent/'review/user-close-incomplete.json')
        assert closure['authorization']=='Close as incomplete' and closure['resume_disabled']
    manual=read(parent/'review/manual-review.json');assert manual['status']=='passed'
    browser=read(parent/'review/browser-verification.json');assert browser['status']=='passed'
    store=TaskStore(ROOT/'runs/tasks')
    source=experiment.source
    todo=[(source.group.child('authoring'),'authoring',True),(source.group.child('audit'),'audit',True),
      (source.group.child('team-cold'),'team-cold',False),(source.group.child('team-warm'),'team-warm',False),
      (experiment.group.child('team-cold'),'team-cold',True),(experiment.group.child('team-warm'),'team-warm',not incomplete)]
    if hasattr(experiment,'cold_source'):
        todo.append((experiment.cold_source.group.child('team-warm'),'team-warm',False))
    if link.get('final_warm'):
        previous=FreshWarm(ROOT/'.orchestration'/link['prior_warm_parent'])
        todo.append((previous.group.child('team-warm'),'team-warm',False))
    for run,condition,retained in todo:
        if (run/'closeout.json').exists() and read(run/'closeout.json')['status']=='completed':
            print('Already closed: '+run.name,flush=True)
            continue
        calls=[read(p) for p in sorted((run/'calls').glob('*/record.json'))]
        contributors={LEAD['id']:dict(LEAD)};items=[];events=[];receipts=[]
        row=next((x for x in data['conditions'] if x['run_id']==run.name),None)
        note=('Retained supplied-text '+condition+' response under its declared logical worker identity. '
          'Creation and audit artifacts were reviewed before freeze; scored outputs are evaluated benchmark attempts, not production changes. '
          'Original calls, provider usage, allowed file operations and all 240 test outcomes are preserved. '
          'Partial audit hints only; no scored answer was captured until both scored runs ended. See '+parent.name+'/review/results.json and review/manual-review.json.')
        for r in calls:
            native=r['provider']=='OpenAI'
            actor='native:'+r['actor_id'] if native else 'worker:'+('claude' if r['provider']=='Anthropic' else 'grok')
            if native:
                contributors[actor]=dict(id=actor,name=r['actor_id'],provider='OpenAI',model='unknown')
                if r['status']=='succeeded':
                    events.append(event(r['call_id'],actor,'delegation',r['call_id'],normalize_usage(r),r.get('actual_models',[])))
                    if retained:items.append(item(r['call_id'],r['actor_id']+' '+condition,actor,note,weight=1/sum(x['actor_id']==r['actor_id'] for x in calls)))
                continue
            job=r.get('job_id')
            if not job:continue
            path=store.directory(job)/'result.json'
            canonical=read(path)
            if canonical['status']=='held':
                assert not retained and r.get('provider_calls')==0 and canonical.get('reservation_id') is None
                assert canonical.get('quota_before',{}).get('allowed') is False
                write(run/'review'/('original-held-'+job+'.json'),canonical)
                with file_lock(store.directory(job)/'review.lock'):
                    canonical.update(status='failed',execution_status='failed',review_status='not_required',
                      finalized_at=now(),ended_at=now(),provider_calls=0,
                      reconciliation=dict(reviewer='ASTRA',at=now(),evidence='review/hold-reconciliation.json',
                        reason='Condition abandoned after verified pre-provider admission hold. Original held result archived; no inference or reservation was started.'))
                    store.save(job,canonical)
            elif canonical['status']=='awaiting_review':
                if retained:
                    proposed=copy.deepcopy(canonical);proposed.update(status='accepted',review_status='accepted',review={'reviewer':'ASTRA'})
                    per_task=task_ledger(proposed)
                    per_task['work_items']=[item('retained-response','Retained benchmark response',actor,note),
                      item('acceptance-review','Independent evidence review','reviewer:ASTRA',note,category='review')]
                    reviewed=store.review(job,'accepted','ASTRA',note,contributions=per_task)
                    assert reviewed['memory_outcome']['status']=='remembered',reviewed
                else:
                    store.review(job,'rejected','ASTRA','Incomplete benchmark attempt after verified quota holds or explicit user closure. This successful response is retained as usage and partial-output evidence but is not accepted as a completed benchmark implementation. Remaining inference was canceled; this does not assert a provider execution failure.')
            canonical=read(path)
            observed=task_ledger(canonical)
            worker=next(a for a in observed['contributors'] if a['id']==actor)
            if actor in contributors:
                contributors[actor]['model']=', '.join(sorted(set(contributors[actor]['model'].split(', '))|set(worker['model'].split(', '))))
            else:contributors[actor]=dict(worker)
            if canonical.get('execution_status')=='succeeded':
                events.append(next(e for e in observed['activity'] if e['kind']=='delegation'))
            if retained:
                assert canonical['status']=='accepted'
                receipt=read(store.directory(job)/'memory-outcome.json');assert receipt['status']=='remembered'
                receipts.append(dict(job_id=job,status='remembered'))
                items.append(item(job,r['actor_id']+' '+condition,actor,note,weight=1/sum(x['actor_id']==r['actor_id'] for x in calls)))
        items.append(item('controller-evidence-review','Validate and reconcile recorded experiment evidence',LEAD['id'],
          'review/validation.json; original run evidence and separate dashboard conditions',category='review'))
        events.append(event('controller-review',LEAD['id'],'review',run.name))
        ledger=dict(schema_version=1,scope_id=run.name,title=read(run/'run.json').get('display_name',run.name),
          basis='Estimated accepted-work credit: one point per retained logical worker divided across its calls, plus one controller review point. Usage is recorded separately; rejected or pre-provider calls earn no accepted work credit.',
          contributors=list(contributors.values()),work_items=items,activity=events)
        if row and row['wall_seconds'] is not None:
            knowledge=f"Harbor six-worker {condition}: {row['wall_seconds']:.2f} seconds, {row['usage']['totals']['known_total_tokens']} measured provider tokens. "
        else:knowledge='Harbor '+condition+' preparation or abandoned condition, outside the final timed comparison. '
        knowledge+=('Same six logical identities: two OpenAI Astra, two Claude Opus, two Grok. Original creation/audit and interrupted attempt remain separately recorded. '
          'Only six approved partial audit facts, also in public fixture docs, entered the fresh warm Brain after cold. Controller usage unknown. '
          +('Warm comparison was closed incomplete by explicit user request; no warm timing or efficiency conclusion is valid. ' if incomplete else 'One fixed-order pair. ')
          +'Evidence '+parent.name+'/results.md.')
        validation=dict(status='passed' if retained else 'reconciled_abandoned',condition=condition,retained=retained,
          comparison_row=row,hosted_reviews=receipts,manual_review=manual,browser=browser,
          preserved_calls=[dict(call_id=r['call_id'],status=r['status'],actor=r['actor_id'],provider_calls=r.get('provider_calls')) for r in calls])
        evidence=['review/validation.json','contributions-ledger.json']
        if (run/'trials'/condition/'summary.json').exists():evidence.append('trials/'+condition+'/summary.json')
        if (run/'trials'/condition/'final-grade.json').exists():evidence.append('trials/'+condition+'/final-grade.json')
        if (run/'review/hold-reconciliation.json').exists():evidence.append('review/hold-reconciliation.json')
        finish(run,ledger,validation,knowledge,evidence)
    parents=list(dict.fromkeys([ROOT/'.orchestration'/read(r/'condition.json')['parent_run_id'] for r,_,_ in todo]))
    for run in parents:
        if (run/'closeout.json').exists() and read(run/'closeout.json')['status']=='completed':
            print('Already closed: '+run.name,flush=True)
            continue
        children=[r for r,_,_ in todo if read(r/'condition.json')['parent_run_id']==run.name]
        ledger=dict(schema_version=1,scope_id=run.name,title='Six-worker comparison coordination',
          basis='Parent credits only setup and evidence review; actual provider and accepted worker activity is isolated in children.',
          contributors=[LEAD],work_items=[item('coordination','Coordinate, isolate and verify six-worker experiment',LEAD['id'],
            'review/validation.json; child capture and closeout records',category='coordination')],
          activity=[event('controller-coordination',LEAD['id'],'coordination',run.name)])
        validation=dict(status='passed',children=[dict(run_id=r.name,status=read(r/'closeout.json')['status']) for r in children],
          comparison_parent=parent.name,results=data if run==parent else None)
        knowledge=('Six-worker Harbor experiment authored and independently cross-audited by the same six logical identities. Cold completed 240/240; interrupted attempts remain separately visible. '
          +('User explicitly closed warm as incomplete; no full warm score or valid time/usage comparison. ' if incomplete else 'A fresh warm condition also completed. ')
          +'Measured creation/audit costs and condition evidence are in '+parent.name+'/results.md. No general optimal team-size or causal memory-speed claim follows.')
        evidence=['review/validation.json','contributions-ledger.json']
        if run==parent:evidence+=['results.md','review/results.json','review/manual-review.json','review/browser-verification.json']
        finish(run,ledger,validation,knowledge,evidence)

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--run',type=Path,required=True)
    finalize(p.parse_args().run.resolve(strict=True))
