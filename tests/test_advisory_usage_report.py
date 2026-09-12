import copy
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
from datetime import timedelta

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'app'))
from usage_guard import Guard, DEFAULT_POLICY, now, stamp
from usage_report import render_usage


class AdvisoryUsageReportTests(unittest.TestCase):
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
            self.assertIn('READY · CACHED', html)
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
            self.assertIn('USAGE UNKNOWN', page)
            self.assertNotIn('0% available', page)


if __name__ == '__main__':
    unittest.main()
