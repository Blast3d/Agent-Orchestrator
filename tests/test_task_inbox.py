"""An inbox must preserve saved evidence and distinguish action from observation."""
import hashlib
import json
import os
from pathlib import Path
import re
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'app'))
from task_inbox import collect, generate


class TaskInboxTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.tasks = self.root / 'tasks'
        self.tasks.mkdir()
        self.output = self.root / 'inbox.html'

    def save(self, number=1, **changes):
        job_id = f'{number:032x}'
        folder = self.tasks / job_id
        folder.mkdir(exist_ok=True)
        data = {'job_id': job_id, 'task': 'Review a project', 'worker': 'claude',
                'status': 'awaiting_review', 'execution_status': 'succeeded',
                'review_status': 'pending', 'created_at': '2026-09-07T10:00:00+00:00',
                'started_at': '2026-09-07T10:01:00+00:00',
                'finalized_at': '2026-09-07T10:02:00+00:00', 'response': 'A readable answer.'}
        data.update(changes)
        (folder / 'result.json').write_text(json.dumps(data), encoding='utf-8')
        (folder / 'record.json').write_text(json.dumps({k: v for k, v in data.items() if k != 'response'}), encoding='utf-8')
        return folder

    def test_all_records_in_newest_saved_order_including_more_than_twenty(self):
        for n in range(1, 37):
            folder = self.save(n, task=f'Assignment {n}', response=f'Unique answer {n}')
            for path in folder.iterdir():
                os.utime(path, (1700000000+n, 1700000000+n))
        result = collect(self.tasks)
        self.assertEqual(len(result['tasks']), 36)
        self.assertEqual(result['tasks'][0]['title'], 'Assignment 36')
        self.assertEqual(result['tasks'][-1]['answer'], 'Unique answer 1')

    def test_review_and_execution_states_have_distinct_labels_and_steps(self):
        cases = [
            ({}, 'ready', 'Ready to check'),
            ({'status': 'accepted', 'review_status': 'accepted'}, 'accepted', 'Finished'),
            ({'status': 'rejected', 'review_status': 'rejected'}, 'rejected', 'Needs attention'),
            ({'status': 'failed', 'execution_status': 'failed'}, 'failed', 'Needs attention'),
            ({'status': 'recovery_required', 'execution_status': 'uncertain'}, 'uncertain', 'Needs attention'),
            ({'status': 'held', 'execution_status': 'held'}, 'held', 'Needs attention'),
            ({'status': 'running', 'execution_status': 'running', 'finalized_at': None}, 'working', 'Working'),
            ({'status': 'preparing', 'execution_status': 'pending', 'finalized_at': None}, 'pending', 'Working'),
            ({'status': 'future-unknown-status'}, 'unknown', 'Needs attention'),
        ]
        for n, (changes, _, _) in enumerate(cases, 1):
            self.save(n, **changes)
        records = {row['id']: row for row in collect(self.tasks)['tasks']}
        labels = set()
        for n, (_, state, group) in enumerate(cases, 1):
            row = records[f'{n:032x}']
            self.assertEqual((row['state'], row['group']), (state, group))
            self.assertTrue(row['next_step'])
            labels.add(row['label'])
        self.assertEqual(len(labels), len(cases))
        self.assertIn('cannot confirm', records[f'{5:032x}']['next_step'])
        self.assertIn('not a live connection', records[f'{7:032x}']['next_step'])

    def test_acceptance_claim_without_matching_review_is_unknown(self):
        self.save(status='accepted', review_status='pending')
        self.assertEqual(collect(self.tasks)['tasks'][0]['state'], 'unknown')

    def test_progress_handoff_and_reviewed_answer_remain_separate(self):
        prior = self.save(1, worker='claude', assignment_project_id='test-project',
                          status='held', execution_status='held')
        current = self.save(2, worker='grok', assignment_project_id='test-project',
                            handoff_from_job_id=prior.name, size='medium', timeout_seconds=600,
                            status='accepted', review_status='accepted', response='Reviewed final answer')
        workspaces = self.root / 'workspaces'
        work = workspaces / 'tasks' / current.name
        work.mkdir(parents=True)
        (work / 'execution-progress.json').write_text(json.dumps({
            'state':'finished', 'process_status':'exited', 'terminal_received':True,
            'preview_truncated':True, 'observed_answer_chars':100, 'retained_answer_chars':5,
            'timeout_seconds':300}), encoding='utf-8')
        (work / 'partial-response.txt').write_text('draft', encoding='utf-8')
        rows = {r['id']:r for r in collect(self.tasks, workspaces=workspaces)['tasks']}
        row = rows[current.name]
        self.assertEqual(row['answer'], 'Reviewed final answer')
        self.assertTrue(row['preview']['incomplete'])
        self.assertTrue(row['preview']['truncated'])
        self.assertEqual(row['preview']['text'], 'draft')
        self.assertEqual(row['progress']['timeout_seconds'], 600)
        self.assertEqual(row['progress']['size'], 'medium')
        self.assertTrue(row['handoff']['lineage'][0]['verified'])
        self.assertEqual(row['handoff']['lineage'][0]['worker'], 'claude')
        (current / 'result.json').unlink()
        index_only = {r['id']:r for r in collect(self.tasks, workspaces=workspaces)['tasks']}[current.name]
        self.assertFalse(index_only['handoff']['lineage'][0]['verified'])

    def test_damaged_progress_does_not_hide_the_saved_answer(self):
        folder = self.save()
        workspaces = self.root / 'workspaces'
        work = workspaces / 'tasks' / folder.name
        work.mkdir(parents=True)
        (work / 'execution-progress.json').write_text('broken', encoding='utf-8')
        row = collect(self.tasks, workspaces=workspaces)['tasks'][0]
        self.assertEqual(row['answer'], 'A readable answer.')
        self.assertEqual(row['group'], 'Needs attention')
        self.assertFalse(row['progress']['present'])

    def test_normal_unobserved_progress_metrics_are_unknown_without_warning(self):
        from worker_progress import ClaudeProgress
        folder = self.save(size='small', timeout_seconds=300)
        workspaces = self.root / 'workspaces'
        work = workspaces / 'tasks' / folder.name
        work.mkdir(parents=True)
        snapshot = dict(ClaudeProgress().snapshot(), timeout_seconds=300, process_status='starting')
        (work / 'execution-progress.json').write_text(json.dumps(snapshot), encoding='utf-8')
        row = collect(self.tasks, workspaces=workspaces)['tasks'][0]
        self.assertTrue(row['progress']['present'])
        self.assertEqual(row['notices'], [])

    def test_hold_explanation_is_readable_without_opening_provider_payload(self):
        self.save(status='held', execution_status='held', reason='A fresh official quota reading is required',
                  provider_result={'error': 'PRIVATE_PROVIDER_ERROR'})
        row = collect(self.tasks)['tasks'][0]
        self.assertEqual(row['explanation'], 'A fresh official quota reading is required')
        self.assertNotIn('PRIVATE_PROVIDER_ERROR', json.dumps(row))

    def test_surviving_canonical_answer_overrides_stale_index(self):
        folder = self.save(status='accepted', review_status='accepted',
                           review={'reviewer': 'Codex', 'note': 'Checked the supporting evidence.', 'reviewed_at': '2026-09-07T10:03:00Z'})
        index = json.loads((folder / 'record.json').read_text())
        index.update(status='awaiting_review', review_status='pending')
        (folder / 'record.json').write_text(json.dumps(index))
        result = collect(self.tasks)['tasks'][0]
        self.assertEqual(result['state'], 'accepted')
        self.assertEqual(result['review_note'], 'Checked the supporting evidence.')
        self.assertEqual(result['reviewer'], 'Codex')

    def test_malformed_records_do_not_hide_healthy_tasks(self):
        self.save(1)
        bad = self.save(2)
        (bad / 'record.json').write_text('not JSON')
        (bad / 'result.json').write_text('[false]')
        result = collect(self.tasks)
        self.assertEqual(len(result['tasks']), 2)
        self.assertEqual(result['warnings'], 1)
        self.assertEqual(sum(row['state'] == 'ready' for row in result['tasks']), 1)
        self.assertEqual(sum(row['state'] == 'unknown' for row in result['tasks']), 1)

    def test_damaged_summary_keeps_surviving_answer_visible(self):
        folder = self.save()
        (folder / 'record.json').write_text('null')
        row = collect(self.tasks)['tasks'][0]
        self.assertEqual(row['answer'], 'A readable answer.')
        self.assertEqual(row['group'], 'Needs attention')
        self.assertIn('summary', row['notices'][0])

    def test_missing_final_answer_is_visible_and_never_inferred_from_payload(self):
        folder = self.save(status='accepted', review_status='accepted', provider_result={'response': 'SECRET RAW PROVIDER ANSWER'})
        (folder / 'result.json').unlink()
        row = collect(self.tasks)['tasks'][0]
        self.assertEqual(row['answer'], '')
        self.assertTrue(row['notices'])
        self.assertEqual(row['group'], 'Needs attention')
        self.assertNotIn('SECRET', json.dumps(row))

    def test_wrong_task_identity_is_not_shown_as_another_tasks_answer(self):
        folder = self.save()
        data = json.loads((folder / 'result.json').read_text())
        data.update(job_id='f'*32, response='Another task private answer')
        (folder / 'result.json').write_text(json.dumps(data))
        row = collect(self.tasks)['tasks'][0]
        self.assertEqual(row['answer'], '')
        self.assertNotIn('Another task', json.dumps(row))

    def test_no_evidence_paths_account_payloads_or_usage_in_display_data(self):
        self.save(provider_result={'secret': 'PROVIDER_SECRET'}, quota_before={'secret': 'QUOTA_SECRET'},
                  requested_output='PRIVATE_OUTPUT_PATH', canonical_result='PRIVATE_CANONICAL_PATH',
                  usage={'input_tokens': 111}, response='A normal answer')
        data = json.dumps(collect(self.tasks))
        for text in ('PROVIDER_SECRET', 'QUOTA_SECRET', 'PRIVATE_OUTPUT_PATH', 'PRIVATE_CANONICAL_PATH', 'input_tokens'):
            self.assertNotIn(text, data)

    def test_untrusted_text_cannot_close_embedded_script_data(self):
        hostile = '</script><script>window.PWNED=1</script><img src=x onerror=alert(1)>\u2028&'
        self.save(task=hostile, response=hostile, review={'reviewer': hostile, 'note': hostile})
        result = generate(self.output, self.tasks)
        page = self.output.read_text(encoding='utf-8')
        self.assertNotIn(hostile, page)
        embedded = re.search(r'<script id="inbox-data" type="application/json">(.*?)</script>', page, re.S).group(1)
        self.assertNotIn('<', embedded)
        self.assertNotIn('&', embedded)
        self.assertEqual(json.loads(embedded)['tasks'][0]['answer'], hostile)
        self.assertEqual(result['count'], 1)

    def test_generation_preserves_all_task_sources_and_makes_no_external_calls(self):
        self.save(status='recovery_required', execution_status='uncertain', reservation_id='unchanged-reservation')
        reservation = self.root / 'reservations.json'
        reservation.write_text('{"reservation":"unchanged"}')
        sources = list(self.tasks.rglob('*.json')) + [reservation]
        before = {path: (hashlib.sha256(path.read_bytes()).hexdigest(), path.stat().st_mtime_ns) for path in sources}
        with patch('subprocess.Popen', side_effect=AssertionError('No provider processes')), \
                patch('socket.create_connection', side_effect=AssertionError('No network')), \
                patch('webbrowser.open', side_effect=AssertionError('No browser unless requested')):
            generate(self.output, self.tasks)
        after = {path: (hashlib.sha256(path.read_bytes()).hexdigest(), path.stat().st_mtime_ns) for path in sources}
        self.assertEqual(before, after)
        self.assertEqual(list(self.tasks.rglob('*.json')), sources[:-1])

    def test_output_cannot_overwrite_or_add_task_evidence(self):
        folder = self.save()
        before = (folder / 'result.json').read_bytes()
        with self.assertRaises(ValueError):
            generate(folder / 'result.json', self.tasks)
        with self.assertRaises(ValueError):
            generate(self.tasks / 'new.html', self.tasks)
        self.assertEqual(before, (folder / 'result.json').read_bytes())

    def test_empty_or_missing_store_needs_no_creation_and_no_demo_tasks(self):
        missing = self.root / 'missing-tasks'
        result = generate(self.output, missing)
        self.assertEqual(result['count'], 0)
        self.assertFalse(missing.exists())
        self.assertTrue(self.output.is_file())

    def test_invalid_timestamps_and_nontext_answer_remain_unknown(self):
        self.save(created_at='yesterday', started_at='2026-09-07T10:00:00', response={'payload': 'not a display answer'})
        row = collect(self.tasks)['tasks'][0]
        self.assertIsNone(row['created_at'])
        self.assertIsNone(row['started_at'])
        self.assertEqual(row['answer'], '')
        self.assertTrue(row['notices'])
        self.assertIsNotNone(row['updated_at'])


if __name__ == '__main__':
    unittest.main()
