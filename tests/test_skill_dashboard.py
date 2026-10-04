from pathlib import Path
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent))
import test_brain_dashboard as base


class SkillDashboardTests(unittest.TestCase):
    setUp = base.DashboardTests.setUp
    stop = base.DashboardTests.stop
    request = base.DashboardTests.request

    def test_catalog_requires_token_and_passes_exact_project(self):
        with patch('skill_flow.catalog', return_value={'project_id': 'openwhispr', 'packs': []}) as call:
            self.assertEqual(self.request(path='/api/skills/catalog?project_id=openwhispr', authorized=False)[0], 403)
            status, _, value = self.request(path='/api/skills/catalog?project_id=openwhispr')
        self.assertEqual(status, 200, value)
        call.assert_called_once_with(self.root, 'openwhispr')

    def test_recommend_rejects_cross_origin_and_unknown_fields(self):
        data = {'project_id': 'openwhispr', 'task': 'Fix a test', 'jev': False}
        with patch('skill_flow.recommend', return_value={'id': 'a' * 32}) as call:
            self.assertEqual(self.request('POST', '/api/skills/recommend', data=data, origin=False)[0], 403)
            self.assertEqual(self.request('POST', '/api/skills/recommend', data=dict(data, path='secret'))[0], 400)
            status, _, value = self.request('POST', '/api/skills/recommend', data=data)
        self.assertEqual(status, 200, value)
        call.assert_called_once_with(self.root, 'openwhispr', 'Fix a test', jev=False, run_id=None)
