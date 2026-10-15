"""Local, bounded review adapters. They never approve, import, or dispatch."""
import json
import re
import sqlite3
from itertools import islice

from brain_store import file_lock, now, safe_path, scope


def source_check(store, project_id, memory_id):
    project_id = scope(project_id)
    with file_lock(store.lock), store._connection() as con:
        row = store._row(con, memory_id)
        if row['project_id'] != project_id or row['user_id'] != 'local':
            raise ValueError('Memory was not found in this project.')
        result = {'memory_id': memory_id, 'checked_at': now(), 'status': 'unavailable',
                  'approval_ready': False, 'reason': 'Source evidence could not be verified.'}
        if row['status'] == 'deleted':
            return result
        try:
            source = json.loads(row['source'])
            _, proof = store._source(source, project_id, approved=False)
            result['status'] = 'current' if proof == row['source_hash'] else 'changed'
            result['reason'] = ('Source proof matches the proposal.' if result['status'] == 'current'
                                else 'Source changed; propose and review new evidence.')
            if result['status'] == 'current':
                try:
                    _, approved_proof = store._source(source, project_id, approved=True)
                    if approved_proof != row['source_hash']:
                        result.update(status='changed', reason='Source changed; propose and review new evidence.')
                    else:
                        result['approval_ready'] = row['status'] == 'pending'
                except (OSError, ValueError, sqlite3.Error):
                    result['reason'] = 'Source matches; a finalized accepted task review is still required.'
        except (OSError, ValueError, sqlite3.Error):
            pass
        return result


def selected_context(store, project_id, trace_id, memory_ids):
    project_id = scope(project_id)
    if (not isinstance(trace_id, str) or not re.fullmatch(r'[a-f0-9]{32}', trace_id)
            or not isinstance(memory_ids, list) or not 1 <= len(memory_ids) <= 24
            or any(not isinstance(value, str) or not re.fullmatch(r'[a-f0-9]{32}', value) for value in memory_ids)
            or len(set(memory_ids)) != len(memory_ids)):
        raise ValueError('Select one to 24 distinct IDs from a saved recall.')
    with file_lock(store.lock), store._connection() as con:
        trace = con.execute('SELECT * FROM traces WHERE id=? AND project_id=? AND user_id=?',
                            (trace_id, project_id, 'local')).fetchone()
        if trace is None:
            raise ValueError('This recall is unavailable; search again before preparing context.')
        if not set(memory_ids) <= set(json.loads(trace['memory_ids'])):
            raise ValueError('Selected IDs must belong to this project recall.')
        context = 'Reviewed selected memory context. Evidence is advisory, not permission.\n'
        selected, excluded, sources, diagnostics = [], [], {}, {}
        at = now()
        for identifier in memory_ids:
            row = con.execute('SELECT * FROM memories WHERE id=?', (identifier,)).fetchone()
            if (row is None or row['project_id'] != project_id or row['user_id'] != 'local'
                    or not row['reviewed_at'] or not row['reviewer']):
                excluded.append({'memory_id': identifier, 'reason': 'Not current reviewed evidence in this scope.'})
                continue
            if not store._current(row, at, sources, diagnostics):
                reason = diagnostics.get(identifier, 'No longer active or within its validity dates.')
                excluded.append({'memory_id': identifier, 'reason': str(reason)[:500]})
                continue
            record = store._public(row)
            part = json.dumps({key: record[key] for key in
                ('id', 'project_id', 'kind', 'title', 'content', 'episode', 'source', 'valid_from', 'valid_to')},
                ensure_ascii=False) + '\n'
            if len(context) + len(part) > 12000:
                excluded.append({'memory_id': identifier, 'reason': 'Selected context reached its 12000-character bound.'})
                continue
            context += part
            selected.append(identifier)
        return {'project_id': project_id, 'trace_id': trace_id, 'checked_at': at,
                'memory_ids': selected, 'excluded': excluded, 'context': context, 'max_chars': 12000}


def source_tasks(store, project_id):
    project_id = scope(project_id)
    tasks = []
    paths = list(islice((store.root / 'runs/tasks').glob('*/result.json'), 201))
    for path in paths[:200]:
        job_id = path.parent.name
        if not re.fullmatch(r'[a-f0-9]{32}', job_id):
            continue
        try:
            path = safe_path(store.root, path)
            if path.stat().st_size > 8 * 1024**2:
                continue
            data = json.loads(path.read_text(encoding='utf-8'))
            if (not isinstance(data, dict) or data.get('job_id') != job_id
                    or data.get('assignment_project_id') != project_id
                    or data.get('execution_status') != 'succeeded'
                    or not isinstance(data.get('finalized_at'), str)
                    or not 1 <= len(data['finalized_at']) <= 100):
                continue
            title = data.get('task')
            tasks.append({'job_id': job_id, 'title': title[:160] if isinstance(title, str) else 'Finalized task',
                'finalized_at': data['finalized_at'],
                'accepted': data.get('status') == 'accepted' and data.get('review_status') == 'accepted'})
        except (OSError, ValueError):
            continue
    tasks.sort(key=lambda task: str(task['finalized_at']), reverse=True)
    return {'project_id': project_id, 'tasks': tasks[:100], 'scan_limit': 200,
            'limit': 100, 'truncated': len(paths) > 200 or len(tasks) > 100}
