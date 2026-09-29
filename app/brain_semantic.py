"""Optional, explicitly authorized embeddings; lookup never builds the index.

The existing Brain owns source validity, scope, deletion, and storage admission.
Only explicit indexing transmits memory text. Retrieval transmits one query after
finding usable scoped vectors. No retries, automatic model installation, or costs
inferred from token usage. Network failures leave keyword retrieval available.
"""
from contextlib import closing
import hashlib
from http.client import HTTPException
import json
import math
import os
import re
import sqlite3
import struct
import time
from urllib.error import URLError
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener

from jev_prechecks import scan_sensitive
from storage_budget import StorageLimitError
from usage_guard import file_lock

MAX_CONFIG_BYTES = 16 * 1024
MAX_REQUEST_BYTES = 1024 * 1024
MAX_RESPONSE_BYTES = 8 * 1024 * 1024
MAX_INPUT_BYTES = 32 * 1024
MAX_DIMENSIONS = 3072
MAX_INDEX_BATCH = 100
MAX_SCAN_ROWS = 1000
MAX_CANDIDATES = 60
SCHEMA = '''CREATE TABLE IF NOT EXISTS semantic_vectors (
 memory_id TEXT PRIMARY KEY REFERENCES memories(id), project_id TEXT NOT NULL,
 user_id TEXT NOT NULL, config_id TEXT NOT NULL, model TEXT NOT NULL,
 fingerprint TEXT NOT NULL, dimensions INTEGER NOT NULL, vector BLOB NOT NULL,
 indexed_at TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS semantic_scope ON semantic_vectors(project_id,user_id,config_id);'''


class _Unavailable(Exception):
    def __init__(self, status, reason):
        self.status, self.reason = status, reason
        super().__init__(reason)


def _json(raw):
    def unique(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError('Duplicate JSON key')
            result[key] = value
        return result
    return json.loads(raw, object_pairs_hook=unique,
                      parse_constant=lambda _: (_ for _ in ()).throw(ValueError('Nonfinite JSON')))


def _encode(value):
    return json.dumps(value, sort_keys=True, ensure_ascii=False,
                      separators=(',', ':'), allow_nan=False).encode('utf-8')


def fingerprint(row):
    """Bind an embedding to the exact reviewed source and embedded fields."""
    values = {key: row[key] for key in ('source_hash', 'title', 'content', 'tags', 'episode')}
    for key in ('tags', 'episode'):
        if isinstance(values[key], str):
            values[key] = _json(values[key])
    return hashlib.sha256(_encode(values)).hexdigest()


def _number(value, lower, upper):
    return (type(value) in (int, float) and math.isfinite(value)
            and lower <= value <= upper)


def _load_config(brain, project_id):
    from brain_store import safe_path, scope
    try:
        project_id = scope(project_id)
        path = safe_path(brain.root, brain.home / 'semantic-config.json')
        if not path.exists():
            raise _Unavailable('not_configured', 'Semantic retrieval has not been configured.')
        with path.open('rb') as handle:
            raw = handle.read(MAX_CONFIG_BYTES + 1)
        if len(raw) > MAX_CONFIG_BYTES:
            raise ValueError('Oversized configuration')
        value = _json(raw)
        if not isinstance(value, dict) or type(value.get('enabled')) is not bool:
            raise ValueError('Explicit enabled flag required')
        if not value['enabled']:
            raise _Unavailable('disabled', 'Semantic retrieval is disabled.')
        required = {'enabled', 'endpoint', 'model', 'api_key_env', 'authorized_projects'}
        if not required <= value.keys() or value.keys() - required - {'timeout_seconds', 'similarity_threshold'}:
            raise ValueError('Unsupported or missing configuration fields')
        endpoint, model, env = value['endpoint'], value['model'], value['api_key_env']
        if (not isinstance(endpoint, str) or len(endpoint) > 2048
                or any(ord(char) < 33 or ord(char) > 126 for char in endpoint)):
            raise ValueError('Invalid endpoint')
        url = urlsplit(endpoint)
        if (url.scheme != 'https' or not url.hostname or url.username is not None
                or url.password is not None or url.query or url.fragment or url.port == 0):
            raise ValueError('Use an HTTPS endpoint without credentials, query, or fragment')
        if (not isinstance(model, str) or not 1 <= len(model) <= 200
                or model != model.strip() or any(ord(char) < 32 for char in model)):
            raise ValueError('Invalid model')
        if not isinstance(env, str) or not re.fullmatch(r'[A-Za-z_][A-Za-z0-9_]{0,99}', env):
            raise ValueError('Invalid environment variable name')
        projects = value['authorized_projects']
        if (not isinstance(projects, list) or len(projects) > 100
                or any(not isinstance(item, str) or scope(item) != item for item in projects)
                or len(set(projects)) != len(projects)):
            raise ValueError('Invalid authorized projects')
        timeout = value.get('timeout_seconds', 2)
        threshold = value.get('similarity_threshold', .65)
        if not _number(timeout, .1, 5) or not _number(threshold, 0, 1):
            raise ValueError('Invalid timeout or threshold')
        if project_id not in projects:
            raise _Unavailable('not_authorized', 'This project is not authorized for the embedding endpoint.')
        key = os.environ.get(env, '')
        if not key or len(key) > 8192 or any(ord(char) < 33 or ord(char) > 126 for char in key):
            raise _Unavailable('missing_credentials', 'The configured embedding credential is unavailable.')
        config = dict(value, timeout_seconds=timeout, similarity_threshold=threshold)
        config['config_id'] = hashlib.sha256(_encode(config)).hexdigest()
        config['_key'] = key
        return config
    except _Unavailable:
        raise
    except (ValueError, TypeError, OSError, UnicodeError, OverflowError, RecursionError):
        raise _Unavailable('invalid_config', 'Semantic configuration is invalid or cannot be read safely.') from None


def config_status(brain, project_id):
    """Public status deliberately excludes endpoint and credentials."""
    try:
        config = _load_config(brain, project_id)
        return {'status': 'ready', 'reason': 'Explicit semantic access is configured for this project.',
                'enabled': True, 'model': config['model'], 'config_id': config['config_id'],
                'timeout_seconds': config['timeout_seconds'],
                'similarity_threshold': config['similarity_threshold']}
    except _Unavailable as exc:
        return {'status': exc.status, 'reason': exc.reason, 'enabled': False}


def _unit(vector):
    if not isinstance(vector, (list, tuple)) or not 1 <= len(vector) <= MAX_DIMENSIONS:
        raise ValueError('Invalid embedding dimensions')
    if any(type(item) not in (int, float) for item in vector):
        raise ValueError('Embedding must contain numbers')
    try:
        values = [float(item) for item in vector]
    except OverflowError:
        raise ValueError('Invalid embedding magnitude') from None
    if any(not math.isfinite(item) for item in values):
        raise ValueError('Nonfinite embedding')
    scale = max(abs(item) for item in values)
    if not scale:
        raise ValueError('Zero embedding')
    values = [item / scale for item in values]
    norm = math.sqrt(math.fsum(item * item for item in values))
    return [item / norm for item in values]


def cosine(left, right):
    left, right = _unit(left), _unit(right)
    if len(left) != len(right):
        raise ValueError('Embedding dimensions differ')
    return max(-1., min(1., math.fsum(a * b for a, b in zip(left, right))))


def _stored_vector(item, model):
    dimensions, blob = item['dimensions'], item['vector']
    if (item['model'] != model or type(dimensions) is not int
            or not 1 <= dimensions <= MAX_DIMENSIONS or not isinstance(blob, bytes)
            or len(blob) != dimensions * 4):
        return None
    try:
        return _unit(struct.unpack('<' + 'f' * dimensions, blob))
    except ValueError:
        return None


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def _embed(config, texts):
    payload = _encode({'model': config['model'], 'input': texts, 'encoding_format': 'float'})
    if not 1 <= len(texts) <= MAX_INDEX_BATCH or len(payload) > MAX_REQUEST_BYTES:
        raise _Unavailable('input_limit', 'Embedding input exceeds the bounded request limit.')
    request = Request(config['endpoint'], data=payload, method='POST', headers={
        'Content-Type': 'application/json', 'Accept': 'application/json',
        'Authorization': 'Bearer ' + config['_key']})
    try:
        opener = build_opener(_NoRedirect())
        with closing(opener.open(request, timeout=config['timeout_seconds'])) as response:
            if response.status != 200:
                raise _Unavailable('unavailable', 'Embedding endpoint did not return a successful response.')
            raw = response.read(MAX_RESPONSE_BYTES + 1)
            if len(raw) > MAX_RESPONSE_BYTES:
                raise _Unavailable('invalid_response', 'Embedding response exceeded its size limit.')
    except (OSError, HTTPException) as exc:
        # OSError includes socket/TLS failures; only explicit timeouts are labeled timeout.
        if isinstance(exc, TimeoutError) or (isinstance(exc, URLError) and isinstance(exc.reason, TimeoutError)):
            raise _Unavailable('timeout', 'Embedding request timed out; it was not retried.') from None
        raise _Unavailable('unavailable', 'Embedding request failed; it was not retried.') from None
    try:
        data = _json(raw)
        if not isinstance(data, dict) or data.get('model') != config['model']:
            raise ValueError('Reported embedding model does not match configuration')
        entries = data.get('data')
        if not isinstance(entries, list) or len(entries) != len(texts):
            raise ValueError('Embedding result count mismatch')
        vectors = [None] * len(texts)
        dimensions = set()
        for entry in entries:
            if not isinstance(entry, dict) or type(entry.get('index')) is not int:
                raise ValueError('Invalid embedding index')
            index = entry['index']
            if not 0 <= index < len(texts) or vectors[index] is not None:
                raise ValueError('Duplicate or out-of-range embedding index')
            vectors[index] = _unit(entry.get('embedding'))
            dimensions.add(len(vectors[index]))
        if len(dimensions) != 1:
            raise ValueError('Embedding dimensions differ')
        usage = data.get('usage')
        tokens = usage.get('prompt_tokens') if isinstance(usage, dict) else None
        if type(tokens) is not int or not 0 <= tokens <= 2**53 - 1:
            tokens = None
        return {'vectors': vectors, 'model': data['model'], 'input_tokens': tokens}
    except (ValueError, TypeError, UnicodeError, OverflowError, RecursionError):
        raise _Unavailable('invalid_response', 'Embedding response failed model, vector, or index validation.') from None


def _current(brain, row, at, sources, source_keys=None, evidence=None):
    """Keep a shared distinct-task budget while refreshing phase-local proofs."""
    source = _json(row['source'])
    from brain_links import task_key
    key = task_key(source, row['project_id'])
    if key is not None:
        keys = sources if source_keys is None else source_keys
        if key not in keys:
            if len(keys) >= 128:
                if evidence is not None:
                    evidence['scan_limited'] = True
                return False
            if source_keys is not None:
                source_keys.add(key)
    return brain._current(row, at, sources)


def _memory_text(row):
    values = {key: row[key] for key in ('title', 'content', 'tags', 'episode')}
    for key in ('tags', 'episode'):
        values[key] = _json(values[key])
    raw = _encode(values)
    if len(raw) > MAX_INPUT_BYTES:
        return None
    return raw.decode('utf-8')


def _result(start, status='ok', reason='', **updates):
    result = {'status': status, 'reason': reason, 'candidates': [], 'provider_calls': 0,
              'input_tokens': None, 'model': None, 'indexed_count': 0,
              'elapsed_ms': round((time.perf_counter() - start) * 1000, 3)}
    result.update(updates)
    return result


def index_memories(brain, project_id, user_id='local', limit=100):
    """Explicit one-batch indexing. Network occurs outside the Brain lock."""
    from brain_store import now, scope
    start = time.perf_counter()
    evidence = {'provider_calls': 0, 'indexed_count': 0, 'selected_count': 0, 'scan_limited': False,
                'skipped_sensitive': 0}
    try:
        config = _load_config(brain, project_id)
        project_id, user_id = scope(project_id), scope(user_id, 'User')
        if type(limit) is not int or not 1 <= limit <= MAX_INDEX_BATCH:
            raise _Unavailable('input_limit', 'Indexing requires a batch size from 1 to 100.')
        selected, texts, sources, source_keys = [], [], {}, set()
        with file_lock(brain.lock), brain._connection() as con:
            at = now()
            has_vectors = con.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name='semantic_vectors'").fetchone()
            rows = con.execute('''SELECT * FROM memories WHERE project_id=? AND user_id=?
                AND status='active' AND valid_from<=? AND (valid_to IS NULL OR valid_to>?)
                ORDER BY id LIMIT ?''', (project_id, user_id, at, at, MAX_SCAN_ROWS))
            for row in rows:
                if not _current(brain, row, at, sources, source_keys, evidence):
                    continue
                old_hash = fingerprint(row)
                if has_vectors:
                    # Look up this memory's own vector; a scope-wide LIMIT could miss it and re-embed.
                    stored = con.execute('''SELECT memory_id,fingerprint,model,dimensions,vector
                        FROM semantic_vectors WHERE memory_id=? AND project_id=? AND user_id=?
                        AND config_id=? AND model=? AND length(vector)<=?''',
                        (row['id'], project_id, user_id, config['config_id'], config['model'],
                         MAX_DIMENSIONS * 4)).fetchone()
                    if (stored is not None and stored['fingerprint'] == old_hash
                            and _stored_vector(stored, config['model']) is not None):
                        continue
                content = _memory_text(row)
                if content is None:
                    continue
                if scan_sensitive(content)['blocked']:
                    # Same local secret check Jev applies; flagged memories are never embedded.
                    evidence['skipped_sensitive'] += 1
                    continue
                if len(_encode({'model': config['model'], 'input': texts + [content],
                                'encoding_format': 'float'})) > MAX_REQUEST_BYTES:
                    break
                selected.append((row['id'], old_hash))
                texts.append(content)
                if len(selected) >= limit:
                    break
        evidence['selected_count'] = len(selected)
        if not selected:
            return _result(start, 'empty_scope', 'No current approved memories fit the indexing limits.', **evidence)
        # Reserve before transmitting so low storage does not incur avoidable API usage.
        estimate = max(2 * 1024**2, brain.db.stat().st_size * 2 + 1024**2)
        with brain.budget.allocation(estimate, kind='brain'):
            if _load_config(brain, project_id)['config_id'] != config['config_id']:
                raise _Unavailable('stale_config', 'Semantic configuration changed before indexing.')
            evidence['provider_calls'] = 1
            embedded = _embed(config, texts)
            evidence.update(input_tokens=embedded['input_tokens'], model=embedded['model'])
            with file_lock(brain.lock), brain._connection() as con:
                if _load_config(brain, project_id)['config_id'] != config['config_id']:
                    raise _Unavailable('stale_config', 'Semantic configuration changed; embeddings were not saved.')
                sources, valid, at = {}, [], now()
                for (identifier, old_hash), vector in zip(selected, embedded['vectors']):
                    row = con.execute('SELECT * FROM memories WHERE id=? AND project_id=? AND user_id=?',
                                      (identifier, project_id, user_id)).fetchone()
                    if row is not None and _current(brain, row, at, sources, source_keys, evidence) and fingerprint(row) == old_hash:
                        valid.append((identifier, project_id, user_id, config['config_id'], embedded['model'],
                                      old_hash, len(vector), struct.pack('<' + 'f' * len(vector), *vector), at))
                if valid:
                    # DDL is restricted to explicit indexing and shares the insert transaction.
                    con.executescript('BEGIN IMMEDIATE;\n' + SCHEMA)
                    try:
                        con.executemany('''INSERT OR REPLACE INTO semantic_vectors
                            (memory_id,project_id,user_id,config_id,model,fingerprint,dimensions,vector,indexed_at)
                            VALUES(?,?,?,?,?,?,?,?,?)''', valid)
                        con.commit()
                        evidence['indexed_count'] = len(valid)
                    except BaseException:
                        con.rollback()
                        raise
                    try:
                        brain._checkpoint(con)
                    except sqlite3.Error:
                        pass
        return _result(start, 'indexed' if evidence['indexed_count'] else 'stale_sources',
                       'Embeddings saved for current approved memories.' if evidence['indexed_count'] else
                       'Selected memories changed during indexing; no embeddings were saved.', **evidence)
    except _Unavailable as exc:
        return _result(start, exc.status, exc.reason, **evidence)
    except StorageLimitError:
        if evidence['indexed_count']:
            return _result(start, 'indexed', 'Embeddings were saved; storage reservation cleanup needs review.',
                           cleanup_pending=True, **evidence)
        return _result(start, 'storage_limited', 'Storage admission held optional semantic indexing.', **evidence)
    except (OSError, ValueError, TypeError, KeyError, AttributeError, sqlite3.Error, TimeoutError):
        return _result(start, 'unavailable', 'Semantic indexing could not safely complete.', **evidence)


def semantic_candidates(brain, query, project_id, user_id='local', source_keys=None, *, checked_source_keys=None):
    """Read-only search with a shared 128-task budget across retrieval phases.

    The optional set contains (job_id, project_id) pairs already checked by the
    caller. It is extended in place, including failed source checks. Proof caches
    remain phase-local so an in-flight source edit is never hidden by old proofs.
    """
    from brain_store import now, scope, text
    start = time.perf_counter()
    evidence = {'provider_calls': 0, 'indexed_count': 0, 'scan_limited': False}
    sources = {}
    try:
        config = _load_config(brain, project_id)
        query, project_id, user_id = text(query, 'Query', 500), scope(project_id), scope(user_id, 'User')
        if scan_sensitive(query)['blocked']:
            return _result(start, 'sensitive_input',
                           'Query contains sensitive content; no embedding request was made.', **evidence)
        if source_keys is None:
            source_keys = set()
        if (not isinstance(source_keys, set) or len(source_keys) > 128
                or any(not isinstance(key, tuple) or len(key) != 2
                       or not isinstance(key[0], str) or not re.fullmatch(r'[a-f0-9]{32}', key[0])
                       or not isinstance(key[1], str) or scope(key[1]) != key[1] for key in source_keys)):
            raise _Unavailable('input_limit', 'Source validation requires a bounded set of task and project identifiers.')
        vectors, sources = [], {}
        with file_lock(brain.lock), brain._connection() as con:
            if not con.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='semantic_vectors'").fetchone():
                return _result(start, 'empty_index', 'No semantic index exists; no embedding request was made.', **evidence)
            args = (project_id, user_id, config['config_id'])
            evidence['indexed_count'] = con.execute('''SELECT count(*) FROM semantic_vectors
                WHERE project_id=? AND user_id=? AND config_id=?''', args).fetchone()[0]
            evidence['scan_limited'] = evidence['indexed_count'] > MAX_SCAN_ROWS
            rows = con.execute('''SELECT memory_id,model,fingerprint,dimensions,vector FROM semantic_vectors
                WHERE project_id=? AND user_id=? AND config_id=? AND length(vector)<=?
                ORDER BY indexed_at DESC,memory_id LIMIT ?''', (*args, MAX_DIMENSIONS * 4, MAX_SCAN_ROWS))
            at = now()
            for item in rows:
                row = con.execute('SELECT * FROM memories WHERE id=? AND project_id=? AND user_id=?',
                                  (item['memory_id'], project_id, user_id)).fetchone()
                if row is None or not _current(brain, row, at, sources, source_keys, evidence) or fingerprint(row) != item['fingerprint']:
                    continue
                vector = _stored_vector(item, config['model'])
                if vector is None:
                    continue
                vectors.append((item['memory_id'], item['fingerprint'], vector))
        if not vectors:
            if evidence['scan_limited']:
                return _result(start, 'empty_index', 'Source validation reached its bound; no usable embeddings were found within it.', **evidence)
            return _result(start, 'empty_index', 'No current scoped embeddings are available; no request was made.', **evidence)
        if _load_config(brain, project_id)['config_id'] != config['config_id']:
            raise _Unavailable('stale_config', 'Semantic configuration changed before retrieval.')
        evidence['provider_calls'] = 1
        embedded = _embed(config, [query])
        evidence.update(input_tokens=embedded['input_tokens'], model=embedded['model'])
        query_vector = embedded['vectors'][0]
        candidates = []
        for identifier, old_hash, vector in vectors:
            if len(vector) != len(query_vector):
                continue
            score = max(-1., min(1., math.fsum(a * b for a, b in zip(vector, query_vector))))
            if score >= config['similarity_threshold']:
                candidates.append({'id': identifier, 'score': round(score, 8), 'fingerprint': old_hash})
        candidates.sort(key=lambda item: (-item['score'], item['id']))
        # Do not return records forgotten or changed while the query was in flight.
        with file_lock(brain.lock), brain._connection() as con:
            if _load_config(brain, project_id)['config_id'] != config['config_id']:
                raise _Unavailable('stale_config', 'Semantic configuration changed during retrieval.')
            if checked_source_keys is not None:
                checked_source_keys.update(sources)
            sources, valid, at = {}, [], now()
            for candidate in candidates:
                row = con.execute('SELECT * FROM memories WHERE id=? AND project_id=? AND user_id=?',
                                  (candidate['id'], project_id, user_id)).fetchone()
                if row is not None and _current(brain, row, at, sources, source_keys, evidence) and fingerprint(row) == candidate['fingerprint']:
                    valid.append(candidate)
                if len(valid) >= MAX_CANDIDATES:
                    break
        return _result(start, 'ok', 'Source validation reached its bound; additional semantic matches may exist.'
                       if evidence['scan_limited'] else 'Scoped semantic candidates validated against current memory sources.',
                       candidates=valid, **evidence)
    except _Unavailable as exc:
        return _result(start, exc.status, exc.reason, **evidence)
    except (OSError, ValueError, TypeError, KeyError, AttributeError, sqlite3.Error, TimeoutError):
        return _result(start, 'unavailable', 'Semantic retrieval could not safely complete.', **evidence)
    finally:
        if checked_source_keys is not None:
            checked_source_keys.update(sources)
