"""Real SQLite wider recall and race regressions; provider decisions are offline."""
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'app'))
from brain_store import BrainStore, digest
from brain_semantic import fingerprint
from brain_retrieval_metadata import public_retrieval
import jev_openrouter


class WiderJevRecallTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.brain = BrainStore(self.root)
        self.config = {'status': 'ready', 'key_present': True,
                       'authorized_projects': ['alpha'], 'purposes': ['memory_rank'],
                       'min_confidence': .8}
        mocked = patch('jev_openrouter.load_config', return_value=self.config)
        mocked.start()
        self.addCleanup(mocked.stop)
        guarded = patch('jev_openrouter.evaluate', side_effect=AssertionError('Unexpected provider call'))
        guarded.start()
        self.addCleanup(guarded.stop)

    def memory(self, index, **updates):
        data = {'project_id': 'alpha', 'kind': 'fact', 'title': 'Retry detail ' + str(index),
                'content': 'Retry evidence with unique detail ' + str(index) + '.',
                'source': {'type': 'user', 'note': 'Reviewed synthetic retrieval fixture.'}}
        data.update(updates)
        proposed = self.brain.propose(data)
        return self.brain.approve(proposed['id'], 'Tester', 'Checked the synthetic source evidence.')

    def memories(self, count=8, **updates):
        return [self.memory(index, **updates) for index in range(count)]

    def search(self, **updates):
        values = {'strategy': 'keyword', 'record_trace': False}
        values.update(updates)
        return self.brain.search('retry', 'alpha', **values)

    def baseline(self, **updates):
        with patch('jev_openrouter.load_config', return_value={'status': 'disabled'}):
            return self.search(**updates)

    @staticmethod
    def response(state, target=None, uncertain=()):
        return {'status': 'ok', 'provider_calls': 1,
                'answers': {'memory_' + str(index): {
                    'type': 'score', 'score': 3 if item['id'] == target else 0,
                    'confidence': .2 if index in uncertain else .9}
                    for index, item in enumerate(state['candidates'])}}

    def test_seventh_candidate_reaches_six_and_pool_stays_private(self):
        self.memories(20)
        baseline = self.baseline()
        observed = []

        def provider(root, project, purpose, state, questions, **kwargs):
            observed.extend(state['candidates'])
            self.assertEqual(len(observed), 16)
            return self.response(state, observed[6]['id'])

        with patch('jev_openrouter.evaluate', side_effect=provider):
            found = self.search(record_trace=True)
        self.assertNotIn(observed[6]['id'], [row['id'] for row in baseline['results']])
        self.assertEqual(found['results'][0]['id'], observed[6]['id'])
        self.assertEqual(len(found['results']), 6)
        self.assertLessEqual(found['context_chars'], 8000)
        self.assertNotIn('candidate_pool', found)
        metadata = found['retrieval']['jev']
        self.assertEqual((metadata['candidate_count'], metadata['scored_candidate_count']), (16, 16))
        self.assertEqual(metadata['returned_count'], 6)
        self.assertEqual(metadata['promoted_count'], 1)
        self.assertTrue(metadata['order_changed'])
        self.assertFalse(metadata['fallback'])
        serialized = json.dumps(found)
        for item in observed[7:]:
            self.assertNotIn(item['id'], serialized)
            self.assertNotIn(item['content'], serialized)
        with self.brain._connection() as con:
            trace = con.execute('SELECT memory_ids FROM traces WHERE id=?', (found['trace_id'],)).fetchone()
            detail = con.execute('SELECT detail FROM retrieval_traces WHERE trace_id=?', (found['trace_id'],)).fetchone()
        self.assertEqual(json.loads(trace[0]), [row['id'] for row in found['results']])
        self.assertNotIn(observed[-1]['id'], detail[0])
        self.assertEqual(public_retrieval(found['retrieval'])['jev']['candidate_count'], 16)

    def test_caller_limit_applies_after_wider_ranking(self):
        self.memories()
        target = []

        def provider(root, project, purpose, state, questions, **kwargs):
            target.append(state['candidates'][6]['id'])
            return self.response(state, target[0])

        with patch('jev_openrouter.evaluate', side_effect=provider):
            found = self.search(limit=2)
        self.assertEqual(len(found['results']), 2)
        self.assertEqual(found['results'][0]['id'], target[0])

    def test_noisy_tail_and_uncertain_first_slot_do_not_veto_reliable_promotion(self):
        self.memories()
        seen = []

        def provider(root, project, purpose, state, questions, **kwargs):
            seen.extend(row['id'] for row in state['candidates'])
            return self.response(state, seen[6], uncertain=(0, 7))

        with patch('jev_openrouter.evaluate', side_effect=provider):
            found = self.search()
        self.assertEqual([row['id'] for row in found['results']], [seen[0], seen[6], *seen[1:5]])
        self.assertEqual(found['retrieval']['jev']['eligible_candidate_count'], 6)
        self.assertEqual(found['retrieval']['jev']['held_candidate_count'], 2)
        self.assertEqual(found['retrieval']['jev']['confidence_threshold'], .8)

    def test_uncertain_high_score_and_equal_reliable_scores_keep_baseline_positions(self):
        self.memories()
        baseline = self.baseline()
        with patch('jev_openrouter.evaluate', side_effect=lambda root, project, purpose, state, questions, **kw:
                   self.response(state, state['candidates'][6]['id'], uncertain=(6,))):
            found = self.search()
        self.assertEqual([row['id'] for row in found['results']], [row['id'] for row in baseline['results']])
        self.assertEqual(found['context'], baseline['context'])

    def test_all_uncertain_or_only_one_reliable_preserves_exact_baseline(self):
        self.memories()
        baseline = self.baseline()
        for uncertain in (range(8), range(1, 8)):
            with self.subTest(uncertain=list(uncertain)), patch('jev_openrouter.evaluate',
                    side_effect=lambda root, project, purpose, state, questions, **kw:
                        self.response(state, state['candidates'][6]['id'], uncertain=uncertain)):
                found = self.search()
            self.assertEqual(found['context'], baseline['context'])
            self.assertEqual(found['results'], baseline['results'])
            self.assertFalse(found['retrieval']['jev']['applied'])
            self.assertTrue(found['retrieval']['jev']['fallback'])

    def test_malformed_score_sets_fall_back_without_partial_application(self):
        self.memories()
        baseline = self.baseline()
        changes = [('missing', None), ('extra', None), ('score', float('nan')),
                   ('score', True), ('score', 4), ('confidence', -1), ('confidence', 'high')]
        for field, value in changes:
            def provider(root, project, purpose, state, questions, **kwargs):
                response = self.response(state, state['candidates'][6]['id'])
                if field == 'missing':
                    response['answers'].pop('memory_0')
                elif field == 'extra':
                    response['answers']['other'] = {}
                else:
                    response['answers']['memory_0'][field] = value
                return response
            with self.subTest(field=field, value=value), patch('jev_openrouter.evaluate', side_effect=provider):
                found = self.search()
            self.assertEqual(found['context'], baseline['context'])
            self.assertEqual(found['retrieval']['jev']['status'], 'invalid_response')
            self.assertFalse(found['retrieval']['jev']['applied'])

    def test_failed_request_does_not_refill_from_tail_after_forget(self):
        self.memories()
        baseline = self.baseline()
        forgotten = baseline['results'][0]['id']

        def provider(*args, **kwargs):
            self.brain.forget(forgotten, 'Tester', 'Synthetic concurrent forget during provider call.')
            return {'status': 'error', 'answers': {}, 'provider_calls': 1}

        with patch('jev_openrouter.evaluate', side_effect=provider):
            found = self.search()
        self.assertEqual([row['id'] for row in found['results']], [row['id'] for row in baseline['results'][1:]])
        self.assertNotIn(forgotten, found['context'])

    def test_invalidated_slot_never_pulls_uncertain_tail_into_final_six(self):
        self.memories()
        observed = []

        def provider(root, project, purpose, state, questions, **kwargs):
            observed.extend(row['id'] for row in state['candidates'])
            self.brain.forget(observed[0], 'Tester', 'Synthetic invalidation of an initial baseline slot.')
            return self.response(state, observed[1], uncertain=(6, 7))

        with patch('jev_openrouter.evaluate', side_effect=provider):
            found = self.search()
        self.assertEqual([row['id'] for row in found['results']], observed[1:6])
        self.assertEqual(found['retrieval']['jev']['promoted_count'], 0)

    def test_actual_serialized_request_bound_with_unicode_and_long_excerpts(self):
        self.memories(20, content='Retry ' + '\u96ea\\"' * 490)
        requests = []

        def provider(root, project, purpose, state, questions, **kwargs):
            requests.append(jev_openrouter._request(state, questions))
            self.assertEqual(len(state['candidates']), 16)
            self.assertTrue(all(row['excerpt_truncated'] for row in state['candidates']))
            return self.response(state, state['candidates'][6]['id'])

        with patch('jev_openrouter.evaluate', side_effect=provider):
            found = self.search(profile='review')
        self.assertLessEqual(len(requests[0]), 16 * 1024)
        self.assertEqual(found['retrieval']['jev']['request_bytes'], len(requests[0]))
        self.assertLessEqual(found['context_chars'], 8000)

    def test_candidates_are_collected_before_context_character_packing(self):
        self.memories(8, content='Retry ' + 'long evidence. ' * 160)
        baseline = self.baseline(max_chars=1000)
        self.assertLess(len(baseline['results']), 6)
        target = []

        def provider(root, project, purpose, state, questions, **kwargs):
            self.assertEqual(len(state['candidates']), 8)
            target.append(state['candidates'][6]['id'])
            return self.response(state, target[0])

        with patch('jev_openrouter.evaluate', side_effect=provider):
            found = self.search(max_chars=1000)
        self.assertEqual(found['results'][0]['id'], target[0])
        self.assertLessEqual(found['context_chars'], 1000)

    def test_tail_content_scope_source_and_validity_changes_are_excluded(self):
        self.memories()
        changes = {'content': 'Changed evidence.', 'project_id': 'beta', 'user_id': 'other',
                   'valid_to': '2020-01-01T00:00:00Z', 'valid_from': '2999-01-01T00:00:00Z',
                   'source': json.dumps({'type': 'user', 'note': 'Changed source.'})}
        for field, value in changes.items():
            target, previous = [], []

            def provider(root, project, purpose, state, questions, **kwargs):
                target.append(state['candidates'][6]['id'])
                with self.brain._connection() as con:
                    previous.append(con.execute('SELECT ' + field + ' FROM memories WHERE id=?', (target[0],)).fetchone()[0])
                    con.execute('UPDATE memories SET ' + field + '=? WHERE id=?', (value, target[0]))
                    con.commit()
                return self.response(state, target[0])

            with self.subTest(field=field), patch('jev_openrouter.evaluate', side_effect=provider):
                found = self.search()
            self.assertNotIn(target[0], [row['id'] for row in found['results']])
            self.assertNotIn(target[0], found['context'])
            self.assertEqual(found['retrieval']['jev']['revalidated_candidate_count'], 7)
            with self.brain._connection() as con:
                con.execute('UPDATE memories SET ' + field + '=? WHERE id=?', (previous[0], target[0]))
                con.commit()

    def test_changed_canonical_source_rejects_scored_evidence(self):
        self.memories(7)
        job = 'a' * 32
        path = self.root / 'runs/tasks' / job / 'result.json'
        path.parent.mkdir(parents=True)
        canonical = {'job_id': job, 'assignment_project_id': 'alpha', 'response': 'Reviewed fact',
                     'execution_status': 'succeeded', 'status': 'accepted', 'review_status': 'accepted',
                     'finalized_at': '2026-09-01T00:00:00Z', 'review': {'note': 'Synthetic review'}}
        path.write_text(json.dumps(canonical), encoding='utf-8')
        memory = self.memory(8, source={'type': 'task', 'job_id': job,
                                       'review_sha256': digest(canonical['review'])})

        def provider(root, project, purpose, state, questions, **kwargs):
            canonical['response'] = 'Canonical evidence changed during scoring.'
            path.write_text(json.dumps(canonical), encoding='utf-8')
            return self.response(state, memory['id'])

        with patch('jev_openrouter.evaluate', side_effect=provider):
            found = self.search()
        self.assertNotIn(memory['id'], [row['id'] for row in found['results']])
        self.assertEqual(found['retrieval']['jev']['source_validation']['excluded_memories'], 1)

    def test_tail_graph_candidate_requires_live_seed_and_relation(self):
        seed = self.memory(0)
        neighbors = [self.memory(index, title='Neighbor ' + str(index), content='Independent graph evidence.')
                     for index in range(1, 8)]
        edges = {row['id']: self.brain.relate(seed['id'], row['id'], 'supports', 'Tester')
                 for row in neighbors}
        target = []

        def provider(root, project, purpose, state, questions, **kwargs):
            target.append(state['candidates'][6]['id'])
            self.assertNotEqual(target[0], seed['id'])
            with self.brain._connection() as con:
                con.execute("UPDATE relations SET valid_to='2020-01-01T00:00:00Z' WHERE id=?", (edges[target[0]]['id'],))
                con.commit()
            return self.response(state, target[0])

        with patch('jev_openrouter.evaluate', side_effect=provider):
            found = self.search(strategy='graph')
        self.assertNotIn(target[0], [row['id'] for row in found['results']])
        self.assertEqual(found['retrieval']['jev']['revalidated_candidate_count'], 7)
        with patch('jev_openrouter.evaluate', side_effect=lambda *args, **kwargs:
                   (self.brain.forget(seed['id'], 'Tester', 'Synthetic forgotten graph seed during scoring.')
                    and {'status': 'error', 'answers': {}, 'provider_calls': 1})):
            found = self.search(strategy='graph')
        self.assertEqual(found['results'], [])

    def test_semantic_local_candidates_can_supply_wider_pool(self):
        memories = [self.memory(index, title='Independent ' + str(index), content='Unmatched evidence.')
                    for index in range(8)]
        with self.brain._connection() as con:
            candidates = [{'id': row['id'], 'score': .9 - index / 100,
                           'fingerprint': fingerprint(self.brain._row(con, row['id']))}
                          for index, row in enumerate(memories)]
        semantic = {'status': 'ok', 'candidates': candidates, 'provider_calls': 1, 'input_tokens': 2}
        with patch('brain_semantic.semantic_candidates', return_value=semantic), \
                patch('jev_openrouter.evaluate', side_effect=lambda root, project, purpose, state, questions, **kw:
                      self.response(state, state['candidates'][6]['id'])):
            found = self.search(strategy='semantic')
        self.assertEqual(found['retrieval']['jev']['candidate_count'], 8)
        self.assertEqual(found['results'][0]['id'], memories[6]['id'])

    def test_disabled_has_original_context_and_never_exposes_wider_pool(self):
        self.memories(20)
        found = self.baseline()
        self.assertEqual(len(found['results']), 6)
        self.assertNotIn('jev', found['retrieval'])
        self.assertNotIn('candidate_pool', found)
        self.assertNotIn('candidates', found)


if __name__ == '__main__':
    unittest.main()
