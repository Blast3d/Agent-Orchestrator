"""Capture retry fencing, bounded candidate scans and faithful recall packing."""
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'app'))
from brain_recall import _pack, _source_room
from brain_store import BrainStore
from coordinator_handoff import Coordinator
from init_run import create_run
from memory_bundle import capture_run
from memory_delivery import fit, heading
from task_store import TaskStore


class BrainRecallFixTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        mocked = patch('jev_openrouter.load_config', return_value={'status': 'disabled'})
        mocked.start(); self.addCleanup(mocked.stop)
        self.brain = BrainStore(self.root)

    def memory(self, index):
        value = self.brain.propose(dict(project_id='alpha', kind='fact',
            title='Retry step ' + str(index), content='Retry detail ' + str(index),
            source={'type': 'user', 'note': 'Synthetic bounded recall test'}))
        return self.brain.approve(value['id'], 'Tester', 'Checked this synthetic fixture evidence.')

    def row(self, index, content):
        return dict(id=format(index, '032x'), project_id='alpha', kind='fact',
            title='Packing fixture', content=content, episode=None,
            source={'type': 'user', 'note': 'Synthetic packing fixture'},
            valid_from='2026-09-01T12:00:00+00:00', valid_to=None,
            reason='Synthetic matching evidence')

    def capture_fixture(self):
        run, manifest = create_run(self.root, 'bundle-test',
            'Review the synthetic system', project_id='alpha')
        state = Coordinator(run).read()
        (run / 'review/evidence.md').write_text(
            'Synthetic checks verify recovery and saved output.\n', encoding='utf-8')
        bundle = {'project_id': 'alpha', 'capture_id': 'verified-recovery',
            'evidence': ['review/evidence.md'], 'memories': [
                {'key': 'incident', 'kind': 'fact', 'title': 'Zebra incident',
                 'content': 'The saved answer survives an interrupted coordinator session.'},
                {'key': 'recovery', 'kind': 'procedure', 'title': 'Recovery procedure',
                 'content': 'Inspect the saved result before attempting another worker dispatch.'}],
            'relations': [{'from': 'recovery', 'to': 'incident', 'relation': 'solves',
                'reason': 'The reviewed procedure addresses this observed interruption.'}]}
        def capture():
            return capture_run(self.root, run.name, bundle,
                owner=state['owner'], session=state['session'], generation=state['generation'],
                reviewer='Synthetic reviewer',
                note='Verified the two claims against the saved local evidence file.')
        return run, capture

    def memory_ids(self):
        with self.brain._connection() as con:
            return {row['id'] for row in con.execute('SELECT id FROM memories')}

    def assert_capture_once(self, capture):
        first = capture()
        self.assertEqual(first['memory_outcome']['status'], 'remembered')
        self.assertEqual(first['provider_calls'], 0)
        identifiers = set(first['memory_outcome']['memory_ids'])
        self.assertEqual(len(identifiers), 2)
        self.assertEqual(self.memory_ids(), identifiers)
        repeated = capture()
        self.assertEqual(repeated['job_id'], first['job_id'])
        self.assertTrue(repeated['memory_outcome']['reused'])
        self.assertEqual(self.memory_ids(), identifiers)

    def test_capture_retries_after_initial_result_save_fails(self):
        run, capture = self.capture_fixture()
        with patch.object(TaskStore, 'save', side_effect=OSError('Synthetic result write failure')):
            with self.assertRaises(OSError):
                capture()
        self.assertEqual(self.memory_ids(), set())
        self.assert_capture_once(capture)

    def test_stranded_importing_journal_is_finished_on_retry(self):
        # A journal written before the task result (the pre-fix failure) must not block retries.
        run, capture = self.capture_fixture()
        real_save = TaskStore.save
        calls = []
        def fail_first(store, job_id, result):
            calls.append(job_id)
            if len(calls) == 1:
                raise OSError('Synthetic crash between journal and result')
            return real_save(store, job_id, result)
        with patch.object(TaskStore, 'save', fail_first):
            with self.assertRaises(OSError):
                capture()
        journal = json.loads((run / 'review/memory-capture-verified-recovery.json').read_text(encoding='utf-8'))
        self.assertEqual(journal['status'], 'importing')
        self.assertFalse((TaskStore(self.root / 'runs/tasks').directory(journal['job_id']) / 'result.json').exists())
        self.assertEqual(self.memory_ids(), set())
        self.assert_capture_once(capture)
        self.assertEqual(len(list((self.root / 'runs/tasks').glob('*/record.json'))), 1)

    def capped_admission(self):
        def task_row(index):
            return {'project_id': 'alpha', 'source': json.dumps(
                {'type': 'task', 'job_id': format(index, '032x')})}
        admitted = set()
        for index in range(128):
            self.assertTrue(_source_room(task_row(index), admitted))
        self.assertEqual(len(admitted), 128)
        seen = []
        def check(row, source_keys):
            source_keys.update(admitted)
            seen.append(row['id'])
            # Inject the cap boundary while keeping canonical fixtures user-backed.
            if len(seen) == 1:
                return _source_room(task_row(128), source_keys)
            if len(seen) == 2:
                return _source_room(task_row(0), source_keys)
            return _source_room(row, source_keys)
        return check, seen

    def test_lexical_scan_keeps_admitted_and_user_rows_after_task_cap(self):
        for index in range(3):
            self.memory(index)
        check, seen = self.capped_admission()
        with patch('brain_recall._source_room', side_effect=check):
            result = self.brain.search('retry', 'alpha', strategy='keyword',
                hops=0, record_trace=False)
        self.assertEqual(len(seen), 3)
        self.assertEqual({row['id'] for row in result['results']}, set(seen[1:]))
        self.assertTrue(result['recall_incomplete'])
        self.assertEqual(result['candidate_checks'], 2)

    def test_semantic_scan_keeps_admitted_and_user_rows_after_task_cap(self):
        memories = [self.memory(index) for index in range(3)]
        check, seen = self.capped_admission()
        semantic = {'status': 'ready', 'reason': 'Synthetic semantic candidates',
            'model': None, 'provider_calls': 0, 'input_tokens': None,
            'candidates': [{'id': row['id'], 'score': 0.8, 'fingerprint': 'fixture'}
                           for row in memories]}
        route = {'reason': 'Synthetic semantic route', 'graph_hops': 0, 'use_semantic': True}
        with patch('brain_recall.plan', return_value=route), \
                patch('brain_semantic.semantic_candidates', return_value=semantic), \
                patch('brain_semantic.fingerprint', return_value='fixture'), \
                patch('brain_recall._source_room', side_effect=check):
            result = self.brain.search('platypus', 'alpha', record_trace=False)
        self.assertEqual(len(seen), 3)
        self.assertEqual({row['id'] for row in result['results']}, set(seen[1:]))
        self.assertTrue(result['recall_incomplete'])
        self.assertEqual(result['retrieval']['semantic_added'], 2)

    def test_pack_uses_longest_prefix_that_fits_escaped_content(self):
        for content in ['\n' * 2000, 'x' * 2000, '\\"\t' * 1000]:
            with self.subTest(content=repr(content[:6])):
                row = self.row(1, content)
                _, full, _ = _pack(None, [row], 100000, False, {})
                overhead = len(full) - (len(json.dumps(content, ensure_ascii=False)) - 2)
                maximum = overhead + 520
                packed, context, omitted = _pack(None, [row], maximum, False, {})
                self.assertEqual(omitted, [])
                self.assertEqual(len(packed), 1)
                self.assertTrue(packed[0]['truncated'])
                self.assertLessEqual(len(context), maximum)
                shortened = packed[0]['content']
                self.assertTrue(shortened.endswith('…'))
                self.assertEqual(shortened[:-1], content[:len(shortened)-1])
                next_prefix = content[:len(shortened)] + '…'
                self.assertGreater(overhead + len(json.dumps(next_prefix, ensure_ascii=False)) - 2,
                    maximum)
                self.assertEqual(row['content'], content)

    def test_delivery_preserves_exclusion_reasons_when_repacking(self):
        cross = 'Cross-project reference could not be verified'
        task = 'Task evidence could not be verified'
        cases = [([cross], True, False), ({'foreign': cross}, True, False),
                 ([task], False, True), ([cross, task], True, True)]
        rows = [self.row(index, 'x' * 800) for index in range(3)]
        for reasons, expect_cross, expect_task in cases:
            with self.subTest(reasons=reasons):
                excluded = reasons if isinstance(reasons, dict) else dict(enumerate(reasons))
                packed, context, omitted = _pack(None, rows, 100000, False, excluded)
                _, smaller, _ = _pack(None, rows[:2], 100000, False, excluded)
                maximum = len((heading('general') + smaller).encode('utf-8'))
                recalled = {'results': packed, 'context': context, 'context_chars': len(context),
                    'recall_incomplete': False, 'context_omitted_ids': omitted,
                    'source_validation': {'excluded_memories': len(excluded), 'reasons': reasons}}
                with patch.dict('memory_delivery.BYTE_LIMITS', {'gemini': maximum}):
                    delivered = fit(recalled, worker='gemini', base_prompt='')
                self.assertEqual(len(delivered['results']), 2)
                self.assertEqual('Unverifiable cross-project reference memories were excluded.'
                    in delivered['context'], expect_cross)
                self.assertEqual('Unverifiable task-backed memories were excluded.'
                    in delivered['context'], expect_task)
                self.assertLessEqual(delivered['delivery']['request_bytes'], maximum)
                self.assertEqual(delivered['source_validation'], recalled['source_validation'])


if __name__ == '__main__':
    unittest.main()
