"""Explicitly reconcile and resume the saved warm condition; default is no inference.

Completed calls are replayed only after exact prompt identity checks. A resumed
condition can finish its quality/usage evidence but cannot supply clean wall time.
"""
import argparse
from contextlib import contextmanager
from datetime import datetime
import hashlib
import json
import os
from pathlib import Path
import time
import uuid
from warm_final import FinalWarm, ROOT, read, write, now, base, refresh
from transport import Transport
from experiment_runs import publish_condition
from usage_guard import Guard, file_lock

PARENT=ROOT/'.orchestration/six-bot-audit-memory-final-20260913T180341Z-aa2d3536'

def inspect():
    experiment=FinalWarm(PARENT);experiment.initialize()
    child=experiment.group.child('team-warm')
    pause=read(PARENT/'review/pause.json')
    assert pause['status']=='paused' and pause['no_inflight_calls']
    records=[read(p) for p in sorted((child/'calls').glob('*/record.json'))]
    assert len(records)==18 and all(r['status']=='succeeded' for r in records)
    expected=set(read(child/'review/seed-insertion.json')['memory_outcome']['memory_ids'])
    assert len(expected)==6 and all(set(r['memory_ids'])==expected for r in records)
    for r in records:
        if r.get('job_id'):
            canonical=read(ROOT/'runs/tasks'/r['job_id']/'result.json')
            assert canonical['execution_status']=='succeeded' and canonical['status']=='awaiting_review'
    return experiment,child,records

@contextmanager
def resumed_condition(experiment,identifier):
    group=experiment.group;child=group.child(identifier);lease_id=uuid.uuid4().hex
    with file_lock(group.gate):
        previous=read(group.active)
        assert previous['status']=='uncertain' and previous['run_id']==child.name
        assert group.status(identifier)=='uncertain'
        write(PARENT/'review/pre-resume-lease.json',previous)
        write(group.active,dict(schema_version=1,status='active',lease_id=lease_id,
          parent_run_id=PARENT.name,condition_id=identifier,run_id=child.name,pid=os.getpid(),started_at=now()))
        publish_condition(child,status='running',stage='explicitly_resumed_from_verified_calls')
    group._parent_status('running')
    try:
        yield child
    except BaseException:
        status='uncertain'
        publish_condition(child,status=status,stage='resume_requires_reconciliation',ended_at=now())
        raise
    else:
        status='completed'
        summary_path=child/'trials/team-warm/summary.json';summary=read(summary_path)
        summary.update(wall_time_comparable=False,wall_seconds=None,
          resume_execution_seconds=summary['wall_seconds'],resumed=True,
          original_started_at=previous['started_at'],
          total_elapsed_including_pause_seconds=(datetime.fromisoformat(now())-datetime.fromisoformat(previous['started_at'])).total_seconds(),
          note='Explicit resume after quota pause. Previously completed calls reused after exact prompt hash checks; no completed inference repeated. Clean wall-time comparison unavailable.')
        write(summary_path,summary)
        publish_condition(child,status=status,stage='completed_after_quota_pause',ended_at=now())
    finally:
        with file_lock(group.gate):
            active=read(group.active);assert active['lease_id']==lease_id
            active.update(status=status,ended_at=now());write(group.active,active)
        group._parent_status(status)

def execute():
    experiment,child,records=inspect()
    policy=ROOT/'runtime/policy.json'
    # User explicitly approved 10 temporarily for this same warm condition.
    with file_lock(ROOT/'runtime/policy.lock'):
        before=read(policy);assert before['worker_start_threshold_pct']==20
        during=dict(before,worker_start_threshold_pct=10);write(policy,during)
    try:
        refresh(child)
        guard=Guard();claude=guard.check('claude')
        # Do not start another partial wave unless both Claude slots have room.
        assert claude['allowed'] and all(w['available_pct']>13 and w['freshness']=='fresh' for w in claude['windows']), 'Fresh allowance still insufficient; no inference started'
        for worker in ('codex','grok'):
            assert guard.check(worker)['allowed'],worker+' allowance held; no inference started'
        write(PARENT/'review/resume-reconciliation.json',dict(at=now(),status='verified_for_explicit_resume',
          prior_completed_calls=[dict(call_id=r['call_id'],base_prompt_sha256=r['base_prompt_sha256']) for r in records],
          runner_sha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
          cold_time_comparison=False,claude_preflight=claude,
          authorization='Original six-bot test plus explicit user-approved temporary 10 percent threshold; no additional inference outside remaining calls'))
        experiment.group.condition=lambda identifier:resumed_condition(experiment,identifier)
        base.Transport=Transport
        experiment.trial('team-warm')
    finally:
        with file_lock(ROOT/'runtime/policy.lock'):
            current=read(policy);assert current['worker_start_threshold_pct']==10
            current['worker_start_threshold_pct']=20;write(policy,current)
            write(PARENT/'review/resume-threshold-restoration.json',dict(at=now(),status='restored',threshold=20,reservations='preserved'))

if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--execute',action='store_true')
    args=parser.parse_args()
    if args.execute:execute()
    else:
        experiment,child,records=inspect()
        print(json.dumps(dict(status='resume_inputs_verified',model_calls=0,completed_calls=len(records),
          pending_calls=7,run=child.name,command='python benchmarks/six_bot_memory/resume_paused.py --execute',
          note='No background resume scheduled. Execution requires fresh sufficient allowance; interrupted elapsed time will remain unscored.')))
