"""Review and close a completed six-arm comparison using canonical application APIs."""
import argparse
import copy
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'app'))
from contribution_tasks import task_ledger
from contributions import write_report
from coordinator_handoff import Coordinator
from memory_bundle import capture_run
from memory_usage import record_feedback
from orchestration_lifecycle import start_run, closeout_run
from task_store import TaskStore, write_json

LEAD = dict(id='lead-sol', name='GPT-6 Sol xhigh orchestrator', provider='OpenAI', model='gpt-6-sol')
REVIEWER = 'GPT-6 Sol'


def read(path):
    return json.loads(path.read_text(encoding='utf-8-sig'))


def item(key, label, category, agent, evidence, weight=1):
    return dict(id=key, label=label, category=category, status='accepted', weight=weight,
        allocations=[dict(agent_id=agent, percent=100, evidence=evidence)])


def event(key, agent, kind, task_id, usage=None, models=None):
    usage = usage or {}
    return dict(id=key, agent_id=agent, kind=kind, status='succeeded', task_id=task_id,
        input_tokens=usage.get('input_tokens'), output_tokens=usage.get('output_tokens'), actual_models=models or [])


def finish(run, ledger, validation, knowledge, evidence):
    write_json(run / 'review/validation.json', validation)
    write_json(run / 'contributions-ledger.json', ledger)
    assert write_report(run, ledger)['attribution_complete']
    manifest = read(run / 'run.json')
    manifest.update(status='in_progress', validation=[dict(status='passed', evidence='review/validation.json')])
    manifest['final_artifacts'] = list(dict.fromkeys(manifest.get('final_artifacts', []) + ['review/validation.json', 'contribution-audit.md']))
    write_json(run / 'run.json', manifest)
    coord = Coordinator(run)
    state = coord.read()
    checkpoint = state['checkpoint']
    checkpoint.update(completed=['All six conditions finished before answer reviews. Measurements, grades, source controls and memory delivery reviewed by GPT-6 Sol xhigh.'], next_steps=[], open_jobs=[],
        validation=['review/validation.json'], artifacts=manifest['final_artifacts'])
    state = coord.checkpoint(checkpoint, state['owner'], state['session'], state['generation'])
    identity = {k: state[k] for k in ('owner', 'session', 'generation')}
    packet = start_run(run=run, no_memory=True, **identity)
    assert packet['operating_context']['context'] == (ROOT / 'app/assets/orchestration-context.md').read_text(encoding='utf-8')
    bundle = dict(capture_id='sol-jev-reviewed-result', project_id=manifest['project_id'], evidence=evidence,
        memories=[dict(key='measured-result', kind='fact', title=manifest.get('display_name', run.name) + ' reviewed measurement',
            content=knowledge, tags=['benchmark', 'jev', 'measured-results'], importance=.55)], relations=[])
    write_json(run / 'review/final-capture-bundle.json', bundle)
    captured = capture_run(ROOT, run.name, bundle, reviewer=REVIEWER,
        note='Verified retained measured results, exact source controls, usage coverage and memory delivery after all six scored conditions completed. No causal speed or billing inference.', **identity)
    assert captured['memory_outcome']['status'] == 'remembered', captured
    closed = closeout_run(run, root=ROOT, **identity)
    print(json.dumps({'run': run.name, 'capture': captured['memory_outcome']['status'], 'status': closed['status'],
        'held': [c for c in closed['checks'] if c['status'] != 'passed']}), flush=True)
    assert closed['status'] == 'completed', closed


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--run', required=True, type=Path)
    args = parser.parse_args()
    parent = args.run.resolve(strict=True)
    data = read(parent / 'results.json')
    review = read(parent / 'review/manual-review.json')
    assert data['status'] == 'completed' and len(data['current']) == 6
    assert review['status'] == 'passed' and review['all_trials_finished_before_reviews']
    assert set(review['conditions']) == {r['id'] for r in data['current']}
    store = TaskStore(ROOT / 'runs/tasks')
    child_ledgers = []
    for trial in data['current']:
        condition_id = trial.get('source_condition_id', trial['id'])
        child = parent.parent / trial['run_id']
        if (child / 'closeout.json').exists() and read(child / 'closeout.json')['status'] == 'completed':
            child_ledgers.append(read(child / 'contributions-ledger.json'))
            continue
        raw = read(child / 'trials' / condition_id / 'summary.json')
        contributors = {LEAD['id']: dict(LEAD)}
        items, events, receipts = [], [], []
        for actor in raw['actors']:
            records = [read(child / 'calls' / key / 'record.json') for key in actor['calls']]
            native = actor['provider'] == 'OpenAI'
            actor_id = 'native-astra-contestant' if native else 'worker:' + ('claude' if actor['provider'] == 'Anthropic' else 'grok')
            note = ('Retained benchmark response under frozen Tern contract, bounded simulated file tools and exact memory condition. '
                    f"Final integrated score {trial['final_passed']}/{trial['total']}; initial {trial['initial_passed']}/{trial['total']}. "
                    'This is acceptance as measured experiment evidence, not a production change. Lead independently checked grades, file actions, memory receipts and usage. '
                    'No answer review or capture occurred until all six scored conditions finished. ' + str(parent / 'review/manual-review.json'))
            weight = (3 if actor['role'] == 'all' else 1) / len(records)
            for record in records:
                assert record['status'] == 'succeeded'
                jev = (record.get('memory_retrieval') or {}).get('jev')
                if isinstance(jev, dict):
                    contributors['jev'] = dict(id='jev', name='JEV memory decisions', provider='TypeSafe via OpenRouter', model=jev.get('model') or 'unknown')
                    decision = event('jev:' + record['call_id'], 'jev', 'memory-decision', record['call_id'],
                        {'input_tokens': jev.get('input_tokens'), 'output_tokens': jev.get('output_tokens')}, [jev['model']] if jev.get('model') else [])
                    decision.update(status=jev.get('status', 'unknown'), provider_calls=jev.get('provider_calls'),
                        reported_cost_usd=jev.get('cost_usd'), applied=jev.get('applied'), order_changed=jev.get('order_changed'))
                    events.append(decision)
                if native:
                    models = record.get('actual_models', [])
                    contributors[actor_id] = dict(id=actor_id, name='Astra contestant (requested)', provider='OpenAI', model=', '.join(models) or 'unknown')
                    events.append(event(record['call_id'], actor_id, 'delegation', record['call_id'], record.get('usage'), models))
                    items.append(item(record['call_id'], actor['role'] + ' contestant evidence', 'benchmark', actor_id, note, weight))
                else:
                    job = record['job_id']
                    canonical = read(store.directory(job) / 'result.json')
                    if canonical['status'] == 'awaiting_review':
                        proposed = copy.deepcopy(canonical)
                        proposed.update(status='accepted', review_status='accepted', review={'reviewer': REVIEWER})
                        per_task = task_ledger(proposed)
                        per_task['work_items'] = [item('retained-response', 'Retained benchmark response', 'benchmark', actor_id, note),
                            item('acceptance-review', 'Independent measured-evidence review', 'review', 'reviewer:' + REVIEWER, note)]
                        reviewed = store.review(job, 'accepted', REVIEWER, note, contributions=per_task)
                        assert reviewed['memory_outcome']['status'] == 'remembered', reviewed.get('memory_outcome')
                    canonical = read(store.directory(job) / 'result.json')
                    assert canonical['status'] == 'accepted'
                    if trial['memory']:
                        feedback = record_feedback(store, job, 'neutral', REVIEWER,
                            'Exact four-note delivery and source binding were verified. One fixed-order observation does not isolate a benefit attributable to these notes or JEV; self-reported use is retained separately.',
                            expected_project_id=canonical['assignment_project_id'])
                        write_json(child / 'review' / ('memory-feedback-' + job + '.json'), feedback)
                    receipt = read(store.directory(job) / 'memory-outcome.json')
                    assert receipt['status'] == 'remembered'
                    receipts.append(dict(job_id=job, status='remembered'))
                    observed = task_ledger(canonical)
                    worker = next(a for a in observed['contributors'] if a['id'] == actor_id)
                    if actor_id in contributors:
                        worker['model'] = ', '.join(sorted(set(contributors[actor_id]['model'].split(', ')) | set(worker['model'].split(', '))))
                    contributors[actor_id] = worker
                    events.append(next(e for e in observed['activity'] if e['kind'] == 'delegation'))
                    items.append(item(job, actor['role'] + ' contestant evidence', 'benchmark', actor_id, note, weight))
        items.append(item('independent-review', 'Verify source, grade, memory and usage evidence', 'review', LEAD['id'], 'review/validation.json'))
        events.append(event('controller-review', LEAD['id'], 'review', child.name))
        ledger = dict(schema_version=1, scope_id=child.name, title=trial['id'] + ' measured result',
            basis='Estimated accepted work: one point per measured module, split over its actual calls; one point for independent lead review. Usage is separate.',
            contributors=list(contributors.values()), work_items=items, activity=events)
        tokens = trial['usage']['totals']
        knowledge = (f"Tern {trial['id']} on 2026-09-26, Sol xhigh controller: {trial['wall_seconds']:.2f}s wall, "
            f"{trial['model_calls']} contestant invocations, {tokens['input_tokens']} logical input and {tokens['output_tokens']} output tokens, "
            f"initial {trial['initial_passed']}/280 and final {trial['final_passed']}/280; "
            f"{trial['calls_with_memory']} calls received {trial['unique_memories_delivered']} distinct notes. "
            f"JEV: {trial['jev']['lookups']} lookups; applied {trial['jev']['applied']}, order changed {trial['jev']['order_changed']}. "
            'One fixed-order observation, controller excluded, actual subscription charges unknown. Evidence: ' + child.name)
        finish(child, ledger, dict(trial=trial, manual_review=review['conditions'][trial['id']], hosted_receipts=receipts), knowledge,
            ['review/validation.json', 'contributions-ledger.json', 'trials/' + condition_id + '/summary.json'])
        child_ledgers.append(ledger)
    if data.get('fresh_repeat'):
        link = data['fresh_repeat']
        failed = parent.parent / link['failed_run_id']
        if not (failed/'closeout.json').exists() or read(failed/'closeout.json')['status']!='completed':
            contributors = {LEAD['id']: dict(LEAD)}
            events = []
            for path in sorted((failed/'calls').glob('*/record.json')):
                record = read(path)
                if record['provider']=='OpenAI':
                    actor_id='native-astra-contestant'
                    contributors[actor_id]=dict(id=actor_id,name='Astra contestant (requested)',provider='OpenAI',model='unknown')
                    if record['status']=='succeeded':
                        events.append(event(record['call_id'],actor_id,'delegation',record['call_id'],record.get('usage'),record.get('actual_models',[])))
                    else:
                        assert record['status']=='held' and record.get('pid') is None and record.get('reservation_id') is None
                        events.append(dict(event(record['call_id'],actor_id,'preflight',record['call_id'],{'input_tokens':0,'output_tokens':0}),status='held'))
                else:
                    job=record['job_id'];canonical=read(store.directory(job)/'result.json')
                    if canonical['status']=='awaiting_review':
                        store.review(job,'rejected',REVIEWER,'Interrupted benchmark attempt after a verified local pre-provider quota-admission hold. Completed provider outputs and measured usage are preserved, but this attempt has no complete integrated score. A separately timed fresh repeat supplies the scored comparison; no original answer was reused.')
                    canonical=read(store.directory(job)/'result.json');assert canonical['status']=='rejected'
                    observed=task_ledger(canonical)
                    actor_id='worker:'+canonical['worker']
                    worker=next(a for a in observed['contributors'] if a['id']==actor_id)
                    if actor_id in contributors:
                        worker['model']=', '.join(sorted(set(contributors[actor_id]['model'].split(', '))|set(worker['model'].split(', '))))
                    contributors[actor_id]=worker
                    events.append(next(e for e in observed['activity'] if e['kind']=='delegation'))
                jev=(record.get('memory_retrieval') or {}).get('jev')
                if isinstance(jev,dict):
                    contributors['jev']=dict(id='jev',name='JEV memory decisions',provider='TypeSafe via OpenRouter',model=jev.get('model') or 'unknown')
                    events.append(dict(event('jev:'+record['call_id'],'jev','memory-decision',record['call_id'],jev,[jev['model']] if jev.get('model') else []),
                        status=jev.get('status','unknown'),provider_calls=jev.get('provider_calls'),reported_cost_usd=jev.get('cost_usd')))
            failed_ledger=dict(schema_version=1,scope_id=failed.name,title='Interrupted team JEV attempt: preserved without final score',
                basis='Only the verified reconciliation earns accepted-work credit. Incomplete responses and JEV activity remain measured separately.',
                contributors=list(contributors.values()),work_items=[item('reconciliation','Reconcile admission hold and retain interrupted usage','review',LEAD['id'],'review/hold-reconciliation.json')],activity=events)
            finish(failed,failed_ledger,dict(status='reconciled_incomplete',attempt=data['interrupted_attempts'][0],repeat=link),
                'Interrupted Tern team JEV attempt: seven completed contestant calls, one local OpenAI admission hold before reservation/process, no final integrated score or complete duration. Completed estimates were retired only by official quota refresh. Original usage is preserved separately from the new independent repeat.',
                ['review/validation.json','review/hold-reconciliation.json','contributions-ledger.json'])
        child_ledgers.append(read(failed/'contributions-ledger.json'))
        repeat_parent=parent.parent/link['repeat_parent_run_id']
        if not (repeat_parent/'closeout.json').exists() or read(repeat_parent/'closeout.json')['status']!='completed':
            repeat_ledger=dict(schema_version=1,scope_id=repeat_parent.name,title='Fresh team JEV repeat coordination',
                basis='One estimated point for explicit fresh-repeat preparation; scored work is attributed in the child.',contributors=[LEAD],
                work_items=[item('repeat-coordination','Prepare independent repeat after verified hold','coordination',LEAD['id'],'review/source-link.json')],
                activity=[event('repeat-coordination',LEAD['id'],'coordination',repeat_parent.name)])
            finish(repeat_parent,repeat_ledger,dict(status='passed',repeat=link),
                f"Fresh team Brain+JEV Tern repeat on 2026-09-26 under Sol xhigh, linked to {parent.name}. Original held attempt preserved. Clean repeat {link['wall_seconds']:.2f}s; measured report in original parent results.json. Same fixture, notes, grader and contestant roster. No threshold reduction or uncertain retry.",
                ['review/validation.json','review/source-link.json','contributions-ledger.json'])
        child_ledgers.append(read(repeat_parent/'contributions-ledger.json'))
    # Parent own-work audit; scored actors have full canonical audits in children.
    astra = dict(id='setup-astra', name='GPT-6 Astra harness worker', provider='OpenAI', model='gpt-6-astra')
    controller = data.get('controller_usage', {})
    ledger = dict(schema_version=1, scope_id=parent.name, title='Sol xhigh JEV comparison preparation and review',
        basis='Estimated accepted setup work: Sol six points for historical reconciliation, integration, validation and reporting; Astra four for harness adaptation and audit. Contestant contributions and usage are in six child audits.',
        contributors=[LEAD, astra], work_items=[item('sol-comparison', 'Historical reconciliation, integration, validation and report', 'coordination', LEAD['id'], 'results.json; review/manual-review.json', 6),
            item('astra-harness', 'Six-condition harness and independent contract audit', 'coding', astra['id'], 'review/astra-harness-review.md', 4)],
        activity=[event('lead-work', LEAD['id'], 'coordination', parent.name, controller.get('lead', {}).get('usage'), ['gpt-6-sol']),
            event('astra-harness', astra['id'], 'delegation', 'astra-harness-adaptation', controller.get('astra', {}).get('usage'), ['gpt-6-astra'])])
    combined = copy.deepcopy(ledger)
    combined['scope_id'] = parent.name + '-all-work'
    combined['title'] = 'Sol-led JEV suite: all accepted work'
    combined['basis'] = 'Rollup of parent setup and six child ledgers, counted once. Work points are coordinator estimates, not measured effort, tokens or quality.'
    contributors = {a['id']: a for a in combined['contributors']}
    for child_ledger in child_ledgers:
        for contributor in child_ledger['contributors']:
            existing = contributors.get(contributor['id'])
            if existing:
                existing['model'] = ', '.join(sorted(set(existing['model'].split(', ')) | set(contributor['model'].split(', '))))
            else:
                contributors[contributor['id']] = copy.deepcopy(contributor)
        for work in child_ledger['work_items']:
            combined['work_items'].append(dict(work, id=child_ledger['scope_id'] + ':' + work['id']))
        for activity in child_ledger['activity']:
            combined['activity'].append(dict(activity, id=child_ledger['scope_id'] + ':' + activity['id']))
    combined['contributors'] = list(contributors.values())
    write_json(parent / 'review/overall-contributions-ledger.json', combined)
    assert write_report(parent / 'review/overall-contributions', combined)['attribution_complete']
    finish(parent, ledger, dict(status='passed', conditions=[r['run_id'] for r in data['current']], review=review),
        'Completed six-condition Tern 280-check comparison led by verified GPT-6 Sol xhigh on 2026-09-26. '
        + '; '.join(f"{t['id']}: {t['wall_seconds']:.2f}s, {t['final_passed']}/280, {t['usage']['totals']['known_total_tokens']} measured tokens" for t in data['current'])
        + '. One observation per arm; JEV timing, cost, applied and order change are separate; no causal or subscription billing conclusion. Full report: ' + str(parent / 'results.json'),
        ['results.json', 'review/manual-review.json', 'review/astra-harness-review.md', 'review/validation.json', 'contributions-ledger.json'])


if __name__ == '__main__':
    main()
