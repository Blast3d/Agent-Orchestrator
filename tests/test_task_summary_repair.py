"""Explicit summary repair keeps the canonical result and review identity."""
import hashlib
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'app'))
from task_summary_repair import (
    inspect_summary,
    main,
    repair_summary,
    summary_notices,
    SummaryRepairError,
)
import task_summary_repair as repair_module
from task_store import TaskStore


class TaskSummaryRepairTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name) / 'tasks'
        self.root.mkdir()
        self.job = 'a' * 32
        self.folder = self.root / self.job
        self.folder.mkdir()

    def _result(self, **changes):
        data = {
            'job_id': self.job,
            'schema_version': 1,
            'task': 'Review a project',
            'worker': 'claude',
            'status': 'accepted',
            'execution_status': 'succeeded',
            'review_status': 'accepted',
            'created_at': '2026-09-07T10:00:00+00:00',
            'started_at': '2026-09-07T10:01:00+00:00',
            'finalized_at': '2026-09-07T10:02:00+00:00',
            'canonical_result': str(self.folder / 'result.json'),
            'response': 'A readable canonical answer that must not enter the index.',
            'provider_result': {'secret': 'PROVIDER_SECRET'},
            'quota_before': {'secret': 'QUOTA_SECRET'},
            'quota_refresh': {'secret': 'REFRESH_SECRET'},
            'contribution_ledger': {'tokens': 99},
            'review': {
                'reviewer': 'Codex',
                'note': 'Checked the supporting evidence against the brief.',
                'reviewed_at': '2026-09-07T10:03:00+00:00',
            },
        }
        data.update(changes)
        return data

    def _write(self, result=None, record=None, omit_record=False):
        result = self._result() if result is None else result
        raw = (json.dumps(result, indent=2) + '\n').encode('utf-8')
        (self.folder / 'result.json').write_bytes(raw)
        if not omit_record:
            if record is None:
                record = {k: v for k, v in result.items()
                          if k not in ('response', 'provider_result', 'quota_before',
                                       'quota_refresh', 'contribution_ledger')}
            (self.folder / 'record.json').write_text(json.dumps(record, indent=2) + '\n', encoding='utf-8')
        return raw

    def test_summary_notices_missing_index_and_stale_review_without_payloads(self):
        result = self._result()
        self.assertTrue(any('missing' in n.lower() for n in summary_notices(None, result)))
        stale = dict(result, status='awaiting_review', review_status='pending', review=None)
        text = ' '.join(summary_notices(stale, result)).lower()
        self.assertIn('disagrees', text)
        self.assertIn('review', text)
        self.assertNotIn('provider_secret', text)
        self.assertNotIn('quota_secret', text)
        self.assertNotIn('readable canonical answer', text)
        error_result = dict(result, contribution_audit={'status': 'error', 'error': 'OSError'})
        self.assertTrue(any('audit' in n.lower() for n in summary_notices(None, error_result)))
        before = json.dumps(result)
        summary_notices(stale, result)
        self.assertEqual(json.dumps(result), before)

    def test_repair_stale_index_keeps_canonical_bytes_and_accepted_review(self):
        raw = self._write()
        stale = json.loads((self.folder / 'record.json').read_text(encoding='utf-8'))
        stale.update(status='awaiting_review', review_status='pending', review=None)
        (self.folder / 'record.json').write_text(json.dumps(stale) + '\n', encoding='utf-8')
        plan = inspect_summary(self.job, tasks_root=self.root)
        self.assertEqual(plan['action'], 'repair_index')
        self.assertEqual(plan['expected_result_sha256'], hashlib.sha256(raw).hexdigest())
        outcome = repair_summary(
            self.job,
            expected_result_sha256=plan['expected_result_sha256'],
            reviewer='Codex',
            note='Rebuild the stale index from the accepted canonical result.',
            tasks_root=self.root,
        )
        self.assertTrue(outcome['applied'])
        self.assertEqual((self.folder / 'result.json').read_bytes(), raw)
        index = json.loads((self.folder / 'record.json').read_text(encoding='utf-8'))
        self.assertEqual(index['status'], 'accepted')
        self.assertEqual(index['review_status'], 'accepted')
        self.assertEqual(index['review']['reviewer'], 'Codex')
        self.assertNotIn('response', index)
        self.assertNotIn('provider_result', index)
        self.assertNotIn('quota_before', index)
        self.assertNotIn('quota_refresh', index)
        self.assertNotIn('contribution_ledger', index)
        audits = list((self.folder / 'summary-repair-audit').glob('*.json'))
        self.assertGreaterEqual(len(audits), 2)

    def test_missing_index_is_rebuilt(self):
        raw = self._write(omit_record=True)
        plan = inspect_summary(self.job, tasks_root=self.root)
        self.assertEqual(plan['index_status'], 'missing')
        repair_summary(
            self.job,
            expected_result_sha256=plan['expected_result_sha256'],
            reviewer='Codex',
            note='Recreate the missing small index from the saved answer.',
            tasks_root=self.root,
        )
        self.assertTrue((self.folder / 'record.json').is_file())
        self.assertEqual((self.folder / 'result.json').read_bytes(), raw)

    def test_matching_index_is_explicit_no_op(self):
        self._write()
        plan = inspect_summary(self.job, tasks_root=self.root)
        self.assertEqual(plan['action'], 'no_op')
        before = (self.folder / 'record.json').read_bytes()
        outcome = repair_summary(
            self.job,
            expected_result_sha256=plan['expected_result_sha256'],
            reviewer='Codex',
            note='Confirm the matching index needs no replacement at all.',
            tasks_root=self.root,
        )
        self.assertEqual(outcome['status'], 'no_op')
        self.assertFalse(outcome['applied'])
        self.assertEqual((self.folder / 'record.json').read_bytes(), before)

    def test_stale_expected_hash_is_refused(self):
        self._write()
        with self.assertRaises(SummaryRepairError):
            repair_summary(
                self.job,
                expected_result_sha256='0' * 64,
                reviewer='Codex',
                note='Attempt repair with a stale inspect digest on purpose.',
                tasks_root=self.root,
            )

    def test_malformed_and_conflicting_identity_refused(self):
        self._write()
        (self.folder / 'record.json').write_text('not json', encoding='utf-8')
        with self.assertRaises(SummaryRepairError):
            inspect_summary(self.job, tasks_root=self.root)
        other = self._result(job_id='b' * 32)
        (self.folder / 'result.json').write_text(json.dumps(other), encoding='utf-8')
        with self.assertRaises(SummaryRepairError):
            inspect_summary(self.job, tasks_root=self.root)

    def test_invalid_job_id_refused(self):
        with self.assertRaises(SummaryRepairError):
            inspect_summary('ZZ', tasks_root=self.root)

    def test_symlink_result_refused(self):
        raw = self._write()
        target = self.folder / 'real-result.json'
        target.write_bytes(raw)
        result = self.folder / 'result.json'
        result.unlink()
        try:
            result.symlink_to(target)
        except OSError as exc:
            self.skipTest('Link creation unavailable: ' + type(exc).__name__)
        with self.assertRaises(SummaryRepairError):
            inspect_summary(self.job, tasks_root=self.root)

    def test_path_escape_job_rejected_by_id_rules(self):
        with self.assertRaises(SummaryRepairError):
            inspect_summary('../' + 'c' * 29, tasks_root=self.root)

    def test_cli_inspect_then_repair(self):
        self._write()
        stale = json.loads((self.folder / 'record.json').read_text(encoding='utf-8'))
        stale['status'] = 'awaiting_review'
        (self.folder / 'record.json').write_text(json.dumps(stale), encoding='utf-8')
        from io import StringIO
        buf = StringIO()
        with mock.patch('sys.stdout', buf):
            code = main(['inspect', self.job, '--tasks-root', str(self.root)])
        self.assertEqual(code, 0)
        plan = json.loads(buf.getvalue())
        buf2 = StringIO()
        with mock.patch('sys.stdout', buf2):
            code = main([
                'repair', self.job,
                '--expected-result-sha256', plan['expected_result_sha256'],
                '--reviewer', 'Codex',
                '--note', 'Apply the inspected projection to the stale index file.',
                '--tasks-root', str(self.root),
            ])
        self.assertEqual(code, 0)
        self.assertTrue(json.loads(buf2.getvalue())['applied'])

    def test_incoherent_accepted_without_review_refused(self):
        self._write(self._result(status='accepted', review_status='accepted', review=None))
        with self.assertRaises(SummaryRepairError):
            inspect_summary(self.job, tasks_root=self.root)

    def _repair(self):
        digest = hashlib.sha256((self.folder / 'result.json').read_bytes()).hexdigest()
        return repair_summary(self.job, expected_result_sha256=digest, reviewer='Codex',
                              note='Verified the canonical task before replacing its summary.', tasks_root=self.root)

    def test_actual_task_store_projection_is_a_noop_and_repair_matches_it(self):
        store = TaskStore(self.root)
        result = self._result()
        store.save(self.job, result)
        expected = (self.folder / 'record.json').read_bytes()
        canonical = (self.folder / 'result.json').read_bytes()
        self.assertEqual(inspect_summary(self.job, tasks_root=self.root)['action'], 'no_op')
        stale = json.loads(expected)
        stale['task'] = 'Stale title'
        (self.folder / 'record.json').write_text(json.dumps(stale), encoding='utf-8')
        self.assertEqual(inspect_summary(self.job, tasks_root=self.root)['action'], 'repair_index')
        outcome = self._repair()
        self.assertEqual((self.folder / 'record.json').read_bytes(), expected)
        self.assertEqual(outcome['index_sha256'], hashlib.sha256(expected).hexdigest())
        self.assertEqual((self.folder / 'result.json').read_bytes(), canonical)

    def test_semantically_matching_index_with_other_whitespace_is_noop(self):
        self._write()
        path = self.folder / 'record.json'
        path.write_bytes(json.dumps(json.loads(path.read_bytes()), separators=(',', ':')).encode())
        before = path.read_bytes()
        self.assertEqual(self._repair()['status'], 'no_op')
        self.assertEqual(path.read_bytes(), before)
        self.assertFalse((self.folder / 'summary-repair-audit').exists())

    def test_stale_metadata_and_audit_error_are_pure_notices(self):
        result = self._result(contribution_audit={'status': 'error', 'error': 'PRIVATE_ERROR'})
        record = repair_module._project(result)
        record['task'] = 'Old task title'
        before = json.dumps([record, result])
        notices = summary_notices(record, result)
        self.assertTrue(any('stale summary fields' in note for note in notices))
        self.assertTrue(any('audit' in note for note in notices))
        self.assertNotIn('PRIVATE_ERROR', str(notices))
        self.assertEqual(json.dumps([record, result]), before)

    def test_duplicate_json_and_nonfinite_values_are_refused(self):
        self._write()
        original = (self.folder / 'result.json').read_bytes()
        for raw in (original.replace(b'{', b'{"job_id":"wrong",', 1),
                    original.replace(b'{', b'{"extra":NaN,', 1),
                    original.replace(b'{', b'{"extra":1e999,', 1)):
            with self.subTest(raw=raw[:30]):
                (self.folder / 'result.json').write_bytes(raw)
                with self.assertRaises(SummaryRepairError):
                    inspect_summary(self.job, tasks_root=self.root)

    def test_unfinished_or_conflicting_lifecycle_is_refused(self):
        for changes in ({'status': 'running', 'execution_status': 'running', 'review_status': 'pending',
                         'review': None, 'finalized_at': None},
                        {'finalized_at': 'not a date'},
                        {'status': 'held', 'execution_status': 'succeeded'},
                        {'status': 'awaiting_review', 'review_status': 'accepted'},
                        {'status': []}):
            with self.subTest(changes=changes):
                self._write(self._result(**changes))
                with self.assertRaises(SummaryRepairError):
                    self._repair()

    def test_conflicting_canonical_location_or_assignment_identity_refused(self):
        self._write(self._result(canonical_result=str(self.root / 'other.json')))
        with self.assertRaises(SummaryRepairError):
            self._repair()
        self._write()
        record = json.loads((self.folder / 'record.json').read_bytes())
        record['assignment_id'] = 'another-assignment'
        (self.folder / 'record.json').write_text(json.dumps(record), encoding='utf-8')
        with self.assertRaises(SummaryRepairError):
            self._repair()

    def test_changed_index_between_intent_and_replacement_is_preserved(self):
        self._write(omit_record=True)
        write_audit = repair_module._write_audit
        replacement = json.dumps(self._result(worker='grok')).encode()
        def intervene(folder, payload):
            name = write_audit(folder, payload)
            if payload['phase'] == 'intent':
                (self.folder / 'record.json').write_bytes(replacement)
            return name
        with mock.patch.object(repair_module, '_write_audit', side_effect=intervene):
            with self.assertRaisesRegex(SummaryRepairError, 'index changed'):
                self._repair()
        self.assertEqual((self.folder / 'record.json').read_bytes(), replacement)

    def test_changed_canonical_after_intent_does_not_replace_index(self):
        self._write(omit_record=True)
        write_audit = repair_module._write_audit
        def intervene(folder, payload):
            name = write_audit(folder, payload)
            (self.folder / 'result.json').write_text(json.dumps(self._result(task='Changed')), encoding='utf-8')
            return name
        with mock.patch.object(repair_module, '_write_audit', side_effect=intervene):
            with self.assertRaisesRegex(SummaryRepairError, 'Canonical result changed'):
                self._repair()
        self.assertFalse((self.folder / 'record.json').exists())

    def test_intent_failure_prevents_index_creation(self):
        canonical = self._write(omit_record=True)
        with mock.patch.object(repair_module, '_write_audit', side_effect=OSError('disk full')):
            with self.assertRaisesRegex(SummaryRepairError, 'intent evidence'):
                self._repair()
        self.assertFalse((self.folder / 'record.json').exists())
        self.assertEqual((self.folder / 'result.json').read_bytes(), canonical)

    def test_success_evidence_failure_is_visible_on_later_readonly_inspect(self):
        self._write(omit_record=True)
        write_audit = repair_module._write_audit
        def fail_success(folder, payload):
            if payload['phase'] == 'applied':
                raise OSError('disk full')
            return write_audit(folder, payload)
        with mock.patch.object(repair_module, '_write_audit', side_effect=fail_success):
            with self.assertRaisesRegex(SummaryRepairError, 'Index was replaced'):
                self._repair()
        before = {str(p.relative_to(self.folder)): p.read_bytes() for p in self.folder.rglob('*') if p.is_file()}
        plan = inspect_summary(self.job, tasks_root=self.root)
        self.assertTrue(plan['index_matches_projection'])
        self.assertTrue(plan['needs_review'])
        self.assertIsNone(plan['repair_applied'])
        self.assertTrue(plan['repair_history']['unresolved'])
        after = {str(p.relative_to(self.folder)): p.read_bytes() for p in self.folder.rglob('*') if p.is_file()}
        self.assertEqual(before, after)
        with self.assertRaisesRegex(SummaryRepairError, 'Prior repair evidence'):
            self._repair()

    def test_failed_index_write_retains_intent_and_needs_review(self):
        self._write(omit_record=True)
        write_json = repair_module.write_json
        def fail_index(path, payload):
            if path.name == 'record.json':
                raise OSError('index unavailable')
            return write_json(path, payload)
        with mock.patch.object(repair_module, 'write_json', side_effect=fail_index):
            with self.assertRaisesRegex(SummaryRepairError, 'Index replacement failed'):
                self._repair()
        self.assertTrue(inspect_summary(self.job, tasks_root=self.root)['needs_review'])
        self.assertFalse((self.folder / 'record.json').exists())

    def test_simultaneous_repairs_have_one_replacement(self):
        from concurrent.futures import ThreadPoolExecutor
        canonical = self._write(omit_record=True)
        with ThreadPoolExecutor(max_workers=2) as pool:
            outcomes = list(pool.map(lambda _: self._repair(), range(2)))
        self.assertEqual(sorted(item['status'] for item in outcomes), ['no_op', 'repaired'])
        self.assertEqual((self.folder / 'result.json').read_bytes(), canonical)

    def test_reparse_attribute_is_rejected_on_windows_without_link_privileges(self):
        self._write()
        real_lstat = Path.lstat
        def reparse_lstat(path):
            actual = real_lstat(path)
            if path == self.folder / 'result.json':
                return mock.Mock(st_file_attributes=0x400, st_mode=actual.st_mode)
            return actual
        with mock.patch.object(Path, 'lstat', reparse_lstat):
            with self.assertRaisesRegex(SummaryRepairError, 'reparse'):
                inspect_summary(self.job, tasks_root=self.root)

    def test_directories_and_oversized_evidence_are_refused(self):
        self._write(omit_record=True)
        path = self.folder / 'record.json'
        path.mkdir()
        with self.assertRaises(SummaryRepairError):
            self._repair()
        path.rmdir()
        path.write_bytes(b' ' * (repair_module._INDEX_LIMIT + 1))
        with self.assertRaisesRegex(SummaryRepairError, 'bounded read'):
            self._repair()

    def test_oversized_projection_is_refused_before_writing(self):
        self._write(self._result(extra='x' * repair_module._INDEX_LIMIT), omit_record=True)
        with self.assertRaisesRegex(SummaryRepairError, 'Projected task index'):
            self._repair()
        self.assertFalse((self.folder / 'record.json').exists())


if __name__ == '__main__':
    unittest.main()
