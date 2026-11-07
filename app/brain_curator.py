"""Read-only curator report for one Brain project; it recommends, it never changes memory.

Lists reviewed contradictions, near-duplicate memories, memories absent from the
retained recall evidence, memories supplied to tasks rated harmful, environment
facts with no end date, and stale pending candidates. Every action stays a
reviewer's explicit command (supersede, relate, forget, approve). No model or
network request is made.
"""
import argparse
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import re
import sys

from brain_store import BrainStore, now, safe_path, scope
from usage_guard import file_lock

MAX_MEMORIES = 2000
MAX_DUPLICATE_SCAN = 1000
MAX_TASK_DIRS = 2000
MAX_TASK_BYTES = 8 * 1024**2
MAX_FINDINGS = 50
DUPLICATE_THRESHOLD = .72
# Words in more than this share of a project's memories are template text
# (for example automatic review episodes) and cannot make two memories similar.
COMMON_WORD_SHARE = .3
# Phrases that describe a passing state of a machine, account or tool. A memory
# stating one with no end date can outlive the state and harden into a false rule.
ENVIRONMENT = re.compile(
    r"\b(?:(?:is|are) not installed|isn't installed|aren't installed|is (?:currently )?down|"
    r"(?:is|was) rate[- ]limited|quota (?:is )?(?:exhausted|exceeded|reached|ran out)|"
    r"(?:key|token|credential|login|session) (?:has |have )?expired|temporarily|"
    r"for now|at the moment|right now|until (?:it is|this is) fixed|"
    r"(?:is|are) (?:currently )?broken|(?:does not|doesn't|do not|don't) work)\b", re.I)
# Words of three or more characters; inner dots and dashes stay (file names, versions).
_WORD = re.compile(r'[a-z0-9][a-z0-9_.-]*[a-z0-9]')


def _words(row):
    return {word for word in _WORD.findall((row['title'] + ' ' + row['content']).casefold()) if len(word) >= 3}


def near_duplicates(rows, threshold=DUPLICATE_THRESHOLD):
    """Token-overlap pairs among current memories, ignoring project-wide template words."""
    rows = rows[:MAX_DUPLICATE_SCAN]
    sets = [_words(row) for row in rows]
    frequency = {}
    for words in sets:
        for word in words:
            frequency[word] = frequency.get(word, 0) + 1
    limit = max(2, int(len(rows) * COMMON_WORD_SHARE))
    sets = [{word for word in words if frequency[word] <= limit} for words in sets]
    pairs = []
    for i, left in enumerate(sets):
        if len(left) < 3:
            continue
        for j in range(i + 1, len(sets)):
            right = sets[j]
            if len(right) < 3 or min(len(left), len(right)) / max(len(left), len(right)) < threshold:
                continue
            similarity = len(left & right) / len(left | right)
            if similarity >= threshold:
                pairs.append((similarity, rows[i], rows[j]))
    pairs.sort(key=lambda item: (-item[0], item[1]['id'], item[2]['id']))
    return [{'similarity': round(similarity, 3), 'a_id': a['id'], 'a_title': a['title'],
             'b_id': b['id'], 'b_title': b['title'],
             'suggestion': 'If they state the same fact, supersede the older one; otherwise relate them.'}
            for similarity, a, b in pairs[:MAX_FINDINGS]]


def _task_evidence(root, project_id):
    """Supplied memory IDs and current harmful ratings from bounded canonical task reads."""
    from memory_usage import summary
    tasks = safe_path(root, root / 'runs/tasks')
    supplied, harmful, scanned = set(), [], 0
    if not tasks.is_dir():
        return supplied, harmful, scanned
    directories = sorted((entry for entry in tasks.iterdir() if entry.is_dir()),
                         key=lambda entry: entry.stat().st_mtime, reverse=True)[:MAX_TASK_DIRS]
    for directory in directories:
        path = directory / 'result.json'
        try:
            if not path.is_file() or path.stat().st_size > MAX_TASK_BYTES:
                continue
            result = json.loads(path.read_text(encoding='utf-8'))
        except (OSError, ValueError):
            continue
        scanned += 1
        if not isinstance(result, dict) or result.get('assignment_project_id') != project_id:
            continue
        context = result.get('memory_context')
        ids = [item for item in context.get('ids', []) if isinstance(item, str)] if isinstance(context, dict) else []
        supplied.update(ids)
        try:
            feedback = summary(result).get('feedback') or {}
        except (ValueError, TypeError, KeyError, AttributeError):
            continue
        if feedback.get('status') == 'reviewed' and feedback.get('rating') == 'harmful':
            harmful.append({'job_id': result.get('job_id'), 'memory_ids': ids,
                            'note': str(feedback.get('note', ''))[:300]})
    return supplied, harmful, scanned


def report(root, project_id, user_id='local', *, stale_days=30, pending_days=14, store=None):
    root = Path(root).resolve()
    project_id, user_id = scope(project_id), scope(user_id, 'User')
    store = store or BrainStore(root, read_only=True)
    at = now()
    stale_before = (datetime.now(timezone.utc) - timedelta(days=stale_days)).isoformat(timespec='microseconds')
    pending_before = (datetime.now(timezone.utc) - timedelta(days=pending_days)).isoformat(timespec='microseconds')
    with file_lock(store.lock), store._connection() as con:
        rows = con.execute("""SELECT * FROM memories WHERE project_id=? AND user_id=? AND status='active'
                              AND valid_from<=? AND (valid_to IS NULL OR valid_to>?)
                              ORDER BY created_at DESC, id LIMIT ?""",
                           (project_id, user_id, at, at, MAX_MEMORIES + 1)).fetchall()
        truncated = len(rows) > MAX_MEMORIES
        sources, excluded = {}, {}
        current = [dict(row) for row in rows[:MAX_MEMORIES] if store._current(row, at, sources, excluded)]
        by_id = {row['id']: row for row in current}
        contradictions = [{'source_id': edge['source_id'], 'source_title': by_id[edge['source_id']]['title'],
                           'target_id': edge['target_id'], 'target_title': by_id[edge['target_id']]['title'],
                           'since': edge['valid_from'], 'actor': edge['actor'],
                           'suggestion': 'Supersede the outdated memory, or end the relation once reconciled.'}
                          for edge in con.execute("""SELECT * FROM relations WHERE relation='contradicts'
                                AND valid_from<=? AND (valid_to IS NULL OR valid_to>?) ORDER BY valid_from""", (at, at))
                          if edge['source_id'] in by_id and edge['target_id'] in by_id][:MAX_FINDINGS]
        pending = [{'id': row['id'], 'title': row['title'], 'created_at': row['created_at']}
                   for row in con.execute("""SELECT id,title,created_at FROM memories WHERE project_id=? AND user_id=?
                        AND status='pending' AND created_at<? ORDER BY created_at LIMIT ?""",
                                          (project_id, user_id, pending_before, MAX_FINDINGS))]
        traced = set()
        for trace in con.execute('SELECT memory_ids FROM traces WHERE project_id=? AND user_id=?', (project_id, user_id)):
            try:
                traced.update(json.loads(trace['memory_ids']))
            except (ValueError, TypeError):
                continue
    supplied, harmful, scanned = _task_evidence(root, project_id)
    seen = traced | supplied
    unrecalled = [{'id': row['id'], 'title': row['title'], 'kind': row['kind'], 'created_at': row['created_at']}
                  for row in sorted(current, key=lambda row: row['created_at'])
                  if row['created_at'] < stale_before and row['id'] not in seen]
    harmful_rows = [dict(item, current_memory_ids=[i for i in item['memory_ids'] if i in by_id]) for item in harmful]
    environment = [{'id': row['id'], 'title': row['title'], 'match': match.group(0),
                    'suggestion': 'Add an end date, or supersede it when the environment changes.'}
                   for row in current if row['valid_to'] is None
                   for match in [ENVIRONMENT.search(row['title'] + ' ' + row['content'])] if match][:MAX_FINDINGS]
    findings = {'contradictions': contradictions, 'near_duplicates': near_duplicates(current),
                'not_recently_recalled': unrecalled[:MAX_FINDINGS], 'harmful_rated': harmful_rows[:MAX_FINDINGS],
                'open_ended_environment': environment, 'stale_pending': pending}
    return {'schema_version': 1, 'project_id': project_id, 'user_id': user_id, 'generated_at': at,
            'current_memories': len(current), 'source_excluded': len(excluded),
            'counts': {key: len(value) for key, value in findings.items()},
            'not_recently_recalled_total': len(unrecalled), 'findings': findings,
            'limits': {'memories_scanned': min(len(rows), MAX_MEMORIES), 'memory_scan_truncated': truncated,
                       'duplicate_scan': min(len(current), MAX_DUPLICATE_SCAN), 'tasks_scanned': scanned,
                       'findings_per_list': MAX_FINDINGS},
            'notes': ['Read-only: nothing was changed and no model was called.',
                      'Recall evidence covers the retained traces (the newest 200 searches across all projects) '
                      'and the scanned task records, so "not recently recalled" is not "never recalled".',
                      'Similarity is word overlap, not meaning; review each pair before acting.']}


def main(argv=None):
    from paths import ROOT
    parser = argparse.ArgumentParser(prog='brain curator', description=__doc__.splitlines()[0])
    parser.add_argument('--root', type=Path, default=ROOT)
    parser.add_argument('--project', required=True)
    parser.add_argument('--user', default='local')
    parser.add_argument('--stale-days', type=int, default=30)
    parser.add_argument('--pending-days', type=int, default=14)
    args = parser.parse_args(argv)
    try:
        result = report(args.root, args.project, args.user, stale_days=max(1, args.stale_days),
                        pending_days=max(1, args.pending_days))
    except (ValueError, OSError, TimeoutError) as exc:
        print(json.dumps({'status': 'error', 'error': str(exc)}))
        return 2
    print(json.dumps(result, ensure_ascii=True))
    return 0


if __name__ == '__main__':
    sys.exit(main())
