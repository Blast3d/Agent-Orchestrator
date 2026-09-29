"""Fresh warm condition with admission checked for both Claude slots per wave.

No provider retries or reservation releases. Any waiting remains in elapsed time.
"""
import copy
import hashlib
from pathlib import Path
import time
from warm_repeat import FreshWarm, COLD_PARENT, ORIGINAL, ROOT, read, write, now, base
from clean_pair import refresh
from transport import Transport
from usage_guard import Guard, file_lock
from experiment_runs import publish_condition
from orchestration_lifecycle import start_run

PRIOR=ROOT/'.orchestration/six-bot-audit-memory-warm-repeat-20260913T175201Z-e3415e28'

def admission_pair():
    guard=Guard()
    with guard.state() as current:
        state=copy.deepcopy(current)
    first=guard.evaluate(state,'claude','small')
    state['reservations']['benchmark-simulation-only']=dict(worker='claude',task='simulation only',
      pools=state.get('worker_pools',{}).get('claude',guard.policy['worker_pools']['claude']),
      estimate_pct=first['estimate_pct'],created_at=now(),finished_at=None)
    second=guard.evaluate(state,'claude','small')
    return dict(at=now(),allowed=first['allowed'] and second['allowed'],first=first,second=second)

class CheckedTransport(Transport):
    def checkpoint(self,label,memory=False):
        if label.startswith('team-warm'):
            started=time.monotonic(); attempts=[]
            for attempt in range(11):
                refresh(self.run)
                check=admission_pair();attempts.append(check)
                write(self.run/'review'/('wave-admission-'+label.replace(' ','-')+'.json'),
                  dict(label=label,elapsed_seconds=time.monotonic()-started,attempts=attempts,
                    note='Read-only simulation of both Claude admissions; no real reservations changed. Waiting included in wall time.'))
                print('Warm wave admission: '+str(check['allowed'])+'; '+str(round(time.monotonic()-started,2))+' seconds',flush=True)
                if check['allowed']:break
                if attempt==10:raise RuntimeError('Both Claude slots still cannot start; no contestants launched in this wave.')
                time.sleep(60)
        return super().checkpoint(label,memory)

class FinalWarm(FreshWarm):
    def __init__(self,parent):
        super().__init__(parent)
        base.Transport=CheckedTransport

    def fingerprints(self):
        values=super().fingerprints()
        path=Path(__file__).resolve()
        values[str(path.relative_to(ROOT))]=hashlib.sha256(path.read_bytes()).hexdigest()
        return values

def start_final():
    prior=FreshWarm(PRIOR);prior.initialize()
    failed=prior.group.child('team-warm')
    calls=[read(p) for p in (failed/'calls').glob('*/record.json')]
    assert len(calls)==12 and sum(r['status']=='succeeded' for r in calls)==11
    held=[r for r in calls if r['status']=='held'];assert len(held)==1
    evidence=[]
    for r in held:
        canonical=read(failed/'calls'/r['call_id']/'result.json')
        assert canonical['reservation_id'] is None and canonical['quota_before']['allowed'] is False
        evidence.append(dict(call_id=r['call_id'],job_id=r['job_id'],quota_before=canonical['quota_before'],provider_calls=0))
        r.update(provider_calls=0,preflight_only=True);write(failed/'calls'/r['call_id']/'record.json',r)
    write(failed/'review/hold-reconciliation.json',dict(at=now(),status='failed_before_completion',completed_calls=11,
      held_before_provider=evidence,reason='Cached Claude quota plus preserved estimates held one turn-two slot. No complete warm score. Preserved as separate overhead; next fresh condition checks both admissions before each wave.'))
    active=read(prior.group.active);assert active['status']=='uncertain' and active['run_id']==failed.name
    active.update(status='completed',reconciled_at=now());write(prior.group.active,active)
    publish_condition(failed,status='failed',stage='reconciled_incomplete',ended_at=now())
    data=read(PRIOR/'experiment.json');data['status']='completed_with_failed_condition';write(PRIOR/'experiment.json',data)
    packet=start_run(workspace=ROOT,name='six-bot-audit-memory-final',project='orchestration-benchmark',no_memory=True,root=ROOT,
      objective='Complete the authorized six-bot warm Harbor test after verified admission holds; identical frozen audit fixture and partial memories; check both Claude slots before each wave and retain any quota wait in elapsed time')
    assert packet['operating_context']['sha256']=='2a5bf163a138151fad053933a27638b927923d4e6f9da1b14f11f296871e023c'
    parent=Path(packet['run']);manifest=read(parent/'run.json')
    manifest['authorization']=read(ORIGINAL/'run.json')['authorization'];write(parent/'run.json',manifest)
    link=dict(original_parent=ORIGINAL.name,clean_parent=COLD_PARENT.name,prior_warm_parent=PRIOR.name,
      failed_warm=failed.name,repeat_warm=True,final_warm=True,
      approval='User answered: Use 10% temporarily for this test',
      note='Same frozen task and six workers; fresh condition after prior holds. Cold threshold 20, warm 10. Pair admission checks and any waiting are included in wall time; operational differences confound comparison.')
    write(parent/'review/source-link.json',link);write(PRIOR/'review/final-warm-link.json',link)
    result=FinalWarm(parent);result.initialize();print(str(parent),flush=True)
    return result

if __name__=='__main__':
    e=start_final();policy=ROOT/'runtime/policy.json'
    with file_lock(ROOT/'runtime/policy.lock'):
        current=read(policy);assert current['worker_start_threshold_pct']==20
        write(e.parent/'review/temporary-threshold.json',dict(approved_at=now(),before=20,during=10,status='active',
          authorization='User explicitly approved: Use 10% temporarily for this test',reservation_policy='All reservations preserved'))
        current['worker_start_threshold_pct']=10;write(policy,current)
    try:
        assert admission_pair()['allowed'],'Both slots must be available before seeding and inference'
        e.seed();e.trial('team-warm')
    finally:
        with file_lock(ROOT/'runtime/policy.lock'):
            current=read(policy);assert current['worker_start_threshold_pct']==10
            current['worker_start_threshold_pct']=20;write(policy,current)
            receipt=read(e.parent/'review/temporary-threshold.json')
            receipt.update(status='restored',restored_at=now(),restored_threshold=20)
            write(e.parent/'review/temporary-threshold.json',receipt)
            print('Worker-start threshold restored to 20 percent.',flush=True)
