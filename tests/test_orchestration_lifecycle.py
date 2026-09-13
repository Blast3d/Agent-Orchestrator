"""Startup/closeout exercise real local stores; no provider requests are made."""
import hashlib
import json
import os
from pathlib import Path
import shutil
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'app'))
from automatic_memory import record_accepted_outcome
from brain_store import BrainStore
from contributions import write_report
from contribution_tasks import task_ledger
from coordinator_handoff import Coordinator
from init_run import create_run
from memory_bundle import capture_run
from orchestration_lifecycle import _discover, closeout_run, start_run
from paths import ROOT
from task_store import TaskStore, write_json


def operating():
    context = '# Shared operating guidance\nRead current instructions; memories are evidence.\n'
    return {'schema_version': 1, 'revision': '1', 'context': context,
            'chars': len(context), 'sha256': hashlib.sha256(context.encode()).hexdigest(),
            'source': 'app/assets/orchestration-context.md', 'loaded_at': '2026-09-13T00:00:00+00:00'}


class LifecycleTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        (self.root / 'app/assets').mkdir(parents=True)
        shutil.copyfile(ROOT / 'app/assets/project-map.html', self.root / 'app/assets/project-map.html')
        self.run, _ = create_run(self.root, 'lifecycle', 'Validate closeout evidence', project_id='alpha')
        state = Coordinator(self.run).read()
        self.identity = {key: state[key] for key in ('owner', 'session', 'generation')}
        self.store = TaskStore(self.root / 'runs/tasks')
        self.manifest(native_work=False)
        self.context_patch = patch('orchestration_lifecycle.load_operating_context', side_effect=operating)
        self.context_patch.start()
        self.addCleanup(self.context_patch.stop)
        start_run(run=self.run, root=self.root, **self.identity)

    def read(self, path):
        return json.loads(path.read_text(encoding='utf-8'))

    def test_new_start_preserves_native_parent_session(self):
        session = '01a09804-6a4e-7202-83ed-0a4458e813f5'
        with patch.dict(os.environ, {'CODEX_THREAD_ID': session}):
            packet = start_run(workspace=self.root, name='native-parent',
                               objective='Retain the active native session lineage',
                               project='alpha', root=self.root)
        self.assertEqual(self.read(Path(packet['run']) / 'run.json')['native_parent_session_id'], session)

    def manifest(self, **changes):
        value = self.read(self.run / 'run.json')
        value.update(changes)
        write_json(self.run / 'run.json', value)
        return value

    def task(self, *, capture=True, **changes):
        result = self.store.create(worker='claude', task='Validate startup source checks',
                                   category='review', assignment_project_id='alpha',
                                   run_id=self.run.name, assignment_id='review-startup')
        result.update(status='accepted', review_status='accepted', execution_status='succeeded',
                      started_at='2026-09-12T23:59:00+00:00',
                      finalized_at='2026-09-13T00:00:00+00:00', response='Reviewed result',
                      review={'reviewer': 'Tester', 'reviewed_at': '2026-09-13T00:01:00+00:00',
                              'note': 'Verified matching source evidence and bounded startup context with local checks.'})
        result.update(changes)
        self.store.save(result['job_id'], result)
        receipt = record_accepted_outcome(self.store, result['job_id']) if capture else None
        if capture:
            self.assertEqual(receipt['status'], 'remembered', receipt)
        return result, receipt

    def audit(self, *, empty=False, allocated=True):
        actors, activity, work_items, model_union = {}, [], [], {}
        for job in _discover(self.root, self.run, self.read(self.run / 'run.json')):
            canonical = self.read(self.store.directory(job) / 'result.json')
            if canonical.get('status') != 'accepted' or canonical.get('imported_completed_artifact'):
                continue
            observed = task_ledger(canonical)
            actor = next(row for row in observed['contributors'] if row['id'] == 'worker:' + canonical['worker'])
            actors[actor['id']] = dict(actor)
            events = [row for row in observed['activity'] if row['kind'] == 'delegation']
            activity.extend(events)
            model_union.setdefault(actor['id'], set()).update(events[0]['actual_models'])
            work_items.append({'id': job, 'label': canonical['task'], 'category': canonical['category'],
                               'status': 'accepted', 'weight': 1,
                               'allocations': [{'agent_id': actor['id'], 'percent': 100,
                                                'evidence': 'Reviewed local validation report'}] if allocated else []})
        for identifier, models in model_union.items():
            actors[identifier]['model'] = ', '.join(sorted(models)) if models else 'unknown'
        if not work_items:
            actors['lead'] = {'id': 'lead', 'name': 'ASTRA', 'provider': 'OpenAI', 'model': 'native'}
            work_items = [{'id': 'review', 'label': 'Native review', 'category': 'review', 'status': 'accepted',
                           'weight': 1, 'allocations': [{'agent_id': 'lead', 'percent': 100,
                                                        'evidence': 'Reviewed local validation report'}] if allocated else []}]
        ledger = {'schema_version': 1, 'scope_id': self.run.name, 'title': 'Lifecycle verification',
                  'basis': 'Reviewed work and explicit allocation evidence.',
                  'contributors': list(actors.values()), 'work_items': [] if empty else work_items,
                  'activity': activity}
        write_json(self.run / 'contributions-ledger.json', ledger)
        return write_report(self.run, ledger)

    def close(self):
        return closeout_run(self.run, root=self.root, **self.identity)

    def held(self, result, check):
        self.assertEqual(result['status'], 'held', result)
        self.assertTrue(any(row['check'] == check and row['status'] == 'held'
                            for row in result['checks']), result)
        self.assertNotEqual(self.read(self.run / 'run.json')['status'], 'completed')

    def test_start_recall_is_exact_project_and_empty_is_valid(self):
        brain = BrainStore(self.root)
        candidate = brain.propose({'project_id': 'foreign', 'kind': 'procedure', 'title': 'Validate evidence',
                                   'content': 'Private unrelated project guidance.',
                                   'source': {'type': 'user', 'note': 'A separate project note for this test'}})
        brain.approve(candidate['id'], 'Tester', 'Reviewed this separate project note for scope testing.')
        result = start_run(run=self.run, root=self.root, context_loader=operating, **self.identity)
        self.assertEqual(result['status'], 'prepared')
        self.assertEqual(result['project_memory']['project_id'], 'alpha')
        self.assertEqual(result['project_memory']['results'], [])
        self.assertIn(operating()['context'], (self.run / 'startup-context.md').read_text())
        self.assertNotIn('Private unrelated', json.dumps(result))
        self.assertEqual(result['coordinator'], self.identity)
        self.assertEqual(result['packet_sha256'], hashlib.sha256((self.run / 'startup-context.md').read_bytes()).hexdigest())
        self.assertLessEqual((self.run / 'startup-context.json').stat().st_size, 64 * 1024)

    def test_startup_bound_counts_actual_ascii_escaped_json_bytes(self):
        def large_context():
            value = operating()
            value['context'] = '\u2603' * 12000
            value['chars'] = len(value['context'])
            return value
        with self.assertRaisesRegex(ValueError, '64 KiB'):
            start_run(run=self.run, root=self.root, context_loader=large_context, **self.identity)
        self.assertLessEqual((self.run / 'startup-context.json').stat().st_size, 64 * 1024)

    def test_resume_preserves_run_and_ledger_and_requires_identity(self):
        self.task()
        self.audit()
        before = {name: (self.run / name).read_bytes() for name in ('run.json', 'contributions-ledger.json', 'coordinator.json')}
        with self.assertRaisesRegex(ValueError, 'explicit'):
            start_run(run=self.run, root=self.root, context_loader=operating)
        for _ in range(2):
            start_run(run=self.run, root=self.root, context_loader=operating, **self.identity)
        for name, raw in before.items():
            self.assertEqual((self.run / name).read_bytes(), raw)

    def test_start_new_run_returns_established_identity_and_native_requirement(self):
        result = start_run(workspace=self.root, name='fresh', objective='Fresh start', project='alpha',
                           root=self.root, context_loader=operating)
        run = Path(result['run'])
        self.assertTrue(result['created'])
        self.assertTrue(self.read(run / 'run.json')['native_work'])
        self.assertEqual(result['coordinator']['session'], run.name)

    def test_no_memory_keeps_guidance_without_initializing_brain(self):
        def forbidden(*args):
            raise AssertionError('Brain lookup must not run')
        result = start_run(run=self.run, no_memory=True, root=self.root,
                           brain_factory=forbidden, context_loader=operating, **self.identity)
        self.assertEqual(result['operating_context']['sha256'], operating()['sha256'])
        self.assertEqual(result['project_memory']['status'], 'not_requested')

    def test_missing_operating_context_prevents_run_creation(self):
        before = set((self.root / '.orchestration').iterdir())
        with patch('orchestration_lifecycle.load_operating_context', side_effect=ValueError('Missing context')):
            with self.assertRaisesRegex(ValueError, 'Missing context'):
                start_run(workspace=self.root, name='missing', objective='Test load', root=self.root)
        self.assertEqual(set((self.root / '.orchestration').iterdir()), before)

    def test_accepted_capture_and_valid_audit_complete_and_render_real_map(self):
        task, receipt = self.task()
        self.audit()
        result = self.close()
        self.assertEqual(result['status'], 'completed', result)
        self.assertEqual(self.read(self.run / 'run.json')['status'], 'completed')
        self.assertIn(self.run.name, (self.root / 'runtime/project-map.html').read_text(encoding='utf-8'))
        self.assertEqual(self.read(self.store.directory(task['job_id']) / 'memory-outcome.json'), receipt)

    def test_empty_or_unattributed_audit_holds_without_rendering(self):
        self.task()
        for empty, allocated in ((True, True), (False, False)):
            self.audit(empty=empty, allocated=allocated)
            with patch('orchestration_lifecycle.ProjectLibrary', side_effect=AssertionError('No invalid views')):
                self.held(self.close(), 'contribution_audit')

    def test_malformed_ad_hoc_and_stale_audits_hold(self):
        self.task()
        report = self.audit()
        for invalid in ({'title': 'Ad hoc map'}, dict(report, accepted_weight=99)):
            write_json(self.run / 'contribution-audit.json', invalid)
            self.held(self.close(), 'contribution_audit')
        (self.run / 'contribution-audit.json').write_text('{broken', encoding='utf-8')
        self.held(self.close(), 'contribution_audit')

    def test_missing_foreign_or_changed_receipt_holds(self):
        task, receipt = self.task()
        self.audit()
        path = self.store.directory(task['job_id']) / 'memory-outcome.json'
        path.unlink()
        self.held(self.close(), 'task:' + task['job_id'])
        for invalid in (dict(receipt, job_id='f' * 32), dict(receipt, project_id='foreign'),
                        dict(receipt, source_sha256='0' * 64), dict(receipt, status='writing'),
                        dict(receipt, request_sha256='0' * 64)):
            write_json(path, invalid)
            self.held(self.close(), 'task:' + task['job_id'])

    def test_receipt_pointing_to_other_tasks_memory_holds(self):
        task, receipt = self.task()
        _, other = self.task()
        self.audit()
        write_json(self.store.directory(task['job_id']) / 'memory-outcome.json', dict(receipt, memory_id=other['memory_id']))
        self.held(self.close(), 'task:' + task['job_id'])

    def test_deleted_foreign_memory_cannot_substitute_for_capture(self):
        task, receipt = self.task()
        brain = BrainStore(self.root)
        foreign = brain.propose({'project_id': 'foreign', 'kind': 'procedure', 'title': 'Foreign retained fact',
                                 'content': 'Unrelated historical project knowledge.',
                                 'source': {'type': 'user', 'note': 'Foreign note for receipt identity testing'}})
        brain.forget(foreign['id'], 'Tester', 'Remove unrelated record before the receipt check.')
        write_json(self.store.directory(task['job_id']) / 'memory-outcome.json', dict(receipt, memory_id=foreign['id']))
        self.audit()
        self.held(self.close(), 'task:' + task['job_id'])

    def test_missing_stale_foreign_or_changed_startup_holds(self):
        self.task()
        self.audit()
        path = self.run / 'startup-context.json'
        saved = self.read(path)
        path.unlink()
        self.held(self.close(), 'startup_context')
        for invalid in (dict(saved, project_id='foreign'),
                        dict(saved, coordinator=dict(saved['coordinator'], generation=99))):
            write_json(path, invalid)
            self.held(self.close(), 'startup_context')
        write_json(path, saved)
        (self.run / 'startup-context.md').write_text('Changed packet', encoding='utf-8')
        self.held(self.close(), 'startup_context')

    def test_accepted_task_omitted_from_ledger_or_foreign_actor_holds(self):
        self.task()
        self.audit()
        ledger = self.read(self.run / 'contributions-ledger.json')
        for variant in ('omit_activity', 'omit_job_work_item', 'foreign_provider'):
            data = json.loads(json.dumps(ledger))
            if variant == 'omit_activity':
                data['activity'] = []
            elif variant == 'omit_job_work_item':
                data['work_items'][0]['id'] = 'unrelated-complete-work'
            else:
                data['contributors'][0]['provider'] = 'OpenAI'
            write_json(self.run / 'contributions-ledger.json', data)
            write_report(self.run, data)
            self.held(self.close(), 'contribution_audit')

    def test_actor_can_have_different_observed_model_sets_across_tasks(self):
        self.task(model='opus', modelUsage={'opus': {}, 'haiku': {}})
        self.task(model='opus', modelUsage={'opus': {}})
        self.audit()
        result = self.close()
        self.assertEqual(result['status'], 'completed', result)

    def test_nonaccepted_canonical_tasks_cannot_receive_accepted_credit(self):
        self.task()
        cases = (
            ('failed', 'pending', 'failed', '2026-09-13T00:00:00+00:00'),
            ('rejected', 'rejected', 'succeeded', '2026-09-13T00:00:00+00:00'),
            ('awaiting_review', 'pending', 'succeeded', '2026-09-13T00:00:00+00:00'),
            ('running', 'pending', 'running', None),
            ('recovery_required', 'pending', 'unknown', None),
        )
        for status, review_status, execution_status, finalized_at in cases:
            with self.subTest(status=status):
                task, _ = self.task(capture=False, status=status, review_status=review_status,
                                    execution_status=execution_status, finalized_at=finalized_at)
                self.audit()
                if status in ('failed', 'rejected'):
                    self.assertEqual(self.close()['status'], 'completed')
                ledger = self.read(self.run / 'contributions-ledger.json')
                ledger['work_items'].append({
                    'id': task['job_id'], 'label': 'Incorrectly credited task', 'category': 'review',
                    'status': 'accepted', 'weight': 10,
                    'allocations': [{'agent_id': 'worker:claude', 'percent': 100,
                                     'evidence': 'Mistakenly retained nonaccepted output in the ledger'}]})
                ledger['activity'].extend(row for row in task_ledger(task)['activity']
                                          if row['kind'] == 'delegation')
                write_json(self.run / 'contributions-ledger.json', ledger)
                write_report(self.run, ledger)
                before = (self.store.directory(task['job_id']) / 'result.json').read_bytes()
                result = self.close()
                self.held(result, 'contribution_audit')
                check = next(row for row in result['checks'] if row['check'] == 'contribution_audit')
                self.assertIn(task['job_id'], check['reason'])
                self.assertEqual((self.store.directory(task['job_id']) / 'result.json').read_bytes(), before)

    def test_native_accepted_contributor_requires_capture_even_without_marker(self):
        self.audit()
        self.manifest(native_work=False)
        self.held(self.close(), 'native_memory_capture')
        manifest = self.read(self.run / 'run.json')
        manifest.pop('native_work')
        write_json(self.run / 'run.json', manifest)
        self.held(self.close(), 'native_memory_capture')
        start_run(run=self.run, root=self.root, **self.identity)
        self.assertTrue(self.read(self.run / 'run.json')['native_work'])

    def test_failed_revalidation_withdraws_previous_completion(self):
        task, receipt = self.task()
        self.audit()
        self.assertEqual(self.close()['status'], 'completed')
        completed_at = self.read(self.run / 'run.json')['completed_utc']
        write_json(self.store.directory(task['job_id']) / 'memory-outcome.json', dict(receipt, status='writing'))
        self.held(self.close(), 'task:' + task['job_id'])
        self.assertEqual(self.read(self.run / 'run.json')['last_completed_utc'], completed_at)

    def test_forgotten_memory_is_historical_capture_and_never_resurrected(self):
        task, receipt = self.task()
        self.audit()
        brain = BrainStore(self.root)
        brain.forget(receipt['memory_id'], 'Tester', 'Explicit forgetting after successful capture for retention testing.')
        self.assertEqual(self.close()['status'], 'completed')
        self.assertEqual(brain.get(receipt['memory_id'])['status'], 'deleted')
        # A later maintained retry labels a forgotten receipt as skipped.
        forgotten = record_accepted_outcome(self.store, task['job_id'])
        self.assertEqual(forgotten['reason'], 'forgotten')
        self.assertEqual(self.close()['status'], 'completed')

    def test_stale_owner_has_no_closeout_write(self):
        self.task()
        self.audit()
        with self.assertRaisesRegex(ValueError, 'Stale coordinator'):
            closeout_run(self.run, root=self.root, **dict(self.identity, generation=self.identity['generation'] + 1))
        self.assertFalse((self.run / 'closeout.json').exists())

    def test_other_run_same_project_pending_task_is_excluded(self):
        self.task()
        self.task(capture=False, run_id='another-run', status='running', review_status='pending')
        self.audit()
        result = self.close()
        self.assertEqual(result['status'], 'completed', result)
        self.assertEqual(len([row for row in result['checks'] if row['check'].startswith('task:')]), 1)

    def test_output_and_manifest_links_find_tasks_without_run_id(self):
        output, _ = self.task(run_id=None, requested_output=str(self.run / 'answer.json'))
        explicit, _ = self.task(run_id=None)
        self.manifest(tasks=[{'job_id': explicit['job_id']}])
        self.audit()
        result = self.close()
        self.assertEqual(result['status'], 'completed', result)
        names = {row['check'] for row in result['checks']}
        self.assertTrue({'task:' + output['job_id'], 'task:' + explicit['job_id']} <= names)

    def test_pending_task_holds_and_is_never_auto_accepted(self):
        task, _ = self.task(capture=False, status='awaiting_review', review_status='pending')
        self.audit()
        self.held(self.close(), 'task:' + task['job_id'])
        self.assertEqual(self.read(self.store.directory(task['job_id']) / 'result.json')['review_status'], 'pending')

    def test_native_work_requires_real_imported_capture(self):
        self.task()
        self.audit()
        self.manifest(native_work=True)
        self.held(self.close(), 'native_memory_capture')
        (self.run / 'review/evidence.md').write_text('Verified bounded startup and exact closeout behavior.', encoding='utf-8')
        bundle = {'capture_id': 'native-knowledge', 'project_id': 'alpha', 'evidence': ['review/evidence.md'],
                  'memories': [{'key': 'startup', 'kind': 'procedure', 'title': 'Read shared startup context',
                                'content': 'Load shared startup context before dispatch and review closeout evidence.'}]}
        outcome = capture_run(self.root, self.run.name, bundle, **self.identity,
                              reviewer='Tester', note='Verified startup context and native knowledge capture with local evidence.')
        self.assertEqual(outcome['memory_outcome']['status'], 'remembered')
        result = self.close()
        self.assertEqual(result['status'], 'completed', result)
        self.manifest(status='in_progress')
        (self.run / 'review/evidence.md').write_text('Changed after capture.', encoding='utf-8')
        self.held(self.close(), 'task:' + outcome['job_id'])

    def test_changed_generation_during_render_cannot_mark_complete(self):
        self.task()
        self.audit()
        original = Coordinator.require_owner
        calls = [0]
        def changed(state, owner, session, generation):
            calls[0] += 1
            if calls[0] >= 3:
                raise ValueError('Stale coordinator identity or generation')
            return original(state, owner, session, generation)
        with patch.object(Coordinator, 'require_owner', side_effect=changed):
            with self.assertRaisesRegex(ValueError, 'Stale coordinator'):
                self.close()
        self.assertNotEqual(self.read(self.run / 'run.json')['status'], 'completed')


if __name__ == '__main__':
    unittest.main()
