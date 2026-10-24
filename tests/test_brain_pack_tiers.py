"""Summary-first recall packing and reviewed-contradiction warnings."""
import json
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'app'))
from brain_recall import FLOOR_CHARS, _floor, _pack
from brain_store import BrainStore


def row(index, content, **extra):
    return dict({'id': f'{index:032x}', 'project_id': 'alpha', 'kind': 'fact', 'title': 'Memory ' + str(index),
                 'content': content, 'episode': {}, 'source': {'type': 'user', 'note': 'Synthetic'},
                 'valid_from': '2026-01-01T00:00:00+00:00', 'valid_to': None, 'reason': 'Synthetic'}, **extra)


class SummaryFirstPackingTests(unittest.TestCase):
    def test_everything_that_fits_is_packed_whole_and_unchanged(self):
        rows = [row(1, 'a' * 900), row(2, 'b' * 50), row(3, 'c ' * 300)]
        packed, context, omitted = _pack(None, rows, 100000, False, {})
        self.assertEqual(omitted, [])
        self.assertEqual([item['content'] for item in packed], [item['content'] for item in rows])
        self.assertFalse(any(item.get('truncated') for item in packed))
        self.assertEqual(len(context.splitlines()), 4)

    def test_one_long_memory_cannot_crowd_out_later_ones(self):
        long_text = ' '.join('word' + str(i) for i in range(900))
        rows = [row(1, long_text)] + [row(i, 'Short reviewed fact number ' + str(i) + '.') for i in range(2, 6)]
        _, full_first, _ = _pack(None, rows[:1], 100000, False, {})
        budget = len(full_first) // 2
        packed, context, omitted = _pack(None, rows, budget, False, {})
        self.assertEqual([item['id'] for item in packed], [item['id'] for item in rows])
        self.assertEqual(omitted, [])
        self.assertLessEqual(len(context), budget)
        first = packed[0]
        self.assertTrue(first['truncated'])
        self.assertTrue(first['content'].endswith('…'))
        self.assertTrue(long_text.startswith(first['content'][:-1]))
        # The leftover budget deepened the top memory well past its floor excerpt.
        self.assertGreater(len(first['content']), FLOOR_CHARS * 2)
        self.assertEqual([item['content'] for item in packed[1:]], [item['content'] for item in rows[1:]])

    def test_floor_excerpt_of_a_truncated_row_stays_an_exact_prefix(self):
        original = 'x' * 1000
        truncated = row(1, original[:600] + '…', truncated=True)
        floor = _floor(truncated)
        self.assertTrue(floor['content'].endswith('…'))
        self.assertNotIn('……', floor['content'])
        self.assertTrue(original.startswith(floor['content'][:-1]))
        short = row(2, 'short')
        self.assertIs(_floor(short), short)

    def test_max_results_still_bounds_the_selection(self):
        rows = [row(i, 'Fact ' + str(i)) for i in range(1, 9)]
        packed, _, omitted = _pack(None, rows, 100000, False, {}, max_results=3)
        self.assertEqual(len(packed), 3)
        self.assertEqual(omitted, [])


class ContradictionWarningTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.brain = BrainStore(Path(temporary.name))

    def active(self, title, content):
        proposed = self.brain.propose({'project_id': 'alpha', 'kind': 'fact', 'title': title, 'content': content,
                                       'source': {'type': 'user', 'note': 'Synthetic reviewed source'}})
        return self.brain.approve(proposed['id'], 'Tester', 'Checked against the synthetic source.')

    def test_contradicts_is_a_supported_relation_and_recall_warns_both_sides(self):
        old = self.active('Retry limit', 'The retry limit is three attempts.')
        new = self.active('Retry limit raised', 'The retry limit is five attempts.')
        self.brain.relate(new['id'], old['id'], 'contradicts', 'Tester')
        found = self.brain.search('retry limit', 'alpha', record_trace=False, jev=False)
        reasons = {item['id']: item['reason'] for item in found['results']}
        self.assertIn('Reviewed contradiction with ' + old['id'], reasons[new['id']])
        self.assertIn('Reviewed contradiction with ' + new['id'], reasons[old['id']])
        self.assertEqual(found['retrieval']['contradiction_count'], 2)
        self.assertIn('Reviewed contradiction', found['context'])

    def test_superseded_peer_no_longer_triggers_the_warning(self):
        old = self.active('Retry limit', 'The retry limit is three attempts.')
        new = self.active('Retry limit raised', 'The retry limit is five attempts.')
        self.brain.relate(new['id'], old['id'], 'contradicts', 'Tester')
        self.brain.supersede(old['id'], new['id'], 'Tester', 'Five attempts is the current policy.')
        found = self.brain.search('retry limit', 'alpha', record_trace=False, jev=False)
        self.assertEqual([item['id'] for item in found['results']], [new['id']])
        self.assertNotIn('Reviewed contradiction', found['results'][0]['reason'])
        self.assertEqual(found['retrieval']['contradiction_count'], 0)

    def test_unknown_relations_are_still_rejected(self):
        a, b = self.active('Alpha fact', 'One.'), self.active('Beta fact', 'Two.')
        with self.assertRaises(ValueError):
            self.brain.relate(a['id'], b['id'], 'refutes', 'Tester')
        json.dumps(self.brain.get(a['id']))


if __name__ == '__main__':
    unittest.main()
