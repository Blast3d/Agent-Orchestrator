"""Cold-first real-provider repair pilot with controlled Brain memory injection."""
import argparse
from concurrent.futures import ThreadPoolExecutor
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import sys
import time

from transport import ROOT, Transport, now, read, write, parse_response, QUERY
from workspace import Workspace, check_submission
from brain_store import BrainStore
from memory_bundle import capture_run
from usage_guard import Guard
from experiment_runs import ExperimentRuns, publish_condition
from comparison_protocol import TRIALS, JEV_TRIALS, conditions, child_project, ranking_policy, validate_retrieval
from jev_openrouter import load_config

HERE=Path(__file__).resolve().parent


def freeze_hashes():
    paths=[HERE/name for name in ('fixture-contract.md','fixture-spec.json','memory-seeds.json','grader.py','run_experiment.py','transport.py','workspace.py','comparison_protocol.py')]
    paths += [p for p in (HERE/'fixture').rglob('*') if p.is_file() and '__pycache__' not in p.parts]
    return {p.relative_to(HERE).as_posix():hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(paths)}


def preflight(run, jev_comparison=False, identifier=None):
    """Read-only readiness check: no child creation, quota call, Brain search or seed."""
    parent=Path(run).resolve(strict=True)
    declarations=conditions(jev_comparison)
    if identifier and identifier not in [row['id'] for row in declarations]:
        raise ValueError('Unknown condition for selected suite')
    config=load_config(ROOT)
    result=dict(status='ready',provider_calls=0,read_only=True,run=parent.name,
                suite_mode='jev_comparison' if jev_comparison else 'four_condition',
                source_sha256=freeze_hashes(),conditions=[],errors=[])
    lock=parent/'review/experiment-lock.json'
    if lock.exists() and read(lock)['sha256']!=result['source_sha256']:
        result['errors'].append('Frozen experimental inputs changed')
    saved=read(parent/'experiment.json') if (parent/'experiment.json').exists() else None
    if saved and [row['id'] for row in saved['conditions']] != [row['id'] for row in declarations]:
        result['errors'].append('Saved suite identity/order differs from selected suite')
    for row in declarations:
        project=child_project(parent.name,row['id'])
        item=dict(row,project_id=project)
        if not identifier or row['id']==identifier:
            try:item['ranking_policy']=ranking_policy(row,project,config)
            except ValueError as exc:result['errors'].append(str(exc))
        result['conditions'].append(item)
    if result['errors']:result['status']='held'
    return result


class Experiment:
    def __init__(self, run, jev_comparison=False):
        self.parent=Path(run).resolve(strict=True)
        self.jev_comparison=jev_comparison
        self.trials=JEV_TRIALS if jev_comparison else TRIALS
        declarations=conditions(jev_comparison)
        self.group=ExperimentRuns(self.parent,'memory_control',declarations,root=ROOT)
        for row in declarations:
            child=self.group.child(row['id'])
            metadata=read(child/'condition.json')
            extra={key:row[key] for key in ('suite_mode','memory_ranking_mode')}
            if any(key in metadata and metadata[key]!=value for key,value in extra.items()):
                raise ValueError('Saved memory ranking protocol changed')
            if any(key not in metadata for key in extra):
                if list((child/'calls').glob('*/record.json')):
                    raise ValueError('Cannot add condition protocol after inference')
                publish_condition(child,**extra)
        self.run=self.parent
        self.transport=None
        self.spec=read(HERE/'fixture-spec.json')
        self.view=Workspace(HERE/'fixture')
        self.lock=self.parent/'review/experiment-lock.json'

    def initialize(self):
        fingerprints=freeze_hashes()
        if self.lock.exists():
            if read(self.lock)['sha256']!=fingerprints:raise ValueError('Frozen experimental inputs changed')
            if tuple(read(self.lock)['order'])!=self.trials:raise ValueError('Frozen condition order changed')
            return
        if any(list((self.group.child(name)/'calls').glob('*/record.json')) for name in self.trials):
            raise ValueError('Cannot freeze after inference')
        for row in self.group.data['conditions']:
            project=read(self.group.child(row['id'])/'run.json')['project_id']
            if BrainStore(ROOT).list_memories(project,limit=100):raise ValueError('New condition Brain must be empty')
        write(self.lock,{'created_at':now(),'sha256':fingerprints,'order':self.trials,
                        'suite_mode':'jev_comparison' if self.jev_comparison else 'four_condition',
                        'repetitions':1,'max_initial_turns':4,'max_actions_per_turn':6,'revision_turns':1,
                        'cold_first_required_by_user':True,'seed_scope_verified_empty':True,
                        'models':{'OpenAI':'gpt-6-astra / medium','Anthropic':'Opus / medium','xAI':'configured grok-4.6 / low'},
                        'limits':['Simulated file interface; real models and Brain.','One repetition; fixed-order and cache/load confounding.']})
        write(self.parent/'review/allowance-before.json',Guard().status())
        manifest=read(self.parent/'run.json')
        manifest.update(status='in_progress',display_name=('Brain + JEV matched comparison - cold first, six conditions'
                        if self.jev_comparison else 'Brain memory pilot - cold first, four conditions'),
                        authorization={'status':'authorized-by-current-request','provider_content_scopes':[
                            {'providers':['OpenAI','Anthropic','xAI'],'content':'Frozen synthetic Tern repository, tool results, candidate code, public feedback and prewritten partial Brain seeds.'}]},
                        source_snapshot='review/experiment-lock.json',providers=['OpenAI','Anthropic','xAI'],
                        requested_deliverables=[str(len(self.trials))+' scored conditions','Relevant partial memories in real Brain after cold phase','Correctness, file discovery, actual memory delivery, usage and time report'],
                        tasks=[{'id':name,'agent':'controlled-trial','status':'pending'} for name in self.trials],
                        final_artifacts=['results.html','results.md','results.json','review/experiment-lock.json'])
        write(self.parent/'run.json',manifest)

    def owned(self, role):
        roles=list(self.spec['roles']) if role=='all' else [role]
        return {self.spec['roles'][name]['path'] for name in roles}

    def prompt(self, actor, turn, revision=False):
        role=actor['role']
        roles=list(self.spec['roles']) if role=='all' else [role]
        goals='\n'.join(self.spec['roles'][name]['function']+': '+self.spec['roles'][name]['description'] for name in roles)
        prompt=('# Objective\nRepair the owned Tern project functions.\n\n# Inputs\n'
                'This is a compact simulated repository. The controller owns the exact orchestration run '+self.run.name+'.\n'
                'Your owned functions (only their implementation modules may be replaced):\n'+goals+
                '\n\nProject README:\n'+self.view.files['README.md']+
                '\n\n# Repository protocol\nUse NO native tools, commands, browsing, agents, plugins, or outside files. '
                'The controller supplies a uniform simulated file interface through your JSON response. '
                'Available actions: list (value = relative directory prefix, or empty for all fixture paths), '
                'search (value = case-insensitive literal text, up to 100 characters), read (value = exact relative POSIX file path). '
                'Up to six actions per response. Search returns at most 40 matching lines of at most 240 characters each. '
                'You may request any relevant fixture files; reference solutions and hidden tests are unavailable. '
                'Read the current owned implementation modules before submitting replacements. '
                'Project memories, when supplied, are evidence to verify against current files. '
                'Do not start another orchestrator or create memories.\n\n'
                '# Required output\nReturn ONLY JSON with exactly these fields: '
                'actions (array of {"op":"list|search|read","value":"..."}), '
                'files (array of {"path":"relative/module.py","content":"complete module source"}), '
                'memory_used (array of {"id":"an actually supplied memory ID","use":"specific way it informed your action"}), '
                'notes (brief text). For discovery use actions with empty files. For final delivery use files containing EVERY owned module exactly once and empty actions. '
                'Use an empty memory_used array if no supplied memory informed your work. Do not invent IDs or provide hidden reasoning.\n\n'
                '# Acceptance criteria\nMeet the complete discoverable public contracts, signatures and documented output rules. '
                'Standard library only; no I/O or import-time actions, no unowned file edits. '
                'At most four discovery/implementation turns followed by one public-feedback audit/revision turn. '
                'Do not assume unstated requirements.\n\n')
        prompt += 'Current step: '+('FINAL AUDIT/REVISION. Return every owned module; no further file actions.' if revision else
                                   f'Discovery/implementation turn {turn} of 4. '+('Submit your best complete owned modules now; no further file actions.' if turn==4 else 'You may inspect files or submit complete modules.'))+'\n'
        if actor['history']:
            prompt+='\n## Your prior conversation and controller file results\n'+json.dumps(actor['history'],ensure_ascii=False)+'\n'
        if revision:
            prompt+='\n## Public test feedback\n'+json.dumps(actor['public_feedback'])+'\nReview your implementation against its contract and correct justified defects; preserve correct behavior.\n'
        return prompt

    def grade(self, workspace, role='all', public=False):
        argv=[sys.executable,'-I',str(HERE/'grader.py'),'--workspace',str(workspace),'--role',role]
        if public:argv.append('--public-only')
        result=subprocess.run(argv,capture_output=True,timeout=20,cwd=self.transport.empty)
        try:return json.loads(result.stdout.decode('utf-8-sig'))
        except ValueError:raise RuntimeError('Grader did not return JSON: '+result.stderr.decode('utf-8',errors='replace')[:1200])

    def materialize(self, destination, overrides):
        if not destination.exists():
            shutil.copytree(HERE/'fixture',destination,ignore=shutil.ignore_patterns('__pycache__','*.pyc'))
        for relative,content in overrides.items():
            path=(destination/relative).resolve()
            if not path.is_relative_to(destination.resolve()):raise ValueError('Candidate output escaped workspace')
            path.write_text(content,encoding='utf-8')

    def check_memory(self, record, memory):
        validate_retrieval(read(self.run/'condition.json'),record)
        if memory:
            expected=set(read(self.run/'review/seed-insertion.json')['memory_outcome']['memory_ids'])
            if set(record['memory_ids'])!=expected:raise ValueError('Warm call did not receive the exact frozen memory set')
            if record['provider']!='OpenAI' and not record.get('memory_execution_requested'):
                raise ValueError('Hosted memory context was not bound to requested execution')
        elif record.get('memory_ids') or record.get('memory_context'):
            raise ValueError('Cold call received project memory')

    def trial(self, identifier):
        self.initialize()
        child=self.group.child(identifier)
        if self.group.status(identifier)=='completed':return read(child/'trials'/identifier/'summary.json')
        if identifier.endswith('warm') and any(self.group.status(cold)!='completed' for cold in self.trials[:2]):
            raise ValueError('Both cold conditions must finish before any warm inference')
        if identifier.endswith('warm') and not (child/'review/seed-insertion.json').exists():
            raise ValueError('Insert the prewritten Brain seeds before starting a warm condition')
        if self.jev_comparison:
            preceding=self.trials[:self.trials.index(identifier)]
            if any(self.group.status(name)!='completed' for name in preceding):
                raise ValueError('Complete earlier matched conditions in the frozen order')
        condition=read(child/'condition.json')
        ranking_policy(condition,condition['project_id'],load_config(ROOT))
        with self.group.condition(identifier):
            self.run=child;self.transport=Transport(child);self.project=self.transport.project
            write(child/'review/experiment-lock.json',read(self.lock))
            return self._trial(identifier)

    def _trial(self, identifier):
        memory=identifier.endswith('warm')
        folder=self.run/'trials'/identifier
        if (folder/'summary.json').exists():return read(folder/'summary.json')
        if memory and not (self.run/'review/seed-insertion.json').exists():raise ValueError('Warm trial requires seeded Brain')
        if not memory and BrainStore(ROOT).list_memories(self.project,limit=100):raise ValueError('Cold phase Brain must be empty')
        folder.mkdir(parents=True,exist_ok=True)
        started=time.monotonic();start_at=now()
        members=[('solo','all','OpenAI')] if identifier.startswith('solo') else [
            ('aggregation','aggregation','Anthropic'),('allocation','allocation','xAI'),('summary','summary','OpenAI')]
        actors={name:{'name':name,'role':role,'provider':provider,'history':[],'reads':[],'calls':[],
                      'operations':[],'initial_files':{},'final_files':{},'done':False,'errors':[]} for name,role,provider in members}
        wave_times=[]
        for turn in range(1,5):
            active=[actor for actor in actors.values() if not actor['done']]
            if not active:break
            publish_condition(self.run,stage=f'discovery_implementation_{turn}')
            self.transport.checkpoint(identifier+f' discovery/implementation wave {turn}',memory)
            def invoke(actor):
                return self.transport.call(identifier+'-'+actor['name']+f'-turn{turn}',actor['provider'],self.prompt(actor,turn),memory)
            with ThreadPoolExecutor(max_workers=len(active)) as pool:records=list(pool.map(invoke,active))
            wave_times.append(max(record['elapsed_seconds'] for record in records))
            for actor,record in zip(active,records):
                self.check_memory(record,memory)
                actor['calls'].append(record['call_id'])
                actor['history'].append({'speaker':'assistant','response':record['response']})
                try:
                    reply=parse_response(record['response'])
                    if any(note.get('id') not in record['memory_ids'] for note in reply['memory_used']):
                        actor['errors'].append('Unsupported claimed memory ID')
                    if reply['files']:
                        if reply['actions']:raise ValueError('Submit files or request actions, not both')
                        actor['initial_files']=check_submission(reply['files'],self.owned(actor['role']),set(actor['reads']))
                        actor['done']=True
                    elif turn<4:
                        results=self.view.actions(reply['actions'])
                        actor['operations'].extend(results)
                        actor['reads'].extend(r['path'] for r in results if r.get('ok') and r['action']['op']=='read')
                        actor['history'].append({'speaker':'controller','file_results':results})
                    else:
                        raise ValueError('Final discovery turn did not deliver code')
                except (ValueError,TypeError,KeyError) as exc:
                    actor['errors'].append(str(exc))
                    actor['history'].append({'speaker':'controller','protocol_error':str(exc)})
                write(folder/'actors'/actor['name']/'state.json',actor)
                publish_condition(self.run,stage=f'discovery_implementation_{turn}',last_actor=actor['name'])
        initial={path:content for actor in actors.values() for path,content in actor['initial_files'].items()}
        self.materialize(folder/'initial',initial)
        publish_condition(self.run,stage='initial_grading')
        first=self.grade(folder/'initial')
        write(folder/'initial-grade.json',first)
        for actor in actors.values():actor['public_feedback']=self.grade(folder/'initial',actor['role'],True)
        publish_condition(self.run,stage='audit_revision')
        self.transport.checkpoint(identifier+' public-feedback audit and revision',memory)
        def revise(actor):
            return self.transport.call(identifier+'-'+actor['name']+'-revision',actor['provider'],self.prompt(actor,5,True),memory)
        members=list(actors.values())
        with ThreadPoolExecutor(max_workers=len(members)) as pool:records=list(pool.map(revise,members))
        wave_times.append(max(record['elapsed_seconds'] for record in records))
        for actor,record in zip(members,records):
            self.check_memory(record,memory);actor['calls'].append(record['call_id'])
            actor['history'].append({'speaker':'assistant','response':record['response']})
            try:
                reply=parse_response(record['response'])
                if reply['actions']:raise ValueError('Revision must return complete files')
                if any(note.get('id') not in record['memory_ids'] for note in reply['memory_used']):
                    actor['errors'].append('Unsupported claimed memory ID in revision')
                actor['final_files']=check_submission(reply['files'],self.owned(actor['role']),set(actor['reads']))
            except (ValueError,TypeError,KeyError) as exc:
                actor['errors'].append('Revision: '+str(exc))
                actor['final_files']=actor['initial_files']
            write(folder/'actors'/actor['name']/'state.json',actor)
        final={path:content for actor in actors.values() for path,content in actor['final_files'].items()}
        self.materialize(folder/'final',final)
        publish_condition(self.run,stage='final_grading')
        last=self.grade(folder/'final');write(folder/'final-grade.json',last)
        condition=read(self.run/'condition.json')
        summary=dict(id=identifier,status='completed',team=identifier.startswith('team'),memory=memory,
                     suite_mode=condition.get('suite_mode'),
                     memory_ranking_mode=condition.get('memory_ranking_mode'),
                     started_at=start_at,ended_at=now(),wall_seconds=time.monotonic()-started,call_path_seconds=sum(wave_times),
                     initial_grade=first,final_grade=last,calls=[call for actor in actors.values() for call in actor['calls']],
                     actors=[{key:actor[key] for key in ('name','role','provider','calls','reads','operations','errors')} for actor in actors.values()],
                     complete_submission=set(final)==self.owned('all'))
        summary['wall_time_comparable']=all(read(self.run/'calls'/call/'record.json')['ended_at']>=start_at for call in summary['calls'])
        write(folder/'summary.json',summary)
        publish_condition(self.run,stage='results_published',summary_path=f'trials/{identifier}/summary.json')
        manifest=read(self.parent/'run.json')
        for task in manifest['tasks']:
            if task.get('id')==identifier:task.update(status='completed',child_run_id=self.run.name)
        write(self.parent/'run.json',manifest)
        print(json.dumps({'trial':identifier,'status':'completed','seconds':round(summary['wall_seconds'],2),
                          'initial_grade':first.get('passed'),'final_grade':last.get('passed'),'total':last.get('total')}),flush=True)
        return summary

    def seed(self):
        if any(self.group.status(trial)!='completed' for trial in self.trials[:2]):
            raise ValueError('Both cold trials must finish before Brain insertion')
        self.initialize()
        results={}
        for identifier in self.trials[2:]:
            self.run=self.group.child(identifier);self.transport=Transport(self.run);self.project=self.transport.project
            from usage_guard import file_lock
            with file_lock(self.run/'review/seed.lock'):
                results[identifier]=self._seed_condition()
        return results

    def _seed_condition(self):
        path=self.run/'review/seed-insertion.json'
        if path.exists():return read(path)
        if BrainStore(ROOT).list_memories(self.project,limit=100):raise ValueError('Seed scope is no longer empty')
        self.initialize()  # Validate frozen seed bytes, never adapt to cold answers.
        template=read(HERE/'memory-seeds.json')
        memories=template['memories']
        evidence={'fixture_sha256':read(self.lock)['sha256'],
                  'source_text':self.view.files,'seed_template':template,
                  'review':'Seeds were authored before cold inference; every fact is discoverable in the original public fixture. No contestant outputs or evaluator answers were used.'}
        write(self.run/'review/seed-source-evidence.json',evidence)
        write(self.run/'review/experiment-lock.json',read(self.lock))
        bundle={'capture_id':'prewritten-project-seeds','project_id':self.project,
                'evidence':['review/experiment-lock.json','review/seed-source-evidence.json'],
                'memories':[{key:value for key,value in row.items() if key in ('key','kind','title','content','tags','importance','episode')} for row in memories],
                'relations':[]}
        write(self.run/'review/seed-bundle.json',bundle)
        self.transport.checkpoint('Both cold trials complete; insert prewritten project knowledge',False)
        state=self.transport.coordinator.read()
        result=capture_run(ROOT,self.run.name,bundle,owner='astra',session=state['session'],generation=state['generation'],
                           reviewer='ASTRA',note='Verified prewritten partial memory facts against frozen fixture sources before inference; both no-memory trials have finished and no contestant answers enter these seeds.')
        if result['memory_outcome']['status']!='remembered':raise RuntimeError('Brain seed capture did not complete')
        write(path,dict(result,inserted_at=now()))
        publish_condition(self.run,stage='seeds_ready',seed_memory_ids=result['memory_outcome']['memory_ids'],
                          seed_source_sha256=hashlib.sha256((HERE/'memory-seeds.json').read_bytes()).hexdigest())
        print(json.dumps({'phase':'Brain seeded after both cold trials','memories':len(result['memory_outcome']['memory_ids'])}),flush=True)
        return result

    def run_all(self):
        self.initialize()
        for trial in self.trials[:2]:self.trial(trial)
        self.seed()
        write(self.parent/'review/codex-refresh-before-warm.json',Guard().refresh('codex'))
        for trial in self.trials[2:]:self.trial(trial)
        write(self.parent/'review/allowance-after.json',Guard().status())
        print(json.dumps({'status':'all_scored_conditions_completed','condition_count':len(self.trials),'run':self.parent.name}),flush=True)


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--run',type=Path,required=True)
    parser.add_argument('--phase',choices=['all','freeze','cold','seed','warm','ordinary','jev','trial','preflight'],default='all')
    parser.add_argument('--jev-comparison',action='store_true',help='Opt in to six isolated cold/ordinary/JEV conditions')
    parser.add_argument('--condition',help='Exact condition for --phase trial or a narrowed read-only preflight')
    args=parser.parse_args()
    selected=JEV_TRIALS if args.jev_comparison else TRIALS
    if args.condition and (args.condition not in selected or args.phase not in ('trial','preflight')):
        parser.error('--condition must select a declared condition with trial or preflight')
    if args.phase=='trial' and not args.condition:parser.error('--phase trial requires --condition')
    if args.phase=='jev' and not args.jev_comparison:parser.error('--phase jev requires --jev-comparison')
    if args.phase=='preflight':
        result=preflight(args.run,args.jev_comparison,args.condition)
        print(json.dumps(result,indent=2));return 0 if result['status']=='ready' else 2
    experiment=Experiment(args.run,args.jev_comparison)
    experiment.initialize()
    if args.phase=='all':experiment.run_all()
    elif args.phase=='cold':
        for trial in experiment.trials[:2]:experiment.trial(trial)
    elif args.phase=='seed':experiment.seed()
    elif args.phase=='warm':
        for trial in experiment.trials[2:]:experiment.trial(trial)
    elif args.phase=='ordinary':
        for trial in TRIALS[2:]:experiment.trial(trial)
    elif args.phase=='jev':
        for trial in JEV_TRIALS[4:]:experiment.trial(trial)
    elif args.phase=='trial':experiment.trial(args.condition)


if __name__=='__main__':sys.exit(main())
