"""Phase timing and scoped dashboard metadata, without live providers."""
from datetime import datetime, timezone
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'app'))
from task_activity import PhaseTracker, snapshot
from task_store import TaskStore, write_json


class ActivityTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.run = self.root / '.orchestration/audit'
        self.run.mkdir(parents=True)
        write_json(self.run / 'run.json', {'run_id': 'audit'})
        self.store = TaskStore(self.root / 'runs/tasks')

    def task(self, project='audit', **values):
        task = self.store.create(worker='grok', task='PRIVATE TASK TITLE', assignment_project_id=project)
        task.update(status='running', execution_status='running', started_at=datetime.now(timezone.utc).isoformat(),
                    response='PRIVATE ANSWER', timeout_seconds=450)
        task.update(values)
        self.store.save(task['job_id'], task)
        return task

    def test_phase_durations_measure_waits_without_output_content(self):
        task = self.task()
        ticks = iter([10, 12.5, 17.5])
        tracker = PhaseTracker(self.store.directory(task['job_id']), clock=lambda: next(ticks))
        tracker.set('quota_refresh')
        tracker.set('provider_execution')
        receipt = tracker.set('awaiting_review')
        self.assertEqual(receipt['phase_durations_ms'], {'quota_refresh': 2500, 'provider_execution': 5000})
        self.assertNotIn('PRIVATE', json.dumps(receipt))

    def test_snapshot_reads_exact_project_and_real_progress_location(self):
        task = self.task()
        self.task('unrelated')
        progress = self.root / 'runtime/workspaces/tasks' / task['job_id']
        progress.mkdir(parents=True)
        write_json(progress / 'execution-progress.json', {'state': 'answering', 'answer_chars': 42, 'thought': 'PRIVATE THOUGHT'})
        rows = snapshot(self.root, 'audit')['tasks']
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]['progress']['answer_chars'], 42)
        self.assertIsNotNone(rows[0]['updated_at'])
        self.assertIsNotNone(rows[0]['deadline_at'])
        self.assertNotIn('PRIVATE', json.dumps(rows))

    def test_canonical_project_wins_over_forged_index_scope(self):
        task = self.task('unrelated')
        index = dict(task, assignment_project_id='audit')
        write_json(self.store.directory(task['job_id']) / 'record.json', index)
        self.assertEqual(snapshot(self.root, 'audit')['tasks'], [])

    def test_explicit_checkpoint_job_and_shared_project(self):
        task = self.task('shared')
        write_json(self.run / 'coordinator.json', {'checkpoint': {'open_jobs': [{'job_id': task['job_id']}]}})
        self.assertEqual(snapshot(self.root, 'audit')['task_count'], 1)
        write_json(self.run / 'coordinator.json', {})
        self.assertEqual(snapshot(self.root, 'audit', ['shared'])['task_count'], 1)

    def test_explicit_job_survives_missing_index(self):
        task = self.task()
        write_json(self.run / 'run.json', {'tasks': [{'job_id': task['job_id']}]})
        (self.store.directory(task['job_id']) / 'record.json').unlink()
        self.assertEqual(snapshot(self.root, 'audit')['task_count'], 1)

    def test_explicit_job_is_read_before_capped_project_scan(self):
        task = self.task()
        write_json(self.run / 'run.json', {'tasks': [{'job_id': task['job_id']}]})
        omitted = [self.root / ('unrelated-' + str(i)) for i in range(2001)]
        with patch.object(Path, 'iterdir', return_value=iter(omitted)):
            result = snapshot(self.root, 'audit')
        self.assertEqual(result['tasks'][0]['job_id'], task['job_id'])
        self.assertTrue(result['truncated'])

    def test_native_checkpoint_is_labeled_without_invented_live_timing(self):
        write_json(self.run / 'run.json', {'tasks': [{'agent': 'audit_bot', 'status': 'running'}]})
        row = snapshot(self.root, 'audit')['tasks'][0]
        self.assertEqual(row['phase'], 'recorded_running')
        self.assertIsNone(row['updated_at'])
        self.assertIsNone(row['elapsed_seconds'])
        self.assertIn('reported by the coordinator', row['reason'])
        self.assertIn('No Codex parent session', row['reason'])

    def test_native_adapter_supplies_verified_timing_even_after_handoff(self):
        parent = '12345678-1234-5678-abcd-123456789abc'
        write_json(self.run / 'run.json', {'native_parent_session_id': parent, 'created_utc': '2026-09-09T00:00:00Z',
            'completed_utc': '2026-09-09T01:00:00Z', 'status': 'completed',
            'tasks': [{'agent': 'audit_bot', 'status': 'completed'}]})
        write_json(self.run / 'coordinator.json', {'owner': 'fable', 'session': 'receiving-lead'})
        metadata = {'source': 'codex_local_metadata', 'started_at': '2026-09-09T00:00:02Z',
            'updated_at': '2026-09-09T00:20:02Z', 'elapsed_seconds': 1200,
            'elapsed_label': 'Session span (includes pauses)', 'last_turn_duration_seconds': 90,
            'lifecycle_state': 'idle'}
        with patch('native_activity.snapshot', return_value={'status': 'available', 'agents': {'audit_bot': metadata}}) as adapter:
            row = snapshot(self.root, 'audit', home=self.root / 'home')['tasks'][0]
        self.assertEqual(row['elapsed_seconds'], 1200)
        self.assertEqual(row['last_turn_duration_seconds'], 90)
        self.assertEqual(row['phase'], 'completed')
        self.assertEqual(row['updated_label'], 'Saved activity')
        self.assertIsNone(row['deadline_at'])
        self.assertEqual(adapter.call_args.args[:3], (self.root / 'home', parent, ['audit_bot']))
        self.assertEqual(adapter.call_args.kwargs['run_ended_at'], '2026-09-09T01:00:00Z')

    def test_stale_viewer_binding_does_not_read_an_unrelated_parent(self):
        write_json(self.run / 'run.json', {'tasks': [{'agent': 'audit_bot', 'status': 'completed'}]})
        write_json(self.run / 'coordinator.json', {'owner': 'astra', 'session': 'current', 'checkpoint_at': '2026-09-09T01:00:00Z'})
        write_json(self.run / 'viewer-session.json', {'provider': 'codex', 'owner': 'astra', 'session': 'old', 'session_id': '12345678-1234-5678-abcd-123456789abc'})
        with patch('native_activity.snapshot', side_effect=AssertionError('Unrelated binding must not be read')):
            row = snapshot(self.root, 'audit')['tasks'][0]
        self.assertIsNone(row['elapsed_seconds'])
        self.assertEqual(row['updated_label'], 'Coordinator checkpoint')
        self.assertEqual(row['updated_at'], '2026-09-09T01:00:00+00:00')

    def test_missing_start_is_unknown_not_zero_execution(self):
        self.task(started_at=None, created_at=None, timeout_seconds=None)
        row = snapshot(self.root, 'audit')['tasks'][0]
        self.assertIsNone(row['elapsed_seconds'])
        self.assertIsNone(row['deadline_at'])

    def test_accepted_review_overrides_old_dispatch_phase(self):
        task = self.task(status='accepted')
        PhaseTracker(self.store.directory(task['job_id'])).set('awaiting_review')
        write_json(self.store.directory(task['job_id']) / 'memory-outcome.json', {'status': 'remembered'})
        row = snapshot(self.root, 'audit')['tasks'][0]
        self.assertEqual(row['phase'], 'accepted')
        self.assertEqual(row['memory_status'], 'remembered')

    def test_malformed_durations_cannot_break_panel(self):
        task = self.task()
        write_json(self.store.directory(task['job_id']) / 'activity.json', {'job_id': task['job_id'],
            'phase': 'quota_refresh', 'phase_durations_ms': {'quota_refresh': 10**1000, 'provider_execution': -1}})
        self.assertEqual(snapshot(self.root, 'audit')['tasks'][0]['phase_durations_ms'], {})

    def test_activity_write_failure_is_visible_to_dispatcher_without_raising(self):
        task = self.task()
        tracker = PhaseTracker(self.store.directory(task['job_id']))
        with patch('task_activity.write_json', side_effect=OSError('disk')):
            tracker.set('quota_refresh')
        self.assertEqual(tracker.error, 'OSError')

    def test_invalid_run_is_not_resolved(self):
        self.assertEqual(snapshot(self.root, '../audit')['tasks'], [])


if __name__ == '__main__':
    unittest.main()
