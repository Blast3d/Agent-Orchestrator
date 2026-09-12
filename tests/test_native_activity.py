"""Native timing uses exact parent proof, bounded reads and honest run scoping."""
from contextlib import contextmanager
from datetime import datetime, timezone
import json
from pathlib import Path
import sqlite3
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'app'))
import native_activity
from native_activity import snapshot

PARENT = '11111111-1111-1111-1111-111111111111'
OTHER = '22222222-2222-2222-2222-222222222222'
CHILD = '33333333-3333-3333-3333-333333333333'
CHILD2 = '44444444-4444-4444-4444-444444444444'
BASE = datetime(2026, 9, 10, tzinfo=timezone.utc).timestamp()


def stamp(seconds):
    return datetime.fromtimestamp(BASE + seconds, timezone.utc).isoformat()


class NativeActivityTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.home = Path(temporary.name)
        self.base = self.home / '.codex'
        self.sessions = self.base / 'sessions'
        self.sessions.mkdir(parents=True)
        self.database = self.base / 'state_5.sqlite'
        with self.connect() as connection:
            connection.execute('CREATE TABLE threads (id TEXT PRIMARY KEY, rollout_path TEXT, created_at_ms INTEGER, '
                'updated_at_ms INTEGER, source TEXT, agent_path TEXT)')
            connection.execute('CREATE TABLE thread_spawn_edges (parent_thread_id TEXT, child_thread_id TEXT, status TEXT)')
        with native_activity._CACHE_LOCK:
            native_activity._CACHE.clear()

    @contextmanager
    def connect(self):
        connection = sqlite3.connect(self.database)
        try:
            with connection:
                yield connection
        finally:
            connection.close()

    def child(self, name='worker', identifier=CHILD, parent=PARENT, source_parent=None,
              started=10, saved=60, source_path=None, source=None):
        path = self.sessions / ('rollout-2026-09-10T00-00-10-' + identifier + '.jsonl')
        path.write_text('', encoding='utf-8')
        metadata = {'subagent': {'thread_spawn': {'parent_thread_id': source_parent or parent,
            'agent_path': source_path or '/root/' + name, 'private_field': 'PRIVATE SOURCE'}}}
        with self.connect() as connection:
            connection.execute('INSERT INTO threads VALUES (?,?,?,?,?,?)', (identifier, str(path),
                (BASE + started) * 1000, (BASE + saved) * 1000,
                json.dumps(metadata) if source is None else source, '/root/' + name))
            connection.execute('INSERT INTO thread_spawn_edges VALUES (?,?,?)', (parent, identifier, 'open'))
        return path

    def event(self, path, kind='task_complete', at=60, started=40, ended=60, duration=20000, **extra):
        payload = {'type': kind, 'last_agent_message': 'PRIVATE ANSWER', 'started_at': BASE + started,
            'completed_at': BASE + ended, 'duration_ms': duration, **extra}
        with path.open('a', encoding='utf-8') as stream:
            stream.write(json.dumps({'type': 'event_msg', 'timestamp': stamp(at), 'payload': payload}) + '\n')

    def read(self, **kwargs):
        return snapshot(self.home, PARENT, ['worker'], **kwargs)

    def test_exact_parent_and_source_proof_returns_timing_without_private_fields(self):
        path = self.child()
        self.event(path)
        result = self.read(run_started_at=stamp(0), run_ended_at=stamp(100))
        self.assertEqual(result['status'], 'available')
        row = result['agents']['worker']
        self.assertEqual(row['elapsed_seconds'], 50)
        self.assertEqual(row['elapsed_label'], 'Session span (includes pauses)')
        self.assertEqual(row['last_turn_duration_seconds'], 20)
        self.assertEqual(row['lifecycle_state'], 'idle')
        self.assertEqual(row['last_event'], 'task_complete')
        self.assertIsNone(row['deadline_at'])
        text = json.dumps(result)
        for excluded in ('PRIVATE', str(path), CHILD, 'last_agent_message'):
            self.assertNotIn(excluded, text)

    def test_same_named_agent_in_other_parent_is_excluded(self):
        self.child(parent=OTHER)
        self.assertEqual(self.read()['agents'], {})

    def test_conflicting_source_parent_or_path_is_excluded(self):
        for values in ({'source_parent': OTHER}, {'source_path': '/root/other'}, {'source': '{broken'}):
            with self.subTest(values=values):
                with self.connect() as connection:
                    connection.execute('DELETE FROM threads')
                    connection.execute('DELETE FROM thread_spawn_edges')
                self.child(**values)
                self.assertEqual(self.read()['agents'], {})

    def test_duplicate_exact_child_name_fails_closed(self):
        self.child()
        self.child(identifier=CHILD2)
        self.assertEqual(self.read()['agents'], {})
        self.assertEqual(self.read()['status'], 'partial')

    def test_open_spawn_edge_is_not_a_running_worker_claim(self):
        self.child()
        row = self.read()['agents']['worker']
        self.assertEqual(row['lifecycle_state'], 'unknown')
        self.assertIn('not a live heartbeat', row['reason'])
        self.assertEqual(row['elapsed_seconds'], 50)

    def test_missing_database_or_schema_is_an_explained_unavailable_result(self):
        self.database.unlink()
        result = self.read()
        self.assertEqual(result['status'], 'unavailable')
        self.assertIn('unavailable', result['reason'])
        self.assertFalse(self.database.exists(), 'Read-only lookup must not create a database')
        with self.connect() as connection:
            connection.execute('CREATE TABLE threads (id TEXT)')
        self.assertIn('unsupported schema', self.read()['reason'])

    def test_locked_database_fails_without_unbounded_wait(self):
        connection = sqlite3.connect(self.database)
        self.addCleanup(connection.close)
        connection.execute('BEGIN EXCLUSIVE')
        result = self.read()
        self.assertEqual(result['status'], 'unavailable')
        self.assertIn('busy', result['reason'])
        connection.rollback()

    def test_database_is_not_modified_by_snapshot(self):
        self.child()
        original = self.database.read_bytes()
        self.read()
        self.assertEqual(self.database.read_bytes(), original)

    def test_reused_session_uses_only_turn_inside_this_run(self):
        path = self.child(started=0, saved=200)
        self.event(path, at=60, started=40, ended=60)
        self.event(path, at=200, started=180, ended=200)
        row = self.read(run_started_at=stamp(100))['agents']['worker']
        self.assertEqual(row['started_at'], stamp(180))
        self.assertEqual(row['elapsed_seconds'], 20)
        self.assertEqual(row['elapsed_label'], 'Latest turn span (includes waits)')
        self.assertIn('reused', row['reason'])

    def test_reused_turn_crossing_run_start_does_not_invent_assignment_start(self):
        path = self.child(started=0, saved=200)
        self.event(path, at=200, started=80, ended=200)
        row = self.read(run_started_at=stamp(100))['agents']['worker']
        self.assertIsNone(row['started_at'])
        self.assertIsNone(row['elapsed_seconds'])
        self.assertIsNone(row['last_turn_duration_seconds'])

    def test_later_followup_does_not_inflate_completed_run(self):
        path = self.child(started=10, saved=200)
        self.event(path, at=60, started=40, ended=60)
        self.event(path, at=200, started=180, ended=200)
        row = self.read(run_started_at=stamp(0), run_ended_at=stamp(100))['agents']['worker']
        self.assertEqual(row['updated_at'], stamp(60))
        self.assertEqual(row['elapsed_seconds'], 50)
        self.assertEqual(row['last_turn_duration_seconds'], 20)
        self.assertIsNone(row['session_updated_at'])

    def test_no_in_range_evidence_does_not_use_run_end_as_a_provider_update(self):
        path = self.child(started=10, saved=200)
        self.event(path, at=200, started=180, ended=200)
        row = self.read(run_started_at=stamp(0), run_ended_at=stamp(100))['agents']['worker']
        self.assertIsNone(row['updated_at'])
        self.assertIsNone(row['elapsed_seconds'])
        self.assertIn('within its bounds', row['reason'])

    def test_agent_entirely_outside_run_is_not_attributed(self):
        self.child(started=10, saved=60)
        self.assertEqual(self.read(run_started_at=stamp(100))['agents'], {})
        self.assertEqual(self.read(run_ended_at=stamp(5))['agents'], {})

    def test_started_event_timestamp_can_scope_reused_active_turn(self):
        path = self.child(started=0, saved=180)
        self.event(path, kind='task_started', at=180, started_at=None, completed_at=None)
        row = self.read(run_started_at=stamp(100))['agents']['worker']
        self.assertEqual(row['lifecycle_state'], 'active')
        self.assertEqual(row['started_at'], stamp(180))
        self.assertIsNone(row['last_turn_duration_seconds'])

    def test_invalid_input_is_bounded_and_never_queries_database(self):
        with patch('native_activity._database_rows', side_effect=AssertionError('must not read')):
            self.assertEqual(snapshot(self.home, '../other', ['worker'])['status'], 'unavailable')
            self.assertEqual(snapshot(self.home, PARENT, ['worker'] * 33)['status'], 'unavailable')
            self.assertEqual(self.read(run_started_at='bad')['status'], 'unavailable')
            self.assertEqual(self.read(run_started_at=stamp(100), run_ended_at=stamp(0))['status'], 'unavailable')
            self.assertEqual(snapshot(self.home, PARENT, ['../worker'])['agents'], {})

    def test_tail_ignores_huge_prefix_partial_records_and_private_reasoning(self):
        path = self.child()
        path.write_text(json.dumps({'type': 'response_item', 'payload': {'type': 'reasoning',
            'text': 'PRIVATE REASONING' * 10000}}) + '\n', encoding='utf-8')
        self.event(path)
        with path.open('ab') as stream:
            stream.write(b'{"type":"event_msg","timestamp":"incomplete')
        row = self.read()['agents']['worker']
        self.assertEqual(row['last_turn_duration_seconds'], 20)
        self.assertNotIn('PRIVATE', json.dumps(row))

    def test_unchanged_tail_is_cached_and_appends_are_observed(self):
        path = self.child()
        self.event(path)
        self.read()
        with patch.object(Path, 'open', side_effect=AssertionError('unchanged tail must not be reread')):
            self.assertEqual(self.read()['agents']['worker']['last_event'], 'task_complete')
        self.event(path, kind='turn_aborted', at=61, started=60, ended=61)
        self.assertEqual(self.read()['agents']['worker']['last_event'], 'turn_aborted')

    def test_outside_or_wrong_session_transcript_retains_only_database_timing(self):
        self.child()
        outside = self.home / ('rollout-' + CHILD + '.jsonl')
        outside.write_text('', encoding='utf-8')
        self.event(outside)
        with self.connect() as connection:
            connection.execute('UPDATE threads SET rollout_path=?', (str(outside),))
        row = self.read()['agents']['worker']
        self.assertEqual(row['elapsed_seconds'], 50)
        self.assertIsNone(row['last_event'])
        self.assertIn('unavailable', row['warning'])
        wrong = self.sessions / ('rollout-' + OTHER + '.jsonl')
        wrong.write_bytes(outside.read_bytes())
        with self.connect() as connection:
            connection.execute('UPDATE threads SET rollout_path=?', (str(wrong),))
        self.assertIn('does not match', self.read()['agents']['worker']['warning'])

    def test_symlink_transcript_is_not_followed(self):
        path = self.child()
        target = self.home / 'outside.jsonl'
        target.write_text('', encoding='utf-8')
        self.event(target)
        path.unlink()
        try:
            path.symlink_to(target)
        except OSError:
            self.skipTest('Symlink creation is unavailable')
        row = self.read()['agents']['worker']
        self.assertIsNone(row['last_event'])
        self.assertIsNotNone(row['warning'])

    def test_malformed_lifecycle_values_cannot_escape_or_break_snapshot(self):
        path = self.child()
        self.event(path, duration=10 ** 500, started_at='bad', completed_at='bad')
        with path.open('a', encoding='utf-8') as stream:
            stream.write('not json\n[]\n')
            stream.write(json.dumps({'type': 'event_msg', 'timestamp': stamp(60), 'payload': {'type': []}}) + '\n')
        row = self.read()['agents']['worker']
        self.assertIsNone(row['last_turn_duration_seconds'])
        self.assertIsNone(row['last_turn_started_at'])
        self.assertEqual(row['elapsed_seconds'], 50)


if __name__ == '__main__':
    unittest.main()
