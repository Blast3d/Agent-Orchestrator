"""Real writer/repair projections must fit the bounded dashboard reader."""
import hashlib
import json
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'app'))
from project_memory_usage import MAX_INDEX_BYTES, project_summary
from task_store import TaskStore, timestamp, write_json
from task_summary_repair import inspect_summary, repair_summary


class TaskIndexBoundsTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.store = TaskStore(self.root / 'runs/tasks')
        self.brief = {'schema_version': 1, 'ok': True, 'format': 'markdown',
                      'sections': {'inputs': 'Synthetic supplied source.\n' * 5000},
                      'errors': [], 'missing': [], 'warnings': ['Structure only.'],
                      'character_count': 135000}

    def create(self, **metadata):
        return self.store.create(worker='claude', task='Synthetic indexed task',
            assignment_project_id='index-test', assignment_id='sample',
            assignment_intent_id='b' * 32, assignment_contract_sha256='c' * 64,
            prompt_sha256='d' * 64, size='small', category='review',
            require_brief_check=True, brief_check=self.brief, **metadata)

    def accepted(self):
        result = self.create()
        result.update(status='accepted', execution_status='succeeded', review_status='accepted',
            finalized_at=timestamp(), response='Synthetic accepted response.',
            review={'reviewer': 'ASTRA', 'note': 'Verified this synthetic task against its fixture.',
                    'reviewed_at': timestamp()},
            usage={'input_tokens': 123, 'output_tokens': 45},
            modelUsage={'test-model': {'costUSD': 0.01}}, cleanup_errors=[],
            reservation_state='finished_pending_fresh_quota')
        self.store.save(result['job_id'], result)
        return result

    def test_initial_index_is_small_without_losing_in_memory_brief(self):
        result = self.create()
        path = self.store.directory(result['job_id']) / 'record.json'
        self.assertLessEqual(path.stat().st_size, MAX_INDEX_BYTES)
        index = json.loads(path.read_bytes())
        self.assertNotIn('sections', index['brief_check'])
        self.assertEqual(index['brief_check']['ok'], True)
        self.assertEqual(index['assignment_contract_sha256'], 'c' * 64)
        self.assertEqual(result['brief_check'], self.brief)
        self.assertIn('sections', self.brief)

    def test_final_index_is_visible_and_retains_identity_usage_and_review(self):
        result = self.accepted()
        folder = self.store.directory(result['job_id'])
        self.assertLessEqual((folder / 'record.json').stat().st_size, MAX_INDEX_BYTES)
        canonical = json.loads((folder / 'result.json').read_bytes())
        index = json.loads((folder / 'record.json').read_bytes())
        self.assertEqual(canonical['brief_check'], self.brief)
        self.assertNotIn('response', index)
        for field in ('assignment_project_id', 'assignment_id', 'assignment_intent_id',
                      'assignment_contract_sha256', 'prompt_sha256', 'canonical_result',
                      'review', 'usage', 'modelUsage', 'cleanup_errors', 'reservation_state'):
            self.assertEqual(index[field], canonical[field])
        sample = project_summary(self.root, 'index-test', force=True)
        self.assertEqual([row['job_id'] for row in sample['tasks']], [result['job_id']])
        self.assertEqual(sample['eligible'], 1)
        self.assertEqual(sample['unreadable'], 0)
        self.assertFalse(sample['truncated'])
        self.assertEqual(inspect_summary(result['job_id'], tasks_root=self.store.root)['action'], 'no_op')

    def test_reviewed_repair_recovers_oversized_legacy_index_without_changing_source(self):
        result = self.accepted()
        folder = self.store.directory(result['job_id'])
        canonical_before = (folder / 'result.json').read_bytes()
        expected_index = (folder / 'record.json').read_bytes()
        old_index = {key: value for key, value in result.items()
                     if key not in ('response', 'provider_result', 'quota_before', 'quota_refresh', 'contribution_ledger')}
        write_json(folder / 'record.json', old_index)
        self.assertGreater((folder / 'record.json').stat().st_size, MAX_INDEX_BYTES)
        self.assertEqual(project_summary(self.root, 'index-test', force=True)['tasks'], [])
        plan = inspect_summary(result['job_id'], tasks_root=self.store.root)
        self.assertEqual(plan['action'], 'repair_index')
        receipt = repair_summary(result['job_id'], tasks_root=self.store.root,
            expected_result_sha256=hashlib.sha256(canonical_before).hexdigest(), reviewer='ASTRA',
            note='Verified the bounded projection preserves canonical source and assignment identity.')
        self.assertTrue(receipt['applied'])
        self.assertEqual((folder / 'result.json').read_bytes(), canonical_before)
        self.assertEqual((folder / 'record.json').read_bytes(), expected_index)
        self.assertEqual(len(project_summary(self.root, 'index-test', force=True)['tasks']), 1)
        self.assertEqual(inspect_summary(result['job_id'], tasks_root=self.store.root)['action'], 'no_op')

    def test_unexpected_oversized_metadata_is_rejected_before_task_creation(self):
        with self.assertRaisesRegex(ValueError, '64 KiB'):
            self.create(extra_payload='x' * (MAX_INDEX_BYTES + 1))
        self.assertEqual(list(self.store.root.iterdir()), [])

    def test_index_limit_does_not_erase_a_completed_canonical_answer(self):
        result = self.accepted()
        folder = self.store.directory(result['job_id'])
        old_index = (folder / 'record.json').read_bytes()
        result['extra_payload'] = 'x' * (MAX_INDEX_BYTES + 1)
        with self.assertRaisesRegex(ValueError, '64 KiB'):
            self.store.save(result['job_id'], result)
        self.assertEqual(json.loads((folder / 'result.json').read_bytes())['response'], result['response'])
        self.assertEqual(json.loads((folder / 'result.json').read_bytes())['extra_payload'], result['extra_payload'])
        self.assertEqual((folder / 'record.json').read_bytes(), old_index)


if __name__ == '__main__':
    unittest.main()
