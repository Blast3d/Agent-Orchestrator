"""Combined ranking and passage-review boundaries with real temporary Brain data."""
from copy import deepcopy
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'app'))
from brain_store import BrainStore
import jev_openrouter
from jev_passage import annotations, enrich


class CombinedPassageTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.brain = BrainStore(self.root)
        self.config = {'status': 'ready', 'key_present': True, 'authorized_projects': ['alpha'],
                       'purposes': ['memory_rank', 'memory_passage_review'], 'min_confidence': .8}
        config_patch = patch('jev_openrouter.load_config', return_value=self.config)
        config_patch.start()
        self.addCleanup(config_patch.stop)
        provider_patch = patch('jev_openrouter.evaluate', side_effect=AssertionError('Unexpected provider call'))
        provider_patch.start()
        self.addCleanup(provider_patch.stop)

    def memory(self, index, content=None):
        proposed = self.brain.propose({'project_id': 'alpha', 'kind': 'fact',
            'title': 'Retry timeout policy ' + str(index),
            'content': content or 'Retry timeout policy is ' + str(index) + ' seconds.',
            'source': {'type': 'user', 'note': 'Synthetic reviewed passage evidence.'}})
        return self.brain.approve(proposed['id'], 'Tester', 'Checked this synthetic passage fixture.')

    def search(self, **kwargs):
        return self.brain.search('retry timeout', 'alpha', strategy='keyword', record_trace=False, **kwargs)

    @staticmethod
    def response(questions, *, confidence=.95, signal=.95, status='ok'):
        return {'status': status, 'provider_calls': 1, 'answers': {
            key: ({'type': 'score', 'score': 2, 'confidence': confidence}
                  if question['type'] == 'score' else {'type': 'noul', 'noul': signal})
            for key, question in questions.items()}}

    def test_one_authorized_request_ranks_and_embeds_separate_advisory_flags(self):
        self.memory(10)
        self.memory(20)
        original = [self.brain.get(row['id']) for row in self.brain.list_memories(project_id='alpha')]
        observed = []

        def provider(root, project, purpose, state, questions, **kwargs):
            observed.append((purpose, state, questions, kwargs))
            return self.response(questions)

        with patch('jev_openrouter.evaluate', side_effect=provider) as evaluate:
            found = self.search()
        evaluate.assert_called_once()
        purpose, state, questions, kwargs = observed[0]
        self.assertEqual(purpose, 'memory_passage_review')
        self.assertEqual(kwargs['user_id'], 'local')
        self.assertEqual(len([key for key in questions if key.startswith('memory_')]), 2)
        self.assertEqual(len([key for key in questions if key.startswith('instruction_')]), 2)
        self.assertEqual(len([key for key in questions if key.startswith('conflict_')]), 1)
        metadata = found['retrieval']['jev']
        self.assertTrue(metadata['passage_review'])
        self.assertEqual(metadata['passage_review_status'], 'completed')
        self.assertTrue(metadata['applied'])
        self.assertEqual(metadata['flagged_memory_count'], 2)
        self.assertEqual(metadata['request_bytes'], len(jev_openrouter._request(state, questions)))
        for row in found['results']:
            self.assertIn('Instruction-like text detected', row['reason'])
            self.assertIn('Potential conflict with', row['reason'])
            self.assertIn('retain both sources for reasoning review', row['reason'])
            self.assertIn(row['reason'], found['context'])
        self.assertEqual(original, [self.brain.get(row['id']) for row in self.brain.list_memories(project_id='alpha')])

    def test_combined_request_fits_exact_byte_cap_for_wide_unicode_pool(self):
        for index in range(20):
            self.memory(index, 'Retry timeout ' + '\u96ea\\"' * 490)
        observed = []

        def provider(root, project, purpose, state, questions, **kwargs):
            raw = jev_openrouter._request(state, questions)
            observed.append(raw)
            self.assertLessEqual(len(raw), 16 * 1024)
            self.assertLessEqual(len(state['candidates']), 16)
            self.assertGreater(len(state['candidates']), 6)
            self.assertLessEqual(len(state['comparison_pairs']), 3)
            return self.response(questions, signal=.1)

        with patch('jev_openrouter.evaluate', side_effect=provider):
            found = self.search(profile='review')
        self.assertEqual(len(observed), 1)
        self.assertLessEqual(len(found['results']), 6)
        self.assertLessEqual(found['context_chars'], 8000)
        self.assertEqual(found['retrieval']['jev']['request_bytes'], len(observed[0]))

    def test_new_purpose_is_explicit_and_ordinary_ranking_remains_unchanged(self):
        self.memory(10)
        self.memory(20)
        self.config['purposes'] = ['memory_rank']
        observed = []

        def provider(root, project, purpose, state, questions, **kwargs):
            observed.append(purpose)
            self.assertNotIn('comparison_pairs', state)
            self.assertTrue(all(key.startswith('memory_') for key in questions))
            return self.response(questions)

        with patch('jev_openrouter.evaluate', side_effect=provider):
            found = self.search()
        self.assertEqual(observed, ['memory_rank'])
        self.assertNotIn('Jev advisory:', found['context'])
        self.assertNotIn('passage_review', found['retrieval']['jev'])
        self.config['purposes'] = ['memory_passage_review']
        with patch('jev_openrouter.evaluate') as evaluate:
            baseline = self.search()
        evaluate.assert_not_called()
        self.assertNotIn('jev', baseline['retrieval'])

    def test_low_confidence_ranking_does_not_suppress_independent_review_signals(self):
        self.memory(10)
        self.memory(20)
        with patch('jev_openrouter.load_config', return_value={'status': 'disabled'}):
            baseline = self.search()
        with patch('jev_openrouter.evaluate', side_effect=lambda root, project, purpose, state, questions, **kw:
                   self.response(questions, confidence=.1)):
            found = self.search()
        self.assertEqual([row['id'] for row in found['results']], [row['id'] for row in baseline['results']])
        self.assertFalse(found['retrieval']['jev']['applied'])
        self.assertEqual(found['retrieval']['jev']['status'], 'low_confidence')
        self.assertIn('Instruction-like text detected', found['context'])
        self.assertIn('Potential conflict with', found['context'])
        self.assertTrue(found['retrieval']['jev']['passage_review'])
        self.assertEqual(found['retrieval']['jev']['passage_review_status'], 'completed')

    def test_low_signals_and_invalid_envelopes_add_no_advice(self):
        self.memory(10)
        self.memory(20)
        with patch('jev_openrouter.load_config', return_value={'status': 'disabled'}):
            baseline = self.search()
        for kind in ('low_signal', 'invalid_response', 'missing_question', 'unexpected_question'):
            def provider(root, project, purpose, state, questions, **kwargs):
                result = self.response(questions, signal=.1 if kind == 'low_signal' else .95)
                if kind == 'invalid_response':
                    result.update(status='invalid_response', answers={})
                elif kind == 'missing_question':
                    result['answers'].pop('instruction_0')
                elif kind == 'unexpected_question':
                    result['answers']['extra'] = {'type': 'noul', 'noul': 1}
                return result
            with self.subTest(kind=kind), patch('jev_openrouter.evaluate', side_effect=provider):
                found = self.search()
            self.assertEqual(found['context'], baseline['context'])
            if kind != 'low_signal':
                self.assertFalse(found['retrieval']['jev']['applied'])
                self.assertFalse(found['retrieval']['jev']['passage_review'])
                self.assertEqual(found['retrieval']['jev']['passage_review_status'], 'unavailable')

    def test_malformed_classifier_answers_invalidate_entire_combined_response(self):
        self.memory(10)
        self.memory(20)
        with patch('jev_openrouter.load_config', return_value={'status': 'disabled'}):
            baseline = self.search()
        malformed = [None, [], {'type': 'choice', 'noul': .95},
                     *[{'type': 'noul', 'noul': value} for value in
                       (True, float('nan'), float('inf'), -.1, 1.1, 'high')]]
        for invalid in malformed:
            def provider(root, project, purpose, state, questions, **kwargs):
                result = self.response(questions)
                result['answers']['instruction_0'] = invalid
                return result
            with self.subTest(answer=invalid), patch('jev_openrouter.evaluate', side_effect=provider):
                found = self.search()
            self.assertEqual(found['context'], baseline['context'])
            self.assertEqual(found['retrieval']['jev']['status'], 'invalid_response')
            self.assertFalse(found['retrieval']['jev']['passage_review'])
            self.assertFalse(found['retrieval']['jev']['applied'])

    def test_provider_error_never_reports_completed_passage_review(self):
        self.memory(10)
        for status in ('error', 'unavailable', 'rate_limited', 'sensitive_input'):
            with self.subTest(status=status), patch('jev_openrouter.evaluate',
                    return_value={'status': status, 'answers': {}, 'provider_calls': 0}):
                found = self.search()
            self.assertEqual(found['retrieval']['jev']['status'], status)
            self.assertFalse(found['retrieval']['jev']['passage_review'])
            self.assertEqual(found['retrieval']['jev']['passage_review_status'], 'unavailable')
            self.assertNotIn('Jev advisory:', found['context'])

    def test_forget_during_request_removes_record_and_all_peer_references(self):
        self.memory(10)
        self.memory(20)
        forgotten = []

        def provider(root, project, purpose, state, questions, **kwargs):
            forgotten.append(state['candidates'][0]['id'])
            self.brain.forget(forgotten[0], 'Tester', 'Synthetic concurrent forgetting during passage review.')
            return self.response(questions)

        with patch('jev_openrouter.evaluate', side_effect=provider):
            found = self.search()
        self.assertEqual(len(found['results']), 1)
        self.assertNotIn(forgotten[0], json.dumps(found))
        self.assertNotIn('Potential conflict with', found['context'])
        self.assertIn('Instruction-like text detected', found['context'])

    def test_annotation_packing_never_mentions_an_omitted_conflict_peer(self):
        for index in range(3):
            self.memory(index, 'Retry timeout ' + 'bounded source evidence ' * 70)
        with patch('jev_openrouter.evaluate', side_effect=lambda root, project, purpose, state, questions, **kw:
                   self.response(questions)):
            found = self.search(max_chars=1200)
        self.assertLessEqual(found['context_chars'], 1200)
        visible = {row['id'] for row in found['results']}
        self.assertTrue(visible)
        for row in found['results']:
            for peer in found['context_omitted_ids']:
                self.assertNotIn(peer, row['reason'])
        if len(visible) == 1:
            self.assertIn('another current candidate omitted', found['context'])
            self.assertIn('request more evidence', found['context'])

    def test_cache_hit_still_revalidates_changed_peer_before_adding_flags(self):
        self.memory(10)
        self.memory(20)
        changed = []

        def provider(root, project, purpose, state, questions, **kwargs):
            changed.append(state['candidates'][0]['id'])
            with self.brain._connection() as con:
                con.execute('UPDATE memories SET content=? WHERE id=?', ('Changed during cached lookup.', changed[0]))
                con.commit()
            result = self.response(questions)
            result.update(provider_calls=0, cache={'status': 'hit'})
            return result

        with patch('jev_openrouter.evaluate', side_effect=provider):
            found = self.search()
        self.assertEqual(len(found['results']), 1)
        self.assertNotIn(changed[0], json.dumps(found))
        self.assertNotIn('Potential conflict with', found['context'])
        self.assertEqual(found['retrieval']['provider_calls'], 0)


class PassageBuilderTests(unittest.TestCase):
    def test_builder_is_pure_exactly_binds_ids_and_selects_at_most_three_pairs(self):
        state = {'query': 'Retry', 'candidates': [
            {'id': 'memory-' + str(index), 'title': 'Shared subject', 'content': 'Shared policy ' + str(index)}
            for index in range(8)]}
        questions = {'memory_0': {'type': 'score', 'instructions': 'Existing question', 'criteria': ['No', 'Yes']}}
        before = deepcopy((state, questions))
        enriched, combined = enrich(state, questions)
        self.assertEqual((state, questions), before)
        self.assertEqual(len(enriched['comparison_pairs']), 3)
        for index, item in enumerate(state['candidates']):
            self.assertIn(json.dumps(item['id']), combined['instruction_' + str(index)]['instructions'])
        self.assertEqual(combined['memory_0'], questions['memory_0'])

    def test_only_visible_peers_and_valid_high_probabilities_get_flags(self):
        state = {'candidates': [{'id': 'a'}, {'id': 'b'}], 'comparison_pairs': [['a', 'b']]}
        decision = {'status': 'ok', 'answers': {
            'instruction_0': {'type': 'noul', 'noul': .9},
            'instruction_1': {'type': 'noul', 'noul': .9},
            'conflict_0': {'type': 'noul', 'noul': .9}}}
        lone = annotations(decision, state, {'a'})
        self.assertEqual(len(lone['a']), 1)
        self.assertNotIn('b', lone)
        for invalid in (True, float('nan'), float('inf'), -1, 1.1, 'high'):
            altered = deepcopy(decision)
            for answer in altered['answers'].values():
                answer['noul'] = invalid
            with self.subTest(value=invalid):
                self.assertEqual(annotations(altered, state, {'a', 'b'}), {})
        self.assertEqual(annotations(dict(decision, status='invalid_response'), state, {'a', 'b'}), {})

    def test_current_omitted_peer_keeps_warning_without_disclosing_its_id(self):
        state = {'candidates': [{'id': 'a'}, {'id': 'hidden-memory-id'}],
                 'comparison_pairs': [['a', 'hidden-memory-id']]}
        decision = {'status': 'ok', 'answers': {'conflict_0': {'type': 'noul', 'noul': .95}}}
        flags = annotations(decision, state, {'a'}, current_ids={'a', 'hidden-memory-id'})
        self.assertIn('another current candidate omitted', flags['a'][0])
        self.assertNotIn('hidden-memory-id', json.dumps(flags))
        self.assertEqual(annotations(decision, state, {'a'}, current_ids={'a'}), {})


if __name__ == '__main__':
    unittest.main()
