"""Capture reviewed atomic knowledge and explicit links without extraction models."""
import json
import hashlib
from pathlib import Path
import re
import uuid

from brain_store import BrainStore, RELATIONS, digest, now, safe_path, scope, text
from storage_budget import StorageLimitError
from task_store import TaskStore, timestamp, write_json
from usage_guard import file_lock

MAX_BYTES = 96 * 1024


def read_json(path, root, maximum=MAX_BYTES):
    path = safe_path(root, path)
    with path.open('rb') as stream:
        raw = stream.read(maximum + 1)
    if len(raw) > maximum:
        raise ValueError('Reviewed memory input exceeds its size limit')
    return json.loads(raw.decode('utf-8-sig'))


def validate_bundle(bundle, project):
    if not isinstance(bundle, dict) or len(json.dumps(bundle, allow_nan=False).encode()) > MAX_BYTES:
        raise ValueError('Use a bounded JSON memory bundle')
    if bundle.get('project_id', project) != project:
        raise ValueError('Bundle must belong to the exact source project')
    rows, links = bundle.get('memories'), bundle.get('relations', [])
    if not isinstance(rows, list) or not 1 <= len(rows) <= 24:
        raise ValueError('A reviewed bundle needs 1 to 24 atomic memories')
    if not isinstance(links, list) or len(links) > 64:
        raise ValueError('A reviewed bundle supports at most 64 explicit relationships')
    keys = set()
    for row in rows:
        key = row.get('key') if isinstance(row, dict) else None
        if not isinstance(key, str) or not re.fullmatch(r'[a-z][a-z0-9_-]{0,59}', key) or key in keys:
            raise ValueError('Every atomic memory needs a unique short key')
        if row.get('project_id', project) != project or row.get('user_id', 'local') != 'local':
            raise ValueError('Atomic memories must keep the exact project and local user')
        keys.add(key)
    for link in links:
        if not isinstance(link, dict) or link.get('relation') not in RELATIONS:
            raise ValueError('Use a supported explicit relationship')
        text(link.get('reason'), 'Relationship evidence', 500, 10)
        for key in ('from', 'to'):
            value = link.get(key)
            if not isinstance(value, str) or (value not in keys and not re.fullmatch(r'[a-f0-9]{32}', value)):
                raise ValueError('Relationship endpoints must be bundle keys or existing memory IDs')
        if link['from'] == link['to']:
            raise ValueError('A relationship needs two different memories')
        if link['from'] not in keys and link['to'] not in keys:
            raise ValueError('Each reviewed relationship must include a new bundle memory')
    return rows, links


def _insert(brain, result, bundle):
    project = scope(result['assignment_project_id'])
    rows, links = validate_bundle(bundle, project)
    review = result['review']
    reviewer = text(review.get('reviewer'), 'Reviewer', 100)
    note = text(review.get('note'), 'Review note', 1000, 20)
    source = {'type': 'task', 'job_id': result['job_id'], 'review_sha256': digest(review)}
    fields = []
    for row in rows:
        payload = dict(row, project_id=project, user_id='local', source=source)
        payload.setdefault('valid_from', review['reviewed_at'])
        fields.append((row['key'], brain.candidate(payload)))
    ids, relations, explanations = {}, [], []
    with brain._write() as con:
        # Recheck authoritative acceptance while applying the single transaction.
        for key, item in fields:
            _, proof = brain._source(item['source'], project, approved=True)
            if proof != item['source_hash']:
                raise ValueError('Reviewed task changed while capturing knowledge')
            if con.execute('SELECT count(*) FROM memories').fetchone()[0] >= brain.limits['max_memories']:
                con.execute("DELETE FROM memories WHERE status='deleted'")
                if con.execute('SELECT count(*) FROM memories').fetchone()[0] >= brain.limits['max_memories']:
                    raise StorageLimitError('Memory record limit reached; inspect storage before adding knowledge')
            identifier = uuid.uuid4().hex
            con.execute('''INSERT INTO memories
              (id,project_id,user_id,kind,title,content,tags,importance,status,created_at,
               valid_from,valid_to,source,source_hash,episode,fingerprint,reviewer,review_note,reviewed_at)
              VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)''',
              (identifier,project,'local',item['kind'],item['title'],item['content'],json.dumps(item['tags']),
               item['importance'],'active',now(),item['valid_from'],item['valid_to'],json.dumps(source),proof,
               json.dumps(item['episode']),item['fingerprint'],reviewer,note,review['reviewed_at']))
            stored = brain._row(con, identifier)
            con.execute('INSERT INTO memory_fts(rowid,title,content,tags) VALUES(?,?,?,?)',
                        (stored['rowid'],item['title'],item['content']+' '+' '.join(item['episode'].values()),' '.join(item['tags'])))
            brain._change(con,stored,'approved')
            ids[key] = identifier
        for link in links:
            left, right = ids.get(link['from'],link['from']), ids.get(link['to'],link['to'])
            endpoints = [brain._row(con, identifier) for identifier in (left,right)]
            if any(row['project_id'] != project or row['user_id'] != 'local' for row in endpoints):
                raise ValueError('Relationships cannot cross project or user scopes')
            if not all(brain._current(row, now(), {}) for row in endpoints):
                raise ValueError('Relationships require currently valid approved memories')
            existing = con.execute('''SELECT id FROM relations WHERE source_id=? AND target_id=? AND relation=?
              AND valid_from<=? AND (valid_to IS NULL OR valid_to>?)''',(left,right,link['relation'],now(),now())).fetchone()
            if existing:
                relations.append(existing['id'])
                explanations.append({'id':existing['id'],'source_id':left,'target_id':right,
                                     'relation':link['relation'],'reason':link['reason']})
                continue
            if con.execute('SELECT count(*) FROM relations').fetchone()[0] >= brain.limits['max_relations']:
                raise StorageLimitError('Relationship limit reached')
            identifier = uuid.uuid4().hex
            con.execute('INSERT INTO relations VALUES(?,?,?,?,?,?,?)',
                        (identifier,left,right,link['relation'],now(),None,reviewer))
            brain._change(con,endpoints[0],'linked')
            relations.append(identifier)
            explanations.append({'id':identifier,'source_id':left,'target_id':right,
                                 'relation':link['relation'],'reason':link['reason']})
        brain._drop_export()
    return ids, relations, explanations


def record_accepted_bundle(task_store, job_id, bundle):
    """One atomic write with durable retry fencing; never resurrect forgotten nodes."""
    directory = task_store.directory(job_id)
    root = Path(task_store.root).absolute().parent.parent.resolve()
    directory = safe_path(root, directory)
    if Path(task_store.root).name != 'tasks' or Path(task_store.root).parent.name != 'runs':
        raise ValueError('Reviewed capture requires the canonical task store')
    receipt_path = directory / 'memory-outcome.json'
    with file_lock(directory / 'memory.lock'):
        result = read_json(directory / 'result.json', root, 8 * 1024**2)
        if not (isinstance(result,dict) and result.get('job_id') == job_id and result.get('status') == result.get('review_status') == 'accepted'
                and result.get('execution_status') == 'succeeded' and result.get('finalized_at')):
            raise ValueError('Memory capture requires a finalized accepted canonical review')
        project = scope(result.get('assignment_project_id'))
        validate_bundle(bundle, project)
        request_hash = digest(bundle)
        source_hash = digest({k:result.get(k) for k in ('job_id','assignment_project_id','response','review','finalized_at')})
        old = read_json(receipt_path, root) if receipt_path.exists() else None
        if receipt_path.exists():
            if (not isinstance(old,dict) or old.get('schema_version') != 1
                    or old.get('status') not in ('remembered','writing','error')
                    or old.get('project_id') != project
                    or old.get('mode') != 'curated_bundle' or old.get('job_id') != job_id
                    or old.get('request_sha256') != request_hash or old.get('source_sha256') != source_hash):
                raise ValueError('Capture source or bundle changed; preserve its original receipt')
            if old.get('status') == 'remembered':
                identifiers, keys = old.get('memory_ids'), old.get('memory_keys')
                if (not isinstance(identifiers,list) or len(identifiers) != len(bundle['memories'])
                        or any(not isinstance(i,str) or not re.fullmatch(r'[a-f0-9]{32}',i) for i in identifiers)
                        or len(set(identifiers)) != len(identifiers)
                        or old.get('memory_id') != identifiers[0] or not isinstance(keys,dict)
                        or set(keys) != {row['key'] for row in bundle['memories']}
                        or list(keys.values()) != identifiers):
                    raise ValueError('Capture receipt memory identifiers cannot be verified')
                brain = BrainStore(root)
                with file_lock(brain.lock), brain._connection() as con:
                    for identifier in identifiers:
                        row = con.execute('SELECT * FROM memories WHERE id=?',(identifier,)).fetchone()
                        # Absent/deleted rows are historical captures, never recreated.
                        if row is None or row['status'] == 'deleted':
                            continue
                        source = json.loads(row['source'])
                        if (row['project_id'] != project or row['user_id'] != 'local'
                                or source.get('type') != 'task' or source.get('job_id') != job_id
                                or source.get('review_sha256') != digest(result['review'])
                                or brain._source(source,project,approved=True)[1] != row['source_hash']):
                            raise ValueError('Capture receipt points to unrelated memory evidence')
                # The receipt records historical capture. Forget never causes replay.
                return dict(old, reused=True)
            if old.get('status') == 'writing':
                raise ValueError('Capture completion is uncertain; inspect the existing receipt before retrying')
            if old.get('retryable') is not True or old.get('memory_id') is not None or old.get('memory_ids') != []:
                raise ValueError('Capture retry is not verified safe')
        receipt = {'schema_version':1,'mode':'curated_bundle','job_id':job_id,'project_id':project,
                   'status':'writing','request_sha256':request_hash,'source_sha256':source_hash,
                   'memory_id':None,'memory_ids':[],'retryable':False,'updated_at':timestamp()}
        write_json(receipt_path,receipt)
        try:
            ids, relations, explanations = _insert(BrainStore(root),result,bundle)
        except (ValueError,StorageLimitError):
            # Validation/admission rolls back the SQLite transaction. An I/O or
            # arbitrary interruption remains writing/uncertain, never guessed safe.
            receipt.update(status='error',retryable=True,updated_at=timestamp())
            write_json(receipt_path,receipt)
            raise
        receipt.update(status='remembered',memory_id=next(iter(ids.values())),memory_ids=list(ids.values()),
                       memory_keys=ids,relation_ids=relations,relation_evidence=explanations,
                       retryable=False,updated_at=timestamp())
        write_json(receipt_path,receipt)
        return receipt


def capture_run(root, run_id, bundle, *, owner, session, generation, reviewer, note):
    """Import reviewed closeout knowledge as an artifact, without pretending to run a worker."""
    from coordinator_handoff import Coordinator
    root = Path(root).resolve()
    if not isinstance(run_id,str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,160}',run_id):
        raise ValueError('Select an exact orchestration run')
    run = safe_path(root,root / '.orchestration' / run_id)
    coordinator = Coordinator(run)
    with file_lock(coordinator.lock):
        state = coordinator.read(); coordinator.require_owner(state,owner,session,generation)
        manifest = read_json(run / 'run.json',root)
        project = scope(manifest.get('project_id'))
        validate_bundle(bundle,project)
        capture_id = bundle.get('capture_id')
        if not isinstance(capture_id,str) or not re.fullmatch(r'[a-z][a-z0-9_-]{0,59}',capture_id):
            raise ValueError('Capture needs a short stable capture_id')
        reviewer,note = text(reviewer,'Reviewer',100),text(note,'Review note',1000,20)
        if len(note.split()) < 4:
            raise ValueError('Use a substantive closeout validation note')
        proof = []
        evidence = bundle.get('evidence')
        if not isinstance(evidence,list) or not 1 <= len(evidence) <= 12:
            raise ValueError('List 1 to 12 reviewed evidence files inside this run')
        for relative in evidence:
            if not isinstance(relative,str) or Path(relative).is_absolute():
                raise ValueError('Evidence paths must be relative to the exact run')
            path = safe_path(run,run / relative)
            with path.open('rb') as stream:
                raw = stream.read(2 * 1024**2 + 1)
            if len(raw) > 2 * 1024**2:
                raise ValueError('Closeout evidence file exceeds 2 MiB')
            proof.append({'path':relative,'sha256':hashlib.sha256(raw).hexdigest()})
        identity = digest({'bundle':bundle,'proof':proof,'reviewer':reviewer,'note':note})
        journal = run / 'review' / ('memory-capture-' + capture_id + '.json')
        store = TaskStore(root / 'runs/tasks')
        old = read_json(journal,root) if journal.exists() else None
        if journal.exists() and (not isinstance(old,dict) or old.get('request_sha256') != identity
                or old.get('status') not in ('importing','remembered','error','writing')):
            raise ValueError('This closeout changed or its journal is invalid; preserve its evidence')
        if journal.exists():
            job_id = old.get('job_id')
            if not isinstance(job_id,str) or not re.fullmatch(r'[a-f0-9]{32}',job_id):
                raise ValueError('Closeout journal has an invalid task identifier')
            result = read_json(store.directory(job_id) / 'result.json',root,8*1024**2)
            if (not isinstance(result,dict) or result.get('job_id') != job_id
                    or result.get('run_id') != run_id or result.get('assignment_project_id') != project
                    or result.get('imported_completed_artifact') is not True
                    or result.get('artifact_origin') != 'reviewed-run-closeout'
                    or type(result.get('provider_calls')) is not int or result.get('provider_calls') != 0
                    or result.get('worker') != 'native-review' or result.get('category') != 'memory-curation'
                    or result.get('response') != json.dumps({'knowledge':bundle,'evidence':proof},ensure_ascii=False)):
                raise ValueError('Closeout journal does not match its canonical imported evidence')
        else:
            with BrainStore(root).budget.allocation(2*1024**2,kind='task'):
                result = store.create(worker='native-review',task='Reviewed project closeout: '+capture_id,
                                      category='memory-curation',assignment_project_id=project,run_id=run_id,
                                      imported_completed_artifact=True)
                job_id = result['job_id']
                write_json(journal,{'job_id':job_id,'request_sha256':identity,'status':'importing'})
                result.update(status='awaiting_review',execution_status='succeeded',review_status='pending',
                              response=json.dumps({'knowledge':bundle,'evidence':proof},ensure_ascii=False),
                              finalized_at=timestamp(),ended_at=timestamp(),provider_calls=0,
                              artifact_origin='reviewed-run-closeout')
                store.save(job_id,result)
        if result.get('review_status') == 'pending':
            result = store.review(job_id,'accepted',reviewer,note,curated_payload=bundle)
            outcome = result.get('memory_outcome',{})
        else:
            outcome = record_accepted_bundle(store,job_id,bundle)
        write_json(journal,{'job_id':job_id,'request_sha256':identity,'status':outcome.get('status','error'),
                            'memory_outcome':outcome})
        if not any(task.get('job_id') == job_id for task in manifest.get('tasks',[]) if isinstance(task,dict)):
            manifest.setdefault('tasks',[]).append({'job_id':job_id,'status':'accepted','kind':'reviewed-closeout'})
            write_json(run / 'run.json',manifest)
        return {'job_id':job_id,'memory_outcome':outcome,'provider_calls':0,'imported_artifact':True}
