"""Saved progress, deadlines, and handoff lineage stay read-only and bounded."""
import json
import os
import sys
import tempfile
import unittest
from unittest.mock import patch
from types import SimpleNamespace
import builtins
import stat
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'app'))
from task_progress_view import (
    PREVIEW_DISPLAY_CHARS,
    PREVIEW_MAX_BYTES,
    PROGRESS_MAX_BYTES,
    enrich_task,
    _bounded_read,
    _contained_file,
)


def _row(job='a' * 32, **extra):
    base = {
        'id': job,
        'title': 'Current task',
        'worker': 'claude',
        'state': 'working',
        'group': 'Working',
        'label': 'Recorded as working',
        'next_step': 'This was the last saved state, not a live connection.',
        'answer': '',
        'explanation': '',
        'reviewer': '',
        'review_note': '',
        'reviewed_at': None,
        'created_at': '2026-09-07T10:00:00+00:00',
        'started_at': None,
        'finished_at': None,
        'updated_at': '2026-09-07T10:00:00+00:00',
        'notices': [],
    }
    base.update(extra)
    return base


class TaskProgressViewTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.tasks = self.root / 'tasks'
        self.workspaces = self.root / 'workspaces'
        self.tasks.mkdir()
        (self.workspaces / 'tasks').mkdir(parents=True)

    def work(self, job):
        folder = self.workspaces / 'tasks' / job
        folder.mkdir(parents=True, exist_ok=True)
        return folder

    def task_folder(self, job, **data):
        folder = self.tasks / job
        folder.mkdir(parents=True, exist_ok=True)
        payload = {
            'job_id': job,
            'task': 'Prior work',
            'worker': 'codex',
            'status': 'accepted',
            'execution_status': 'succeeded',
            'review_status': 'accepted',
            'created_at': '2026-09-07T09:00:00+00:00',
            'finalized_at': '2026-09-07T09:30:00+00:00',
        }
        payload.update(data)
        (folder / 'record.json').write_text(json.dumps({k: v for k, v in payload.items() if k != 'response'}), encoding='utf-8')
        (folder / 'result.json').write_text(json.dumps(payload), encoding='utf-8')
        return folder

    def enrich(self, row, data=None, current_known=False):
        return enrich_task(row, data or {}, tasks_root=self.tasks, workspaces_root=self.workspaces, current_known=current_known)

    def test_absent_progress_stays_unknown_without_notices(self):
        row = self.enrich(_row())
        self.assertFalse(row['progress']['present'])
        self.assertFalse(row['progress']['timeout_known'])
        self.assertEqual(row['preview']['text'], '')
        self.assertEqual(row['handoff']['lineage'], [])
        self.assertEqual(row['notices'], [])
        self.assertEqual(row['group'], 'Working')

    def test_canonical_size_and_timeout_without_snapshot(self):
        job = 'c0' + 'a' * 30
        row = self.enrich(_row(job), {
            'job_id': job,
            'status': 'running',
            'size': 'medium',
            'timeout_seconds': 180,
        })
        self.assertFalse(row['progress']['present'])
        self.assertEqual(row['progress']['size'], 'medium')
        self.assertEqual(row['progress']['timeout_seconds'], 180)
        self.assertTrue(row['progress']['timeout_known'])
        self.assertEqual(row['notices'], [])

    def test_canonical_timeout_wins_over_conflicting_snapshot(self):
        job = 'c1' + 'b' * 30
        folder = self.work(job)
        (folder / 'execution-progress.json').write_text(json.dumps({
            'state': 'answering',
            'timeout_seconds': 30,
            'process_status': 'running',
        }), encoding='utf-8')
        row = self.enrich(_row(job), {
            'job_id': job,
            'status': 'running',
            'size': 'large',
            'timeout_seconds': 600,
        })
        self.assertTrue(row['progress']['present'])
        self.assertEqual(row['progress']['size'], 'large')
        self.assertEqual(row['progress']['timeout_seconds'], 600)
        self.assertTrue(row['progress']['timeout_known'])

    def test_invalid_size_is_not_guessed(self):
        job = 'c2' + 'c' * 30
        row = self.enrich(_row(job), {'size': 'huge', 'timeout_seconds': -4, 'status': 'running'})
        self.assertNotIn('size', row['progress'])
        self.assertFalse(row['progress']['timeout_known'])
        self.assertNotIn('timeout_seconds', row['progress'])

    def test_valid_progress_and_deadline_are_copied_from_saved_snapshot(self):
        job = 'b' * 32
        folder = self.work(job)
        snapshot = {
            'state': 'answering',
            'event_count': 4,
            'answer_chars': 12,
            'elapsed_s': 9.5,
            'timeout_seconds': 120,
            'process_status': 'running',
            'output_mode': 'stream-json',
            'terminal_received': False,
            'retry_count': 0,
            'model': 'claude-sonnet-4',
            'stdout_chars': 40,
            'stderr_chars': 0,
        }
        (folder / 'execution-progress.json').write_text(json.dumps(snapshot), encoding='utf-8')
        (folder / 'partial-response.txt').write_text('Draft so far', encoding='utf-8')
        row = self.enrich(_row(job))
        self.assertTrue(row['progress']['present'])
        self.assertEqual(row['progress']['timeout_seconds'], 120)
        self.assertTrue(row['progress']['timeout_known'])
        self.assertEqual(row['progress']['state'], 'answering')
        self.assertTrue(row['progress']['incomplete'])
        self.assertEqual(row['preview']['text'], 'Draft so far')
        self.assertTrue(row['preview']['incomplete'])
        self.assertFalse(row['preview']['truncated'])

    def test_historic_deadline_unknown_when_timeout_missing(self):
        job = 'c' * 32
        folder = self.work(job)
        (folder / 'execution-progress.json').write_text(json.dumps({
            'state': 'waiting', 'event_count': 1, 'process_status': 'starting',
        }), encoding='utf-8')
        row = self.enrich(_row(job))
        self.assertTrue(row['progress']['present'])
        self.assertFalse(row['progress']['timeout_known'])
        self.assertNotIn('timeout_seconds', row['progress'])
        self.assertTrue(any('deadline' in note for note in row['notices']))

    def test_malformed_progress_is_a_notice_not_content(self):
        job = 'd' * 32
        folder = self.work(job)
        (folder / 'execution-progress.json').write_text('not-json<script>', encoding='utf-8')
        row = self.enrich(_row(job))
        self.assertFalse(row['progress']['present'])
        self.assertTrue(row['notices'])
        self.assertNotIn('<script>', json.dumps(row))

    def test_oversized_progress_is_refused(self):
        job = 'e' * 32
        folder = self.work(job)
        (folder / 'execution-progress.json').write_bytes(b'{' + b'a' * (PROGRESS_MAX_BYTES + 10) + b'}')
        row = self.enrich(_row(job))
        self.assertFalse(row['progress']['present'])
        self.assertTrue(any('larger' in note for note in row['notices']))

    def test_arbitrary_json_paths_and_private_fields_are_ignored(self):
        job = 'f' * 32
        folder = self.work(job)
        (folder / 'execution-progress.json').write_text(json.dumps({
            'state': 'thinking',
            'timeout_seconds': 30,
            'process_status': 'running',
            'canonical_result': 'C:\\secret\\result.json',
            'thinking': 'PRIVATE THOUGHT',
            'terminal_result': {'result': 'SECRET ANSWER'},
            'stderr': 'PRIVATE STDERR',
            'path': '../escape',
        }), encoding='utf-8')
        row = self.enrich(_row(job))
        dumped = json.dumps(row)
        self.assertNotIn('PRIVATE', dumped)
        self.assertNotIn('SECRET', dumped)
        self.assertNotIn('canonical_result', dumped)
        self.assertNotIn('escape', dumped)
        self.assertEqual(row['progress']['state'], 'thinking')

    def test_invalid_numbers_and_non_bool_flags_are_dropped(self):
        job = '1' * 32
        folder = self.work(job)
        (folder / 'execution-progress.json').write_text(json.dumps({
            'state': 'answering',
            'event_count': -3,
            'elapsed_s': float('nan'),
            'timeout_seconds': True,
            'terminal_received': 1,
            'retry_count': '4',
            'process_status': 'running',
        }), encoding='utf-8')
        row = self.enrich(_row(job))
        self.assertNotIn('event_count', row['progress'])
        self.assertNotIn('elapsed_s', row['progress'])
        self.assertNotIn('timeout_seconds', row['progress'])
        self.assertNotIn('terminal_received', row['progress'])
        self.assertTrue(row['notices'])

    def test_preview_is_capped_and_labeled_truncated_and_incomplete(self):
        job = '2' * 32
        folder = self.work(job)
        (folder / 'execution-progress.json').write_text(json.dumps({
            'state': 'answering',
            'timeout_seconds': 90,
            'process_status': 'running',
            'terminal_received': False,
        }), encoding='utf-8')
        (folder / 'partial-response.txt').write_text('X' * (PREVIEW_DISPLAY_CHARS + 50), encoding='utf-8')
        row = self.enrich(_row(job))
        self.assertEqual(len(row['preview']['text']), PREVIEW_DISPLAY_CHARS)
        self.assertTrue(row['preview']['truncated'])
        self.assertTrue(row['preview']['incomplete'])

    def test_preview_stays_incomplete_after_terminal_finished_state(self):
        job = 'd9' + 'a' * 30
        folder = self.work(job)
        (folder / 'execution-progress.json').write_text(json.dumps({
            'state': 'finished',
            'timeout_seconds': 90,
            'process_status': 'exited',
            'terminal_received': True,
        }), encoding='utf-8')
        (folder / 'partial-response.txt').write_text('Not the reviewed final answer', encoding='utf-8')
        row = self.enrich(_row(job), {'status': 'accepted', 'job_id': job})
        self.assertTrue(row['preview']['incomplete'])
        self.assertTrue(row['progress']['incomplete'])
        self.assertEqual(row['preview']['text'], 'Not the reviewed final answer')

    def test_oversized_preview_file_is_noticed(self):
        job = '3' * 32
        folder = self.work(job)
        (folder / 'partial-response.txt').write_bytes(b'Y' * (PREVIEW_MAX_BYTES + 20))
        row = self.enrich(_row(job))
        self.assertTrue(row['preview']['truncated'] or row['notices'])
        self.assertTrue(any('draft' in note.lower() or 'larger' in note for note in row['notices']))

    def test_invalid_job_id_does_not_read_workspace(self):
        folder = self.work('not-a-job-id')
        (folder / 'execution-progress.json').write_text('{}', encoding='utf-8')
        row = self.enrich(_row('not-a-job-id'))
        self.assertFalse(row['progress']['present'])

    def test_symlink_progress_is_refused_when_fixture_supported(self):
        job = '4' * 32
        real = self.root / 'outside-progress.json'
        real.write_text(json.dumps({'state': 'answering', 'timeout_seconds': 12, 'process_status': 'running'}), encoding='utf-8')
        folder = self.work(job)
        target = folder / 'execution-progress.json'
        try:
            target.symlink_to(real)
        except (OSError, NotImplementedError):
            self.skipTest('symlinks are not available in this fixture')
        if not target.is_symlink() and not (getattr(os.lstat(target), 'st_file_attributes', 0) & 0x400):
            self.skipTest('created link was not a reparse point')
        row = self.enrich(_row(job))
        self.assertFalse(row['progress']['present'])
        self.assertTrue(row['notices'])
        self.assertNotEqual(row['progress'].get('timeout_seconds'), 12)

    def test_valid_predecessor_lineage_uses_canonical_worker(self):
        prior = '5' * 32
        current = '6' * 32
        self.task_folder(
            prior, worker='grok', task='First pass', handoff_to_job_id=current,
            assignment_project_id='maps',
        )
        data = {
            'handoff_from_job_id': prior,
            'assignment_project_id': 'maps',
            'job_id': current,
            'status': 'running',
        }
        row = self.enrich(_row(current), data, current_known=True)
        self.assertEqual(len(row['handoff']['lineage']), 1)
        self.assertEqual(row['handoff']['lineage'][0]['worker'], 'grok')
        self.assertTrue(row['handoff']['lineage'][0]['known'])
        self.assertTrue(row['handoff']['lineage'][0]['verified'])
        self.assertEqual(row['handoff']['lineage'][0]['title'], 'First pass')

    def test_missing_predecessor_does_not_break_the_row(self):
        current = '7' * 32
        row = self.enrich(_row(current), {'handoff_from_job_id': '8' * 32, 'status': 'running'})
        self.assertEqual(row['id'], current)
        self.assertTrue(row['handoff']['lineage'])
        self.assertFalse(row['handoff']['lineage'][0]['known'])
        self.assertEqual(row['handoff']['lineage'][0]['worker'], 'Unknown worker')

    def test_malformed_predecessor_id_is_noticed(self):
        row = self.enrich(_row(), {'handoff_from_job_id': 'not-hex', 'status': 'running'})
        self.assertEqual(row['handoff']['lineage'], [])
        self.assertTrue(row['notices'])

    def test_cyclic_lineage_stops(self):
        a = '9' * 32
        b = '0' * 32
        self.task_folder(a, worker='claude', handoff_from_job_id=b, handoff_to_job_id=b)
        self.task_folder(b, worker='grok', handoff_from_job_id=a, handoff_to_job_id=a)
        row = self.enrich(_row(a), {'handoff_from_job_id': b, 'status': 'running', 'job_id': a})
        ids = [item['id'] for item in row['handoff']['lineage']]
        self.assertEqual(len(ids), len(set(ids)))
        self.assertTrue(any('repeats' in note for note in row['notices']))

    def test_mismatched_project_evidence_is_not_treated_as_lineage(self):
        prior = 'a1' + 'b' * 30
        current = 'a2' + 'c' * 30
        self.task_folder(prior, worker='gemini', assignment_project_id='alpha')
        row = self.enrich(_row(current), {
            'handoff_from_job_id': prior,
            'assignment_project_id': 'beta',
            'status': 'running',
        })
        self.assertFalse(row['handoff']['lineage'][0]['known'])
        self.assertEqual(row['handoff']['lineage'][0]['worker'], 'Unknown worker')

    def test_one_sided_project_evidence_is_unverified(self):
        prior = 'b1' + 'd' * 30
        current = 'b2' + 'e' * 30
        self.task_folder(prior, worker='grok', assignment_project_id='maps')
        row = self.enrich(_row(current), {
            'handoff_from_job_id': prior,
            'status': 'running',
        })
        self.assertFalse(row['handoff']['lineage'][0]['known'])
        self.assertFalse(row['handoff']['lineage'][0]['verified'])
        self.assertTrue(any('unverified' in note for note in row['notices']))

    def test_arbitrary_valid_task_json_is_not_a_verified_handoff(self):
        prior = 'b3' + 'f' * 30
        current = 'b4' + 'a' * 30
        self.task_folder(prior, worker='codex')
        row = self.enrich(_row(current), {
            'handoff_from_job_id': prior,
            'assignment_project_id': 'maps',
            'status': 'running',
        })
        self.assertFalse(row['handoff']['lineage'][0]['verified'])
        self.assertFalse(row['handoff']['lineage'][0]['known'])

    def test_malformed_result_does_not_fall_back_to_index(self):
        prior = 'b5' + 'c' * 30
        current = 'b6' + 'd' * 30
        folder = self.tasks / prior
        folder.mkdir()
        (folder / 'record.json').write_text(json.dumps({
            'job_id': prior,
            'status': 'accepted',
            'worker': 'should-not-verify',
            'task': 'Stale index',
            'assignment_project_id': 'maps',
        }), encoding='utf-8')
        (folder / 'result.json').write_text('not-json', encoding='utf-8')
        row = self.enrich(_row(current), {
            'handoff_from_job_id': prior,
            'assignment_project_id': 'maps',
            'status': 'running',
        })
        self.assertFalse(row['handoff']['lineage'][0]['known'])
        self.assertNotEqual(row['handoff']['lineage'][0]['worker'], 'should-not-verify')
        self.assertTrue(any('canonical result' in note or 'unknown' in note.lower() for note in row['notices']))

    def test_missing_result_uses_index_with_limitation(self):
        prior = 'b7' + 'e' * 30
        current = 'b8' + 'f' * 30
        folder = self.tasks / prior
        folder.mkdir()
        (folder / 'record.json').write_text(json.dumps({
            'job_id': prior,
            'status': 'accepted',
            'worker': 'grok',
            'task': 'Index only',
            'assignment_project_id': 'maps',
            'handoff_to_job_id': current,
        }), encoding='utf-8')
        row = self.enrich(_row(current), {
            'handoff_from_job_id': prior,
            'assignment_project_id': 'maps',
            'status': 'running',
        })
        self.assertTrue(row['handoff']['lineage'][0]['known'])
        self.assertTrue(row['handoff']['lineage'][0]['index_only'])
        self.assertFalse(row['handoff']['lineage'][0]['verified'])
        self.assertEqual(row['handoff']['lineage'][0]['worker'], 'grok')
        self.assertTrue(any('index' in note for note in row['notices']))

    def test_worker_is_not_inferred_from_unrelated_predecessor_fields(self):
        prior = 'a3' + 'd' * 30
        current = 'a4' + 'e' * 30
        folder = self.tasks / prior
        folder.mkdir()
        (folder / 'record.json').write_text(json.dumps({
            'job_id': prior,
            'status': 'accepted',
            'last_worker_guess': 'should-not-appear',
            'provider_result': {'worker': 'hidden-provider'},
        }), encoding='utf-8')
        row = self.enrich(_row(current), {'handoff_from_job_id': prior, 'status': 'running'})
        dumped = json.dumps(row)
        self.assertNotIn('should-not-appear', dumped)
        self.assertNotIn('hidden-provider', dumped)
        self.assertEqual(row['handoff']['lineage'][0]['worker'], 'Unknown worker')

    def test_output_limit_fields_are_copied_without_calling_observed_saved(self):
        job = 'a0' + '9' * 30
        folder = self.work(job)
        (folder / 'execution-progress.json').write_text(json.dumps({
            'state': 'failed',
            'process_status': 'output_limit',
            'preview_truncated': True,
            'output_truncated': True,
            'stderr_truncated': False,
            'retained_answer_chars': 40,
            'observed_answer_chars': 9000,
            'answer_chars': 40,
            'timeout_seconds': 60,
            'terminal_received': True,
        }), encoding='utf-8')
        row = self.enrich(_row(job))
        self.assertEqual(row['progress']['process_status'], 'output_limit')
        self.assertTrue(row['progress']['preview_truncated'])
        self.assertTrue(row['progress']['output_truncated'])
        self.assertEqual(row['progress']['observed_answer_chars'], 9000)
        self.assertEqual(row['progress']['retained_answer_chars'], 40)
        self.assertTrue(row['preview']['incomplete'])

    def test_old_jobs_without_output_limit_fields_remain_fine(self):
        job = 'a1' + '8' * 30
        folder = self.work(job)
        (folder / 'execution-progress.json').write_text(json.dumps({
            'state': 'answering',
            'process_status': 'running',
            'timeout_seconds': 20,
        }), encoding='utf-8')
        row = self.enrich(_row(job))
        self.assertNotIn('preview_truncated', row['progress'])
        self.assertNotIn('observed_answer_chars', row['progress'])
        self.assertTrue(row['progress']['present'])

    def test_enrich_does_not_write_workspace_or_task_files(self):
        job = 'a5' + 'f' * 30
        folder = self.work(job)
        progress = folder / 'execution-progress.json'
        progress.write_text(json.dumps({'state': 'waiting', 'process_status': 'starting'}), encoding='utf-8')
        before = {path: (path.read_bytes(), path.stat().st_mtime_ns) for path in folder.iterdir()}
        self.enrich(_row(job))
        after = {path: (path.read_bytes(), path.stat().st_mtime_ns) for path in folder.iterdir()}
        self.assertEqual(before, after)

    def test_missing_workspace_root_is_not_an_unsafe_notice(self):
        row = enrich_task(_row(), {'size': 'small', 'timeout_seconds': 45}, tasks_root=self.tasks,
                          workspaces_root=self.root / 'historic-missing-workspaces')
        self.assertEqual(row['notices'], [])
        self.assertEqual(row['progress']['size'], 'small')
        self.assertFalse(row['progress']['present'])

    def test_short_preview_still_reports_upstream_truncation(self):
        job = 'a6' + '1' * 30
        folder = self.work(job)
        (folder / 'execution-progress.json').write_text(json.dumps({
            'state': 'finished', 'terminal_received': True, 'preview_truncated': True,
            'observed_answer_chars': 900, 'retained_answer_chars': 5, 'timeout_seconds': 30,
        }), encoding='utf-8')
        (folder / 'partial-response.txt').write_text('Draft', encoding='utf-8')
        row = self.enrich(_row(job, answer='Different final answer'), {'job_id': job, 'status': 'accepted'})
        self.assertEqual(row['preview']['text'], 'Draft')
        self.assertTrue(row['preview']['truncated'])
        self.assertTrue(row['preview']['source_truncated'])
        self.assertTrue(row['preview']['incomplete'])
        self.assertEqual(row['answer'], 'Different final answer')

    def test_unknown_current_source_does_not_invent_verified_lineage(self):
        prior, current = 'a7' + '2' * 30, 'a8' + '3' * 30
        self.task_folder(prior, assignment_project_id='project', worker='grok')
        data = {'job_id': current, 'handoff_from_job_id': prior, 'assignment_project_id': 'project'}
        row = self.enrich(_row(current), data)
        self.assertTrue(row['handoff']['lineage'][0]['known'])
        self.assertFalse(row['handoff']['lineage'][0]['verified'])

    def test_unrecorded_historic_project_links_stay_unverified(self):
        prior, current = 'a9' + '4' * 30, 'aa' + '5' * 30
        self.task_folder(prior, worker='grok')
        row = self.enrich(_row(current), {'job_id': current, 'handoff_from_job_id': prior}, current_known=True)
        self.assertTrue(row['handoff']['lineage'][0]['known'])
        self.assertFalse(row['handoff']['lineage'][0]['verified'])
        self.assertTrue(any('unverified' in note for note in row['notices']))

    def test_mismatched_current_identity_cannot_claim_verified_lineage(self):
        prior, current = 'ab' + '6' * 30, 'ac' + '7' * 30
        self.task_folder(prior, assignment_project_id='project', worker='grok')
        row = self.enrich(_row(current), {'job_id': 'f' * 32, 'handoff_from_job_id': prior,
                                        'assignment_project_id': 'project'}, current_known=True)
        self.assertFalse(row['handoff']['lineage'][0]['verified'])

    def test_directory_in_place_of_canonical_result_never_uses_index(self):
        prior, current = 'ad' + '8' * 30, 'ae' + '9' * 30
        folder = self.task_folder(prior, assignment_project_id='project')
        (folder / 'result.json').unlink()
        (folder / 'result.json').mkdir()
        row = self.enrich(_row(current), {'job_id': current, 'handoff_from_job_id': prior,
                                        'assignment_project_id': 'project'}, current_known=True)
        self.assertFalse(row['handoff']['lineage'][0]['known'])

    def test_dangling_reparse_file_is_unsafe_even_without_target(self):
        folder = self.work('b0' + 'a' * 30)
        target = folder / 'execution-progress.json'
        real_lstat = os.lstat
        def observation(path, *args, **kwargs):
            if Path(path) == target:
                return SimpleNamespace(st_mode=stat.S_IFLNK, st_file_attributes=0x400)
            return real_lstat(path, *args, **kwargs)
        with patch('task_progress_view.os.lstat', side_effect=observation):
            candidate, problem = _contained_file(self.workspaces, 'tasks', folder.name, target.name)
        self.assertIsNone(candidate)
        self.assertEqual(problem, 'redirected')

    def test_parent_reparse_component_is_refused_before_open(self):
        folder = self.work('b1' + 'b' * 30)
        target = folder / 'partial-response.txt'
        target.write_text('Do not read through a redirected parent')
        real_lstat = os.lstat
        def observation(path, *args, **kwargs):
            if Path(path) == self.workspaces:
                return SimpleNamespace(st_mode=stat.S_IFDIR, st_file_attributes=0x400)
            return real_lstat(path, *args, **kwargs)
        with patch('task_progress_view.os.lstat', side_effect=observation), \
                patch('builtins.open', side_effect=AssertionError('Must not open redirected evidence')):
            data, _, error = _bounded_read(target, 100, self.workspaces)
        self.assertIsNone(data)
        self.assertIsNotNone(error)

    def test_opened_file_must_match_checked_identity_even_at_same_size(self):
        folder = self.work('b2' + 'c' * 30)
        target, different = folder / 'partial-response.txt', self.root / 'unrelated.txt'
        target.write_bytes(b'approved')
        different.write_bytes(b'private!')
        real_open = builtins.open
        def swapped(path, *args, **kwargs):
            return real_open(different if Path(path) == target else path, *args, **kwargs)
        with patch('builtins.open', side_effect=swapped):
            data, _, error = _bounded_read(target, 100, self.workspaces)
        self.assertIsNone(data)
        self.assertIsNotNone(error)

    def test_parent_redirection_after_read_discards_the_read_bytes(self):
        folder = self.work('b3' + 'd' * 30)
        target = folder / 'partial-response.txt'
        target.write_bytes(b'Draft bytes')
        real_open, real_lstat = builtins.open, os.lstat
        was_read = False
        class MarkRead:
            def __enter__(self):
                self.handle = real_open(target, 'rb')
                return self
            def __exit__(self, *args):
                self.handle.close()
            def fileno(self):
                return self.handle.fileno()
            def read(self, amount):
                nonlocal was_read
                value = self.handle.read(amount)
                was_read = True
                return value
        def observation(path, *args, **kwargs):
            if was_read and Path(path) == self.workspaces:
                return SimpleNamespace(st_mode=stat.S_IFDIR, st_file_attributes=0x400)
            return real_lstat(path, *args, **kwargs)
        with patch('builtins.open', return_value=MarkRead()), \
                patch('task_progress_view.os.lstat', side_effect=observation):
            data, _, error = _bounded_read(target, 100, self.workspaces)
        self.assertTrue(was_read)
        self.assertIsNone(data)
        self.assertIsNotNone(error)


if __name__ == '__main__':
    unittest.main()
