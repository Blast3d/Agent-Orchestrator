"""Offline transport, concurrency, cache and Forget checks with temporary Brain data."""
import json
from pathlib import Path
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'app'))
from brain_store import BrainStore
import brain_jev
import jev_openrouter

REAL_EVALUATE = jev_openrouter.evaluate


class BatchedJevRecallTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.brain = BrainStore(self.root)
        self.config = {'status': 'ready', 'key_present': True, 'authorized_projects': ['alpha'],
                       'purposes': ['memory_rank'], 'min_confidence': .8,
                       'cache_enabled': False, 'cache_ttl_seconds': 3600,
                       'max_requests_per_day': 100, 'timeout_seconds': 1}
        self.config_patch = patch('jev_openrouter.load_config', return_value=self.config)
        self.config_patch.start()
        self.addCleanup(self.config_patch.stop)
        guarded = patch('jev_openrouter._post', side_effect=AssertionError('Unexpected network request'))
        guarded.start()
        self.addCleanup(guarded.stop)

    def populate(self, count, *, content=None):
        self.ids = []
        for index in range(count):
            proposed = self.brain.propose({'project_id': 'alpha', 'kind': 'fact',
                'title': 'Retry policy ' + str(index),
                'content': content or 'Retry policy evidence ' + str(index) + '.',
                'source': {'type': 'user', 'note': 'Reviewed synthetic fixture.'}})
            self.ids.append(self.brain.approve(proposed['id'], 'Tester',
                                              'Checked synthetic evidence.')['id'])
        return self.pool()

    def pool(self):
        with self.brain._connection() as con:
            rows = [self.brain._public(self.brain._row(con, identifier)) for identifier in self.ids]
        return [dict(row, score=1, reason='Synthetic local shortlist.', related_ids=[],
                     retrieval_method='keyword') for row in rows]

    def baseline(self):
        with patch('jev_openrouter.load_config', return_value={'status': 'disabled'}):
            return self.brain.search('retry', 'alpha', strategy='keyword', record_trace=False)

    def rank(self, pool=None, **options):
        return brain_jev.maybe_rank(self.brain, self.baseline(), candidate_pool=pool or self.pool(),
                                   limit=24, max_chars=32000, record_trace=False, **options)

    @staticmethod
    def response(state, questions, *, target=None, confidence=.95, signal=0, usage=True):
        response = {'status': 'ok', 'provider_calls': 1,
                    'provider': 'TypeSafe', 'model': jev_openrouter.MODEL,
                    'elapsed_ms': 10000, 'answers': {}}
        for key, question in questions.items():
            if question['type'] == 'score':
                index = int(key.removeprefix('memory_'))
                response['answers'][key] = {'type': 'score', 'confidence': confidence,
                    'score': 3 if state['candidates'][index]['id'] == target else 0}
            else:
                response['answers'][key] = {'type': 'noul', 'noul': signal}
        if usage:
            response.update(input_tokens=10, output_tokens=2, cost_usd=.001)
        return response

    def test_candidate_beyond_sixty_can_enter_twenty_four_with_seven_bounded_batches(self):
        pool = self.populate(100, content='Retry ' + '\u96ea\\"' * 90)
        target = pool[75]['id']
        calls = []

        def provider(root, project, purpose, state, questions, **kwargs):
            raw = jev_openrouter._request(state, questions)
            self.assertLessEqual(len(raw), 16384)
            self.assertLessEqual(len(state['candidates']), 16)
            self.assertTrue(all(len(row['content']) >= 64 for row in state['candidates']))
            self.assertEqual(set(kwargs['cache_context']['candidates']),
                             {row['id'] for row in state['candidates']})
            calls.append(len(raw))
            return self.response(state, questions, target=target)

        with patch('jev_openrouter.evaluate', side_effect=provider):
            result = self.rank(pool)
        self.assertEqual(result['results'][0]['id'], target)
        self.assertEqual(len(result['results']), 24)
        self.assertGreater(result['context_chars'], 8000)
        self.assertLessEqual(result['context_chars'], 32000)
        metadata = result['retrieval']['jev']
        self.assertEqual((metadata['batch_count'], metadata['provider_calls']), (7, 7))
        self.assertEqual((metadata['scored_candidate_count'], metadata['not_scored_candidate_count']), (100, 0))
        self.assertEqual(metadata['request_bytes'], sum(calls))
        self.assertEqual(metadata['request_limit'], 8)
        self.assertEqual(metadata['provider'], 'TypeSafe')
        self.assertEqual(metadata['model'], jev_openrouter.MODEL)
        self.assertEqual(metadata['input_tokens'], 70)
        self.assertAlmostEqual(metadata['cost_usd'], .007)
        for row in pool[80:]:
            self.assertNotIn(row['id'], json.dumps(result))

    def test_three_simultaneous_calls_and_wall_time_not_provider_time_sum(self):
        pool = self.populate(64)
        barrier = threading.Barrier(3)
        guard = threading.Lock()
        active = maximum = started = 0

        def provider(root, project, purpose, state, questions, **kwargs):
            nonlocal active, maximum, started
            with guard:
                ordinal = started
                started += 1
                active += 1
                maximum = max(maximum, active)
            try:
                if ordinal < 3:
                    barrier.wait(timeout=5)
                time.sleep(.01)
                return self.response(state, questions)
            finally:
                with guard:
                    active -= 1

        with patch('jev_openrouter.evaluate', side_effect=provider):
            result = self.rank(pool)
        self.assertEqual((maximum, started), (3, 4))
        metadata = result['retrieval']['jev']
        self.assertEqual(metadata['max_parallel_requests'], 3)
        self.assertLess(metadata['elapsed_ms'], 10000)
        self.assertEqual(result['retrieval']['timings_ms']['jev'], metadata['elapsed_ms'])

    def test_malformed_and_failed_batches_hold_slots_without_poisoning_valid_peers(self):
        pool = self.populate(64)
        ordinal = {row['id']: index for index, row in enumerate(pool)}

        def provider(root, project, purpose, state, questions, **kwargs):
            first = ordinal[state['candidates'][0]['id']]
            response = self.response(state, questions, target=pool[60]['id'])
            if first == 0:
                response['answers']['memory_0']['score'] = True
            elif first == 16:
                response['answers']['memory_4']['confidence'] = .1
            elif first == 32:
                response = {'status': 'error', 'provider_calls': 1, 'answers': {}}
            return response

        with patch('jev_openrouter.evaluate', side_effect=provider):
            result = self.rank(pool)
        # Failed slots and the uncertain slot stay put, while a deeper valid
        # batch can still promote its confident target into an available slot.
        expected = [*range(16), 60, 16, 17, 18, 20, 19, 21, 22]
        self.assertEqual([row['id'] for row in result['results']], [pool[i]['id'] for i in expected])
        metadata = result['retrieval']['jev']
        self.assertEqual(metadata['status'], 'partial')
        self.assertEqual((metadata['valid_batch_count'], metadata['failed_batch_count']), (2, 2))
        self.assertEqual((metadata['scored_candidate_count'], metadata['not_scored_candidate_count']), (32, 32))
        self.assertEqual((metadata['eligible_candidate_count'], metadata['held_candidate_count']), (31, 33))
        self.assertEqual(metadata['provider_calls'], 4)
        self.assertIsNone(metadata['input_tokens'])
        self.assertIsNone(metadata['cost_usd'])

    def test_exception_does_not_cancel_peer_and_keeps_call_usage_unknown(self):
        pool = self.populate(32)

        def provider(root, project, purpose, state, questions, **kwargs):
            if state['candidates'][0]['id'] == pool[16]['id']:
                raise TimeoutError('Synthetic uncertain call')
            return self.response(state, questions, target=pool[10]['id'])

        with patch('jev_openrouter.evaluate', side_effect=provider):
            result = self.rank(pool)
        self.assertEqual(result['results'][0]['id'], pool[10]['id'])
        self.assertEqual(result['retrieval']['jev']['failed_batch_count'], 1)
        self.assertIsNone(result['retrieval']['jev']['provider_calls'])
        self.assertIsNone(result['retrieval']['provider_calls'])
        self.assertIsNone(result['retrieval']['jev']['cost_usd'])

    def test_malformed_batch_envelopes_and_extreme_scores_cannot_break_aggregation(self):
        pool = self.populate(32)
        for malformed in ('not_object', 'status_object', 'huge_score', 'wrong_type'):
            def provider(root, project, purpose, state, questions, **kwargs):
                response = self.response(state, questions, target=pool[10]['id'])
                if state['candidates'][0]['id'] == pool[16]['id']:
                    if malformed == 'not_object':
                        return []
                    if malformed == 'status_object':
                        response['status'] = {'malformed': True}
                    elif malformed == 'huge_score':
                        response['answers']['memory_0']['score'] = 10 ** 1000
                    else:
                        response['answers']['memory_0']['type'] = 'noul'
                return response

            with self.subTest(malformed=malformed), patch('jev_openrouter.evaluate', side_effect=provider):
                result = self.rank(pool)
            self.assertEqual(result['results'][0]['id'], pool[10]['id'])
            self.assertEqual(result['retrieval']['jev']['failed_batch_count'], 1)
            self.assertEqual(result['retrieval']['jev']['status'], 'partial')

    def test_real_cache_reuses_unchanged_batch_snapshots_and_zeroes_actual_usage(self):
        pool = self.populate(32)
        self.config['cache_enabled'] = True
        calls = []

        def transport(raw, key, timeout):
            request = json.loads(raw)
            calls.append(request)
            answers = {}
            for name, question in request['questions'].items():
                answers[name] = {'type': 'score', 'score': 0, 'confidence': 1,
                    'legend': {str(i): label for i, label in enumerate(question['criteria'])},
                    'probabilities': {'0': 1, '1': 0, '2': 0, '3': 0}}
            # Deliberately omit provider usage: cached origin cost remains unknown.
            return json.dumps({'model': jev_openrouter.MODEL, 'provider': 'TypeSafe',
                               'id': 'synthetic-cache-response', 'answers': answers}).encode()

        with patch('jev_openrouter.evaluate', side_effect=REAL_EVALUATE), \
                patch('jev_openrouter._key', return_value='synthetic-key'), \
                patch('jev_openrouter._post', side_effect=transport):
            initial = self.rank(pool)
            self.assertEqual(len(calls), 2)
            self.assertIsNone(initial['retrieval']['jev']['cost_usd'])
            cached = self.rank(pool)
            self.assertEqual(len(calls), 2)
            self.assertEqual(cached['retrieval']['jev']['cache']['status'], 'hit')
            for field in ('provider_calls', 'input_tokens', 'output_tokens', 'cost_usd'):
                self.assertEqual(cached['retrieval']['jev'][field], 0)
            with self.brain._connection() as con:
                con.execute('UPDATE memories SET source=? WHERE id=?',
                    (json.dumps({'type': 'user', 'note': 'Revised source evidence.'}), pool[20]['id']))
                con.commit()
            changed = self.rank(self.pool())
        self.assertEqual(len(calls), 3)
        self.assertEqual(changed['retrieval']['jev']['cache']['hit_count'], 1)
        self.assertEqual(changed['retrieval']['jev']['provider_calls'], 1)
        self.assertIsNone(changed['retrieval']['jev']['cost_usd'])

    def test_forget_during_parallel_call_withholds_queued_evidence_and_final_content(self):
        pool = self.populate(80)
        ordinal = {row['id']: index for index, row in enumerate(pool)}
        forgotten = (pool[1]['id'], pool[65]['id'])
        forgotten_ready = threading.Event()
        observed = []

        def provider(root, project, purpose, state, questions, **kwargs):
            first = ordinal[state['candidates'][0]['id']]
            observed.extend(row['id'] for row in state['candidates'])
            if first == 0:
                for identifier in forgotten:
                    self.brain.forget(identifier, 'Tester', 'Synthetic Forget while other requests run.')
                forgotten_ready.set()
            else:
                self.assertTrue(forgotten_ready.wait(5))
            return self.response(state, questions, target=pool[70]['id'])

        with patch('jev_openrouter.evaluate', side_effect=provider):
            result = self.rank(pool)
        self.assertNotIn(forgotten[1], observed)
        self.assertEqual(result['results'][0]['id'], pool[70]['id'])
        self.assertEqual(result['retrieval']['jev']['revalidated_candidate_count'], 78)
        for identifier in forgotten:
            self.assertNotIn(identifier, json.dumps(result))

    def test_passage_annotations_from_each_batch_are_local_and_revalidate_conflict_peers(self):
        pool = self.populate(32)
        self.config['purposes'].append('memory_passage_review')
        forgotten = []

        def provider(root, project, purpose, state, questions, **kwargs):
            self.assertLessEqual(len(state['comparison_pairs']), 3)
            self.assertLessEqual(len(jev_openrouter._request(state, questions)), 16384)
            if state['candidates'][0]['id'] == pool[0]['id']:
                peer = state['comparison_pairs'][0][1]
                self.brain.forget(peer, 'Tester', 'Synthetic conflict peer forgotten during review.')
                forgotten.append(peer)
            return self.response(state, questions, signal=.95)

        with patch('jev_openrouter.evaluate', side_effect=provider):
            result = self.rank(pool)
        metadata = result['retrieval']['jev']
        self.assertEqual(metadata['conflict_coverage'], 'batch_local')
        self.assertLessEqual(metadata['conflict_pair_count'], metadata['batch_count'] * 3)
        self.assertTrue(metadata['passage_review'])
        self.assertTrue(any(row['id'] == pool[20]['id'] for row in result['results']))
        for row in result['results']:
            self.assertIn('Instruction-like text detected', row['reason'])
        self.assertNotIn(forgotten[0], json.dumps(result))

    def test_eight_batch_limit_reports_unscored_tail_and_preserves_single_request_api(self):
        pool = self.populate(20)
        bounded = brain_jev._bounded_request

        def narrow(query, rows, profile, **kwargs):
            return bounded(query, rows[:1], profile, **kwargs)

        with patch('brain_jev._bounded_request', side_effect=narrow), \
                patch('jev_openrouter.evaluate', side_effect=lambda r, p, u, s, q, **kw:
                      self.response(s, q)) as provider:
            result = self.rank(pool)
        self.assertEqual(provider.call_count, 8)
        self.assertEqual(result['retrieval']['jev']['not_scored_candidate_count'], 12)
        self.assertEqual(result['retrieval']['jev']['scored_candidate_count'], 8)
        with self.assertRaises(ValueError):
            brain_jev.ranking_request('retry', pool[:17])
        with patch('brain_jev._bounded_request', side_effect=narrow), \
                patch('jev_openrouter.evaluate', side_effect=lambda r, p, u, s, q, **kw:
                      self.response(s, q)) as provider:
            compact = self.rank(pool[:16])
        self.assertEqual(provider.call_count, 1)
        self.assertEqual(compact['retrieval']['jev']['request_limit'], 1)


if __name__ == '__main__':
    unittest.main()
