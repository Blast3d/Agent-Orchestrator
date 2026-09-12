"""Bounded project rollups expose saved evidence, not provider-use claims."""
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'app'))
from brain_store import digest
import project_memory_usage as usage


class ProjectMemoryUsageTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.tasks = self.root / 'runs/tasks'
        self.tasks.mkdir(parents=True)
        self.sequence = 0

    def task(self, project='alpha', **changes):
        self.sequence += 1
        identifier = format(self.sequence, '032x')
        at = (datetime(2026, 9, 1, tzinfo=timezone.utc) + timedelta(seconds=self.sequence)).isoformat()
        result = {'schema_version': 1, 'job_id': identifier, 'assignment_project_id': project,
            'task': 'Review recovery behavior', 'worker': 'claude', 'created_at': at,
            'finalized_at': at, 'response': 'PRIVATE_RESPONSE', 'status': 'accepted',
            'execution_status': 'succeeded', 'review_status': 'accepted',
            'review': {'reviewer': 'Reviewer', 'note': 'Checked the saved answer against the task requirements.'},
            'memory_lookup_requested': False, 'memory_policy': 'disabled'}
        result.update(changes)
        directory = self.tasks / identifier
        directory.mkdir()
        self.write(result)
        return result

    def write(self, result, *, index=True):
        directory = self.tasks / result['job_id']
        (directory / 'result.json').write_text(json.dumps(result), encoding='utf-8')
        if index:
            record = {key: value for key, value in result.items() if key not in ('response', 'review')}
            (directory / 'record.json').write_text(json.dumps(record), encoding='utf-8')

    def receipt(self, result, mode='automatic', status='remembered', **changes):
        fields = ('job_id', 'assignment_project_id', 'response', 'review', 'finalized_at') if (
            mode == 'curated_bundle') else ('job_id', 'assignment_project_id', 'response',
            'finalized_at', 'review', 'task', 'category', 'assignment_id')
        receipt = {'schema_version': 1, 'job_id': result['job_id'], 'project_id': result['assignment_project_id'],
            'mode': mode, 'status': status, 'memory_id': 'a' * 32,
            'source_sha256': digest({key: result.get(key) for key in fields})}
        if mode == 'curated_bundle':
            receipt['memory_ids'] = ['a' * 32, 'b' * 32, 'c' * 32]
        receipt.update(changes)
        (self.tasks / result['job_id'] / 'memory-outcome.json').write_text(json.dumps(receipt), encoding='utf-8')
        return receipt

    def sample(self, **kwargs):
        return usage.project_summary(self.root, 'alpha', force=True, **kwargs)

    def test_index_filters_project_and_canonical_identity_is_revalidated(self):
        matching = self.task()
        other = self.task('beta')
        forged = self.task()
        forged['assignment_project_id'] = 'beta'
        self.write(forged, index=False)
        read = usage._bounded_read
        paths = []
        def observed(path, *args):
            paths.append(path)
            return read(path, *args)
        with patch.object(usage, '_bounded_read', side_effect=observed):
            value = self.sample()
        self.assertEqual([row['job_id'] for row in value['tasks']], [matching['job_id']])
        self.assertNotIn(self.tasks / other['job_id'] / 'result.json', paths)
        self.assertEqual(value['unreadable'], 1)
        self.assertEqual(value['scanned'], 3)

    def test_newest_fifty_retained_tasks_are_explicitly_a_sample(self):
        rows = [self.task() for _ in range(55)]
        value = self.sample()
        self.assertEqual(len(value['tasks']), 50)
        self.assertEqual(value['tasks'][0]['job_id'], rows[-1]['job_id'])
        self.assertEqual(value['tasks'][-1]['job_id'], rows[5]['job_id'])
        self.assertEqual(value['eligible'], 50)
        self.assertEqual(value['missing_receipt'], 50)
        self.assertTrue(value['truncated'])
        self.assertIn('not lifetime', value['sample_scope'])

    def test_legacy_bundle_and_imported_receipts_are_counted_without_availability_claim(self):
        legacy = self.task()
        imported = self.task(worker='native-review', imported_completed_artifact=True,
                             artifact_origin='reviewed-run-closeout')
        self.task()
        self.task(status='awaiting_review', review_status='pending')
        self.receipt(legacy)
        self.receipt(imported, mode='curated_bundle')
        value = self.sample()
        self.assertEqual(value['eligible'], 3)
        self.assertEqual(value['remembered'], 2)
        self.assertEqual(value['missing_receipt'], 1)
        by_id = {row['job_id']: row for row in value['tasks']}
        self.assertEqual(by_id[legacy['job_id']]['capture']['memory_count'], 1)
        self.assertEqual(by_id[imported['job_id']]['capture']['memory_count'], 3)
        self.assertTrue(by_id[imported['job_id']]['imported_artifact'])
        self.assertEqual(by_id[imported['job_id']]['worker'], 'native-review')
        for row in value['tasks']:
            self.assertTrue(row['capture']['historical'])
            self.assertEqual(row['capture']['current_availability'], 'not_checked')

    def test_forgotten_historical_receipt_does_not_count_as_remembered(self):
        result = self.task()
        self.receipt(result, status='skipped', reason='forgotten')
        value = self.sample()
        self.assertEqual(value['remembered'], 0)
        self.assertEqual(value['missing_receipt'], 0)
        self.assertEqual(value['tasks'][0]['capture']['status'], 'skipped')
        self.assertEqual(value['tasks'][0]['capture']['memory_count'], 1)

    def test_source_change_invalidates_receipt_and_revision_even_without_public_content(self):
        result = self.task()
        self.receipt(result)
        before = self.sample()
        result['response'] = 'DIFFERENT_PRIVATE_RESPONSE'
        self.write(result)
        after = self.sample()
        self.assertNotEqual(before['revision'], after['revision'])
        self.assertEqual(after['remembered'], 0)
        self.assertEqual(after['tasks'][0]['capture']['status'], 'stale')
        self.assertNotIn('PRIVATE_RESPONSE', json.dumps(after))

    def test_revision_changes_for_feedback_or_receipt_details_without_copying_them(self):
        result = self.task()
        before = self.sample()
        result['memory_feedback'] = {'PRIVATE_FEEDBACK_NOTE': 'unverified content'}
        self.write(result)
        after_feedback = self.sample()
        self.assertNotEqual(before['revision'], after_feedback['revision'])
        self.receipt(result, status='error', error='PRIVATE_ERROR_PATH')
        after_receipt = self.sample()
        self.assertNotEqual(after_feedback['revision'], after_receipt['revision'])
        self.assertNotIn('PRIVATE_', json.dumps(after_receipt))

    def test_public_summary_withholds_prompt_memory_answer_and_private_paths(self):
        self.task(task=r'Review C:\Users\Private\Secret\file.py and /home/private/project/file.py',
            prompt='PRIVATE_PROMPT', query='PRIVATE_QUERY', response='PRIVATE_RESPONSE',
            memory_context={'context': 'PRIVATE_MEMORY', 'query': 'PRIVATE_QUERY'},
            canonical_result=r'C:\Users\Private\Secret\result.json',
            worker='C:/Users/Private/private-worker')
        value = self.sample()
        encoded = json.dumps(value)
        for forbidden in ('PRIVATE_', 'Private', '/home/private', 'C:\\Users'):
            self.assertNotIn(forbidden, encoded)
        self.assertIn('[path]', value['tasks'][0]['task'])
        self.assertEqual(value['tasks'][0]['worker'], 'unknown')

    def test_cache_is_bounded_in_time_and_local_write_can_invalidate(self):
        result = self.task()
        before = usage.project_summary(self.root, 'alpha')
        before['tasks'].clear()
        self.assertEqual(len(usage.project_summary(self.root, 'alpha')['tasks']), 1)
        result['response'] = 'Changed source'
        self.write(result)
        cached = usage.project_summary(self.root, 'alpha')
        usage.invalidate(self.root, 'alpha')
        fresh = usage.project_summary(self.root, 'alpha')
        self.assertNotEqual(cached['revision'], fresh['revision'])
        with patch.object(usage.time, 'monotonic', return_value=10**12):
            with patch.object(usage, '_sample', wraps=usage._sample) as reader:
                usage.project_summary(self.root, 'alpha')
                reader.assert_called_once()

    def test_folder_and_byte_budgets_are_enforced_and_marked_truncated(self):
        for _ in range(12):
            self.task(response='Oversized source ' * 500)
        with patch.object(usage, 'MAX_FOLDERS', 5):
            value = self.sample()
        self.assertEqual(value['scanned'], 5)
        self.assertTrue(value['truncated'])
        read = usage._bounded_read
        consumed = []
        def observed(path, maximum, root):
            raw, oversized, error = read(path, maximum, root)
            consumed.append(len(raw) + int(oversized) if raw is not None else maximum + 1)
            return raw, oversized, error
        with patch.object(usage, 'MAX_BYTES', 4096), patch.object(usage, 'INDEX_BUDGET', 2048), \
                patch.object(usage, '_bounded_read', side_effect=observed):
            value = self.sample()
        self.assertLessEqual(sum(consumed), 4096)
        self.assertTrue(value['truncated'])

    def test_invalid_receipt_identity_never_supplies_capture_evidence(self):
        result = self.task()
        self.receipt(result, job_id='f' * 32)
        value = self.sample()
        self.assertEqual(value['remembered'], 0)
        self.assertEqual(value['tasks'][0]['capture']['status'], 'invalid')
        self.assertEqual(value['unreadable'], 1)

    def test_explicit_other_user_memory_is_not_in_the_local_project_rollup(self):
        self.task(memory_user_id='other', memory_context={'user_id': 'other'})
        value = self.sample()
        self.assertEqual(value['tasks'], [])
        self.assertEqual(value['unreadable'], 1)

    def test_redirected_task_directory_is_not_read(self):
        result = self.task()
        directory = self.tasks / result['job_id']
        external = self.root / 'outside'
        directory.rename(external)
        try:
            directory.symlink_to(external, target_is_directory=True)
        except OSError:
            if sys.platform != 'win32':
                self.skipTest('Directory symlinks unavailable on this filesystem')
            # Exact fixture paths only; creating a junction is reversible and
            # does not invoke providers or traverse an external user directory.
            command = ['cmd.exe', '/c', 'mklink', '/J', str(directory), str(external)]
            completed = subprocess.run(command, capture_output=True)
            if completed.returncode:
                self.skipTest('Directory links unavailable on this filesystem')
        try:
            value = self.sample()
            self.assertEqual(value['tasks'], [])
            self.assertGreaterEqual(value['unreadable'], 1)
        finally:
            directory.unlink() if directory.is_symlink() else directory.rmdir()


if __name__ == '__main__':
    unittest.main()
