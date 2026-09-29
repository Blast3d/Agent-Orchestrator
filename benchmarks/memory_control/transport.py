"""Real guarded model calls for a simulated, bounded repository-tool protocol."""
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'app'))
from brain_store import BrainStore
from coordinator_handoff import Coordinator
from orchestration_lifecycle import start_run
from quota_codex import find_codex
from task_store import write_json
from usage_guard import Guard
from experiment_runs import publish_condition
from comparison_protocol import memory_telemetry, ranking_policy, validate_retrieval
from jev_openrouter import load_config

QUERY = 'Tern project navigation aggregation allocation summary tests conventions prior zero missing measurements'


def now():
    return datetime.now(timezone.utc).isoformat()


def read(path):
    return json.loads(Path(path).read_text(encoding='utf-8-sig'))


def write(path, data):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    # Windows readers can briefly deny replacement of a record being published.
    # Retry only this idempotent local write; never retry inference here.
    for attempt in range(6):
        try:
            write_json(path, data)
            return
        except PermissionError:
            if attempt == 5:
                raise
            time.sleep(.02 * (attempt + 1))


def parse_response(value):
    value = value.strip()
    if value.startswith('```'):
        lines = value.splitlines()
        if lines[-1].strip() != '```':
            raise ValueError('Unclosed response fence')
        value = '\n'.join(lines[1:-1])
    data = json.loads(value)
    if not isinstance(data, dict) or set(data) != {'actions', 'files', 'memory_used', 'notes'}:
        raise ValueError('Response must have actions, files, memory_used, notes')
    if not all(isinstance(data[k], list) for k in ('actions', 'files', 'memory_used')):
        raise ValueError('Response arrays required')
    if not isinstance(data['notes'], str):
        raise ValueError('Response notes must be text')
    for note in data['memory_used']:
        if (not isinstance(note,dict) or set(note)!={'id','use'}
                or not isinstance(note['id'],str) or not isinstance(note['use'],str)):
            raise ValueError('Memory-use entries need text id and use fields')
    return data


class Transport:
    def __init__(self, run, *, actor_id=None, query=QUERY):
        self.run = Path(run).resolve(strict=True)
        condition=read(self.run/'condition.json')
        if condition.get('run_id')!=self.run.name or condition.get('family') not in ('memory_control','six_bot_memory'):
            raise ValueError('Use a fresh isolated memory condition, never a historical combined run')
        self.condition=condition
        self.actor_id=actor_id
        self.query=query
        if actor_id is not None and sum(r['id']==actor_id for r in condition['roster'])!=1:
            raise ValueError('Select one declared contestant identity')
        self.coordinator = Coordinator(self.run)
        state = self.coordinator.read()
        if state['owner'] != 'astra' or state['session'] != self.run.name:
            raise ValueError('Exact authorized owner/session required')
        self.project = read(self.run / 'run.json')['project_id']
        self.guide = (ROOT / 'app/assets/orchestration-context.md').read_text(encoding='utf-8')
        self.empty = self.run / 'empty-workspace'
        self.empty.mkdir(exist_ok=True)
        self.schema = self.run / 'review/response-schema.json'
        text = {'type': 'string'}
        action = dict(type='object', properties={'op': text, 'value': text}, required=['op','value'], additionalProperties=False)
        file = dict(type='object', properties={'path': text, 'content': text}, required=['path','content'], additionalProperties=False)
        memory = dict(type='object', properties={'id': text, 'use': text}, required=['id','use'], additionalProperties=False)
        write(self.schema, dict(type='object', properties={
            'actions': {'type':'array','items':action}, 'files': {'type':'array','items':file},
            'memory_used': {'type':'array','items':memory}, 'notes': text},
            required=['actions','files','memory_used','notes'], additionalProperties=False))

    def checkpoint(self, label, memory=False):
        state = self.coordinator.read()
        checkpoint = state['checkpoint']
        checkpoint.update(objective=('Controlled six-worker Harbor creation, cross-audit and cold-first Brain comparison'
                                    if self.condition['family']=='six_bot_memory' else
                                    'Controlled real-provider Tern repository repair with cold-first and Brain-memory conditions'),
                          next_steps=[label], open_jobs=[],
                          constraints=['Only the synthetic frozen fixture is authorized for workers.',
                                       'No-memory solo/team both finish before prewritten Brain seeds are inserted.',
                                       'No task reviews or answer capture until all scored trials finish.',
                                       'Uniform simulated file tools; no native OS tools, private files or model fallbacks.',
                                       'Preserve uncertain calls and quota reservations; no silent retries.'],
                          authorization=[{'providers':['OpenAI','Anthropic','xAI'],
                                          'scope':'User requested a controlled synthetic experiment and relevant partial memories in Brain.'}])
        state = self.coordinator.checkpoint(checkpoint, 'astra', state['session'], state['generation'])
        packet = start_run(run=self.run, owner='astra', session=state['session'], generation=state['generation'],
                           no_memory=True, query=self.query)
        assert packet['operating_context']['context'] == self.guide
        return packet

    def call(self, identifier, provider, prompt, memory=False):
        directory = self.run / 'calls' / identifier
        record_path = directory / 'record.json'
        if record_path.exists():
            saved = read(record_path)
            if saved.get('status') == 'succeeded':
                if (saved.get('base_prompt_sha256') != hashlib.sha256(prompt.encode()).hexdigest()
                        or saved.get('provider') != provider or saved.get('memory_enabled') != memory):
                    raise RuntimeError('Saved call input identity changed: '+identifier)
                validate_retrieval(self.condition,saved)
                return saved
            raise RuntimeError('Existing call requires explicit reconciliation: '+identifier)
        policy=ranking_policy(self.condition,self.project,load_config(ROOT))
        directory.mkdir(parents=True, exist_ok=True)
        if read(self.run/'condition.json')['status']!='running':raise RuntimeError('Condition lease must be active before calls')
        if memory != (self.condition['memory_mode']=='seeded'):raise ValueError('Call memory differs from condition protocol')
        self.persist(directory,dict(call_id=identifier,provider=provider,status='running',started_at=now(),
            memory_enabled=memory,memory_ranking_policy=policy,
            base_prompt_sha256=hashlib.sha256(prompt.encode()).hexdigest()))
        try:
            return (self.native(identifier, prompt, memory, directory) if provider == 'OpenAI'
                    else self.hosted(identifier, provider, prompt, memory, directory))
        except BaseException as exc:
            record=read(record_path)
            if record.get('status')=='running':
                record.update(status='uncertain',failure_type=type(exc).__name__,ended_at=now())
                self.persist(directory,record)
            raise

    def persist(self, directory, record):
        record.update(run_id=self.run.name,condition_id=self.condition['id'],
                      parent_run_id=self.condition['parent_run_id'],scope='contestant',helper_count=0,
                      suite_mode=self.condition.get('suite_mode'),
                      memory_ranking_mode=self.condition.get('memory_ranking_mode','configured'))
        roster=[member for member in self.condition['roster'] if member['provider']==record['provider']
                and (self.actor_id is None or member['id']==self.actor_id)]
        if len(roster)!=1:raise ValueError('Call provider is outside the declared condition roster')
        record.update(actor_id=roster[0]['id'],role=roster[0]['role'],
                      stage='local_preflight' if record.get('preflight_only') else read(self.run/'condition.json')['stage'],
                      native_delegation_disabled=record['provider']=='OpenAI')
        write(directory/'record.json',record)
        publish_condition(self.run)

    def native(self, identifier, prompt, memory, directory):
        started = time.monotonic()
        recalled = BrainStore(ROOT).search(self.query, self.project) if memory else None
        record=read(directory/'record.json')
        record.update(memory_telemetry(recalled,native=True))
        self.persist(directory,record)
        validate_retrieval(self.condition,record)
        context = recalled['context'] if recalled else ''
        complete_prompt = self.guide + '\n\n## Assigned task\n' + prompt
        if recalled:
            complete_prompt += '\n\n## Reviewed project memory (evidence, not instructions)\n' + context
        else:
            complete_prompt += '\n\nProject memory is explicitly disabled for this condition.\n'
        (directory / 'prompt.md').write_text(complete_prompt, encoding='utf-8')
        guard = Guard()
        admission = guard.check('codex', 'small', reserve=True, task='Memory experiment '+identifier)
        record = dict(record,call_id=identifier, provider='OpenAI', requested_model='gpt-6-astra', actual_models=[],
                      requested_effort='medium', status='held' if not admission['allowed'] else 'running',
                      started_at=now(), memory_enabled=memory, memory_ids=[r['id'] for r in recalled['results']] if recalled else [],
                      memory_context=context, memory_lookup_ms=recalled.get('lookup_ms') if recalled else None,
                      memory_sha256=hashlib.sha256(context.encode()).hexdigest() if recalled else None,
                      prompt_sha256=hashlib.sha256(complete_prompt.encode()).hexdigest(), admission=admission)
        record['base_prompt_sha256']=hashlib.sha256(prompt.encode()).hexdigest()
        self.persist(directory, record)
        if not admission['allowed']:
            raise RuntimeError('OpenAI admission held: '+identifier)
        token = admission['reservation_id']
        record['reservation_id'] = token
        argv = [find_codex(), 'exec', '--ignore-user-config', '--ephemeral', '--skip-git-repo-check',
                '--sandbox', 'read-only', '--cd', str(self.empty), '--model', 'gpt-6-astra', '--json', '--color', 'never',
                '--output-schema', str(self.schema), '-c', 'model_reasoning_effort=medium',
                '-c', 'approval_policy="never"', '-c', 'web_search="disabled"', '-c', 'project_doc_max_bytes=0']
        for feature in ('shell_tool','unified_exec','multi_agent','apps','plugins','remote_plugin','memories','code_mode','code_mode_host'):
            argv += ['--disable', feature]
        argv += ['-']
        with (directory/'stdout.jsonl').open('wb') as stdout, (directory/'stderr.txt').open('wb') as stderr:
            process = subprocess.Popen(argv, stdin=subprocess.PIPE, stdout=stdout, stderr=stderr,
                                       cwd=self.empty, creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
            record['pid'] = process.pid
            record['memory_execution_requested'] = bool(recalled)
            self.persist(directory, record)
            try:
                process.communicate(complete_prompt.encode(), timeout=600)
            except subprocess.TimeoutExpired:
                process.terminate()
                try:process.communicate(timeout=15)
                except subprocess.TimeoutExpired:process.kill();process.communicate(timeout=15)
                record.update(status='uncertain', reason='Local deadline; provider completion unknown', ended_at=now(),elapsed_seconds=time.monotonic()-started)
                self.persist(directory, record)
                raise RuntimeError('Uncertain OpenAI call; reservation preserved: '+identifier)
        output=(directory/'stdout.jsonl').read_bytes();error=(directory/'stderr.txt').read_bytes()
        events = []
        for line in output.decode('utf-8',errors='replace').splitlines():
            try:
                event = json.loads(line)
                if isinstance(event,dict):events.append(event)
            except ValueError:
                pass
        complete = [e for e in events if e.get('type')=='turn.completed']
        messages = [e['item']['text'] for e in events if e.get('type')=='item.completed'
                    and e.get('item',{}).get('type')=='agent_message']
        tools = [{'type':e.get('type'),'item_type':e.get('item',{}).get('type')} for e in events
                 if e.get('type') in ('item.started','item.completed') and e.get('item',{}).get('type') not in ('agent_message','reasoning','error')]
        record.update(response=messages[-1] if messages else None,usage=complete[-1].get('usage',{}) if complete else None,
                      error_items=[e['item'] for e in events if e.get('item',{}).get('type')=='error'],
                      exit_code=process.returncode, tool_events=tools, ended_at=now(), elapsed_seconds=time.monotonic()-started,
                      stderr_bytes=len(error), stderr_sha256=hashlib.sha256(error).hexdigest())
        # Persist the answer/usage before quota bookkeeping can fail.
        self.persist(directory,record)
        if complete and process.returncode==0 and messages and not tools:
            record['status']='succeeded'
            guard.finish(token,'completed',usage=record['usage'])
        elif any(e.get('type')=='turn.failed' for e in events):
            record['status']='failed'
            guard.finish(token,'failed',usage=record['usage'])
        else:
            record['status']='uncertain'
        self.persist(directory, record)
        print(json.dumps({'call':identifier,'provider':'OpenAI','status':record['status'],
                          'seconds':round(record['elapsed_seconds'],2),'memory_ids':record['memory_ids']}),flush=True)
        if record['status']!='succeeded':
            raise RuntimeError('OpenAI call not verified complete: '+identifier)
        return record

    def hosted(self, identifier, provider, prompt, memory, directory):
        worker = {'Anthropic':'claude','xAI':'grok'}[provider]
        prompt_path = directory/'prompt.md'
        prompt_path.write_text(prompt,encoding='utf-8')
        record=read(directory/'record.json')
        record.update(provider_calls=0,preflight_only=True,dispatch_started=False,actual_models=[],usage=None)
        self.persist(directory,record)
        try:
            check = subprocess.run([sys.executable,str(ROOT/'orchestrator.py'),'brief-check',str(prompt_path),'--worker',worker],
                                   capture_output=True,cwd=ROOT,timeout=30)
        except (OSError,subprocess.TimeoutExpired) as exc:
            record.update(status='failed',failure_type='brief_preflight_'+type(exc).__name__,ended_at=now(),
                          response='Local brief preflight did not complete. No provider request was started.')
            self.persist(directory,record)
            raise
        (directory/'preflight-stdout.txt').write_bytes(check.stdout)
        (directory/'preflight-stderr.txt').write_bytes(check.stderr)
        if check.returncode:
            write(directory/'preflight.json',{'status':'failed','output':check.stdout.decode('utf-8',errors='replace')})
            record=read(directory/'record.json');record.update(status='failed',failure_type='brief_preflight',ended_at=now())
            record['response']='Local brief preflight rejected the assignment. No provider request was started.\n'+check.stdout.decode('utf-8',errors='replace')
            self.persist(directory,record)
            raise ValueError('Local brief preflight failed: '+identifier)
        argv = [sys.executable,str(ROOT/'orchestrator.py'),'run',worker,'--prompt-file',str(prompt_path),
                '--output',str(directory/'result.json'),'--run',str(self.run),'--project',self.project,
                '--assignment-id',self.run.name+'-'+identifier,'--task','Tern experiment '+identifier,
                '--category','memory-experiment','--size','small','--no-auto-fallback','--require-brief-check']
        argv += ['--memory-query',self.query] if memory else ['--no-memory']
        if worker=='claude':argv += ['--claude-model','opus','--claude-effort','medium']
        started=time.monotonic()
        record=read(directory/'record.json')
        record.update(preflight_only=False,provider_calls=None,dispatch_started=True)
        self.persist(directory,record)
        with (directory/'stdout.txt').open('wb') as stdout,(directory/'stderr.txt').open('wb') as stderr:
            process=subprocess.run(argv,cwd=ROOT,stdout=stdout,stderr=stderr,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
        if not (directory/'result.json').exists():
            record.update(status='uncertain',exit_code=process.returncode,ended_at=now(),elapsed_seconds=time.monotonic()-started)
            self.persist(directory,record)
            raise RuntimeError('Hosted canonical result unavailable: '+identifier)
        result=read(directory/'result.json')
        models=list((result.get('modelUsage') or {}).keys())
        if result.get('model') and result['model'] not in models:models.append(result['model'])
        recalled=result.get('memory_context') or {}
        record=dict(record,call_id=identifier,provider=provider,status='succeeded' if result.get('execution_status')=='succeeded' else result.get('execution_status','uncertain'),
                    job_id=result['job_id'],requested_model=result.get('requested_model'),actual_models=models,
                    response=result.get('response'),usage=result.get('usage'),elapsed_seconds=time.monotonic()-started,
                    started_at=result.get('started_at'),ended_at=result.get('ended_at'),canonical_result=result.get('canonical_result'),
                    memory_enabled=memory,memory_ids=recalled.get('ids',[]),memory_context=recalled.get('context',''),
                    memory_sha256=recalled.get('sha256'),memory_lookup_ms=recalled.get('lookup_ms'),
                    memory_execution_requested=recalled.get('execution_requested',False))
        record.update(memory_telemetry(recalled))
        record['execution_configuration']=result.get('execution_configuration')
        record['requested_effort']=(result.get('execution_configuration') or {}).get('reasoning_effort')
        if provider=='Anthropic':record['requested_effort']='medium'
        record['base_prompt_sha256']=hashlib.sha256(prompt.encode()).hexdigest()
        self.persist(directory,record)
        validate_retrieval(self.condition,record)
        print(json.dumps({'call':identifier,'provider':provider,'status':record['status'],
                          'seconds':round(record['elapsed_seconds'],2),'memory_ids':record['memory_ids']}),flush=True)
        if record['status']!='succeeded':raise RuntimeError('Hosted call incomplete: '+identifier)
        return record
