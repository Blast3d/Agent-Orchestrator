"""Dashboard searches never start Jev batches that would outlive the page's wait (offline fakes only)."""
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'app'))
from brain_store import BrainStore
import brain_dashboard
import brain_jev
import jev_openrouter


class JevTimeBudgetTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = tempfile.TemporaryDirectory()
        cls.brain = BrainStore(Path(cls.temporary.name))
        cls.ids = []
        for index in range(100):
            proposed = cls.brain.propose({'project_id': 'alpha', 'kind': 'fact', 'title': 'Retry policy ' + str(index),
                                          'content': 'Retry policy evidence ' + str(index) + '. ' + 'detail ' * 60,
                                          'source': {'type': 'user', 'note': 'Reviewed synthetic fixture.'}})
            cls.ids.append(cls.brain.approve(proposed['id'], 'Tester', 'Checked synthetic evidence.')['id'])

    @classmethod
    def tearDownClass(cls):
        cls.temporary.cleanup()

    def setUp(self):
        self.config = {'status': 'ready', 'key_present': True, 'authorized_projects': ['alpha'],
                       'purposes': ['memory_rank'], 'min_confidence': .8, 'cache_enabled': False,
                       'cache_ttl_seconds': 3600, 'max_requests_per_day': 100, 'timeout_seconds': 10}
        for target in (patch('jev_openrouter.load_config', return_value=self.config),
                       patch('jev_openrouter._post', side_effect=AssertionError('Unexpected network request'))):
            target.start()
            self.addCleanup(target.stop)
        self.calls = []

    def evaluate(self, root, project, purpose, state, questions, **options):
        self.calls.append(len(state['candidates']))
        return {'status': 'ok', 'provider_calls': 1, 'model': jev_openrouter.MODEL, 'elapsed_ms': 5,
                'input_tokens': 1, 'output_tokens': 1, 'cost_usd': 0,
                'answers': {key: {'type': 'score', 'confidence': .95, 'score': 1} for key in questions}}

    def rank(self):
        with self.brain._connection() as con:
            pool = [dict(self.brain._public(self.brain._row(con, identifier)), score=1, reason='Synthetic.',
                         related_ids=[], retrieval_method='keyword') for identifier in self.ids]
        with patch('jev_openrouter.load_config', return_value={'status': 'disabled'}):
            baseline = self.brain.search('retry', 'alpha', strategy='keyword', record_trace=False)
        with patch('jev_openrouter.evaluate', side_effect=self.evaluate):
            return brain_jev.maybe_rank(self.brain, baseline, candidate_pool=pool, limit=24,
                                        max_chars=32000, record_trace=False)

    def test_without_a_budget_every_planned_batch_runs(self):
        metadata = self.rank()['retrieval']['jev']
        self.assertGreater(len(self.calls), 3)
        self.assertNotIn('deferred_batch_count', metadata)

    def test_one_wave_budget_starts_only_the_parallel_batches_that_can_finish(self):
        with brain_jev.time_budget(13):  # one 10 s wave plus margin, not two
            metadata = self.rank()['retrieval']['jev']
        self.assertEqual(len(self.calls), brain_jev.MAX_PARALLEL_REQUESTS)
        self.assertEqual(metadata['batch_count'], brain_jev.MAX_PARALLEL_REQUESTS)
        self.assertEqual(metadata['deferred_batch_count'], metadata['planned_batch_count'] - brain_jev.MAX_PARALLEL_REQUESTS)
        self.assertIn('dashboard time limit', metadata['time_limit_note'])
        self.assertGreater(metadata['not_scored_candidate_count'], 0)

    def test_exhausted_budget_makes_no_provider_request_and_keeps_local_order(self):
        with brain_jev.time_budget(1):
            result = self.rank()
        metadata = result['retrieval']['jev']
        self.assertEqual(self.calls, [])
        self.assertEqual((metadata['status'], metadata['provider_calls'], metadata['applied']), ('skipped', 0, False))
        self.assertIn('time limit', metadata['reason'])
        self.assertTrue(result['results'])

    def test_budget_is_scoped_to_its_context(self):
        with brain_jev.time_budget(1):
            pass
        self.assertIsNone(brain_jev._DEADLINE.get())

    def test_dashboard_waits_longer_than_its_jev_budget_and_explains_an_abort(self):
        self.assertGreater(brain_dashboard.PAGE.count('SEARCH_TIMEOUT_MS'), 1)
        wait = int(brain_dashboard.PAGE.split('const SEARCH_TIMEOUT_MS=', 1)[1].split(';', 1)[0])
        # The page must outwait the Jev deadline plus one wave of in-flight requests and local work.
        self.assertGreater(wait / 1000, brain_dashboard.DASHBOARD_JEV_SECONDS + 15)
        self.assertIn('Jev ranking may still finish on the server and count toward usage', brain_dashboard.PAGE)


if __name__ == '__main__':
    unittest.main()
