"""Run the explicitly authorized, small supplied-code benchmark. No production writes.

Existing completed calls are reused; running/uncertain calls are never retried.
The grader stays local. No hidden-case results enter implementation or audit prompts.
"""
import argparse
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import time

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
sys.path.insert(0, str(ROOT/'app'))
from coordinator_handoff import Coordinator
from orchestration_lifecycle import start_run
from quota_codex import find_codex
from usage_guard import Guard
from experiment_runs import ExperimentRuns, publish_condition

ORDER=[('small','solo',1),('small','team',1),('medium','team',1),('medium','solo',1),
       ('small','team',2),('small','solo',2),('medium','solo',2),('medium','team',2)]


def now():
    return datetime.now(timezone.utc).isoformat()


def read(path):
    return json.loads(path.read_text(encoding='utf-8-sig'))


def write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary=path.with_suffix(path.suffix+'.tmp')
    temporary.write_text(json.dumps(value,indent=2,ensure_ascii=False)+'\n',encoding='utf-8')
    temporary.replace(path)


class Pilot:
    def __init__(self, run):
        self.run=Path(run).resolve(strict=True)
        conditions=[]
        for task,condition,repetition in ORDER:
            roster=[dict(id='implementer',provider='OpenAI',role='implementation_and_revision',model='gpt-6-astra',
                         effort='low' if task=='small' else 'medium')]
            if condition=='team':
                roster.append(dict(id='claude-auditor',provider='Anthropic',role='independent_audit',model='opus'))
                if task=='medium':roster.append(dict(id='grok-auditor',provider='xAI',role='independent_audit',model='configured grok-4.6'))
            conditions.append(dict(id=f'{task}-{condition}-r{repetition}',
                label=f'{task.title()} / {condition} / repetition {repetition}',memory_mode='disabled',roster=roster))
        self.group=ExperimentRuns(self.run,'solo_vs_team',conditions,root=ROOT)
        fingerprints={str(path.relative_to(HERE)):hashlib.sha256(path.read_bytes()).hexdigest()
            for path in [HERE/'grader.py',HERE/'run_pilot.py',*(HERE/'fixtures').glob('*.py'),*(HERE/'contracts').glob('*.md')]}
        lock=self.run/'review/benchmark-lock.json'
        if lock.exists() and read(lock)['sha256']!=fingerprints:
            raise ValueError('Frozen experiment inputs changed; use a fresh parent')
        if not lock.exists():write(lock,dict(created_at=now(),sha256=fingerprints,order=ORDER,memory='disabled'))

    def attempt(self, task, condition, repetition):
        identifier=f'{task}-{condition}-r{repetition}'
        child=self.group.child(identifier)
        if self.group.status(identifier)=='completed':return read(child/'attempts'/identifier/'summary.json')
        with self.group.condition(identifier):
            return ConditionPilot(child).attempt(task,condition,repetition)


class ConditionPilot:
    def __init__(self, run):
        self.run=run.resolve(strict=True)
        self.condition=read(self.run/'condition.json')
        if self.condition.get('run_id')!=self.run.name or self.condition.get('family')!='solo_vs_team':
            raise ValueError('Calls require a fresh isolated condition')
        self.project=read(self.run/'run.json')['project_id']
        self.coordinator=Coordinator(self.run)
        state=self.coordinator.read()
        if state['owner']!='astra' or state['session']!=self.run.name:
            raise ValueError('Select the authorized current pilot owner/session.')
        self.session=state['session']
        self.guide=(ROOT/'app/assets/orchestration-context.md').read_text(encoding='utf-8')
        self.calls=self.run/'calls'
        self.empty=self.run/'empty-workspace'
        self.empty.mkdir(exist_ok=True)
        self.schema=self.run/'review/code-response-schema.json'
        write(self.schema,dict(type='object',properties={'code':{'type':'string'},'notes':{'type':'string'}},required=['code','notes'],additionalProperties=False))
        self.locked={str(path.relative_to(HERE)):hashlib.sha256(path.read_bytes()).hexdigest()
                     for path in [HERE/'grader.py',*(HERE/'fixtures').glob('*.py'),*(HERE/'contracts').glob('*.md')]}
        lock=self.run/'review/benchmark-lock.json'
        if lock.exists() and read(lock)['sha256']!=self.locked:
            raise ValueError('Frozen benchmark files changed. Preserve existing trials.')
        if not lock.exists():
            write(lock,{'created_at':now(),'sha256':self.locked,'repetitions':2,
                        'openai_requested_model':'gpt-6-astra','effort':{'small':'low','medium':'medium'},
                        'memory':'disabled for every contestant','repair_rounds':1,
                        'small_team':['OpenAI','Anthropic'],'medium_team':['OpenAI','Anthropic','xAI']})

    def persist(self, directory, record):
        record.update(run_id=self.run.name,condition_id=self.condition['id'],
            parent_run_id=self.condition['parent_run_id'],scope='contestant',helper_count=0,memory_enabled=False)
        roster=[member for member in self.condition['roster'] if member['provider']==record['provider']]
        if len(roster)!=1:raise ValueError('Call provider is outside the declared condition roster')
        record.update(actor_id=roster[0]['id'],role=roster[0]['role'],
                      stage='local_preflight' if record.get('preflight_only') else read(self.run/'condition.json')['stage'],
                      native_delegation_disabled=record['provider']=='OpenAI')
        write(directory/'record.json',record)
        publish_condition(self.run)

    def checkpoint(self, label):
        state=self.coordinator.read()
        checkpoint=state['checkpoint']
        checkpoint['objective']='Controlled compact small/medium solo versus mixed-provider repair-and-audit pilot'
        checkpoint['next_steps']=[label]
        checkpoint['constraints']=['Synthetic supplied-code only; no private project inputs.','Same frozen contract, seed and public feedback; hidden grades never fed back.','No tools in model calls; no model fallback, automatic uncertain retries, publishing, or production app changes.','No project recall; preserve trial artifacts and report incomplete usage.']
        checkpoint['authorization']=[{'providers':['OpenAI','Anthropic','xAI'],'scope':'User explicitly requested small two-provider and medium three-provider benchmark trials using compact synthetic files.'}]
        checkpoint['open_jobs']=[]
        for record_path in self.calls.glob('*/record.json'):
            record=read(record_path)
            if record.get('status') in ('running','uncertain'):
                checkpoint['open_jobs'].append({'id':record_path.parent.name,'status':record['status']})
        updated=self.coordinator.checkpoint(checkpoint,'astra',self.session,state['generation'])
        packet=start_run(run=self.run,owner='astra',session=self.session,generation=updated['generation'],no_memory=True)
        assert packet['operating_context']['context']==self.guide

    def base_prompt(self, task):
        contract=(HERE/'contracts'/f'{task}.md').read_text(encoding='utf-8')
        seed=(HERE/'fixtures'/f'{task}.py').read_text(encoding='utf-8')
        return ('# Objective\nRepair the supplied synthetic Python module.\n\n'
                '# Inputs\n'+contract+'\n\nSeed module:\n```python\n'+seed+'\n```\n\n'
                '# Execution rules\nThis is a controlled supplied-text benchmark. Do not use ANY tools, browse, read files, recall memory, delegate, or run commands. '
                'Do not start an orchestrator; the experimental controller already owns this run. Work only from this prompt. '
                'Allowed imports are collections, heapq, copy, functools, itertools, math and typing; imports are not required. '
                'Do not add I/O or import-time actions. Return the complete module.\n\n'
                '# Required output\nJSON object with code (complete Python module string) and notes (brief reasoning summary, no hidden reasoning).\n\n'
                '# Acceptance criteria\nMeet every stated contract, preserve signature, handle invalid inputs and input immutability. '\
                'No hidden evaluator is available. You will have one later audit-and-revision opportunity.\n')

    def native(self, identifier, task, prompt):
        if read(self.run/'condition.json')['status']!='running':raise RuntimeError('Condition must hold the experiment lease')
        directory=self.calls/identifier
        record_path=directory/'record.json'
        if record_path.exists():
            old=read(record_path)
            if old.get('status')=='succeeded':
                if old.get('base_prompt_sha256')!=hashlib.sha256(prompt.encode()).hexdigest():raise ValueError('Saved prompt identity changed')
                return old
            raise RuntimeError('Existing call requires explicit reconciliation: '+identifier)
        self.checkpoint('OpenAI benchmark call '+identifier)
        directory.mkdir(parents=True,exist_ok=True)
        complete_prompt=self.guide+'\n\nProject recall: explicitly disabled.\n\n'+prompt
        (directory/'prompt.md').write_text(complete_prompt,encoding='utf-8')
        guard=Guard()
        started=time.monotonic()
        admission=guard.check('codex','small' if task=='small' else 'medium',reserve=True,task='Benchmark '+identifier)
        if not admission['allowed']:
            self.persist(directory,dict(call_id=identifier,status='held',provider='OpenAI',admission=admission,started_at=now(),ended_at=now()))
            raise RuntimeError('Codex admission held '+identifier)
        token=admission['reservation_id']
        record=dict(call_id=identifier,status='running',provider='OpenAI',requested_model='gpt-6-astra',actual_models=[],
                    effort='low' if task=='small' else 'medium',started_at=now(),reservation_id=token,
                    prompt_sha256=hashlib.sha256(complete_prompt.encode()).hexdigest(),
                    base_prompt_sha256=hashlib.sha256(prompt.encode()).hexdigest(),input_tokens=None,output_tokens=None)
        self.persist(directory,record)
        argv=[find_codex(),'exec','--ignore-user-config','--ephemeral','--skip-git-repo-check',
              '--sandbox','read-only','--cd',str(self.empty),'--model','gpt-6-astra','--json','--color','never',
              '--output-schema',str(self.schema),'-c','model_reasoning_effort='+record['effort'],
              '-c','approval_policy="never"','-c','web_search="disabled"','-c','project_doc_max_bytes=0']
        for feature in ('shell_tool','unified_exec','multi_agent','apps','plugins','remote_plugin','memories','code_mode','code_mode_host'):
            argv += ['--disable',feature]
        argv += ['-']
        execution_start=time.monotonic()
        with (directory/'stdout.jsonl').open('wb') as stdout,(directory/'stderr.txt').open('wb') as stderr:
            try:
                process=subprocess.Popen(argv,stdin=subprocess.PIPE,stdout=stdout,stderr=stderr,
                                         cwd=self.empty,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
                record['pid']=process.pid;self.persist(directory,record)
                try:process.communicate(complete_prompt.encode(),timeout=450 if task=='small' else 900)
                except subprocess.TimeoutExpired:
                    process.terminate()
                    try:process.communicate(timeout=10)
                    except subprocess.TimeoutExpired:process.kill();process.communicate(timeout=10)
                    raise RuntimeError('Local deadline; remote completion unknown')
            except BaseException as exc:
                record.update(status='uncertain',failure_type=type(exc).__name__,ended_at=now(),elapsed_seconds=time.monotonic()-started)
                self.persist(directory,record)
                raise
        output=(directory/'stdout.jsonl').read_bytes();error=(directory/'stderr.txt').read_bytes()
        events=[]
        for line in output.decode('utf-8',errors='replace').splitlines():
            try: events.append(json.loads(line))
            except ValueError:pass
        complete=[e for e in events if e.get('type')=='turn.completed']
        messages=[e.get('item',{}).get('text','') for e in events if e.get('type')=='item.completed' and e.get('item',{}).get('type')=='agent_message']
        tool_events=[{'type':e.get('type'),'item_type':e.get('item',{}).get('type')} for e in events if e.get('type') in ('item.started','item.completed') and e.get('item',{}).get('type') not in ('agent_message','reasoning','error')]
        record['error_items']=[e['item'] for e in events if e.get('item',{}).get('type')=='error']
        # Persist terminal payload before classifying transport success. An adapter
        # failure must not discard a completed answer or force another model call.
        record['response']=messages[-1] if messages else None
        record['usage']=complete[-1].get('usage',{}) if complete else None
        record.update(exit_code=process.returncode,execution_seconds=time.monotonic()-execution_start,
                      elapsed_seconds=time.monotonic()-started,ended_at=now(),tool_events=tool_events,
                      stderr_sha256=hashlib.sha256(error).hexdigest(),stderr_bytes=len(error))
        self.persist(directory,record)
        if complete and process.returncode==0 and messages and not tool_events:
            record.update(status='succeeded',response=messages[-1],usage=complete[-1].get('usage',{}))
            usage=record['usage']
            for key in ('input_tokens','output_tokens','cached_input_tokens'):
                record[key]=usage.get(key)
            guard.finish(token,'completed',usage=usage)
        else:
            definite=any(e.get('type')=='turn.failed' for e in events)
            record.update(status='failed' if definite else 'uncertain',reason='No verified tool-free terminal completion',
                          safe_events=[{k:v for k,v in e.items() if k in ('type','usage')} for e in events])
            if definite:guard.finish(token,'failed')
        self.persist(directory,record)
        if record['status']!='succeeded':
            # Error text stays local and is printed only for operator diagnosis.
            print(error.decode('utf-8',errors='replace')[-2500:],flush=True)
            print(output.decode('utf-8',errors='replace')[-2500:],flush=True)
            raise RuntimeError('Native call did not complete '+identifier)
        print(json.dumps({'call':identifier,'provider':'OpenAI','seconds':round(record['elapsed_seconds'],2),'usage':record['usage']}),flush=True)
        return record

    def hosted(self, identifier, task, worker, prompt, revision_of=None):
        if read(self.run/'condition.json')['status']!='running':raise RuntimeError('Condition must hold the experiment lease')
        directory=self.calls/identifier;directory.mkdir(parents=True,exist_ok=True)
        record_path=directory/'record.json'
        if record_path.exists():
            old=read(record_path)
            if old.get('status')=='succeeded':
                if old.get('base_prompt_sha256')!=hashlib.sha256(prompt.encode()).hexdigest():raise ValueError('Saved prompt identity changed')
                return old
            raise RuntimeError('Existing hosted call requires reconciliation '+identifier)
        prompt_file=directory/'prompt.md';prompt_file.write_text(prompt,encoding='utf-8')
        started=time.monotonic()
        record=dict(call_id=identifier,status='running',provider={'claude':'Anthropic','grok':'xAI'}[worker],started_at=now(),
                    base_prompt_sha256=hashlib.sha256(prompt.encode()).hexdigest(),provider_calls=0,preflight_only=True,
                    dispatch_started=False,actual_models=[],usage=None)
        self.persist(directory,record)
        try:
            check=subprocess.run([sys.executable,str(ROOT/'orchestrator.py'),'brief-check',str(prompt_file),'--worker',worker],
                                 capture_output=True,cwd=ROOT,timeout=30)
        except (OSError,subprocess.TimeoutExpired) as exc:
            record.update(status='failed',failure_type='brief_preflight_'+type(exc).__name__,ended_at=now(),
                          elapsed_seconds=time.monotonic()-started,response='Local brief preflight did not complete. No provider request was started.')
            self.persist(directory,record)
            raise
        (directory/'preflight-stdout.txt').write_bytes(check.stdout)
        (directory/'preflight-stderr.txt').write_bytes(check.stderr)
        if check.returncode:
            record.update(status='failed',failure_type='brief_preflight',ended_at=now(),elapsed_seconds=time.monotonic()-started,
                          response='Local brief preflight rejected the assignment. No provider request was started.\n'+check.stdout.decode('utf-8',errors='replace'))
            self.persist(directory,record)
            raise ValueError('Audit brief failed local preflight '+identifier)
        argv=[sys.executable,str(ROOT/'orchestrator.py'),'run',worker,'--prompt-file',str(prompt_file),
              '--output',str(directory/'result.json'),'--run',str(self.run),'--project',self.project,
              '--assignment-id',self.run.name+'-'+identifier,'--task','Benchmark audit '+identifier,
              '--category','benchmark-review','--size','small','--no-memory','--no-auto-fallback','--require-brief-check']
        if worker=='claude':argv += ['--claude-model','opus','--claude-effort','low' if task=='small' else 'medium']
        if revision_of:argv += ['--revision-of',revision_of]
        record.update(preflight_only=False,provider_calls=None,dispatch_started=True)
        self.persist(directory,record)
        with (directory/'stdout.txt').open('wb') as stdout,(directory/'stderr.txt').open('wb') as stderr:
            try:process=subprocess.run(argv,cwd=ROOT,stdout=stdout,stderr=stderr,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
            except BaseException as exc:
                record.update(status='uncertain',failure_type=type(exc).__name__,ended_at=now(),elapsed_seconds=time.monotonic()-started)
                self.persist(directory,record)
                raise
        if not (directory/'result.json').exists():
            record.update(status='uncertain',exit_code=process.returncode,ended_at=now(),elapsed_seconds=time.monotonic()-started)
            self.persist(directory,record)
            raise RuntimeError('Hosted output unavailable '+identifier)
        result=read(directory/'result.json')
        models=list((result.get('modelUsage') or {}).keys())
        if result.get('model') and result['model'] not in models:models.append(result['model'])
        record=dict(call_id=identifier,status='succeeded' if result.get('execution_status')=='succeeded' else result.get('execution_status','uncertain'),
                    provider={'claude':'Anthropic','grok':'xAI'}[worker],job_id=result['job_id'],actual_models=models,
                    response=result.get('response'),usage=result.get('usage'),elapsed_seconds=time.monotonic()-started,
                    execution_seconds=(result.get('phase_durations_ms') or {}).get('execution',0)/1000 or None,
                    started_at=result.get('started_at'),ended_at=result.get('ended_at'),canonical_result=result.get('canonical_result'))
        record['base_prompt_sha256']=hashlib.sha256(prompt.encode()).hexdigest()
        self.persist(directory,record)
        print(json.dumps({'call':identifier,'provider':record['provider'],'seconds':round(record['elapsed_seconds'],2),'status':record['status']}),flush=True)
        if record['status']!='succeeded':raise RuntimeError('Hosted call incomplete '+identifier)
        return record

    def candidate(self, response, destination):
        text=response.strip()
        if text.startswith('```'):
            text='\n'.join(text.splitlines()[1:-1])
        data=json.loads(text)
        if not isinstance(data,dict) or not isinstance(data.get('code'),str):raise ValueError('Missing code output')
        if len(data['code'].encode())>48*1024:raise ValueError('Candidate exceeds compact-file bound')
        destination.write_text(data['code'],encoding='utf-8')

    def grade(self, task, path, public=False):
        argv=[sys.executable,'-I',str(HERE/'grader.py'),'--task',task,'--candidate',str(path)]
        if public:argv.append('--public-only')
        result=subprocess.run(argv,capture_output=True,timeout=12,cwd=self.empty)
        if result.returncode:raise RuntimeError('Grader execution failure')
        return json.loads(result.stdout)

    def attempt(self, task, condition, repetition):
        identifier=f'{task}-{condition}-r{repetition}'
        directory=self.run/'attempts'/identifier;directory.mkdir(parents=True,exist_ok=True)
        summary_path=directory/'summary.json'
        if summary_path.exists():return read(summary_path)
        start=time.monotonic();started_at=now()
        publish_condition(self.run,stage='initial_implementation')
        print(json.dumps({'attempt':identifier,'status':'started'}),flush=True)
        initial=self.native(identifier+'-initial',task,self.base_prompt(task))
        self.candidate(initial['response'],directory/'initial.py')
        public=self.grade(task,directory/'initial.py',True)
        first=self.grade(task,directory/'initial.py')
        write(directory/'initial-grade.json',first)
        write(directory/'public-feedback.json',public)
        audits=[]
        draft=(directory/'initial.py').read_text(encoding='utf-8')
        if condition=='team':
            publish_condition(self.run,stage='independent_audits')
            self.checkpoint('Independent benchmark audits '+identifier)
            workers=['claude'] if task=='small' else ['claude','grok']
            def audit(worker):
                scope=('Audit validation, immutability and invalid-input edge cases.' if worker=='grok'
                       else 'Audit all correctness and measurement rules.' if task=='small'
                       else 'Audit scheduling order, dependency cycles, budget accounting and blocked reasons.')
                prompt=('# Objective\nIndependently audit this synthetic code against the exact contract. '+scope+
                        '\n\n# Inputs\nThe exact contract, candidate module and public test results follow.\n\n'+(HERE/'contracts'/f'{task}.md').read_text(encoding='utf-8')+
                        '\n\nCandidate:\n```python\n'+draft+'\n```\n\nPublic test feedback:\n'+json.dumps(public)+
                        '\n\n# Required output\nAt most 600 words: concrete defects, counterexamples and suggested corrections, or an explicit no-defects verdict. '
                        'No tools, files, browsing, memory, new agents, or code execution. Do not assume omitted requirements.\n\n'
                        '# Acceptance criteria\nFind reproducible contract violations. Do not pad findings or rewrite correct behavior. Return review to the experiment controller.\n')
                return self.hosted(identifier+'-audit-'+worker,task,worker,prompt)
            with ThreadPoolExecutor(max_workers=len(workers)) as pool:audits=list(pool.map(audit,workers))
        revision=(self.base_prompt(task)+'\n# Audit and final revision\nReview this candidate against EVERY contract requirement, then return the final complete module. '
                  'This is the single allowed revision. Correct defects you can justify; preserve correct behavior.\n\nCandidate:\n```python\n'+draft+
                  '\n```\n\nPublic checks:\n'+json.dumps(public)+'\n')
        for number,audit in enumerate(audits,1):revision+='\nIndependent review '+str(number)+':\n'+audit['response']+'\n'
        publish_condition(self.run,stage='final_revision')
        final=self.native(identifier+'-final',task,revision)
        self.candidate(final['response'],directory/'final.py')
        last=self.grade(task,directory/'final.py')
        write(directory/'final-grade.json',last)
        reused=initial['ended_at'] < started_at
        call_path_seconds=initial['elapsed_seconds']+final['elapsed_seconds']+max([audit['elapsed_seconds'] for audit in audits] or [0])
        summary=dict(id=identifier,task=task,condition=condition,repetition=repetition,status='completed',started_at=started_at,ended_at=now(),
                     elapsed_seconds=time.monotonic()-start,initial_passed=first['passed'],final_passed=last['passed'],total=last['total'],
                     call_path_seconds=call_path_seconds,wall_time_comparable=not reused,
                     initial_all_passed=first['all_passed'],final_all_passed=last['all_passed'],
                     changed_by_revision=(directory/'initial.py').read_bytes()!=(directory/'final.py').read_bytes(),
                     calls=[initial['call_id'],*[audit['call_id'] for audit in audits],final['call_id']],
                     providers=['OpenAI',*[audit['provider'] for audit in audits]],
                     feedback_policy='Only frozen public tests and contract audits; no hidden grades disclosed')
        write(summary_path,summary)
        publish_condition(self.run,stage='results_published',summary_path=f'attempts/{identifier}/summary.json')
        print(json.dumps({'attempt':identifier,'status':'completed','initial':f"{first['passed']}/{first['total']}",'final':f"{last['passed']}/{last['total']}",'seconds':round(summary['elapsed_seconds'],2)}),flush=True)
        return summary


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--run',type=Path,required=True)
    parser.add_argument('--only',help='Optional exact attempt, e.g. small-solo-r1')
    args=parser.parse_args()
    pilot=Pilot(args.run)
    if args.only and args.only not in {f'{t}-{c}-r{r}' for t,c,r in ORDER}:parser.error('Unknown exact condition')
    for task,condition,repetition in ORDER:
        if args.only and args.only!=f'{task}-{condition}-r{repetition}':continue
        pilot.attempt(task,condition,repetition)
    print(json.dumps({'status':'requested_attempts_completed','run':pilot.run.name}),flush=True)


if __name__=='__main__':
    main()
