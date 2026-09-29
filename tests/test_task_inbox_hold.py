"""Held tasks show why, with the allowance snapshot from hold time and a next step."""
import json
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'app'))
from task_inbox import collect

TEMPLATE = Path(__file__).resolve().parents[1] / 'app/assets/task-inbox.html'
QUOTA = {'worker': 'codex', 'allowed': False, 'status': 'held', 'reading_status': 'cached',
         'admission_mode': 'advisory', 'threshold_pct': 20.0,
         'reasons': ['codex-weekly: available allowance is at or below the worker start threshold'],
         'warnings': ['codex-bucket-d62616d234d8-five-hour: usage unknown; refresh or supply a current account reading'],
         'windows': [{'id': 'codex-weekly', 'remaining_pct': 99.0, 'reserved_pct': 81.0, 'pending_pct': 81.0,
                      'unsettled_finished_pct': 0.0, 'available_pct': 18.0, 'observed_at': '2026-09-26T13:17:00+00:00',
                      'max_age_seconds': 600, 'source': 'official test reading', 'reset_passed': False}],
         'secret': 'QUOTA_SECRET'}


class HeldTaskTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.tasks = Path(temp.name) / 'tasks'
        self.tasks.mkdir()

    def save(self, number, **changes):
        job_id = f'{number:032x}'
        folder = self.tasks / job_id
        folder.mkdir()
        data = {'job_id': job_id, 'task': 'Held review', 'worker': 'codex', 'status': 'held',
                'execution_status': 'held', 'review_status': 'pending', 'reason': 'Quota admission refused',
                'created_at': '2026-09-26T18:16:00+00:00', 'ended_at': '2026-09-26T18:17:00+00:00',
                'finalized_at': '2026-09-26T18:17:01+00:00'}
        data.update(changes)
        (folder / 'result.json').write_text(json.dumps(data), encoding='utf-8')
        return job_id

    def rows(self):
        return {row['id']: row for row in collect(self.tasks)['tasks']}

    def test_quota_hold_keeps_the_snapshot_reason_and_next_step_but_no_identifiers_outside_details(self):
        job = self.save(1, quota_before=QUOTA)
        row = self.rows()[job]
        hold = row['hold']
        self.assertEqual((hold['free_pct'], hold['reserved_pct'], hold['remaining_pct']), (18.0, 81.0, 99.0))
        self.assertEqual(hold['reading_age_seconds'], 5 * 3600)  # measured at hold time, not now
        self.assertEqual(hold['at'], '2026-09-26T18:17:00+00:00')
        self.assertEqual(hold['explanation'], 'New work needs more than 20% free.')
        self.assertIn('close tasks that have finished', hold['next_step'])
        self.assertIn('refresh --provider codex', hold['next_step'])
        self.assertTrue(any('codex-bucket-d62616d234d8' in item for item in hold['technical']))
        visible = dict(row, hold={k: v for k, v in hold.items() if k != 'technical'})
        self.assertNotIn('codex-weekly', json.dumps(visible))
        self.assertNotIn('QUOTA_SECRET', json.dumps(row))
        self.assertNotIn('Codex to', row['next_step'])

    def test_hold_without_a_quota_snapshot_keeps_the_saved_reason_only(self):
        job = self.save(2, reason='A fresh official quota reading is required')
        hold = self.rows()[job]['hold']
        self.assertEqual(hold['reason'], 'A fresh official quota reading is required')
        self.assertIsNone(hold['free_pct'])
        self.assertEqual(hold['technical'], [])

    def test_only_held_or_admission_refused_tasks_get_a_hold_box(self):
        accepted = self.save(3, status='accepted', execution_status='succeeded', review_status='accepted',
                             review={'reviewer': 'Lead', 'note': 'Checked.', 'reviewed_at': '2026-09-26T18:20:00Z'},
                             response='Done.')
        refused = self.save(4, status='failed', execution_status='failed', quota_before=QUOTA)
        damaged = self.save(5, quota_before=dict(QUOTA, windows='not a list', reasons=[7, None]))
        rows = self.rows()
        self.assertIsNone(rows[accepted]['hold'])
        self.assertEqual(rows[refused]['hold']['free_pct'], 18.0)
        self.assertEqual(rows[damaged]['hold']['reason'], 'Quota admission refused')

    @unittest.skipUnless(shutil.which('node'), 'Node is needed for the inbox sentence check')
    def test_detail_sentence_reads_like_the_recommended_copy(self):
        page = TEMPLATE.read_text(encoding='utf-8')
        pieces = [re.search(r'const workerLabel = .*?;\r?\n', page).group(0),
                  re.search(r'const dateLabel = .*?;\r?\n', page).group(0),
                  re.search(r'const pct = .*?;\r?\n', page).group(0),
                  re.search(r'const ageText = .*?;\r?\n', page).group(0),
                  re.search(r'function holdSummary\(task\)\{.*?\r?\n\}\r?\n', page, re.S).group(0)]
        task = {'worker': 'codex', 'hold': {'at': '2026-09-26T18:17:00+00:00', 'free_pct': 18, 'reserved_pct': 81,
                                            'reading_age_seconds': 18000, 'explanation': 'New work needs more than 20% free.'}}
        script = ''.join(pieces) + 'process.stdout.write(holdSummary(' + json.dumps(task) + '));'
        result = subprocess.run([shutil.which('node'), '-e', script], capture_output=True, text=True,
                                encoding='utf-8', timeout=10, check=True)
        self.assertRegex(result.stdout, r'^Held on Sep 2[67], .+ — Codex had 18% free \(81% reserved\), '
                                        r'reading 5 h old\. New work needs more than 20% free\.$')


if __name__ == '__main__':
    unittest.main()
