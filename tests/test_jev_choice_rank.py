"""Offline checks for the single-question Jev choice ranking format."""
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'app'))
from brain_store import BrainStore
import brain_jev
import jev_openrouter


class ChoiceRankingTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.brain = BrainStore(self.root)
        self.config = {'status': 'ready', 'key_present': True, 'authorized_projects': ['alpha'],
                       'purposes': ['memory_rank'], 'min_confidence': .8, 'cache_enabled': False,
                       'cache_ttl_seconds': 3600, 'max_requests_per_day': 100, 'timeout_seconds': 1}
        patcher = patch('jev_openrouter.load_config', side_effect=lambda root: self.config)
        patcher.start()
        self.addCleanup(patcher.stop)
        guarded = patch('jev_openrouter._post', side_effect=AssertionError('Unexpected network request'))
        guarded.start()
        self.addCleanup(guarded.stop)
        self.ids = []
        for index in range(4):
            proposed = self.brain.propose({'project_id': 'alpha', 'kind': 'fact', 'title': 'Retry policy ' + str(index),
                                           'content': 'Retry policy evidence ' + str(index) + '.',
                                           'source': {'type': 'user', 'note': 'Reviewed synthetic fixture.'}})
            self.ids.append(self.brain.approve(proposed['id'], 'Tester', 'Checked synthetic evidence.')['id'])
        self.calls = []

    def provider(self, *, confidence=.95, favorite=None, malformed=False, signal=0):
        def evaluate(root, project, purpose, state, questions, **kwargs):
            jev_openrouter._request(state, questions)  # the real transport validator accepts it
            self.calls.append((purpose, questions))
            ids = [item['id'] for item in state['candidates']]
            answers = {}
            for key, question in questions.items():
                if question['type'] == 'choice':
                    chosen = favorite or ids[-1]
                    probabilities = {identifier: (.97 if identifier == chosen else .03 / (len(ids) - 1))
                                     for identifier in ids}
                    if malformed:
                        probabilities.pop(ids[0])
                    answers[key] = {'type': 'choice', 'confidence': confidence, 'choice': chosen,
                                    'probabilities': probabilities}
                elif question['type'] == 'score':
                    index = int(key.removeprefix('memory_'))
                    answers[key] = {'type': 'score', 'confidence': confidence,
                                    'score': 3 if state['candidates'][index]['id'] == favorite else 0}
                else:
                    answers[key] = {'type': 'noul', 'noul': signal}
            return {'status': 'ok', 'provider_calls': 1, 'provider': 'TypeSafe', 'model': jev_openrouter.MODEL,
                    'answers': answers, 'input_tokens': 10, 'output_tokens': 1, 'cost_usd': .0001}
        return evaluate

    def search(self, **options):
        return self.brain.search('retry policy', 'alpha', record_trace=False, **options)

    def test_choice_request_is_one_question_over_the_batch(self):
        candidates = [{'id': identifier, 'title': 'T', 'content': 'C', 'excerpt_truncated': False}
                      for identifier in self.ids]
        state, questions = brain_jev.choice_request('retry', candidates, 'review')
        self.assertEqual(list(questions), [brain_jev.CHOICE_KEY])
        question = questions[brain_jev.CHOICE_KEY]
        self.assertEqual(question['type'], 'choice')
        self.assertEqual(set(question['criteria']), set(self.ids))
        self.assertIn('Do not choose an index, catalog, summary', question['instructions'])
        self.assertLessEqual(len(jev_openrouter._request(state, questions)), jev_openrouter.MAX_REQUEST_BYTES)
        with self.assertRaises(ValueError):
            brain_jev.choice_request('retry', candidates[:1])

    def test_confident_choice_reorders_the_whole_batch(self):
        baseline = self.search(jev=False)
        favorite = baseline['results'][-1]['id']
        with patch('jev_openrouter.evaluate', side_effect=self.provider(favorite=favorite)):
            ranked = self.search(jev_shape='choice')
        jev = ranked['retrieval']['jev']
        self.assertEqual(ranked['results'][0]['id'], favorite)
        self.assertEqual((jev['shape'], jev['requested_shape'], jev['ordering_policy']),
                         ('choice', 'choice', 'confident_choice_order'))
        self.assertTrue(jev['applied'])
        self.assertEqual(jev['choice_confidence'], .95)
        self.assertEqual(len(self.calls), 1)
        self.assertEqual([q['type'] for q in self.calls[0][1].values()], ['choice'])
        self.assertIn('jev_choice_probability', ranked['results'][0])
        self.assertNotIn('jev_score', ranked['results'][0])

    def test_low_confidence_choice_keeps_the_local_order(self):
        baseline = self.search(jev=False)
        with patch('jev_openrouter.evaluate', side_effect=self.provider(confidence=.4,
                                                                         favorite=baseline['results'][-1]['id'])):
            ranked = self.search(jev_shape='choice')
        self.assertEqual([r['id'] for r in ranked['results']], [r['id'] for r in baseline['results']])
        self.assertEqual(ranked['retrieval']['jev']['status'], 'low_confidence')
        self.assertFalse(ranked['retrieval']['jev']['applied'])

    def test_malformed_distribution_keeps_the_local_order(self):
        baseline = self.search(jev=False)
        with patch('jev_openrouter.evaluate', side_effect=self.provider(malformed=True)):
            ranked = self.search(jev_shape='choice')
        self.assertEqual([r['id'] for r in ranked['results']], [r['id'] for r in baseline['results']])
        self.assertEqual(ranked['retrieval']['jev']['status'], 'invalid_response')

    def test_configured_shape_is_used_when_the_search_does_not_choose(self):
        self.config['memory_rank_shape'] = 'choice'
        with patch('jev_openrouter.evaluate', side_effect=self.provider()):
            ranked = self.search()
        self.assertEqual(ranked['retrieval']['jev']['shape'], 'choice')
        with patch('jev_openrouter.evaluate', side_effect=self.provider()):
            ranked = self.search(jev_shape='rubric')
        self.assertEqual(ranked['retrieval']['jev']['shape'], 'rubric')

    def test_shortlists_needing_several_requests_fall_back_to_rubric(self):
        for index in range(20):
            proposed = self.brain.propose({'project_id': 'alpha', 'kind': 'fact', 'title': 'Retry note ' + str(index),
                                           'content': 'Retry policy detail ' + str(index) + '.',
                                           'source': {'type': 'user', 'note': 'Reviewed synthetic fixture.'}})
            self.brain.approve(proposed['id'], 'Tester', 'Checked synthetic evidence.')
        with patch('jev_openrouter.evaluate', side_effect=self.provider()):
            ranked = self.search(jev_shape='choice', depth='balanced')
        jev = ranked['retrieval']['jev']
        self.assertEqual((jev['shape'], jev['requested_shape']), ('rubric', 'choice'))
        self.assertIn('rubric scoring', jev['shape_note'])
        self.assertTrue(all(q['type'] == 'score' for _, questions in self.calls for q in questions.values()))

    def test_choice_works_with_passage_review_questions(self):
        self.config['purposes'] = ['memory_rank', 'memory_passage_review']
        baseline = self.search(jev=False)
        favorite = baseline['results'][-1]['id']
        with patch('jev_openrouter.evaluate', side_effect=self.provider(favorite=favorite)):
            ranked = self.search(jev_shape='choice')
        types = sorted({q['type'] for q in self.calls[0][1].values()})
        self.assertEqual(types, ['choice', 'noul'])
        self.assertEqual(ranked['results'][0]['id'], favorite)
        self.assertEqual(ranked['retrieval']['jev']['purpose'], 'memory_passage_review')

    def test_unknown_shape_is_rejected(self):
        with self.assertRaises(ValueError):
            self.search(jev_shape='tournament')

    def test_public_projection_reports_the_shape(self):
        from brain_retrieval_metadata import public_retrieval
        with patch('jev_openrouter.evaluate', side_effect=self.provider()):
            ranked = self.search(jev_shape='choice')
        public = public_retrieval(json.loads(json.dumps(ranked['retrieval'])))
        self.assertEqual((public['jev']['shape'], public['jev']['choice_confidence']), ('choice', .95))


if __name__ == '__main__':
    unittest.main()
