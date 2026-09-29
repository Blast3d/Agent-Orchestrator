"""Reviewed, scoped SQLite memory with optional explicitly configured embeddings.

Temporal facts and episode provenance follow Graphiti's documented concepts;
this is an independent small implementation, not its driver or runtime.
Canonical task evidence and coordinator ownership always remain authoritative.
"""
from contextlib import contextmanager
from datetime import datetime, timezone
import hashlib
import json
import math
import os
from pathlib import Path
import re
import sqlite3
import uuid

from paths import ROOT
from usage_guard import file_lock
from storage_budget import StorageBudget, StorageLimitError

KINDS = ('fact', 'preference', 'episode', 'procedure')
RELATIONS = ('solves', 'depends_on', 'supports', 'related_to')
LIMITS = {'max_memories': 10000, 'max_pending': 500, 'max_relations': 30000,
          'max_traces': 200, 'max_changes': 1000, 'max_db_bytes': 256 * 1024**2}
FEED_SCHEMA = '''CREATE TABLE IF NOT EXISTS feed_state (
 project_id TEXT NOT NULL, user_id TEXT NOT NULL, trimmed_through INTEGER NOT NULL DEFAULT 0,
 last_seq INTEGER NOT NULL DEFAULT 0, PRIMARY KEY(project_id,user_id));'''
SCHEMA = '''
CREATE TABLE IF NOT EXISTS memories (
 rowid INTEGER PRIMARY KEY, id TEXT UNIQUE NOT NULL, project_id TEXT NOT NULL,
 user_id TEXT NOT NULL, kind TEXT NOT NULL, title TEXT NOT NULL, content TEXT NOT NULL,
 tags TEXT NOT NULL, importance REAL NOT NULL, status TEXT NOT NULL,
 created_at TEXT NOT NULL, valid_from TEXT NOT NULL, valid_to TEXT,
 reviewer TEXT, review_note TEXT, reviewed_at TEXT, source TEXT NOT NULL,
 source_hash TEXT NOT NULL, episode TEXT NOT NULL, fingerprint TEXT NOT NULL,
 superseded_by TEXT, deleted_at TEXT);
CREATE INDEX IF NOT EXISTS scope_status ON memories(project_id,user_id,status,valid_from,valid_to);
CREATE INDEX IF NOT EXISTS exact_duplicate ON memories(project_id,user_id,fingerprint);
CREATE VIRTUAL TABLE IF NOT EXISTS memory_fts USING fts5(title, content, tags, tokenize='unicode61');
CREATE TABLE IF NOT EXISTS relations (
 id TEXT PRIMARY KEY, source_id TEXT NOT NULL REFERENCES memories(id),
 target_id TEXT NOT NULL REFERENCES memories(id), relation TEXT NOT NULL,
 valid_from TEXT NOT NULL, valid_to TEXT, actor TEXT NOT NULL,
 UNIQUE(source_id,target_id,relation,valid_from));
CREATE INDEX IF NOT EXISTS relation_source ON relations(source_id);
CREATE INDEX IF NOT EXISTS relation_target ON relations(target_id);
CREATE TABLE IF NOT EXISTS traces (
 id TEXT PRIMARY KEY, created_at TEXT NOT NULL, project_id TEXT NOT NULL,
 user_id TEXT NOT NULL, query_hash TEXT NOT NULL, memory_ids TEXT NOT NULL, elapsed_ms REAL NOT NULL);
CREATE TABLE IF NOT EXISTS changes (
 seq INTEGER PRIMARY KEY AUTOINCREMENT, memory_id TEXT NOT NULL, operation TEXT NOT NULL,
 project_id TEXT NOT NULL, user_id TEXT NOT NULL, at TEXT NOT NULL);
'''


def now():
    return datetime.now(timezone.utc).isoformat(timespec='microseconds')


def text(value, label, maximum, minimum=1):
    if not isinstance(value, str) or not minimum <= len(value.strip()) <= maximum:
        raise ValueError(f'{label} must contain {minimum} to {maximum} characters')
    if any(ord(c) < 32 and c not in '\n\t\r' for c in value):
        raise ValueError(f'{label} contains control characters')
    return value.strip()


def scope(value, label='Project'):
    value = text(value, label, 100)
    if not re.fullmatch(r'[\w][\w .:@/-]{0,99}', value, re.UNICODE):
        raise ValueError(f'{label} contains unsupported characters')
    return value


def date(value, default=None):
    if value is None:
        return default
    value = text(value, 'Timestamp', 40)
    try:
        parsed = datetime.fromisoformat(value.replace('Z', '+00:00'))
    except ValueError:
        raise ValueError('Use an ISO timestamp with timezone') from None
    if parsed.tzinfo is None:
        raise ValueError('Timestamp needs a timezone')
    return parsed.astimezone(timezone.utc).isoformat(timespec='microseconds')


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def safe_path(root, path):
    root, path = Path(root).resolve(), Path(path).absolute()
    if not path.is_relative_to(root):
        raise ValueError('Brain path is outside application storage')
    for node in (path, *list(path.parents)[:len(path.parts)-len(root.parts)]):
        if node.is_symlink() or (hasattr(node, 'is_junction') and node.is_junction()):
            raise ValueError('Linked brain storage is not supported')
    if not path.resolve().is_relative_to(root):
        raise ValueError('Brain path resolves outside application storage')
    return path


class BrainStore:
    def __init__(self, root=ROOT, *, limits=None, budget=None):
        if sqlite3.sqlite_version_info < (3,42,0):
            raise ValueError('Brain requires SQLite 3.42+ for secure FTS deletion')
        self.root = Path(root).resolve()
        self.home = safe_path(self.root, self.root / 'runtime/brain')
        self.db = self.home / 'memory.sqlite'
        self.lock = self.home / 'brain.lock'
        self.limits = dict(LIMITS, **(limits or {}))
        if any(type(n) is not int or n < 1 for n in self.limits.values()):
            raise ValueError('Brain limits must be positive integers')
        self.budget = budget or StorageBudget(self.root)
        self.home.mkdir(parents=True, exist_ok=True)
        with file_lock(self.lock):
            self._paths()
            self._drop_temps()
            with self._connection() as con:
                version = con.execute('PRAGMA user_version').fetchone()[0]
                tables = {r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table'")}
                if version == 0 and not tables:
                    # A crash may leave an empty file. DDL and version change must
                    # commit together so reopening it can safely finish initialization.
                    with self.budget.allocation(2 * 1024**2, kind='brain'):
                        con.executescript('BEGIN IMMEDIATE;\n' + SCHEMA + FEED_SCHEMA +
                            "INSERT INTO memory_fts(memory_fts,rank) VALUES('secure-delete',1);"
                            'PRAGMA user_version=3; COMMIT;')
                elif version == 1 and {'memories','changes','memory_fts'} <= tables:
                    with self.budget.allocation(2 * 1024**2, kind='brain'):
                        con.executescript('BEGIN IMMEDIATE;\n' + FEED_SCHEMA)
                        # Version 1 did not retain per-scope trim watermarks. A
                        # conservative initial resync covers any already lost events.
                        floor = max(0, (con.execute('SELECT min(seq) FROM changes').fetchone()[0] or 1)-1)
                        con.execute('''INSERT OR IGNORE INTO feed_state
                            SELECT project_id,user_id,?,COALESCE((SELECT max(seq) FROM changes c
                            WHERE c.project_id=m.project_id AND c.user_id=m.user_id),?)
                            FROM (SELECT project_id,user_id FROM memories UNION
                                  SELECT project_id,user_id FROM changes) m''',(floor,floor))
                        con.execute('PRAGMA user_version=2');con.commit()
                elif version not in (2,3,4) or not {'memories','relations','traces','changes','feed_state','memory_fts'} <= tables:
                    raise ValueError('Unsupported brain schema; preserve existing database')
                if con.execute('PRAGMA user_version').fetchone()[0]==2:
                    with self.budget.allocation(max(2*1024**2,self.db.stat().st_size*2+1024**2),kind='brain'):
                        # Preserve all old validity periods while allowing a new
                        # period for a relationship that previously expired.
                        con.executescript('''BEGIN IMMEDIATE;
                          ALTER TABLE relations RENAME TO relations_v2;
                          DROP INDEX relation_source; DROP INDEX relation_target;
                          CREATE TABLE relations (
                            id TEXT PRIMARY KEY, source_id TEXT NOT NULL REFERENCES memories(id),
                            target_id TEXT NOT NULL REFERENCES memories(id), relation TEXT NOT NULL,
                            valid_from TEXT NOT NULL, valid_to TEXT, actor TEXT NOT NULL,
                            UNIQUE(source_id,target_id,relation,valid_from));
                          INSERT INTO relations SELECT * FROM relations_v2;
                          DROP TABLE relations_v2;
                          CREATE INDEX relation_source ON relations(source_id);
                          CREATE INDEX relation_target ON relations(target_id);
                          PRAGMA user_version=3; COMMIT;''')
                if con.execute('PRAGMA user_version').fetchone()[0] == 3:
                    # Older binaries do not validate memory-reference sources:
                    # raise the schema version before any references can exist.
                    with self.budget.allocation(2*1024**2, kind='brain'):
                        con.executescript("""BEGIN IMMEDIATE;
                          CREATE INDEX IF NOT EXISTS reference_target ON memories(
                            json_extract(source,'$.memory_id'))
                            WHERE json_extract(source,'$.type')='memory_reference';
                          PRAGMA user_version=4; COMMIT;""")

    def _paths(self):
        for name in ('memory.sqlite', 'memory.sqlite-wal', 'memory.sqlite-shm', 'brain.lock', 'vault.md'):
            safe_path(self.root, self.home / name)

    def _connect(self):
        self._paths()
        con = sqlite3.connect(self.db, timeout=5)
        con.row_factory = sqlite3.Row
        con.execute('PRAGMA foreign_keys=ON')
        con.execute('PRAGMA secure_delete=ON')
        con.execute('PRAGMA journal_mode=WAL')
        con.execute('PRAGMA synchronous=FULL')
        con.execute('PRAGMA temp_store=MEMORY')
        con.execute('PRAGMA cache_size=-4096')
        con.execute('PRAGMA wal_autocheckpoint=256')
        con.execute(f'PRAGMA max_page_count={max(64,self.limits["max_db_bytes"]//4096)}')
        return con

    @contextmanager
    def _connection(self):
        con = self._connect()
        try:
            yield con
        finally:
            con.close()

    @contextmanager
    def _write(self, *, cleanup=False):
        # The app lock serializes reads with forgetting/purging derived copies.
        with file_lock(self.lock):
            # Callers use this to tell a lock timeout (nothing written) from a
            # failure after a write may have begun.
            self.write_lock_acquired = True
            self._paths()
            estimate = max(2*1024**2, self.db.stat().st_size * 2 + 1024**2)
            allocation = self.budget.allocation(estimate, kind='brain') if not cleanup else None
            token = None
            if allocation:
                token = allocation.__enter__()
            # Returned mutation dictionaries share this outcome until the context
            # finishes. The same app lock prevents concurrent access to it.
            self._write_state = {'committed':False,'cleanup_pending':False}
            try:
                with self._connection() as con:
                    if cleanup:
                        # Secure FTS deletion can allocate pages even while removing
                        # data. Bounded cleanup headroom comes from the recovery reserve.
                        pages = max(con.execute('PRAGMA page_count').fetchone()[0], self.limits['max_db_bytes']//4096+1024)
                        con.execute(f'PRAGMA max_page_count={pages}')
                    try:
                        con.execute('BEGIN IMMEDIATE')
                        yield con
                        con.commit()
                        self._write_state['committed'] = True
                    except BaseException:
                        con.rollback()
                        raise
                    # A committed change is durable even when another SQLite handle
                    # prevents immediate checkpointing. Never report it as rolled back.
                    try:
                        self._checkpoint(con)
                    except sqlite3.Error:
                        pass
            finally:
                try:
                    if allocation:
                        allocation.__exit__(None, None, None)
                except (StorageLimitError,OSError,TimeoutError):
                    # Preserve the committed record or the original transaction
                    # exception. Never release an uncertain reservation by guessing.
                    self._write_state.update(cleanup_pending=True,reservation_id=token,
                        message='Storage reservation cleanup is pending; inspect it before release.')
                finally:
                    del self._write_state

    @staticmethod
    def _checkpoint(con):
        con.execute('PRAGMA wal_checkpoint(TRUNCATE)')

    def _source(self, source, project_id, *, approved=False):
        if not isinstance(source, dict):
            raise ValueError('Source must be a task or a user note')
        if source.get('type') == 'memory_reference':
            from brain_links import validate_source
            validate_source(self, source, project_id)
            return source, digest(source)
        if source.get('type') == 'user':
            result = {'type':'user','note':text(source.get('note'), 'Source note', 1000)}
            return result, digest(result)
        if source.get('type') != 'task' or not re.fullmatch(r'[a-f0-9]{32}', str(source.get('job_id', ''))):
            raise ValueError('Task source needs a valid job identifier')
        proofs = self._task_source_proofs(source['job_id'], project_id, approved=approved)
        return self._task_proof(source, proofs)

    def _task_source_proofs(self, job_id, project_id, *, approved):
        """Read one canonical task and retain only its supported proof digests."""
        path = safe_path(self.root, self.root / 'runs/tasks' / job_id / 'result.json')
        if not path.is_file() or path.stat().st_size > 8*1024**2:
            raise ValueError('Canonical source task unavailable or oversized')
        try:
            result = json.loads(path.read_text(encoding='utf-8'))
        except (ValueError, OSError):
            raise ValueError('Canonical source task cannot be verified') from None
        if not isinstance(result, dict):
            raise ValueError('Canonical source task must be a JSON object')
        if result.get('job_id') != job_id or result.get('assignment_project_id') != project_id:
            raise ValueError('Task source must belong to this exact project')
        if result.get('execution_status') != 'succeeded' or not result.get('finalized_at'):
            raise ValueError('Source task must finish successfully before proposing a memory')
        if approved and not (result.get('status') == 'accepted' and result.get('review_status') == 'accepted'
                             and result.get('execution_status') == 'succeeded' and result.get('finalized_at')):
            raise ValueError('Memory source needs a finalized accepted task review')
        proof = {k:result.get(k) for k in ('job_id','assignment_project_id','response','finalized_at')}
        plain_hash = digest(proof)
        review_hash = digest(result.get('review'))
        proof['review_sha256'] = review_hash
        from task_performance import snapshot
        return {'plain':plain_hash, 'reviewed':digest(proof), 'review_sha256':review_hash,
                'performance_sha256':digest(snapshot(result))}

    @staticmethod
    def _task_proof(source, proofs):
        normalized = {'type':'task','job_id':source['job_id']}
        # Automatically stored episodes derive their text from the accepted
        # review. Preserve that optional proof without invalidating legacy task
        # memories whose source only depended on the canonical answer.
        if 'review_sha256' in source:
            review_hash = source['review_sha256']
            if not isinstance(review_hash,str) or not re.fullmatch(r'[a-f0-9]{64}',review_hash):
                raise ValueError('Task review proof must be a SHA-256 digest')
            if review_hash != proofs['review_sha256']:
                raise ValueError('Canonical task review changed; review the memory separately')
            normalized['review_sha256'] = review_hash
        source_hash = proofs['reviewed'] if 'review_sha256' in source else proofs['plain']
        if 'performance_sha256' in source:
            value = source['performance_sha256']
            if (not isinstance(value,str) or not re.fullmatch(r'[a-f0-9]{64}',value)
                    or value != proofs.get('performance_sha256')):
                raise ValueError('Canonical task performance changed; review the memory separately')
            normalized['performance_sha256'] = value
            source_hash = digest([source_hash,value])
        return normalized, source_hash

    @staticmethod
    def _row(con, memory_id):
        if not isinstance(memory_id, str) or not re.fullmatch(r'[a-f0-9]{32}', memory_id):
            raise ValueError('Invalid memory identifier')
        row = con.execute('SELECT * FROM memories WHERE id=?', (memory_id,)).fetchone()
        if not row:
            raise ValueError('Memory not found')
        return row

    def _public(self, row):
        result = dict(row)
        for key in ('tags','source','episode'):
            result[key] = json.loads(result[key])
        for key in ('rowid','fingerprint','source_hash'):
            result.pop(key, None)
        if result['status'] == 'active' and result['valid_to'] and result['valid_to'] <= now():
            result['status'] = 'expired'
        result['validity_state']='scheduled' if result['valid_from']>now() else ('expired' if result['valid_to'] and result['valid_to']<=now() else 'current')
        if hasattr(self,'_write_state'):
            result['write_status']=self._write_state
        return result

    def _current(self, row, at, sources, excluded=None):
        if row['status'] != 'active' or row['valid_from'] > at or (row['valid_to'] and row['valid_to'] <= at):
            return False
        source = json.loads(row['source'])
        if source.get('type') == 'memory_reference':
            from brain_links import current_reference
            return current_reference(self, row, at, sources, excluded)
        if source.get('type') not in ('task', 'user'):
            if excluded is not None:
                excluded[row['id']] = 'Unknown memory source type'
            return False
        if source.get('type') == 'user':
            try:
                if self._source(source, row['project_id'])[1] != row['source_hash']:
                    raise ValueError('User memory source proof changed')
            except ValueError as exc:
                if excluded is not None:
                    excluded[row['id']] = str(exc)
                return False
        if source.get('type') == 'task':
            key = (source['job_id'], row['project_id'])
            if key not in sources:
                try:
                    # Count/cache canonical tasks, not proof variants. Caching
                    # compact digests also avoids retaining up to 128 full task
                    # payloads while validating both legacy and review proofs.
                    sources[key] = self._task_source_proofs(source['job_id'], row['project_id'], approved=True)
                except ValueError as exc:
                    sources[key] = {'error':str(exc)}
            try:
                if 'error' in sources[key]:
                    raise ValueError(sources[key]['error'])
                if self._task_proof(source, sources[key])[1] != row['source_hash']:
                    raise ValueError('Canonical task evidence changed; review the memory separately')
            except ValueError as exc:
                if excluded is not None:
                    excluded[row['id']] = str(exc)
                return False
        return True

    @staticmethod
    def _source_diagnostics(sources, excluded):
        reasons = {}
        for reason in excluded.values():
            reasons[reason] = reasons.get(reason, 0) + 1
        return {'checked_tasks':len(sources), 'excluded_memories':len(excluded),
                'reasons':[{'reason':reason, 'count':count} for reason,count in sorted(reasons.items())]}

    @staticmethod
    def _source_warnings(excluded):
        return ([f'{len(excluded)} memories excluded because their source evidence could not be verified.']
                if excluded else [])

    def _change(self, con, row, operation):
        sequence=con.execute('INSERT INTO changes(memory_id,operation,project_id,user_id,at) VALUES(?,?,?,?,?)',
                    (row['id'],operation,row['project_id'],row['user_id'],now()))
        con.execute('''INSERT INTO feed_state(project_id,user_id,last_seq) VALUES(?,?,?)
          ON CONFLICT(project_id,user_id) DO UPDATE SET last_seq=excluded.last_seq''',
          (row['project_id'],row['user_id'],sequence.lastrowid))
        trimmed=con.execute('''SELECT project_id,user_id,max(seq) FROM changes WHERE seq NOT IN
          (SELECT seq FROM changes ORDER BY seq DESC LIMIT ?) GROUP BY project_id,user_id''',
          (self.limits['max_changes'],)).fetchall()
        for project,user,through in trimmed:
            con.execute('UPDATE feed_state SET trimmed_through=max(trimmed_through,?) WHERE project_id=? AND user_id=?',
                        (through,project,user))
        con.execute('DELETE FROM changes WHERE seq NOT IN (SELECT seq FROM changes ORDER BY seq DESC LIMIT ?)',
                    (self.limits['max_changes'],))

    def _drop_export(self):
        paths=[self.home/'vault.md']
        for path in self.home.glob('vault-*.md'):
            if re.fullmatch(r'vault-[a-f0-9]{64}\.md',path.name):
                paths.append(path)
        for path in paths:
            self._remove_view(path)
        self._drop_temps()

    def _remove_view(self,path):
        try:
            safe_path(self.root,path).unlink(missing_ok=True)
        except OSError:
            raise ValueError('A vault export is in use or inaccessible; close its viewer, check access and retry.') from None

    def _drop_temps(self):
        for path in self.home.glob('vault-*.tmp'):
            if re.fullmatch(r'vault-[a-f0-9]{32}\.tmp',path.name):
                self._remove_view(path)

    def candidate(self, payload):
        try:
            size = len(json.dumps(payload,allow_nan=False).encode())
        except (TypeError,ValueError):
            raise ValueError('Memory candidate must contain valid JSON values') from None
        if not isinstance(payload, dict) or size > 16384:
            raise ValueError('Memory candidate must be an object smaller than 16 KiB')
        project_id = scope(payload.get('project_id'))
        user_id = scope(payload.get('user_id','local'), 'User')
        kind = payload.get('kind')
        if kind not in KINDS:
            raise ValueError('Select fact, preference, episode or procedure')
        title = text(payload.get('title'), 'Title', 160)
        content = text(payload.get('content'), 'Memory', 3000)
        tags = payload.get('tags', [])
        if not isinstance(tags, list) or len(tags)>12:
            raise ValueError('Use at most 12 tags or aliases')
        tags = sorted(set(text(t,'Tag',60) for t in tags))
        importance = payload.get('importance',0.5)
        if isinstance(importance,bool) or not isinstance(importance,(float,int)) or not math.isfinite(importance) or not 0<=importance<=1:
            raise ValueError('Importance must be between 0 and 1')
        start, end = date(payload.get('valid_from'),now()), date(payload.get('valid_to'))
        if end and end <= start:
            raise ValueError('Validity end must follow its start')
        episode = payload.get('episode',{})
        if not isinstance(episode, dict):
            raise ValueError('Episode must be an object')
        episode = {k:text(episode.get(k),k,1000) for k in ('problem','action','outcome')} if kind=='episode' else {}
        if isinstance(payload.get('source'), dict) and payload['source'].get('type') == 'memory_reference':
            raise ValueError('Create cross-project references through the explicit reviewed link action')
        source, source_hash = self._source(payload.get('source'),project_id)
        # Fingerprint only exact normalized copies. Semantic contradictions need review.
        fp = digest([kind,' '.join(title.casefold().split()),' '.join(content.casefold().split()),episode,
                     source_hash,tags,importance,date(payload.get('valid_from')),end])
        return {'project_id':project_id,'user_id':user_id,'kind':kind,'title':title,
                'content':content,'tags':tags,'importance':importance,'valid_from':start,
                'valid_to':end,'episode':episode,'source':source,'source_hash':source_hash,'fingerprint':fp}

    def propose(self, payload):
        fields = self.candidate(payload)
        project_id,user_id,kind,title,content,tags,importance,start,end,episode,source,source_hash,fp = (
            fields[key] for key in ('project_id','user_id','kind','title','content','tags',
                'importance','valid_from','valid_to','episode','source','source_hash','fingerprint'))
        with self._write() as con:
            same = con.execute("SELECT * FROM memories WHERE project_id=? AND user_id=? AND fingerprint=? AND status IN ('active','pending') AND (valid_to IS NULL OR valid_to>?)", (project_id,user_id,fp,now())).fetchone()
            if same:
                return dict(self._public(same), duplicate=True)
            if con.execute('SELECT count(*) FROM memories').fetchone()[0] >= self.limits['max_memories']:
                # ID-only change tombstones survive for adapters, while forgotten rows
                # can make space for new records. Canonical evidence is untouched.
                con.execute("DELETE FROM memories WHERE status='deleted'")
                if con.execute('SELECT count(*) FROM memories').fetchone()[0] >= self.limits['max_memories']:
                    raise StorageLimitError('Memory record limit reached; forget unneeded memories before adding more')
            if con.execute("SELECT count(*) FROM memories WHERE status='pending'").fetchone()[0] >= self.limits['max_pending']:
                raise StorageLimitError('Pending review limit reached')
            identifier = uuid.uuid4().hex
            con.execute('''INSERT INTO memories(id,project_id,user_id,kind,title,content,tags,importance,status,
             created_at,valid_from,valid_to,source,source_hash,episode,fingerprint)
             VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)''',
             (identifier,project_id,user_id,kind,title,content,json.dumps(tags),importance,'pending',now(),start,end,
              json.dumps(source),source_hash,json.dumps(episode),fp))
            self._change(con,self._row(con,identifier),'proposed')
            return self._public(self._row(con,identifier))

    def approve(self, memory_id, reviewer, note):
        reviewer,note=text(reviewer,'Reviewer',100),text(note,'Review note',1000,20)
        with self._write() as con:
            row=self._row(con,memory_id)
            if row['status']!='pending':
                raise ValueError('Only a pending memory can be approved')
            _, proof=self._source(json.loads(row['source']),row['project_id'],approved=True)
            if proof != row['source_hash']:
                raise ValueError('Source content changed; propose and review a new memory')
            con.execute("UPDATE memories SET status='active',reviewer=?,review_note=?,reviewed_at=?,source_hash=? WHERE id=?", (reviewer,note,now(),proof,memory_id))
            con.execute('INSERT INTO memory_fts(rowid,title,content,tags) VALUES(?,?,?,?)',
                        (row['rowid'],row['title'],row['content']+' '+' '.join(json.loads(row['episode']).values()),' '.join(json.loads(row['tags']))))
            self._change(con,row,'approved'); self._drop_export()
            return self._public(self._row(con,memory_id))

    def get(self, memory_id):
        with file_lock(self.lock), self._connection() as con:
            result=self._public(self._row(con,memory_id))
            result['relations']=[dict(r) for r in con.execute('SELECT * FROM relations WHERE source_id=? OR target_id=? LIMIT 200',(memory_id,memory_id))]
            receipts = {}
            for relation in result['relations']:
                for identifier in (relation['source_id'],relation['target_id']):
                    endpoint = self._row(con,identifier)
                    source = json.loads(endpoint['source'])
                    job_id = source.get('job_id')
                    if source.get('type') != 'task' or not job_id:
                        continue
                    if job_id not in receipts and len(receipts) < 8:
                        receipts[job_id] = []
                        try:
                            from memory_bundle import read_json
                            _, proof = self._source(source,endpoint['project_id'],approved=True)
                            if proof != endpoint['source_hash']:
                                continue
                            directory = self.root / 'runs/tasks' / job_id
                            receipt = read_json(directory/'memory-outcome.json',self.root)
                            canonical = read_json(directory/'result.json',self.root,8*1024**2)
                            expected = digest({k:canonical.get(k) for k in ('job_id','assignment_project_id','response','review','finalized_at')})
                            if (isinstance(receipt,dict) and receipt.get('schema_version') == 1
                                    and receipt.get('status') == 'remembered'
                                    and receipt.get('mode') == 'curated_bundle' and receipt.get('job_id') == job_id
                                    and receipt.get('source_sha256') == expected):
                                receipts[job_id] = receipt.get('relation_evidence',[])[:64]
                        except (OSError,ValueError,TypeError,KeyError):
                            pass
                    for evidence in receipts.get(job_id,[]):
                        if isinstance(evidence,dict) and all(evidence.get(key) == relation[key] for key in ('id','source_id','target_id','relation')):
                            try:
                                relation['evidence_note'] = text(evidence.get('reason'),'Relationship evidence',500,10)
                            except ValueError:
                                pass
            result['history']=[dict(r) for r in con.execute('SELECT * FROM changes WHERE memory_id=? ORDER BY seq DESC LIMIT 50',(memory_id,))]
            return result

    def list_memories(self, project_id=None, user_id='local', status=None, limit=100):
        user_id=scope(user_id,'User'); limit=max(1,min(int(limit),200))
        query='SELECT * FROM memories WHERE user_id=?';args=[user_id]
        if project_id is not None:
            query+=' AND project_id=?';args.append(scope(project_id))
        if status:
            if status not in ('pending','active','superseded','expired','deleted'):
                raise ValueError('Invalid memory status')
            if status=='expired':
                query+=" AND status='active' AND valid_to IS NOT NULL AND valid_to<=?";args.append(now())
            else:
                query+=' AND status=?';args.append(status)
        query+=' ORDER BY created_at DESC,id LIMIT ?';args.append(limit)
        with file_lock(self.lock), self._connection() as con:
            return [self._public(row) for row in con.execute(query,args)]

    def relate(self, source_id, target_id, relation, actor, valid_from=None, valid_to=None):
        if relation not in RELATIONS or source_id==target_id:
            raise ValueError('Select a supported relation between different memories')
        actor=text(actor,'Actor',100);start,end=date(valid_from,now()),date(valid_to)
        if end and end<=start:
            raise ValueError('Relationship end must follow its start')
        with self._write() as con:
            source,target=self._row(con,source_id),self._row(con,target_id)
            self._same_scope(source,target)
            if not all(self._current(r,now(),{}) for r in (source,target)):
                raise ValueError('Relationships need two current, approved memories')
            sql='SELECT * FROM relations WHERE source_id=? AND target_id=? AND relation=?';params=[source_id,target_id,relation]
            if valid_from is None and valid_to is None:
                sql+=' AND valid_from<=? AND (valid_to IS NULL OR valid_to>?)';params.extend([start,start])
            else:
                sql+=' AND valid_from=? AND valid_to IS ?';params.extend([start,end])
            existing=con.execute(sql+' ORDER BY valid_from DESC LIMIT 1',params).fetchone()
            if existing:
                return dict(existing,write_status=self._write_state)
            if con.execute('SELECT count(*) FROM relations').fetchone()[0]>=self.limits['max_relations']:
                raise StorageLimitError('Relationship limit reached')
            result={'id':uuid.uuid4().hex,'source_id':source_id,'target_id':target_id,'relation':relation,'valid_from':start,'valid_to':end,'actor':actor}
            con.execute('INSERT INTO relations VALUES(:id,:source_id,:target_id,:relation,:valid_from,:valid_to,:actor)',result)
            self._change(con,source,'linked');self._drop_export()
            result['write_status']=self._write_state
            return result

    @staticmethod
    def _same_scope(a,b):
        if (a['project_id'],a['user_id'])!=(b['project_id'],b['user_id']):
            raise ValueError('Both memories must belong to the same project and user')

    def supersede(self, old_id, new_id, actor, reason):
        actor,reason=text(actor,'Actor',100),text(reason,'Reason',1000,10)
        if old_id==new_id:
            raise ValueError('A replacement must be a different memory')
        with self._write() as con:
            old,new=self._row(con,old_id),self._row(con,new_id);self._same_scope(old,new)
            if not all(self._current(r,now(),{}) for r in (old,new)):
                raise ValueError('Supersession needs two current, approved memories')
            if con.execute('SELECT count(*) FROM relations').fetchone()[0]>=self.limits['max_relations']:
                raise StorageLimitError('Relationship limit reached')
            at=now()
            con.execute("UPDATE memories SET status='superseded',valid_to=?,superseded_by=? WHERE id=?",(at,new_id,old_id))
            con.execute('DELETE FROM memory_fts WHERE rowid=?',(old['rowid'],))
            con.execute('INSERT INTO relations VALUES(?,?,?,?,?,?,?)',(uuid.uuid4().hex,new_id,old_id,'supersedes',at,None,actor))
            from brain_links import invalidate_references
            invalidate_references(self,con,old_id,at)
            self._change(con,old,'superseded');self._change(con,new,'replacement');self._drop_export()
            return self._public(self._row(con,old_id))

    def forget(self, memory_id, actor, reason):
        text(actor,'Actor',100);text(reason,'Reason',1000,10)
        # Cleanup remains available under pressure; it only shrinks logical data.
        with self._write(cleanup=True) as con:
            from brain_links import forget_rows
            return forget_rows(self, con, memory_id)

    def search(self, query, project_id, user_id='local', limit=None, max_chars=None, hops=None, *, strategy='auto', record_trace=True, profile='general', depth='compact', jev=True):
        from brain_recall import search
        from brain_jev import maybe_rank
        from jev_profiles import validate_profile
        from brain_recall_budget import resolve
        from contextlib import ExitStack
        profile = validate_profile(profile)
        budget = resolve(depth, limit=limit, max_chars=max_chars)
        limit, max_chars = budget['limit'], budget['max_chars']
        candidate_pool = []
        # One cooperative reservation covers the initial trace and its final Jev
        # update. Keep the token (not a filesystem/SQLite lock) across inference.
        # Empty searches never acquire it. This avoids a second full storage scan.
        stack = ExitStack()
        try:
            trace_allocation = {'stack': stack, 'reserved': False}
            result = search(self,query,project_id,user_id,limit,max_chars,hops,strategy=strategy,record_trace=record_trace,profile=profile,_candidate_pool=candidate_pool,_budget=budget,_trace_allocation=trace_allocation)
            result['profile'] = profile
            if not jev:
                # Jev ranking is a billed provider call when enabled; callers can keep a search local.
                result['jev_ranking'] = {'status': 'skipped', 'reason': 'local-only search requested'}
            else:
                result = maybe_rank(self,result,max_chars=max_chars,record_trace=record_trace,profile=profile,
                                  candidate_pool=candidate_pool,limit=limit,
                                  _trace_reserved=trace_allocation['reserved'])
        finally:
            try:
                stack.close()
            except (OSError, StorageLimitError):
                if 'result' in locals():
                    result.setdefault('warnings', []).append('Recall succeeded, but its storage reservation cleanup needs review.')
                    result['trace_cleanup_status'] = 'failed'
        return result

    def status(self,user_id='local'):
        user_id=scope(user_id,'User')
        with file_lock(self.lock), self._connection() as con:
            counts={k:0 for k in ('pending','active','superseded','expired','deleted')}
            for row in con.execute('SELECT status,valid_to,count(*) AS n FROM memories WHERE user_id=? GROUP BY status,valid_to',(user_id,)):
                state='expired' if row['status']=='active' and row['valid_to'] and row['valid_to']<=now() else row['status']
                counts[state]+=row['n']
            projects=[r[0] for r in con.execute("SELECT DISTINCT project_id FROM memories WHERE user_id=? AND status!='deleted' ORDER BY project_id LIMIT 200",(user_id,))]
            project_counts={p:{k:0 for k in counts} for p in projects}
            for row in con.execute('SELECT project_id,status,valid_to,count(*) AS n FROM memories WHERE user_id=? GROUP BY project_id,status,valid_to',(user_id,)):
                state='expired' if row['status']=='active' and row['valid_to'] and row['valid_to']<=now() else row['status']
                if row['project_id'] in project_counts: project_counts[row['project_id']][state]+=row['n']
            relationships=con.execute('SELECT count(*) FROM relations r JOIN memories m ON m.id=r.source_id WHERE m.user_id=?',(user_id,)).fetchone()[0]
        return {'backend':'sqlite','counts':counts,'projects':projects,'project_counts':project_counts,'relationship_count':relationships,
                'storage':self.budget.status(),'adapter':{'name':'sqlite','graphiti_enabled':False},'limits':self.limits}

    def graph(self, project_id, user_id='local', limit=3000):
        from brain_graph import graph_snapshot
        return graph_snapshot(self,project_id,user_id,limit)

    def snapshot(self, project_id=None, user_id='local'):
        memories=self.list_memories(project_id,user_id,limit=200);ids={r['id'] for r in memories}
        with file_lock(self.lock),self._connection() as con:
            # Select this scope's relations in SQL; other projects' newer links must not crowd them out.
            marks=','.join('?'*len(ids));chosen=sorted(ids)
            relations=[dict(r) for r in con.execute(f'SELECT * FROM relations WHERE source_id IN ({marks}) AND target_id IN ({marks}) ORDER BY valid_from DESC LIMIT 2000',chosen+chosen)] if ids else []
            sql='SELECT * FROM traces WHERE user_id=?';args=[scope(user_id,'User')]
            if project_id is not None: sql+=' AND project_id=?';args.append(scope(project_id))
            traces=[dict(r) for r in con.execute(sql+' ORDER BY created_at DESC LIMIT 30',args)]
            has_details=con.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='retrieval_traces'").fetchone()
            from brain_retrieval_metadata import public_retrieval
            for row in traces:
                row['memory_ids']=json.loads(row['memory_ids'])
                row['retrieval']=None
                detail=con.execute('SELECT detail FROM retrieval_traces WHERE trace_id=?',(row['id'],)).fetchone() if has_details else None
                if detail:
                    try:row['retrieval']=public_retrieval(json.loads(detail[0]))
                    except (ValueError,TypeError):pass
        return {'status':self.status(user_id),'memories':memories,'relations':relations,'traces':traces}

    def export(self, project_id, user_id='local'):
        """Portable approved records; original source/ownership stays authoritative."""
        project_id,user_id=scope(project_id),scope(user_id,'User')
        with file_lock(self.lock), self._connection() as con:
            rows=con.execute("SELECT * FROM memories WHERE project_id=? AND user_id=? AND status='active' LIMIT ?",(project_id,user_id,self.limits['max_memories'])).fetchall()
            sources={};excluded={};records=[self._public(r) for r in rows if self._current(r,now(),sources,excluded)];ids={r['id'] for r in records}
            relations=[dict(r) for r in con.execute('SELECT * FROM relations') if r['source_id'] in ids and r['target_id'] in ids]
            state=con.execute('SELECT last_seq FROM feed_state WHERE project_id=? AND user_id=?',(project_id,user_id)).fetchone()
            return {'schema_version':1,'backend':'sqlite','project_id':project_id,'user_id':user_id,'memories':records,'relations':relations,
                    'source_validation':self._source_diagnostics(sources,excluded),'warnings':self._source_warnings(excluded),
                    'change_cursor':state['last_seq'] if state else 0,'generated_at':now(),'graphiti_enabled':False}

    def changes(self, project_id, user_id='local', after=0, limit=100):
        project_id,user_id=scope(project_id),scope(user_id,'User');after=max(0,int(after));limit=max(1,min(int(limit),200))
        with file_lock(self.lock), self._connection() as con:
            state=con.execute('SELECT * FROM feed_state WHERE project_id=? AND user_id=?',(project_id,user_id)).fetchone()
            rows=[dict(r) for r in con.execute('SELECT * FROM changes WHERE project_id=? AND user_id=? AND seq>? ORDER BY seq LIMIT ?',(project_id,user_id,after,limit))]
            # Recall is not a memory mutation. Hash its bounded retained metadata
            # separately so searches, Forget and global retention can refresh the
            # scoped activity panel without changing the adapter's memory cursor.
            recalls=[tuple(r) for r in con.execute('''SELECT id,created_at,memory_ids,elapsed_ms
              FROM traces WHERE project_id=? AND user_id=?
              ORDER BY created_at DESC,id DESC LIMIT 200''',(project_id,user_id))]
        return {'changes':rows,'cursor':rows[-1]['seq'] if rows else after,'head':state['last_seq'] if state else 0,
                'recall_revision':digest(recalls),
                'resync_required':bool((state and (after<state['trimmed_through'] or after>state['last_seq'])) or (not state and after>0)),
                'note':'IDs only; export current records and its change_cursor for a full resync. No Graphiti consumer enabled.'}

    def vault(self, project_id, user_id='local'):
        project_id,user_id=scope(project_id),scope(user_id,'User')
        with file_lock(self.lock),self._connection() as con:
            rows=con.execute("SELECT * FROM memories WHERE project_id=? AND user_id=? AND status='active' LIMIT ?",(project_id,user_id,self.limits['max_memories'])).fetchall()
            sources={};excluded={};records=[self._public(r) for r in rows if self._current(r,now(),sources,excluded)]
            page=f'# Reviewed memory vault\n\nProject: {project_id} | User: {user_id} | Generated: {now()}\n\n'
            page+='Dated snapshot; validity and source reviews can change. Use brain search for current evidence. Edit memories through the dashboard or brain commands.\n\n'
            for warning in self._source_warnings(excluded):
                page+=warning+'\n\n'
            for row in records:
                page+=f"## {row['title']}\n\n{row['content']}\n\nID: {row['id']} | Kind: {row['kind']} | Source: {json.dumps(row['source'])} | Valid from: {row['valid_from']} | Valid until: {row['valid_to'] or 'open'}\n\n"
            encoded=page.encode('utf-8')
            with self.budget.allocation(len(encoded)*2+4096,kind='brain'):
                path=safe_path(self.root,self.home/('vault-'+digest([project_id,user_id])+'.md'));tmp=safe_path(self.root,self.home/('vault-'+uuid.uuid4().hex+'.tmp'))
                try:
                    with tmp.open('xb') as handle:
                        handle.write(encoded);handle.flush();os.fsync(handle.fileno())
                    os.replace(tmp,path)
                finally:
                    tmp.unlink(missing_ok=True)
            return {'path':str(path),'memories':len(records),'bytes':len(encoded),
                    'source_validation':self._source_diagnostics(sources,excluded),'warnings':self._source_warnings(excluded)}
