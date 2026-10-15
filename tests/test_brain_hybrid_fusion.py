"""Real SQLite recall with synthetic vectors; no provider calls."""
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'app'))
from brain_store import BrainStore
from brain_semantic import fingerprint
from brain_retrieval_metadata import public_retrieval


class HybridFusionTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.brain = BrainStore(Path(temporary.name))

    def memory(self, title, content, **extra):
        payload = dict(project_id='alpha', kind='fact', title=title,
            content=content, source={'type': 'user', 'note': 'Synthetic ranking fixture'})
        payload.update(extra)
        item = self.brain.propose(payload)
        return self.brain.approve(item['id'], 'Tester', 'Verified against the synthetic fixture.')

    def vectors(self, *pairs):
        with self.brain._connection() as con:
            candidates = [dict(id=item['id'], score=score, fingerprint=fingerprint(
                con.execute('SELECT * FROM memories WHERE id=?', (item['id'],)).fetchone()))
                for item, score in pairs]
        return dict(status='ok', candidates=candidates, provider_calls=1, input_tokens=4,
                    model='synthetic', indexed_count=len(candidates))

    def search(self, query, **kwargs):
        return self.brain.search(query, 'alpha', record_trace=False, jev=False, **kwargs)

    def test_agreement_between_keyword_and_vector_beats_vector_only(self):
        both = self.memory('Retry procedure', 'Retry with exponential backoff.')
        vector_only = self.memory('Unrelated label', 'A candidate with no lexical overlap.')
        with patch('brain_semantic.semantic_candidates', return_value=self.vectors(
                (vector_only, .99), (both, .70))):
            result = self.search('retry zebra', strategy='semantic')
        self.assertEqual(result['results'][0]['id'], both['id'])
        self.assertEqual(result['results'][0]['retrieval_method'], 'hybrid')
        self.assertEqual(result['retrieval']['fusion']['method'], 'reciprocal_rank')
        self.assertEqual(public_retrieval(result['retrieval'])['fusion']['overlap_count'], 1)

    def test_exact_path_is_not_demoted_when_also_found_by_vectors(self):
        exact = self.memory('Production config', 'Use app/production/config.json.')
        other = self.memory('Other instructions', 'Development settings live elsewhere.')
        with patch('brain_semantic.semantic_candidates', return_value=self.vectors(
                (other, .99), (exact, .66))):
            result = self.search('app/production/config.json', strategy='semantic')
        self.assertEqual(result['results'][0]['id'], exact['id'])
        self.assertTrue(result['results'][0]['ranking']['complete_match'])

    def test_vector_scale_does_not_change_fused_order(self):
        both = self.memory('Retry', 'Follow the retry procedure.')
        other = self.memory('Unrelated', 'No matching terms.')
        orders = []
        for scores in ((.99, .66), (.80, .79)):
            with patch('brain_semantic.semantic_candidates', return_value=self.vectors(
                    (other, scores[0]), (both, scores[1]))):
                result = self.search('retry zebra', strategy='semantic')
            orders.append([row['id'] for row in result['results']])
        self.assertEqual(orders[0], orders[1])
        self.assertEqual(orders[0][0], both['id'])

    def test_packing_can_fill_a_slot_after_an_unfittable_candidate(self):
        oversized = self.memory('Retry', 'Retry is documented here.', kind='episode', episode={
            'problem': 'p' * 600, 'action': 'a' * 600, 'outcome': 'o' * 600})
        small = self.memory('Retry alternative', 'Retry once after checking status.')
        result = self.search('retry', strategy='keyword', limit=1, max_chars=950)
        self.assertEqual([row['id'] for row in result['results']], [small['id']])
        self.assertIn(oversized['id'], result['context_omitted_ids'])
        self.assertLessEqual(result['context_chars'], 950)
        json.loads(result['context'].splitlines()[-1])

    def test_unavailable_semantics_preserves_keyword_scores(self):
        self.memory('Retry', 'Check the original task before retrying.')
        keyword = self.search('retry zebra', strategy='keyword')
        with patch('brain_semantic.semantic_candidates', return_value={
                'status': 'unavailable', 'candidates': [], 'provider_calls': 0}):
            fallback = self.search('retry zebra', strategy='semantic')
        self.assertEqual([(row['id'], row['score']) for row in keyword['results']],
                         [(row['id'], row['score']) for row in fallback['results']])
        self.assertNotIn('fusion', fallback['retrieval'])

    def test_invalidated_vector_does_not_contribute_to_fusion(self):
        current = self.memory('Retry', 'Use the current procedure.')
        forgotten = self.memory('Other', 'No longer valid.')
        reply = self.vectors((forgotten, .99), (current, .7))
        def provider(*args, **kwargs):
            self.brain.forget(forgotten['id'], 'Tester', 'Remove obsolete synthetic evidence.')
            return reply
        with patch('brain_semantic.semantic_candidates', side_effect=provider):
            result = self.search('retry zebra', strategy='semantic')
        self.assertEqual([row['id'] for row in result['results']], [current['id']])
        self.assertEqual(result['retrieval']['fusion']['semantic_count'], 1)
        self.assertNotIn(forgotten['id'], result['context'])

    def test_fusion_metadata_survives_trace_projection_without_query_text(self):
        item = self.memory('Retry', 'Check completion before repeating work.')
        with patch('brain_semantic.semantic_candidates', return_value=self.vectors((item, .8))):
            result = self.brain.search('retry zebra', 'alpha', strategy='semantic', jev=False)
        with self.brain._connection() as con:
            raw = con.execute('SELECT detail FROM retrieval_traces WHERE trace_id=?',
                              (result['trace_id'],)).fetchone()[0]
        self.assertNotIn('retry zebra', raw)
        self.assertEqual(public_retrieval(json.loads(raw))['fusion'], result['retrieval']['fusion'])


if __name__ == '__main__':
    unittest.main()
