"""Six fixed workers repair the same audited fixture cold-first, then with Brain hints."""
import argparse
from concurrent.futures import ThreadPoolExecutor
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import sys
import time
from prepare import ROOT, group, refresh, declaration, Transport, QUERY, MODULES, COMMON, read, write, now, parse_response, publish_condition
from workspace import Workspace, check_submission
from brain_store import BrainStore
from memory_bundle import capture_run
from audit import local_validation
HERE=Path(__file__).resolve().parent

class Experiment:
    def __init__(self,parent):
        self.parent=Path(parent).resolve(strict=True)
        self.group=group(self.parent)
        self.artifacts=self.group.child('audit')/'trials/audit/final'
        self.fixture=self.parent/'fixture'
        self.lock=self.parent/'review/experiment-lock.json'
        self.seeds=self.parent/'review/audit-memory-seeds.json'

    def fingerprints(self):
        paths=[HERE/p for p in ('spec.py','grader.py','run_experiment.py')]
        paths += [ROOT/'benchmarks/memory_control'/p for p in ('transport.py','workspace.py','grader.py')]
        paths += [ROOT/'app/six_bot_roster.py',self.seeds]
        paths += [p for folder in (self.fixture,self.artifacts) for p in folder.rglob('*') if p.is_file() and '__pycache__' not in p.parts]
        return {str(p.relative_to(ROOT)):hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(paths)}

    def initialize(self):
        if self.lock.exists():
            if read(self.lock)['sha256']!=self.fingerprints():raise ValueError('Frozen benchmark bytes changed')
            return
        if self.group.status('audit')!='completed':raise ValueError('Six audits must complete before freezing')
        validation=local_validation(self.artifacts)
        seeds=[]
        for role,(path,fn,contract) in MODULES.items():
            result=validation[role]
            if result['reference'].get('passed')!=40:raise ValueError('Reference not verified: '+role)
            failed=[c for c in result['seed']['roles'][0]['cases'] if not c['passed']]
            if sum(c['group']=='edge' for c in failed)<4:raise ValueError('Seed lacks four failing edge cases: '+role)
            if any(c['public'] and c['group']=='normal' for c in failed):raise ValueError('Seed fails ordinary public cases: '+role)
            audit=read(self.artifacts/role/'audit.json')
            hint=audit['partial_memory_hint']
            if len(hint)>280 or path not in hint or not audit.get('reference_checked') or not audit.get('all_40_cases_checked'):
                raise ValueError('Audit receipt/hint incomplete: '+role)
            seeds.append(dict(key='harbor-'+role+'-audit',kind='fact',title='Harbor '+role+' audit navigation',
              content=hint,tags=['Harbor','audit',role,'navigation'],importance=.65))
        write(self.seeds,{'source':'Same six bots cross-provider audit, frozen before cold','memories':seeds})
        self.fixture.mkdir(exist_ok=True)
        for role,(path,fn,contract) in MODULES.items():
            dest=self.fixture/path;dest.parent.mkdir(parents=True,exist_ok=True)
            dest.write_text((self.artifacts/role/'seed.py').read_text(encoding='utf-8'),encoding='utf-8')
            write(self.fixture/'tests'/('public-'+role+'.json'),[c for c in read(self.artifacts/role/'cases.json')['cases'] if c['public']])
        (self.fixture/'docs').mkdir(exist_ok=True)
        (self.fixture/'README.md').write_text('Harbor is a synthetic operations utility. Implementation is under harbor/, contracts and past maintenance notes under docs/, public examples under tests/. Six independent modules need repair. Locate your owned function and review the full contract. No imports or filesystem access inside implementations.\n',encoding='utf-8')
        (self.fixture/'docs/contracts.md').write_text(COMMON+'\n\n'+'\n\n'.join(fn+'\n'+contract for _,fn,contract in MODULES.values()),encoding='utf-8')
        (self.fixture/'docs/maintenance.md').write_text('\n\n'.join(s['content'] for s in seeds)+'\n',encoding='utf-8')
        Workspace(self.fixture,max_bytes=80000)
        for identifier in ('team-cold','team-warm'):
            child=self.group.child(identifier)
            if BrainStore(ROOT).list_memories(read(child/'run.json')['project_id'],limit=100):raise ValueError('Fresh scored Brain scope not empty')
        write(self.lock,dict(created_at=now(),sha256=self.fingerprints(),roster=declaration(),
          order=['authoring','audit','team-cold','team-warm'],max_initial_turns=4,revision_turns=1,repetitions=1,
          protocol='Fresh native sessions with supplied controller history only; same logical identities/models. All no-memory runs before audit-derived memory insertion. Only partial facts also discoverable in public docs. No helper agents.',
          limits=['Single fixed-order comparison; cache and provider-load effects are not randomized.',
                  'Synthetic medium code repair with simulated file reads; not a design or document benchmark.',
                  'Creation and audit usage separately measured; root controller usage remains unknown.']))

    def grade(self,workspace,role='all',public=False):
        argv=[sys.executable,'-I',str(HERE/'grader.py'),'--artifacts',str(self.artifacts),'--workspace',str(workspace),'--role',role]
        if public:argv.append('--public-only')
        result=subprocess.run(argv,capture_output=True,timeout=20)
        return json.loads(result.stdout.decode('utf-8-sig'))

    def prompt(self,actor,turn,revision=False):
        role=actor['role'];fn=MODULES[role][1]
        text=f"""# Objective
Repair your owned Harbor function {fn}, role {role}. You are {actor['name']}.
# Inputs
A synthetic repository accessible only through the controller's uniform file protocol.
Project README:
{self.view.files['README.md']}
Only implementation of your owned function and its same-module helpers may be replaced. Find its module and full discoverable contract. Other modules, hidden tests and reference solutions cannot be edited or accessed.
# Repository protocol
NO native tools, OS commands, browsing, memory lookup, helpers, agents or outside files.
Return list/search/read actions to the controller: list value is a relative directory prefix or empty; search value is a case-insensitive literal up to 100 characters; read value is an exact POSIX fixture file path. At most six actions per turn. Search returns at most 40 matching lines. Read your current owned implementation before replacing it.
Project memories if supplied are partial evidence to verify against current files. Do not create memories.
# Required output
Return only JSON with exactly actions (array of objects with op and value strings), files (array of objects with path and complete content strings), memory_used (array of objects with id and use strings), notes (brief string).
For discovery: actions and empty files. For submission: empty actions and exactly one full owned module. Use empty memory_used if no supplied memory informed an action; otherwise cite actual supplied IDs and specific use. Never invent IDs.
# Acceptance criteria
Follow full contract and exact signatures, outputs, validation and no-mutation rules. Builtins only; no imports/classes/decorators/async/I/O/private attributes/module-level calls. At most four discovery/implementation turns plus one public-feedback revision. Normal and edge checks are graded separately within this execution.
Current step: {'FINAL REVISION: return complete owned module, no file actions.' if revision else 'turn '+str(turn)+' of 4. '+('Submit now, no more file actions.' if turn==4 else 'Inspect relevant files or submit complete module.')}
"""
        if actor['history']:text+='\n# Current execution history and controller results\n'+json.dumps(actor['history'])+'\n'
        if revision:text+='\n# Public check feedback\n'+json.dumps(actor['public_feedback'])+'\nReview against contract and correct justified defects.'
        return text

    def materialize(self,destination,overrides):
        shutil.copytree(self.fixture,destination)
        for path,content in overrides.items():(destination/path).write_text(content,encoding='utf-8')

    def check_memory(self,record,memory):
        if memory:
            expected=set(read(self.run/'review/seed-insertion.json')['memory_outcome']['memory_ids'])
            # Search has a bounded result count; demand every call receive the same frozen six seeds.
            if set(record.get('memory_ids',[]))!=expected:raise ValueError('Exact six approved memories were not delivered')
            if record['provider']!='OpenAI' and not record.get('memory_execution_requested'):raise ValueError('Memory not bound to hosted execution')
        elif record.get('memory_ids') or record.get('memory_context'):raise ValueError('Cold memory contamination')

    def trial(self,identifier):
        self.initialize()
        self.run=self.group.child(identifier)
        if self.group.status(identifier)=='completed':return read(self.run/'trials'/identifier/'summary.json')
        memory=identifier=='team-warm'
        if memory and (self.group.status('team-cold')!='completed' or not (self.run/'review/seed-insertion.json').exists()):
            raise ValueError('Warm requires completed cold and approved seeds')
        project=read(self.run/'run.json')['project_id']
        if not memory and BrainStore(ROOT).list_memories(project,limit=100):raise ValueError('Cold Brain scope not empty')
        self.view=Workspace(self.fixture,max_bytes=80000)
        self.transports={r['id']:Transport(self.run,actor_id=r['id'],query=QUERY) for r in declaration()}
        refresh(self.run)
        with self.group.condition(identifier):
            started=now();clock=time.monotonic()
            folder=self.run/'trials'/identifier
            actors=[dict(name=r['id'],role=r['role'],provider=r['provider'],history=[],reads=[],calls=[],
              operations=[],initial_files={},final_files={},done=False,errors=[]) for r in declaration()]
            wave_times=[]
            for turn in range(1,5):
                active=[a for a in actors if not a['done']]
                if not active:break
                publish_condition(self.run,stage='discovery_implementation_'+str(turn))
                self.transports['openai_a'].checkpoint(identifier+' wave '+str(turn),memory)
                def invoke(a):
                    return self.transports[a['name']].call(identifier+'-'+a['name']+'-turn'+str(turn),a['provider'],self.prompt(a,turn),memory)
                with ThreadPoolExecutor(max_workers=len(active)) as pool:records=list(pool.map(invoke,active))
                wave_times.append(max(r['elapsed_seconds'] for r in records))
                for actor,record in zip(active,records):
                    self.check_memory(record,memory);actor['calls'].append(record['call_id'])
                    actor['history'].append({'speaker':'assistant','response':record['response']})
                    try:
                        reply=parse_response(record['response'])
                        if any(n['id'] not in record.get('memory_ids',[]) for n in reply['memory_used']):raise ValueError('Unsupported memory citation')
                        if reply['files']:
                            if reply['actions']:raise ValueError('Actions and files are mutually exclusive')
                            actor['initial_files']=check_submission(reply['files'],{MODULES[actor['role']][0]},set(actor['reads']))
                            actor['done']=True
                        elif turn<4:
                            result=self.view.actions(reply['actions']);actor['operations'].extend(result)
                            actor['reads'].extend(r['path'] for r in result if r.get('ok') and r['action']['op']=='read')
                            actor['history'].append({'speaker':'controller','file_results':result})
                        else:raise ValueError('Missing submission at turn limit')
                    except (ValueError,KeyError,TypeError) as exc:
                        actor['errors'].append(str(exc));actor['history'].append({'speaker':'controller','protocol_error':str(exc)})
                    write(folder/'actors'/actor['name']/'state.json',actor)
            initial={p:c for a in actors for p,c in a['initial_files'].items()}
            self.materialize(folder/'initial',initial)
            first=self.grade(folder/'initial');write(folder/'initial-grade.json',first)
            for a in actors:a['public_feedback']=self.grade(folder/'initial',a['role'],True)
            publish_condition(self.run,stage='public_feedback_revision')
            self.transports['openai_a'].checkpoint(identifier+' public-feedback revision',memory)
            def revise(a):
                return self.transports[a['name']].call(identifier+'-'+a['name']+'-revision',a['provider'],self.prompt(a,5,True),memory)
            with ThreadPoolExecutor(max_workers=6) as pool:records=list(pool.map(revise,actors))
            wave_times.append(max(r['elapsed_seconds'] for r in records))
            for actor,record in zip(actors,records):
                self.check_memory(record,memory);actor['calls'].append(record['call_id'])
                actor['history'].append({'speaker':'assistant','response':record['response']})
                try:
                    reply=parse_response(record['response'])
                    if reply['actions']:raise ValueError('No actions in final revision')
                    if any(n['id'] not in record.get('memory_ids',[]) for n in reply['memory_used']):raise ValueError('Unsupported memory citation')
                    actor['final_files']=check_submission(reply['files'],{MODULES[actor['role']][0]},set(actor['reads']))
                except (ValueError,KeyError,TypeError) as exc:
                    actor['errors'].append(str(exc));actor['final_files']=actor['initial_files']
                write(folder/'actors'/actor['name']/'state.json',actor)
            final={p:c for a in actors for p,c in a['final_files'].items()}
            self.materialize(folder/'final',final)
            last=self.grade(folder/'final');write(folder/'final-grade.json',last)
            summary=dict(id=identifier,status='completed',started_at=started,ended_at=now(),wall_seconds=time.monotonic()-clock,
              call_path_seconds=sum(wave_times),team=True,memory=memory,wall_time_comparable=True,
              calls=[c for a in actors for c in a['calls']],initial_grade=first,final_grade=last,
              actors=[{k:a[k] for k in ('name','role','provider','calls','reads','operations','errors')} for a in actors],
              complete_submission=len(final)==6)
            write(folder/'summary.json',summary)
            publish_condition(self.run,stage='results_published',summary_path='trials/'+identifier+'/summary.json')
            print(json.dumps({'trial':identifier,'seconds':summary['wall_seconds'],'passed':last['passed'],'total':last['total']}),flush=True)
            return summary

    def seed(self):
        self.initialize()
        if self.group.status('team-cold')!='completed':raise ValueError('Cold first')
        child=self.group.child('team-warm');path=child/'review/seed-insertion.json'
        if path.exists():return read(path)
        project=read(child/'run.json')['project_id']
        if BrainStore(ROOT).list_memories(project,limit=100):raise ValueError('Seed scope must be empty')
        write(child/'review/seed-source-evidence.json',dict(seeds=read(self.seeds),freeze=read(self.lock),
          auditor_receipts={r:read(self.artifacts/r/'audit.json') for r in MODULES},
          public_maintenance=(self.fixture/'docs/maintenance.md').read_text(encoding='utf-8')))
        transport=Transport(child,actor_id='openai_a',query=QUERY)
        transport.checkpoint('Cold complete; insert six partial audit facts frozen before cold',False)
        state=transport.coordinator.read()
        bundle=dict(capture_id='six-approved-audit-facts',project_id=project,evidence=['review/seed-source-evidence.json'],
          memories=read(self.seeds)['memories'],relations=[])
        result=capture_run(ROOT,child.name,bundle,owner='astra',session=state['session'],generation=state['generation'],
          reviewer='ASTRA',note='Verified six cross-provider audit facts against frozen contracts. Only partial navigation/pitfall memories also in public docs; no cold outputs or full answers.')
        if result['memory_outcome']['status']!='remembered':raise ValueError('Seed capture not remembered')
        write(path,dict(result,inserted_at=now()))
        recalled=BrainStore(ROOT).search(QUERY,project)
        if set(r['id'] for r in recalled['results'])!=set(result['memory_outcome']['memory_ids']):raise ValueError('Search did not return all six seeds')
        publish_condition(child,stage='seeds_ready',seed_memory_ids=result['memory_outcome']['memory_ids'])
        print(json.dumps({'Brain_seeds':len(result['memory_outcome']['memory_ids'])}),flush=True)

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--run',type=Path,required=True)
    p.add_argument('--phase',choices=['freeze','cold','seed','warm','all'],default='all')
    args=p.parse_args();e=Experiment(args.run)
    e.initialize()
    if args.phase in ('all','cold'):e.trial('team-cold')
    if args.phase in ('all','seed'):e.seed()
    if args.phase in ('all','warm'):e.trial('team-warm')
