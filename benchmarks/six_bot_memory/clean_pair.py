"""Fresh scored pair reusing the completed six-author/auditor evidence exactly."""
import argparse
import hashlib
import shutil
from pathlib import Path
import sys
from prepare import ROOT,read,write,now,refresh,initialize_assignments,declaration,QUERY
from experiment_runs import ExperimentRuns,publish_condition
from orchestration_lifecycle import start_run
sys.path.insert(0,str(Path(__file__).resolve().parent))
import run_experiment as base
from transport import Transport

ORIGINAL=ROOT/'.orchestration/six-bot-medium-memory-20260913T170721Z-8ded4d8a'

class WaveTransport(Transport):
    def checkpoint(self,label,memory=False):
        if label.startswith(('team-cold','team-warm')):
            refresh(self.run)
        return super().checkpoint(label,memory)

class FreshPair(base.Experiment):
    def __init__(self,parent):
        self.parent=Path(parent).resolve(strict=True)
        self.source=base.Experiment(ORIGINAL)
        self.source.initialize()
        self.group=ExperimentRuns(self.parent,'six_bot_memory',[
            dict(id=i,label=label,memory_mode=mode,roster=declaration()) for i,label,mode in (
              ('team-cold','Six bots / memory disabled / clean pair','disabled'),
              ('team-warm','Six bots / audited Brain hints / clean pair','seeded'))])
        for r in self.group.data['conditions']:initialize_assignments(self.group.child(r['id']))
        self.artifacts=self.parent/'audited-artifacts'
        self.fixture=self.parent/'fixture'
        self.lock=self.parent/'review/experiment-lock.json'
        self.seeds=self.parent/'review/audit-memory-seeds.json'
        if not self.artifacts.exists():shutil.copytree(self.source.artifacts,self.artifacts)
        if not self.fixture.exists():shutil.copytree(self.source.fixture,self.fixture)
        if not self.seeds.exists():write(self.seeds,read(self.source.seeds))
        base.Transport=WaveTransport

    def fingerprints(self):
        result=super().fingerprints()
        file=Path(__file__).resolve()
        result[str(file.relative_to(ROOT))]=hashlib.sha256(file.read_bytes()).hexdigest()
        return result

    def initialize(self):
        self.source.initialize()
        if self.lock.exists():
            if read(self.lock)['sha256']!=self.fingerprints():raise ValueError('Clean-pair freeze changed')
            return
        for original,copy in ((self.source.fixture,self.fixture),(self.source.artifacts,self.artifacts)):
            old={str(p.relative_to(original)):hashlib.sha256(p.read_bytes()).hexdigest() for p in original.rglob('*') if p.is_file()}
            new={str(p.relative_to(copy)):hashlib.sha256(p.read_bytes()).hexdigest() for p in copy.rglob('*') if p.is_file()}
            if old!=new:raise ValueError('Audited inputs differ from original')
        if read(self.seeds)!=read(self.source.seeds):raise ValueError('Audit memories changed')
        write(self.lock,dict(created_at=now(),sha256=self.fingerprints(),roster=declaration(),order=['team-cold','team-warm'],
          source_parent=ORIGINAL.name,source_freeze=read(self.source.lock),repetitions=1,
          limits=['One fixed-order cold/warm pair; provider cache/load and time order are not randomized.',
            'Synthetic six-module code repair through a simulated file interface; not a design/document benchmark.',
            'Creation/audit are measured in the original parent; root controller usage is unknown and excluded.',
            'Live allowance reads precede each wave in both conditions and are included in elapsed time.',
            'Original interrupted cold attempt remains separate with its usage; no resumed timing is compared.']))

def start_clean():
    original=base.Experiment(ORIGINAL);original.initialize()
    failed=original.group.child('team-cold')
    calls=[read(p) for p in (failed/'calls').glob('*/record.json')]
    assert len(calls)==12 and sum(r['status']=='succeeded' for r in calls)==10
    held=[r for r in calls if r['status']=='held']
    assert len(held)==2 and all(r['provider']=='Anthropic' for r in held)
    evidence=[]
    for r in held:
        canonical=read(failed/'calls'/r['call_id']/'result.json')
        assert canonical['reservation_id'] is None and not canonical['quota_before']['allowed']
        evidence.append(dict(call_id=r['call_id'],job_id=r['job_id'],quota_before=canonical['quota_before'],provider_calls=0))
    write(failed/'review/hold-reconciliation.json',dict(at=now(),status='failed_before_completion',
      completed_calls=10,held_before_provider=evidence,reason='Claude allowance read minus preserved estimates crossed the existing start threshold. No timed result; fresh pair uses identical audited inputs and live quota reads before each wave.'))
    for r in held:
        r.update(provider_calls=0,preflight_only=True)
        write(failed/'calls'/r['call_id']/'record.json',r)
    active=read(original.group.active)
    assert active['status']=='uncertain' and active['run_id']==failed.name
    active.update(status='completed',reconciled_at=now());write(original.group.active,active)
    publish_condition(failed,status='failed',stage='reconciled_incomplete',ended_at=now())
    publish_condition(original.group.child('team-warm'),status='skipped',stage='replaced_by_fresh_pair',ended_at=now())
    data=read(ORIGINAL/'experiment.json');data['status']='completed_with_failed_condition';write(ORIGINAL/'experiment.json',data)
    packet=start_run(workspace=ROOT,name='six-bot-memory-clean-pair',
      objective='Fresh six-bot cold/warm comparison using the existing completed Harbor six-author cross-audit; preserve interrupted attempt and refresh actual allowances before each wave',
      project='orchestration-benchmark',no_memory=True,root=ROOT)
    assert packet['operating_context']['sha256']=='2a5bf163a138151fad053933a27638b927923d4e6f9da1b14f11f296871e023c'
    parent=Path(packet['run'])
    manifest=read(parent/'run.json')
    manifest.update(authorization=read(ORIGINAL/'run.json')['authorization'],display_name='Six bots - audited medium memory comparison (clean pair)')
    write(parent/'run.json',manifest)
    link=dict(original_parent=ORIGINAL.name,clean_parent=parent.name,failed_cold_run=failed.name,
      creation_run=original.group.child('authoring').name,audit_run=original.group.child('audit').name,
      reason='Fresh scored comparison after a verified quota hold; all original evidence and usage retained.')
    write(parent/'review/source-link.json',link);write(ORIGINAL/'review/clean-pair-link.json',link)
    e=FreshPair(parent);e.initialize()
    print(str(parent),flush=True)
    return e

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--run',type=Path);p.add_argument('--start',action='store_true')
    args=p.parse_args();e=start_clean() if args.start else FreshPair(args.run)
    e.initialize();e.trial('team-cold');e.seed();e.trial('team-warm')
