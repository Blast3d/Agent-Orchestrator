"""Large memory packets stay canonical and do not overflow compact task indexes."""
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import sys
import tempfile
import unittest
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'app'))
from task_index import project_task_index, task_index_bytes
from task_store import TaskStore
from task_summary_repair import inspect_summary, repair_summary
from memory_usage import summary


class TaskIndexMemoryTests(unittest.TestCase):
    def test_unicode_and_escaped_context_fit_without_mutating_evidence(self):
        for content in ('\u77e5' * 32000, '\U0001f680' * 32000, '\\"\n' * 10000):
            original = {'memory_context': {'context': content, 'sha256': hashlib.sha256(content.encode()).hexdigest(),
                'ids': ['a' * 32], 'elapsed_ms': 12.5, 'execution_requested': True}}
            before = deepcopy(original)
            index = project_task_index(original)
            self.assertLess(len(task_index_bytes(index)), 65536)
            self.assertNotIn('context', index['memory_context'])
            self.assertEqual(index['memory_context']['context_chars'], len(content))
            self.assertEqual(index['memory_context']['sha256'], original['memory_context']['sha256'])
            self.assertEqual(original, before)

    def test_canonical_save_and_summary_repair_keep_full_provenance(self):
        with tempfile.TemporaryDirectory() as temp:
            store = TaskStore(Path(temp) / 'tasks')
            result = store.create(worker='claude', task='Large recall', size='large', assignment_project_id='alpha')
            content = '\u77e5' * 32000
            result.update(status='awaiting_review', execution_status='succeeded', response='Synthetic answer.',
                finalized_at='2026-09-26T01:00:00+00:00', memory_context={'context': content,
                    'sha256': hashlib.sha256(content.encode()).hexdigest(), 'ids': ['a' * 32],
                    'project_id': 'alpha', 'user_id': 'local', 'execution_requested': True})
            store.save(result['job_id'], result)
            folder = store.directory(result['job_id'])
            canonical = (folder / 'result.json').read_bytes()
            saved = json.loads(canonical)
            self.assertEqual(saved['memory_context']['context'], content)
            self.assertEqual(summary(saved)['stage'], 'execution_requested')
            plan = inspect_summary(result['job_id'], tasks_root=store.root)
            self.assertEqual(plan['action'], 'no_op')
            (folder / 'record.json').unlink()
            plan = inspect_summary(result['job_id'], tasks_root=store.root)
            repair_summary(result['job_id'], expected_result_sha256=plan['expected_result_sha256'],
                reviewer='Tester', note='Restore compact summary from unchanged canonical evidence.', tasks_root=store.root)
            self.assertEqual((folder / 'result.json').read_bytes(), canonical)
            index = json.loads((folder / 'record.json').read_bytes())
            self.assertNotIn('context', index['memory_context'])
            self.assertEqual(index['memory_context']['context_chars'], 32000)

if __name__ == '__main__':
    unittest.main()
