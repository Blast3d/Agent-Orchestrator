"""Explicit, reviewed reuse of one foreign memory in a destination project.

References are one hop only. They copy searchable text but retain a proof of
the original: every evidence read must validate that original before using the
copy. Ordinary relations remain strictly within a project and user scope.
"""
from __future__ import annotations

import json
import re
import uuid

from brain_store import RELATIONS, digest, now, scope, text
from storage_budget import StorageLimitError

REFERENCE_TYPE = 'memory_reference'
CONSENT = 'reuse_in_destination'
_ID = re.compile(r'[a-f0-9]{32}')
_HASH = re.compile(r'[a-f0-9]{64}')
_COPY_FIELDS = ('kind', 'title', 'content', 'tags', 'importance', 'episode')
_PROOF_FIELDS = ('id', 'project_id', 'user_id', *_COPY_FIELDS, 'valid_from',
                 'valid_to', 'source', 'source_hash', 'reviewer', 'review_note',
                 'reviewed_at', 'fingerprint')


def memory_proof(row):
    return digest({key: row[key] for key in _PROOF_FIELDS})


def task_key(source, project_id):
    """Expose a reference's original task to the normal shared recall budget.

    This key is an admission hint, not validation. validate_source verifies it
    against the original before any source can be used.
    """
    if source.get('type') == 'task':
        return (source['job_id'], project_id)
    if source.get('type') == REFERENCE_TYPE and source.get('task_job_id'):
        job, project = source['task_job_id'], source.get('project_id')
        if isinstance(job, str) and _ID.fullmatch(job) and isinstance(project, str):
            return (job, project)
    return None


def validate_source(store, source, project_id, *, user_id=None, at=None, sources=None, con=None):
    """Return the current original or raise, without locks or provider calls.

    The caller owns the Brain lock. One direct original is the strict maximum
    dependency depth, so no recursive reference/cycle can enter evidence.
    """
    expected = {'type', 'memory_id', 'project_id', 'user_id', 'destination_project_id',
                'memory_sha256', 'task_job_id', 'consent', 'actor', 'note'}
    if not isinstance(source, dict) or set(source) != expected or source.get('type') != REFERENCE_TYPE:
        raise ValueError('Invalid cross-project memory reference proof')
    if (not isinstance(source['memory_id'], str) or not _ID.fullmatch(source['memory_id'])
            or not isinstance(source['memory_sha256'], str) or not _HASH.fullmatch(source['memory_sha256'])):
        raise ValueError('Invalid cross-project memory reference identity')
    if (scope(source['project_id']) == scope(project_id)
            or source['destination_project_id'] != project_id
            or source['consent'] != CONSENT
            or (user_id is not None and source['user_id'] != user_id)):
        raise ValueError('Cross-project memory reference scope or consent changed')
    scope(source['user_id'], 'User')
    text(source['actor'], 'Actor', 100)
    text(source['note'], 'Reuse review note', 1000, 20)
    if con is None:
        with store._connection() as connection:
            return validate_source(store, source, project_id, user_id=user_id,
                                   at=at, sources=sources, con=connection)
    original = con.execute('SELECT * FROM memories WHERE id=?', (source['memory_id'],)).fetchone()
    if (original is None or original['project_id'] != source['project_id']
            or original['user_id'] != source['user_id']):
        raise ValueError('Original memory is unavailable in the approved source scope')
    origin_source = json.loads(original['source'])
    if origin_source.get('type') not in ('task', 'user'):
        raise ValueError('Reference chains are not supported; select the original memory')
    if origin_source.get('job_id') != source['task_job_id']:
        raise ValueError('Original memory source identity changed')
    moment = at or now()
    if not store._current(original, moment, sources if sources is not None else {}):
        raise ValueError('Original memory is no longer current reviewed evidence')
    if (not original['reviewer'] or not original['reviewed_at']
            or memory_proof(original) != source['memory_sha256']):
        raise ValueError('Original memory changed; review a new reference explicitly')
    if origin_source['type'] == 'user' and store._source(origin_source, original['project_id'])[1] != original['source_hash']:
        raise ValueError('Original user source proof changed')
    return original


def current_reference(store, row, at, sources, excluded=None):
    try:
        source = json.loads(row['source'])
        original = validate_source(store, source, row['project_id'], user_id=row['user_id'],
                                   at=at, sources=sources)
        if row['source_hash'] != digest(source) or any(row[key] != original[key] for key in _COPY_FIELDS):
            raise ValueError('Referenced evidence copy changed; review a new reference explicitly')
        return True
    except (ValueError, TypeError, KeyError):
        if excluded is not None:
            # No foreign identifiers or source text leak into scope diagnostics.
            excluded[row['id']] = 'Cross-project reference no longer matches current reviewed source evidence'
        return False


def create_reference(store, *, project_id, target_project_id, target_id,
                     actor, note, memory_id=None, relation='related_to', user_id='local'):
    """Atomically approve selected reuse and optionally connect a local memory.

    Calling this method is the explicit approval; no automatic collector calls
    it. ``memory_id`` is a destination node, ``target_id`` the foreign original.
    """
    project_id, target_project_id = scope(project_id), scope(target_project_id)
    user_id = scope(user_id, 'User')
    actor, note = text(actor, 'Actor', 100), text(note, 'Reuse review note', 1000, 20)
    if project_id == target_project_id:
        raise ValueError('Use an ordinary relation for memories in the same project')
    if relation not in RELATIONS:
        raise ValueError('Select a supported relationship type')
    with store._write() as con:
        at = now()
        original = store._row(con, target_id)
        if original['project_id'] != target_project_id or original['user_id'] != user_id:
            raise ValueError('Original memory is not in the selected project and user scope')
        origin_source = json.loads(original['source'])
        if origin_source.get('type') not in ('task', 'user'):
            raise ValueError('Reference chains are not supported; select the original memory')
        source = {'type': REFERENCE_TYPE, 'memory_id': target_id, 'project_id': target_project_id,
                  'user_id': user_id, 'destination_project_id': project_id,
                  'memory_sha256': memory_proof(original), 'task_job_id': origin_source.get('job_id'),
                  'consent': CONSENT, 'actor': actor, 'note': note}
        validate_source(store, source, project_id, user_id=user_id, at=at, con=con)
        local = store._row(con, memory_id) if memory_id is not None else None
        if local is not None:
            if local['project_id'] != project_id or local['user_id'] != user_id:
                raise ValueError('Connection start must belong to the destination project and user')
            if not store._current(local, at, {}):
                raise ValueError('Connection start must be current reviewed evidence')
        existing = con.execute('''SELECT * FROM memories WHERE project_id=? AND user_id=?
            AND status='active' AND json_extract(source,'$.type')=?
            AND json_extract(source,'$.memory_id')=? AND json_extract(source,'$.memory_sha256')=?
            ORDER BY created_at,id LIMIT 1''',
            (project_id, user_id, REFERENCE_TYPE, target_id, source['memory_sha256'])).fetchone()
        duplicate = existing is not None and store._current(existing, at, {})
        if duplicate:
            reference = existing
        else:
            if con.execute('SELECT count(*) FROM memories').fetchone()[0] >= store.limits['max_memories']:
                con.execute("DELETE FROM memories WHERE status='deleted'")
                if con.execute('SELECT count(*) FROM memories').fetchone()[0] >= store.limits['max_memories']:
                    raise StorageLimitError('Memory record limit reached; forget unneeded memories before adding more')
            identifier = uuid.uuid4().hex
            proof = digest(source)
            fingerprint = digest([REFERENCE_TYPE, project_id, user_id, target_id, source['memory_sha256']])
            con.execute('''INSERT INTO memories(id,project_id,user_id,kind,title,content,tags,importance,status,
                created_at,valid_from,valid_to,reviewer,review_note,reviewed_at,source,source_hash,episode,fingerprint)
                VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)''',
                (identifier, project_id, user_id, original['kind'], original['title'], original['content'],
                 original['tags'], original['importance'], 'active', at, at, original['valid_to'],
                 actor, note, at, json.dumps(source), proof, original['episode'], fingerprint))
            reference = store._row(con, identifier)
            con.execute('INSERT INTO memory_fts(rowid,title,content,tags) VALUES(?,?,?,?)',
                (reference['rowid'], reference['title'], reference['content'] + ' ' +
                 ' '.join(json.loads(reference['episode']).values()), ' '.join(json.loads(reference['tags']))))
            store._change(con, reference, 'reference_approved')
        edge = None
        if local is not None:
            if local['id'] == reference['id']:
                raise ValueError('Select a different destination memory to connect')
            store._same_scope(local, reference)
            saved = con.execute('''SELECT * FROM relations WHERE source_id=? AND target_id=? AND relation=?
                AND valid_from<=? AND (valid_to IS NULL OR valid_to>?) ORDER BY valid_from DESC LIMIT 1''',
                (local['id'], reference['id'], relation, at, at)).fetchone()
            if saved is not None:
                edge = dict(saved)
            else:
                if con.execute('SELECT count(*) FROM relations').fetchone()[0] >= store.limits['max_relations']:
                    raise StorageLimitError('Relationship limit reached')
                edge = {'id': uuid.uuid4().hex, 'source_id': local['id'], 'target_id': reference['id'],
                        'relation': relation, 'valid_from': at, 'valid_to': None, 'actor': actor}
                con.execute('INSERT INTO relations VALUES(:id,:source_id,:target_id,:relation,:valid_from,:valid_to,:actor)', edge)
                store._change(con, local, 'linked')
        store._drop_export()
        return {'reference': dict(store._public(reference), duplicate=duplicate), 'relation': edge,
                'write_status': store._write_state}


def invalidate_references(store, con, memory_id, at):
    """Expire direct snapshots when the original is explicitly superseded.

    The source snapshot remains historical evidence; no newer source is reused
    without a new explicit approval. Destination cursors notify open maps.
    """
    rows = con.execute("""SELECT * FROM memories WHERE status='active'
        AND json_extract(source,'$.type')=? AND json_extract(source,'$.memory_id')=?""",
        (REFERENCE_TYPE, memory_id)).fetchall()
    vectors = con.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='semantic_vectors'").fetchone()
    for row in rows:
        con.execute("UPDATE memories SET valid_to=? WHERE id=?", (at, row['id']))
        con.execute('DELETE FROM memory_fts WHERE rowid=?', (row['rowid'],))
        if vectors:
            con.execute('DELETE FROM semantic_vectors WHERE memory_id=?', (row['id'],))
        store._change(con, row, 'reference_invalidated')


def forget_rows(store, con, memory_id):
    """Securely erase a memory and direct derived copies in one transaction."""
    original = store._row(con, memory_id)
    dependents = con.execute('''SELECT * FROM memories WHERE json_extract(source,'$.type')=?
        AND json_extract(source,'$.memory_id')=? AND id<>?''',
        (REFERENCE_TYPE, memory_id, memory_id)).fetchall()
    tables = {r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    for row in [original, *dependents]:
        identifier = row['id']
        con.execute('DELETE FROM memory_fts WHERE rowid=?', (row['rowid'],))
        con.execute('DELETE FROM relations WHERE source_id=? OR target_id=?', (identifier, identifier))
        con.execute("UPDATE memories SET title='',content='',tags='[]',episode='{}',source='{}',source_hash='',fingerprint='',reviewer=NULL,review_note=NULL,status='deleted',deleted_at=? WHERE id=?", (now(), identifier))
        con.execute('DELETE FROM traces WHERE memory_ids LIKE ?', ('%' + identifier + '%',))
        if 'semantic_vectors' in tables:
            con.execute('DELETE FROM semantic_vectors WHERE memory_id=?', (identifier,))
        store._change(con, row, 'forgotten' if identifier == memory_id else 'reference_forgotten')
    if 'retrieval_traces' in tables:
        con.execute('DELETE FROM retrieval_traces WHERE trace_id NOT IN (SELECT id FROM traces)')
    store._drop_export()
    return store._public(store._row(con, memory_id))
