import copy
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
from datetime import datetime, timedelta

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'app'))
from usage_guard import Guard, DEFAULT_POLICY, now, stamp
from usage_report import render_usage


class AdvisoryUsageReportTests(unittest.TestCase):
    def test_current_labels_and_split_reservation_evidence(self):
        observed = stamp(now() - timedelta(hours=40))
        window = {'id': 'codex-weekly', 'remaining_pct': 99, 'reserved_pct': 0, 'pending_pct': 0,
                  'unsettled_finished_pct': 81, 'available_pct': 99, 'observed_at': observed,
                  'max_age_seconds': 600, 'source': 'official test reading', 'reset_at': None,
                  'reset_display': None, 'freshness': 'cached', 'reset_passed': False}
        report = {'worker_start_threshold_pct': 20, 'updated_at': stamp(), 'active_reservations': 0,
                  'workers': [{'worker': 'codex', 'status': 'ready', 'allowed': True, 'reading_status': 'cached',
                               'windows': [window], 'reasons': [], 'warnings': []},
                              {'worker': 'claude', 'status': 'ready', 'allowed': True, 'reading_status': 'unknown',
                               'windows': [], 'reasons': [], 'warnings': []}]}
        html = render_usage(report)
        self.assertIn('Codex (ASTRA / Sol)', html)
        self.assertIn('Claude Code (Opus)', html)
        self.assertNotIn('Fable', html)
        self.assertIn('81% for finished work not yet in a reading (not holding work while the reading is stale)', html)
        self.assertIn('Running work: 0%', html)
        self.assertIn('when status or the dashboard is read', html)
        # Ages count on in the browser instead of freezing at generation time.
        observed_epoch = str(int(datetime.fromisoformat(observed.replace('Z', '+00:00')).timestamp()))
        self.assertIn('class="age" data-observed="' + observed_epoch + '"', html)
        self.assertIn("querySelectorAll('.age[data-observed]')", html)

    def test_guard_and_saved_dashboard_share_cached_admission_evidence(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            policy = copy.deepcopy(DEFAULT_POLICY)
            policy.update(quota_admission_mode='advisory', worker_start_threshold_pct=20,
                          worker_pools={'claude': ['claude-five-hour']})
            (root / 'policy.json').write_text(json.dumps(policy), encoding='utf-8')
            guard = Guard(root)
            observed = stamp(now() - timedelta(hours=2))
            guard.observe([{'id':'claude-five-hour', 'observed_at':observed, 'remaining_pct':70,
                            'max_age_seconds':600, 'source':'official test reading'}], 'claude')
            guard._refresh_failure('claude', RuntimeError('Claude usage panel timed out'))
            decision = guard.check('claude', 'large', reserve=True, task='test')
            self.assertTrue(decision['allowed'])
            self.assertEqual(decision['reading_status'], 'cached')
            with patch('task_panel.render', return_value=''):
                report = guard.dashboard()
            saved = json.loads((root / 'usage-status.json').read_text())
            self.assertEqual(saved, report)
            html = (root / 'usage-dashboard.html').read_text(encoding='utf-8')
            self.assertIn('<span class="pill ready">Ready</span>', html)
            self.assertIn('reading 2 h old', html)
            self.assertIn('Not blocking new work (advisory mode)', html)
            self.assertIn('70% last observed', html)
            self.assertIn('55% available after reservations', html)
            self.assertIn(observed, html)
            self.assertIn('timed out', html)
            self.assertNotIn('Refresh usage before assigning work', html)
            self.assertNotIn("badge.className='badge held'", html)

    def test_empty_state_allows_work_and_never_draws_zero_percentage(self):
        with tempfile.TemporaryDirectory() as directory:
            guard = Guard(Path(directory))
            guard.policy.update(quota_admission_mode='advisory', worker_pools={'claude':['claude-five-hour']})
            report = guard.status()
            self.assertTrue(report['workers'][0]['allowed'])
            page = render_usage(report)
            self.assertIn('Ready · usage unknown', page)
            self.assertNotIn('0% available', page)


if __name__ == '__main__':
    unittest.main()
