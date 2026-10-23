"""Synthetic regression for invalid native captures and audited zero-write retirement."""
from contextlib import redirect_stdout
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
import hashlib
import io
import json
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'app'))
import brain_cli
from brain_store import BrainStore, digest
from memory_bundle import capture_run, record_accepted_bundle
from task_store import timestamp, write_json
import test_orchestration_lifecycle as lifecycle


class CaptureRecoveryTests(unittest.TestCase):
    setUp = lifecycle.LifecycleTests.setUp
    read = lifecycle.LifecycleTests.read
    manifest = lifecycle.LifecycleTests.manifest
    task = lifecycle.LifecycleTests.task
    audit = lifecycle.LifecycleTests.audit
    close = lifecycle.LifecycleTests.close
    held = lifecycle.LifecycleTests.held

    def bundle(self, capture_id='corrected'):
        return {'capture_id': capture_id, 'project_id': 'alpha', 'evidence': ['review/proof.md'],
                'memories': [{'key': 'lesson', 'kind': 'episode', 'title': 'Checked recovery',
                    'content': 'Validate candidate fields before importing reviewed knowledge.',
                    'episode': {'problem': 'Required fields were omitted.', 'action': 'Added the required fields.',
                                'outcome': 'Reviewed capture succeeded.'}}]}

    def capture(self, bundle=None):
        proof = self.run / 'review/proof.md'
        if not proof.exists():
            proof.write_text('Synthetic immutable proof of the reviewed recovery.', encoding='utf-8')
        return capture_run(self.root, self.run, bundle or self.bundle(), **self.identity,
                           reviewer='Tester', note='Verified the synthetic recovery against the saved evidence.')

    def legacy_failure(self):
        bundle = self.bundle('invalid-original')
        bundle['memories'][0].pop('episode')
        proof_path = self.run / 'review/proof.md'
        proof_path.write_text('Synthetic immutable proof of the reviewed recovery.', encoding='utf-8')
        proof = [{'path': 'review/proof.md', 'sha256': hashlib.sha256(proof_path.read_bytes()).hexdigest()}]
        task = self.store.create(worker='native-review', category='memory-curation', task='Reviewed original closeout',
            assignment_project_id='alpha', run_id=self.run.name, run_directory=str(self.run), imported_completed_artifact=True)
        task.update(status='accepted', review_status='accepted', execution_status='succeeded',
            response=json.dumps({'knowledge': bundle, 'evidence': proof}, ensure_ascii=False),
            finalized_at=timestamp(), ended_at=timestamp(), provider_calls=0, artifact_origin='reviewed-run-closeout',
            review={'reviewer': 'Tester', 'note': 'Verified the original synthetic evidence before import.', 'reviewed_at': timestamp()})
        self.store.save(task['job_id'], task)
        with self.assertRaisesRegex(ValueError, 'problem'):
            record_accepted_bundle(self.store, task['job_id'], bundle)
        journal = {'job_id': task['job_id'], 'run_directory': str(self.run), 'status': 'error',
            'request_sha256': digest({'bundle': bundle, 'proof': proof, 'reviewer': task['review']['reviewer'],
                                      'note': task['review']['note']}),
            'memory_outcome': {'job_id': task['job_id'], 'status': 'error', 'error': 'ValueError', 'retryable': True}}
        write_json(self.run / 'review/memory-capture-invalid-original.json', journal)
        manifest = self.read(self.run / 'run.json')
        manifest['tasks'].append({'job_id': task['job_id'], 'status': 'accepted', 'kind': 'reviewed-closeout'})
        write_json(self.run / 'run.json', manifest)
        return task['job_id']

    def retire(self, failed, replacement, **updates):
        from capture_retirement import retire_capture
        args = dict(self.identity, reviewer='Tester', note='Verified zero writes and the corrected replacement capture.',
                    source_sha256=hashlib.sha256((self.store.directory(failed) / 'result.json').read_bytes()).hexdigest())
        args.update(updates)
        return retire_capture(self.root, self.run, failed, replacement_job=replacement, **args)

    def test_semantic_preflight_leaves_no_import_or_brain_writes(self):
        for change in ({'episode': {}}, {'kind': 'unknown'}, {'title': ''}, {'content': ''},
                       {'tags': [False]}, {'importance': 2}, {'valid_from': 'tomorrow'},
                       {'valid_from': '2026-01-01T00:00:00Z', 'valid_to': '2025-01-01T00:00:00Z'}):
            with self.subTest(change=change):
                bundle = self.bundle()
                bundle['memories'][0].update(change)
                before = {str(p.relative_to(self.root)): p.read_bytes() for p in self.root.rglob('*') if p.is_file()}
                with self.assertRaises(ValueError):
                    self.capture(bundle)
                after = {str(p.relative_to(self.root)): p.read_bytes() for p in self.root.rglob('*') if p.is_file()}
                after.pop(str((self.run / 'review/proof.md').relative_to(self.root)), None)
                before.pop(str((self.run / 'review/proof.md').relative_to(self.root)), None)
                self.assertEqual(before, after)
                self.assertEqual(list(self.store.root.iterdir()), [])

    def test_null_valid_from_uses_original_review_time_for_field_validation(self):
        from memory_bundle import validate_capture_fields
        bundle = self.bundle()
        bundle['memories'][0].update(valid_from=None, valid_to='2026-02-01T00:00:00Z')
        fields = validate_capture_fields(bundle, 'alpha', reviewed_at='2026-01-01T00:00:00+00:00')
        self.assertEqual(fields['lesson']['valid_from'], '2026-01-01T00:00:00.000000+00:00')

    def test_original_failure_replacement_retirement_and_closeout(self):
        failed = self.legacy_failure()
        replacement = self.capture()['job_id']
        self.task(worker='claude', model='claude-opus-4-6')
        self.task(worker='grok', model='grok-4.6-build')
        self.audit()
        self.held(self.close(), 'task:' + failed)
        paths = [self.store.directory(failed) / 'result.json', self.store.directory(failed) / 'record.json',
                 self.store.directory(failed) / 'memory-outcome.json', self.run / 'review/memory-capture-invalid-original.json']
        original = [p.read_bytes() for p in paths]
        result = self.retire(failed, replacement)
        self.assertEqual(result['status'], 'retired')
        self.assertEqual(result['replacement_job_id'], replacement)
        self.assertEqual([p.read_bytes() for p in paths], original)
        self.assertEqual(self.retire(failed, replacement), result)
        closed = self.close()
        self.assertEqual(closed['status'], 'completed', closed)
        detail = next(c['detail'] for c in closed['checks'] if c['check'] == 'task:' + failed)
        self.assertEqual(detail['decision'], 'retired')
        native = next(c['detail'] for c in closed['checks'] if c['check'] == 'native_memory_capture')
        self.assertEqual(native['capture_jobs'], [replacement])

    def test_unsafe_receipts_and_owner_are_preserved(self):
        failed = self.legacy_failure()
        replacement = self.capture()['job_id']
        receipt_path = self.store.directory(failed) / 'memory-outcome.json'
        saved = self.read(receipt_path)
        for change in ({'status': 'writing'}, {'status': 'remembered'}, {'retryable': False},
                       {'memory_ids': ['f' * 32]}, {'memory_id': 'f' * 32}, {'source_sha256': 'f' * 64},
                       {'request_sha256': 'f' * 64}, {'project_id': 'foreign'}, {'status': 'unknown'}):
            with self.subTest(change=change):
                write_json(receipt_path, dict(saved, **change))
                before = receipt_path.read_bytes()
                with self.assertRaises(ValueError):
                    self.retire(failed, replacement)
                self.assertEqual(before, receipt_path.read_bytes())
        write_json(receipt_path, saved)
        for change in ({'owner': 'sol'}, {'session': 'foreign'}, {'generation': 1000}, {'source_sha256': 'f' * 64}):
            with self.subTest(change=change), self.assertRaises(ValueError):
                self.retire(failed, replacement, **change)

    def test_changed_evidence_and_forgotten_replacement_hold_recovery(self):
        failed = self.legacy_failure()
        replacement = self.capture()['job_id']
        path = self.run / 'review/proof.md'
        proof = path.read_bytes()
        path.write_text('Changed proof', encoding='utf-8')
        with self.assertRaisesRegex(ValueError, 'evidence'):
            self.retire(failed, replacement)
        path.write_bytes(proof)
        receipt = self.read(self.store.directory(replacement) / 'memory-outcome.json')
        BrainStore(self.root).forget(receipt['memory_id'], 'Tester', 'Forget this synthetic replacement memory.')
        with self.assertRaises(ValueError):
            self.retire(failed, replacement)

    def test_cli_semantic_rejection_does_not_create_application_storage(self):
        app_root = self.root / 'clean-application'
        app_root.mkdir()
        bundle = self.bundle()
        bundle['memories'][0].pop('episode')
        (self.run / 'review/proof.md').write_text('Immutable evidence', encoding='utf-8')
        bundle_path = self.run / 'review/invalid.json'
        write_json(bundle_path, bundle)
        args = ['--root', str(app_root), 'capture', '--run', str(self.run), '--file', str(bundle_path),
                '--reviewer', 'Tester', '--note', 'Verified the bounded synthetic evidence file.']
        for key, value in self.identity.items():
            args.extend(['--' + key, str(value)])
        output = io.StringIO()
        with redirect_stdout(output):
            self.assertEqual(brain_cli.main(args), 2)
        self.assertIn('problem', json.loads(output.getvalue())['error'])
        self.assertEqual(list(app_root.iterdir()), [])

    def test_external_relationship_prevalidation_does_not_import(self):
        bundle = self.bundle()
        bundle['relations'] = [{'from': 'lesson', 'to': 'f' * 32, 'relation': 'supports',
                                'reason': 'A nonexistent memory cannot support this lesson.'}]
        with self.assertRaisesRegex(ValueError, 'Memory not found'):
            self.capture(bundle)
        self.assertEqual(list(self.store.root.iterdir()), [])

    def test_retired_capture_cannot_retry_or_receive_contribution_credit(self):
        failed = self.legacy_failure()
        replacement = self.capture()['job_id']
        self.retire(failed, replacement)
        bundle = json.loads(self.read(self.store.directory(failed) / 'result.json')['response'])['knowledge']
        path = self.store.directory(failed) / 'memory-outcome.json'
        before = path.read_bytes()
        with self.assertRaisesRegex(ValueError, 'retired'):
            record_accepted_bundle(self.store, failed, bundle)
        self.assertEqual(path.read_bytes(), before)
        self.task(worker='claude', model='claude-opus-4-6')
        self.task(worker='grok', model='grok-4.6-build')
        self.audit()
        from contributions import write_report
        ledger = self.read(self.run / 'contributions-ledger.json')
        work = deepcopy(ledger['work_items'][0])
        work.update(id=failed, label='Invalid import is not retained work')
        ledger['work_items'].append(work)
        write_json(self.run / 'contributions-ledger.json', ledger)
        write_report(self.run, ledger)
        self.held(self.close(), 'contribution_audit')

    def test_partial_writes_and_cross_run_replacement_are_rejected(self):
        failed = self.legacy_failure()
        replacement = self.capture()['job_id']
        path = self.store.directory(replacement) / 'result.json'
        original = self.read(path)
        for changes in ({'run_id': 'foreign'}, {'run_directory': str(self.root)}, {'assignment_project_id': 'other'},
                        {'imported_completed_artifact': False}, {'provider_calls': 1}):
            with self.subTest(changes=changes):
                write_json(path, dict(original, **changes))
                with self.assertRaises(ValueError):
                    self.retire(failed, replacement)
        write_json(path, original)
        brain = BrainStore(self.root)
        brain.propose({'project_id': 'alpha', 'kind': 'fact', 'title': 'Unexpected partial write',
                       'content': 'This synthetic source must prevent retirement.',
                       'source': {'type': 'task', 'job_id': failed}})
        with self.assertRaisesRegex(ValueError, 'Brain writes'):
            self.retire(failed, replacement)

    def test_atomic_retirement_and_concurrent_duplicate_requests(self):
        failed = self.legacy_failure()
        replacement = self.capture()['job_id']
        path = self.store.directory(failed) / 'capture-retirement.json'
        with patch('capture_retirement.write_json', side_effect=OSError('Synthetic interrupted disposition')):
            with self.assertRaises(OSError):
                self.retire(failed, replacement)
        self.assertFalse(path.exists())
        with ThreadPoolExecutor(max_workers=2) as pool:
            first, second = list(pool.map(lambda _: self.retire(failed, replacement), range(2)))
        self.assertEqual(first, second)
        self.assertEqual(self.read(path), first)

    def test_retirement_cli_and_tampered_disposition_hold(self):
        failed = self.legacy_failure()
        replacement = self.capture()['job_id']
        args = ['--root', str(self.root), 'retire-capture', failed, '--run', str(self.run),
                '--replacement-job', replacement, '--source-sha256',
                hashlib.sha256((self.store.directory(failed) / 'result.json').read_bytes()).hexdigest(),
                '--reviewer', 'Tester', '--note', 'Verified zero writes and the corrected replacement capture.']
        for key, value in self.identity.items():
            args.extend(['--' + key, str(value)])
        with redirect_stdout(io.StringIO()) as output:
            self.assertEqual(brain_cli.main(args), 0)
        self.assertEqual(json.loads(output.getvalue())['status'], 'retired')
        path = self.store.directory(failed) / 'capture-retirement.json'
        disposition = self.read(path)
        write_json(path, dict(disposition, failure_receipt_sha256='f' * 64))
        self.held(self.close(), 'task:' + failed)

    def test_resuming_unaccepted_invalid_import_leaves_original_bytes(self):
        failed = self.legacy_failure()
        directory = self.store.directory(failed)
        original = self.read(directory / 'result.json')
        bundle = json.loads(original['response'])['knowledge']
        journal_path = self.run / 'review/memory-capture-invalid-original.json'
        journal = self.read(journal_path)
        journal.update(status='importing')
        journal.pop('memory_outcome')
        write_json(journal_path, journal)
        (directory / 'memory-outcome.json').unlink()
        for has_result in (True, False):
            with self.subTest(has_result=has_result):
                pending = dict(original, status='awaiting_review', review_status='pending')
                pending.pop('review')
                self.store.save(failed, pending)
                if not has_result:
                    (directory / 'result.json').unlink()
                    record = self.read(directory / 'record.json')
                    record.update(status='preparing', finalized_at=None)
                    write_json(directory / 'record.json', record)
                before = {str(p): p.read_bytes() for p in directory.iterdir() if p.is_file()}
                with self.assertRaisesRegex(ValueError, 'problem'):
                    capture_run(self.root, self.run, bundle, **self.identity,
                                reviewer=original['review']['reviewer'], note=original['review']['note'])
                self.assertEqual(before, {str(p): p.read_bytes() for p in directory.iterdir() if p.is_file()})
                self.assertEqual(self.read(journal_path), journal)

    def test_forgotten_replacement_after_retirement_holds_closeout(self):
        failed = self.legacy_failure()
        replacement = self.capture()['job_id']
        self.retire(failed, replacement)
        memory = self.read(self.store.directory(replacement) / 'memory-outcome.json')['memory_id']
        BrainStore(self.root).forget(memory, 'Tester', 'Forget synthetic replacement after retirement.')
        self.held(self.close(), 'task:' + failed)

    def test_dynamic_relation_failure_is_not_retirable(self):
        failed = self.legacy_failure()
        replacement = self.capture()['job_id']
        directory = self.store.directory(failed)
        result = self.read(directory / 'result.json')
        payload = json.loads(result['response'])
        payload['knowledge']['memories'][0]['episode'] = self.bundle()['memories'][0]['episode']
        payload['knowledge']['memories'][0]['valid_from'] = '2999-01-01T00:00:00Z'
        payload['knowledge']['relations'] = [{'from': 'lesson', 'to': 'f' * 32, 'relation': 'supports',
                                              'reason': 'This failure depends on relation currency.'}]
        result['response'] = json.dumps(payload, ensure_ascii=False)
        self.store.save(failed, result)
        receipt = self.read(directory / 'memory-outcome.json')
        receipt['source_sha256'] = digest({k: result.get(k) for k in ('job_id', 'assignment_project_id', 'response', 'review', 'finalized_at')})
        receipt['request_sha256'] = digest(payload['knowledge'])
        write_json(directory / 'memory-outcome.json', receipt)
        journal_path = self.run / 'review/memory-capture-invalid-original.json'
        journal = self.read(journal_path)
        journal['request_sha256'] = digest({'bundle': payload['knowledge'], 'proof': payload['evidence'],
                                           'reviewer': result['review']['reviewer'], 'note': result['review']['note']})
        write_json(journal_path, journal)
        with self.assertRaisesRegex(ValueError, 'invalid candidate fields'):
            self.retire(failed, replacement)


if __name__ == '__main__':
    unittest.main()
