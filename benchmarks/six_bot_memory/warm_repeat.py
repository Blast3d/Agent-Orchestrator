"""Fresh warm-only repeat after a verified pre-provider hold and user approval."""
import argparse
import hashlib
from pathlib import Path
import shutil
import sys
from clean_pair import FreshPair,WaveTransport,ORIGINAL,base,ROOT,read,write,now,refresh,initialize_assignments,declaration
from experiment_runs import ExperimentRuns,publish_condition
from orchestration_lifecycle import start_run
from usage_guard import file_lock

COLD_PARENT=ROOT/'.orchestration/six-bot-memory-clean-pair-20260913T174136Z-8149c962'

class WarmGroup(ExperimentRuns):
    def child(self,identifier):
        if identifier=='team-cold':
            return ROOT/'.orchestration/team-cold-20260913T174136Z-1686ad05'
        return super().child(identifier)

class FreshWarm(FreshPair):
    def __init__(self,parent):
        self.parent=Path(parent).resolve(strict=True)
        self.source=base.Experiment(ORIGINAL);self.source.initialize()
        self.cold_source=FreshPair(COLD_PARENT)
        self.group=WarmGroup(self.parent,'six_bot_memory',[dict(id='team-warm',
          label='Six bots / audited Brain hints / fresh warm repeat',memory_mode='seeded',roster=declaration())])
        initialize_assignments(self.group.child('team-warm'))
        self.artifacts=self.parent/'audited-artifacts';self.fixture=self.parent/'fixture'
        self.lock=self.parent/'review/experiment-lock.json';self.seeds=self.parent/'review/audit-memory-seeds.json'
        if not self.artifacts.exists():shutil.copytree(self.source.artifacts,self.artifacts)
        if not self.fixture.exists():shutil.copytree(self.source.fixture,self.fixture)
        if not self.seeds.exists():write(self.seeds,read(self.source.seeds))
        base.Transport=WaveTransport

    def fingerprints(self):
        result=super().fingerprints();file=Path(__file__).resolve()
        result[str(file.relative_to(ROOT))]=hashlib.sha256(file.read_bytes()).hexdigest()
        return result

def start_repeat():
    prior=FreshPair(COLD_PARENT);prior.initialize()
    failed=prior.group.child('team-warm')
    calls=[read(p) for p in (failed/'calls').glob('*/record.json')]
    assert len(calls)==6 and sum(r['status']=='succeeded' for r in calls)==4
    held=[r for r in calls if r['status']=='held']
    assert len(held)==2 and all(r['provider']=='Anthropic' for r in held)
    evidence=[]
    for r in held:
        canonical=read(failed/'calls'/r['call_id']/'result.json')
        assert canonical['reservation_id'] is None and not canonical['quota_before']['allowed']
        evidence.append(dict(call_id=r['call_id'],job_id=r['job_id'],quota_before=canonical['quota_before'],provider_calls=0))
        r.update(provider_calls=0,preflight_only=True);write(failed/'calls'/r['call_id']/'record.json',r)
    write(failed/'review/hold-reconciliation.json',dict(at=now(),status='failed_before_completion',completed_calls=4,
      held_before_provider=evidence,reason='Claude primary allowance parser rejected optional analytics error; cached allowance plus retained estimates held both workers. No complete warm result. Fresh warm uses same audited fixture and hints, same six workers, with explicit user approval for temporary 10 percent start threshold.'))
    active=read(prior.group.active);assert active['status']=='uncertain' and active['run_id']==failed.name
    active.update(status='completed',reconciled_at=now());write(prior.group.active,active)
    publish_condition(failed,status='failed',stage='reconciled_incomplete',ended_at=now())
    data=read(COLD_PARENT/'experiment.json');data['status']='completed_with_failed_condition';write(COLD_PARENT/'experiment.json',data)
    packet=start_run(workspace=ROOT,name='six-bot-audit-memory-warm-repeat',
      objective='Finish the same six-bot audited Harbor memory condition using fresh sessions; user approved temporary 10 percent worker-start threshold with reservations preserved and 20 percent restored afterward',
      project='orchestration-benchmark',no_memory=True,root=ROOT)
    assert packet['operating_context']['sha256']=='2a5bf163a138151fad053933a27638b927923d4e6f9da1b14f11f296871e023c'
    parent=Path(packet['run'])
    manifest=read(parent/'run.json');manifest['authorization']=read(ORIGINAL/'run.json')['authorization'];write(parent/'run.json',manifest)
    link=dict(original_parent=ORIGINAL.name,clean_parent=COLD_PARENT.name,warm_parent=parent.name,
      cold_run=prior.group.child('team-cold').name,failed_warm=failed.name,repeat_warm=True,
      approval='User answered: Use 10% temporarily for this test',
      note='Same frozen fixture, six logical roles/models, partial audit facts and wave protocol. Admission threshold differs: cold 20 percent; warm 10 percent. All prior calls remain separately recorded.')
    write(parent/'review/source-link.json',link);write(COLD_PARENT/'review/warm-repeat-link.json',link)
    result=FreshWarm(parent);result.initialize()
    print(str(parent),flush=True)
    return result

if __name__=='__main__':
    e=start_repeat()
    policy_path=ROOT/'runtime/policy.json'
    with file_lock(ROOT/'runtime/policy.lock'):
        before=read(policy_path);assert before['worker_start_threshold_pct']==20
        write(e.parent/'review/temporary-threshold.json',dict(approved_at=now(),before=20,during=10,
          authorization='User explicitly approved: Use 10% temporarily for this test',status='active',
          reservation_policy='Unchanged; all current and uncertain reservations preserved'))
        during=dict(before,worker_start_threshold_pct=10);write(policy_path,during)
    try:
        e.seed();e.trial('team-warm')
    finally:
        with file_lock(ROOT/'runtime/policy.lock'):
            current=read(policy_path)
            assert current['worker_start_threshold_pct']==10,'Policy changed externally; inspect before restoration'
            current['worker_start_threshold_pct']=20;write(policy_path,current)
            receipt=read(e.parent/'review/temporary-threshold.json');receipt.update(status='restored',restored_at=now(),restored_threshold=20)
            write(e.parent/'review/temporary-threshold.json',receipt)
            print('Worker-start threshold restored to 20 percent.',flush=True)
