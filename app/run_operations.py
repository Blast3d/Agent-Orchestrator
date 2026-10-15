"""Scoped local review and planning adapters. Operator state is not closeout credit."""
from datetime import datetime, timezone
from contextlib import closing
import hashlib
import json
from pathlib import Path
import re
import sqlite3

from brain_store import BrainStore, digest, safe_path, text
from contribution_tasks import task_ledger
from coordinator_handoff import Coordinator
from memory_bundle import validate_bundle
from provider_policy import coverage
from task_store import TaskStore, write_json
from usage_guard import file_lock


def read_object(path, maximum=8 * 1024**2):
    with Path(path).open('rb') as stream:
        raw = stream.read(maximum + 1)
    if len(raw) > maximum:
        raise ValueError('Saved operator evidence exceeds its bound')
    value = json.loads(raw)
    if not isinstance(value, dict):
        raise ValueError('Saved operator evidence must be an object')
    return value


def recovery_checklist(task):
    uncertain = (task.get('execution_status') == 'uncertain' or task.get('status') == 'recovery_required'
                 or task.get('reservation_state') == 'held_for_reconciliation')
    if not uncertain:
        return None
    pid = task.get('process_pid')
    progress = task.get('execution_progress') if isinstance(task.get('execution_progress'),dict) else {}
    return {'process_pid': pid if type(pid) is int and pid > 0 else None,
            'process_status': progress.get('process_status') or task.get('process_status') or 'unknown',
            'local_process_terminated': task.get('local_process_terminated') if type(task.get('local_process_terminated')) is bool else None,
            'saved_progress': {key:progress[key] for key in ('state','process_status','elapsed_s','timeout_seconds','terminal_received','output_truncated') if key in progress},
            'reservation_state': task.get('reservation_state', 'unknown'),
            'provider_execution': 'unknown', 'reservation_release_authorized': False,
            'checks': ['Inspect the original task and saved process/termination evidence.',
                       'Confirm provider execution has stopped independently of the last progress snapshot.',
                       'Reconcile the original reservation with the lead before a deliberate new attempt.'],
            'termination': {key: task[key] for key in ('termination_verified', 'termination_status', 'process_returncode')
                            if key in task},
            'note': 'Saved PID and local termination do not establish whether remote provider work stopped.'}


class SourceValidator(BrainStore):
    """Use maintained candidate/source validation without opening or migrating Brain."""
    def __init__(self, root):
        self.root = Path(root).resolve()


class RunOperations:
    def __init__(self, root, *, voice_path=None):
        self.root = Path(root).resolve()
        if voice_path is None:
            from voice_gateway import home
            voice_path = home() / 'voice-jobs.sqlite'
        self.voice_path = Path(voice_path)

    def run(self, identifier):
        if not isinstance(identifier, str) or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_-]{0,159}', identifier):
            raise ValueError('Choose one exact saved run')
        run = safe_path(self.root, self.root / '.orchestration' / identifier)
        return run

    def identity(self, body):
        if (not isinstance(body, dict) or type(body.get('generation')) is not int
                or body['generation'] < 1 or not isinstance(body.get('owner'), str)
                or not isinstance(body.get('session'), str)):
            raise ValueError('Provide the current owner, session and integer generation')
        run = self.run(body.get('run_id'))
        coordinator = Coordinator(run)
        state = coordinator.read()
        coordinator.require_owner(state, body.get('owner'), body.get('session'), body.get('generation'))
        return coordinator, state, read_object(safe_path(run, run / 'run.json'))

    def jobs(self, run):
        from orchestration_lifecycle import _discover
        return _discover(self.root, run, read_object(safe_path(run, run / 'run.json')))

    def canonical(self, run, job):
        if job not in self.jobs(run):
            raise ValueError('Task does not belong to this exact run')
        path = safe_path(self.root, self.root / 'runs/tasks' / job / 'result.json')
        value = read_object(path)
        if value.get('job_id') != job:
            raise ValueError('Canonical task identity does not match')
        return path, value

    def coverage(self, identifier):
        run = self.run(identifier)
        state = Coordinator(run).read()
        ledger_path = safe_path(run, run / 'contributions-ledger.json')
        ledger = read_object(ledger_path, 2 * 1024**2) if ledger_path.exists() else {}
        if ledger and ledger.get('scope_id') != identifier:
            raise ValueError('Contribution ledger belongs to a different run')
        manifest = read_object(safe_path(run, run / 'run.json'))
        project = manifest.get('project_id') or identifier
        hosted, native, notices = {}, False, []
        from orchestration_lifecycle import _native_capture
        for job in self.jobs(run):
            try:
                _, row = self.canonical(run, job)
                if row.get('assignment_project_id') != project:
                    raise ValueError('Canonical task belongs to a different project')
                if not (row.get('status') == row.get('review_status') == 'accepted'
                        and row.get('execution_status') == 'succeeded' and row.get('finalized_at')):
                    continue
                if row.get('imported_completed_artifact') is True:
                    receipt = read_object(safe_path(self.root, self.root / 'runs/tasks' / job / 'memory-outcome.json'), 96 * 1024)
                    fields = ('job_id','assignment_project_id','response','review','finalized_at')
                    if (receipt.get('status') != 'remembered' or receipt.get('job_id') != job
                            or receipt.get('project_id') != row.get('assignment_project_id')
                            or receipt.get('source_sha256') != digest({k:row.get(k) for k in fields})):
                        raise ValueError('Native capture source receipt is unavailable or changed')
                    native = _native_capture(self.root, run, row, receipt) or native
                else:
                    observed = task_ledger(row)
                    actor = next(x for x in observed['contributors'] if x['id'] == 'worker:' + row['worker'])
                    events = [x for x in observed['activity'] if x['kind'] == 'delegation' and x['agent_id'] == actor['id']]
                    if len(events) == 1:
                        hosted[job] = {'actor':actor, 'event':events[0], 'synthetic':row.get('synthetic_input') is True or row.get('count_as_build_worker') is False}
            except (OSError, ValueError, KeyError, TypeError) as error:
                notices.append({'job_id':job, 'reason':str(error) if isinstance(error,ValueError) else type(error).__name__})
        result = coverage(state['owner'], ledger, hosted, native_capture=native)
        return dict(result, notices=notices, provider_calls=0, preview_only=True,
                    note='Canonical accepted work and allocation evidence only. Full closeout separately checks all reviews, captures, source proofs and audits.')

    def checklist(self, identifier):
        run = self.run(identifier)
        path = safe_path(run, run / 'acceptance-checklist.json')
        result = read_object(path, 64 * 1024) if path.exists() else {'schema_version':1,'run_id':identifier,'items':[]}
        for item in result['items']:
            if item['state'] == 'verified':
                try:
                    if self.proof(run, item['evidence']) != item['proof']:
                        item.update(state='pending', evidence_notice='Evidence changed after operator verification')
                except (OSError, ValueError):
                    item.update(state='pending', evidence_notice='Evidence is unavailable')
        return result

    def proof(self, run, references):
        if not isinstance(references,list) or len(references)>12:
            raise ValueError('Use at most twelve local evidence references')
        rows=[]
        for relative in references:
            if not isinstance(relative,str) or not relative or Path(relative).is_absolute() or '..' in Path(relative).parts:
                raise ValueError('Evidence must be a relative file within this run')
            path=safe_path(run,run/relative)
            with path.open('rb') as stream: raw=stream.read(2*1024**2+1)
            if len(raw)>2*1024**2:raise ValueError('Evidence file exceeds 2 MiB')
            rows.append({'path':relative,'sha256':hashlib.sha256(raw).hexdigest()})
        return rows

    def save_checklist(self, body):
        coordinator,_,_=self.identity(body)
        with file_lock(coordinator.lock):
            self.identity(body)
            items=body.get('items')
            if not isinstance(items,list) or len(items)>100:raise ValueError('Use a checklist with at most 100 items')
            saved=[];ids=set()
            for rank,item in enumerate(items,1):
                if not isinstance(item,dict):raise ValueError('Checklist items must be objects')
                identifier=item.get('id');state=item.get('state')
                if not isinstance(identifier,str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,60}',identifier) or identifier in ids:
                    raise ValueError('Checklist IDs must be short and unique')
                ids.add(identifier)
                if state not in ('pending','working','verified'):raise ValueError('Choose pending, working or verified')
                evidence=item.get('evidence',[])
                proof=self.proof(coordinator.run,evidence)
                if state=='verified' and not proof:raise ValueError('Verification requires existing local evidence')
                saved.append({'id':identifier,'rank':rank,'title':text(item.get('title'),'Checklist title',160),
                              'state':state,'evidence':evidence,'proof':proof})
            result={'schema_version':1,'run_id':coordinator.run.name,'items':saved,'updated_at':datetime.now(timezone.utc).isoformat(),
                    'note':'Operator progress only; no closeout, memory or family credit inferred.'}
            if len(json.dumps(result).encode())>64*1024:raise ValueError('Checklist exceeds 64 KiB')
            write_json(safe_path(coordinator.run,coordinator.run/'acceptance-checklist.json'),result)
            return result

    def review_task(self, body):
        coordinator,_,manifest=self.identity(body)
        with file_lock(coordinator.lock):
            self.identity(body)
            path,row=self.canonical(coordinator.run,body.get('job_id'))
            if hashlib.sha256(path.read_bytes()).hexdigest()!=body.get('source_sha256'):
                raise ValueError('Task source changed; reload before reviewing')
            project=manifest.get('project_id') or coordinator.run.name
            if row.get('assignment_project_id')!=project:raise ValueError('Task project does not match this run')
            payload=body.get('curated_payload')
            if not isinstance(payload,dict):raise ValueError('Provide a supported curated candidate or bundle')
            if payload.get('project_id',project)!=project or payload.get('user_id','local')!='local':
                raise ValueError('Curated knowledge must preserve the exact project and local user')
            validator=SourceValidator(self.root)
            rows,links=validate_bundle(payload,project) if 'memories' in payload else ([payload],[])
            for candidate in rows:
                validator.candidate(dict(candidate,project_id=project,source={'type':'task','job_id':row['job_id']}))
            for link in links:
                for endpoint in ('from','to'):
                    if link[endpoint] not in {r['key'] for r in rows}:
                        self.validate_memory_endpoint(link[endpoint],project)
            return TaskStore(self.root/'runs/tasks').review(row['job_id'],'accepted',
                text(body.get('reviewer'),'Reviewer',100),text(body.get('note'),'Review note',1000,20),
                curated_payload=payload,expected_source_sha256=body.get('source_sha256'))

    def validate_memory_endpoint(self, identifier, project):
        db=safe_path(self.root,self.root/'runtime/brain/memory.sqlite')
        with closing(sqlite3.connect(db.as_uri()+'?mode=ro',uri=True)) as con:
            con.row_factory=sqlite3.Row
            row=con.execute('SELECT * FROM memories WHERE id=?',(identifier,)).fetchone()
            if row is None or row['project_id']!=project or row['user_id']!='local' or row['status']!='active':
                raise ValueError('Relation endpoint is not a current scoped approved memory')
            now=datetime.now(timezone.utc).isoformat()
            if row['valid_from']>now or row['valid_to'] and row['valid_to']<=now:raise ValueError('Relation endpoint is outside validity')
            if SourceValidator(self.root)._source(json.loads(row['source']),project,approved=True)[1]!=row['source_hash']:
                raise ValueError('Relation endpoint source changed')

    def voice_requests(self, identifier):
        manifest=read_object(safe_path(self.run(identifier),self.run(identifier)/'run.json'))
        project=manifest.get('project_id') or identifier
        attachments=self.voice_attachments(identifier,project)['requests']
        if not self.voice_path.is_file():return {'available':False,'requests':[],'attachments':attachments,'note':'No local voice journal is available'}
        if self.voice_path.is_symlink() or getattr(self.voice_path,'is_junction',lambda:False)():raise ValueError('Voice journal is redirected')
        with closing(sqlite3.connect(self.voice_path.resolve().as_uri()+'?mode=ro',uri=True)) as con:
            con.row_factory=sqlite3.Row
            rows=con.execute('SELECT id,project_id,status,conversation_id,created_at FROM requests WHERE project_id=? ORDER BY created_at DESC LIMIT 100',(project,)).fetchall()
        return {'available':True,'project_id':project,'requests':[dict(request_id=r['id'],project_id=r['project_id'],status=r['status'],conversation_id=r['conversation_id'],created_at=r['created_at']) for r in rows], 'attachments':attachments,'bounded':100}

    def voice_attachments(self, identifier, project):
        run=self.run(identifier)
        path=safe_path(run,run/'voice-attachments.json')
        result=read_object(path,64*1024) if path.exists() else {'schema_version':1,'run_id':identifier,'requests':[]}
        rows=result.get('requests')
        if (result.get('run_id')!=identifier or not isinstance(rows,list) or len(rows)>100
                or any(not isinstance(row,dict) or row.get('project_id')!=project for row in rows)):
            raise ValueError('Saved voice attachments do not match this exact run project')
        return result

    def attach_voice(self, body):
        coordinator,_,manifest=self.identity(body)
        with file_lock(coordinator.lock):
            self.identity(body)
            project=manifest.get('project_id') or coordinator.run.name
            if body.get('project_id')!=project:raise ValueError('Voice request must belong to the exact run project')
            row=next((r for r in self.voice_requests(coordinator.run.name)['requests'] if r['request_id']==body.get('request_id')),None)
            if row is None:raise ValueError('Choose an existing scoped voice request ID')
            path=safe_path(coordinator.run,coordinator.run/'voice-attachments.json')
            result=self.voice_attachments(coordinator.run.name,project)
            if row['request_id'] not in {r['request_id'] for r in result['requests']}:
                if len(result['requests'])>=100:raise ValueError('Run attachment limit reached')
                result['requests'].append(row)
                write_json(path,result)
            return result

    def detail(self, identifier):
        run=self.run(identifier)
        tasks=[]
        for job in self.jobs(run):
            try:
                path,row=self.canonical(run,job)
                from dispatch_invocations import invocation_history
                answer=row.get('response') if isinstance(row.get('response'),str) else ''
                tasks.append({'job_id':job,'title':row.get('task'),'status':row.get('status'),'project_id':row.get('assignment_project_id'),
                              'source_sha256':hashlib.sha256(path.read_bytes()).hexdigest(),'recovery':recovery_checklist(row),
                              'answer_preview':answer[:12000],'answer_truncated':len(answer)>12000,
                              'invocations':invocation_history(TaskStore(self.root/'runs/tasks'),job)})
            except (OSError,ValueError):continue
        return {'coverage':self.coverage(identifier),'checklist':self.checklist(identifier),'tasks':tasks,
                'voice':self.voice_requests(identifier),'project_id':read_object(run/'run.json').get('project_id') or identifier}
