"""Reviewed multi-node memory capture with real SQLite and temporary evidence only."""
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'app'))
from brain_store import BrainStore, digest
from coordinator_handoff import Coordinator
from init_run import create_run
from memory_bundle import capture_run, record_accepted_bundle
from storage_budget import StorageLimitError
from task_store import TaskStore, write_json


class MemoryBundleTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.store = TaskStore(self.root / 'runs/tasks')

    def task(self, **updates):
        task = self.store.create(worker='native-review', task='Investigate synthetic zebra recovery',
                                 category='memory-curation', assignment_project_id='alpha')
        task.update(status='accepted', review_status='accepted', execution_status='succeeded',
                    finalized_at='2026-09-01T12:00:00+00:00', response='PRIVATE_RAW_ANSWER_NOT_MEMORY',
                    review={'reviewer': 'Synthetic reviewer', 'reviewed_at': '2026-09-01T12:01:00+00:00',
                            'note': 'Verified the saved result and bounded recovery behavior against local fixtures.'})
        task.update(updates)
        self.store.save(task['job_id'], task)
        return task['job_id']

    def bundle(self):
        return {'project_id': 'alpha', 'memories': [
            {'key': 'incident', 'kind': 'fact', 'title': 'Zebra incident',
             'content': 'The saved answer survives an interrupted coordinator session.'},
            {'key': 'recovery', 'kind': 'procedure', 'title': 'Recovery procedure',
             'content': 'Inspect the saved result before attempting another worker dispatch.'}],
            'relations': [{'from': 'recovery', 'to': 'incident', 'relation': 'solves',
                           'reason': 'The reviewed procedure addresses this observed interruption.'}]}

    def canonical(self, job):
        return json.loads((self.store.directory(job) / 'result.json').read_text(encoding='utf-8'))

    def receipt(self, job):
        return json.loads((self.store.directory(job) / 'memory-outcome.json').read_text(encoding='utf-8'))

    def active(self, **updates):
        brain = BrainStore(self.root)
        item = brain.propose(dict({'project_id': 'alpha', 'kind': 'fact', 'title': 'Existing guidance',
            'content': 'Existing reviewed guidance supports the temporary test.',
            'source': {'type': 'user', 'note': 'Synthetic user-approved test fixture'}}, **updates))
        return brain.approve(item['id'], 'Synthetic reviewer', 'Verified this independent fixture is safe for test recall.')

    def test_accepted_bundle_stores_atomic_nodes_and_recallable_links(self):
        job, bundle = self.task(), self.bundle()
        before = self.canonical(job)
        with patch('subprocess.run', side_effect=AssertionError('No model calls')), \
                patch('subprocess.Popen', side_effect=AssertionError('No background models')):
            receipt = record_accepted_bundle(self.store, job, bundle)
        self.assertEqual(receipt['status'], 'remembered')
        self.assertEqual(len(receipt['memory_ids']), 2)
        self.assertEqual(len(receipt['relation_ids']), 1)
        brain = BrainStore(self.root)
        rows = [brain.get(identifier) for identifier in receipt['memory_ids']]
        self.assertTrue(all(row['status'] == 'active' for row in rows))
        self.assertTrue(all(row['project_id'] == 'alpha' and row['user_id'] == 'local' for row in rows))
        self.assertTrue(all(row['source'] == {'type': 'task', 'job_id': job,
                            'review_sha256': digest(before['review'])} for row in rows))
        self.assertNotIn('PRIVATE_RAW_ANSWER_NOT_MEMORY', json.dumps(rows))
        hits = brain.search('zebra', 'alpha')['results']
        recovery = next(row for row in hits if row['id'] == receipt['memory_keys']['recovery'])
        self.assertIn('solves', recovery['reason'])
        self.assertEqual(brain.search('zebra', 'foreign')['results'], [])
        self.assertEqual(self.canonical(job), before)
        self.assertEqual(bundle, self.bundle(), 'Caller-owned knowledge must not be mutated')

    def test_review_integration_selects_bundle_instead_of_generic_episode(self):
        job = self.task(status='awaiting_review', review_status='pending', review=None)
        result = self.store.review(job, 'accepted', 'Synthetic reviewer',
            'Reviewed both atomic claims and their explicit recovery relationship.', curated_payload=self.bundle())
        self.assertEqual(result['memory_outcome']['mode'], 'curated_bundle')
        self.assertEqual(result['memory_outcome']['status'], 'remembered')
        self.assertEqual(len(BrainStore(self.root).list_memories('alpha')), 2)
        self.assertEqual(self.canonical(job)['review_status'], 'accepted')

    def test_relationship_evidence_survives_receipt_and_memory_detail(self):
        job, bundle = self.task(), self.bundle()
        saved = record_accepted_bundle(self.store, job, bundle)
        explanation = {'id': saved['relation_ids'][0], 'source_id': saved['memory_keys']['recovery'],
                       'target_id': saved['memory_keys']['incident'], 'relation': 'solves',
                       'reason': bundle['relations'][0]['reason']}
        self.assertEqual(self.receipt(job)['relation_evidence'], [explanation])
        for identifier in saved['memory_ids']:
            detail = BrainStore(self.root).get(identifier)
            self.assertEqual(detail['relations'][0]['evidence_note'], explanation['reason'])

    def test_changed_review_hides_unverified_relationship_evidence(self):
        job, bundle = self.task(), self.bundle()
        saved = record_accepted_bundle(self.store, job, bundle)
        canonical = self.canonical(job)
        canonical['review']['note'] = 'The updated review invalidates the earlier relationship evidence.'
        write_json(self.store.directory(job) / 'result.json', canonical)
        detail = BrainStore(self.root).get(saved['memory_ids'][0])
        self.assertNotIn('evidence_note', detail['relations'][0])

    def test_malformed_evidence_receipt_does_not_break_memory_detail(self):
        job, bundle = self.task(), self.bundle()
        saved = record_accepted_bundle(self.store, job, bundle)
        path = self.store.directory(job) / 'memory-outcome.json'
        for invalid in ([], ['unexpected'], {'relation_evidence': None}):
            with self.subTest(invalid=invalid):
                write_json(path, invalid)
                detail = BrainStore(self.root).get(saved['memory_ids'][0])
                self.assertEqual(detail['id'], saved['memory_ids'][0])
                self.assertNotIn('evidence_note', detail['relations'][0])

    def test_receipt_cannot_claim_another_tasks_memories(self):
        job, other, bundle = self.task(), self.task(), self.bundle()
        saved = record_accepted_bundle(self.store, job, bundle)
        foreign = record_accepted_bundle(self.store, other, bundle)
        for key in ('memory_id', 'memory_ids', 'memory_keys', 'relation_ids', 'relation_evidence'):
            saved[key] = foreign[key]
        write_json(self.store.directory(job) / 'memory-outcome.json', saved)
        with self.assertRaises(ValueError):
            record_accepted_bundle(self.store, job, bundle)
        self.assertEqual(self.receipt(job), saved)
        self.assertEqual(len(BrainStore(self.root).list_memories('alpha')), 4)

    def test_malformed_existing_receipts_are_preserved_without_replaying_capture(self):
        job, bundle = self.task(), self.bundle()
        saved = record_accepted_bundle(self.store, job, bundle)
        path = self.store.directory(job) / 'memory-outcome.json'
        invalid_values = ({}, [], None, dict(saved, schema_version=99), dict(saved, status='unexpected'),
                          dict(saved, memory_ids=['not-an-id']), dict(saved, memory_keys={}))
        for invalid in invalid_values:
            with self.subTest(invalid=invalid):
                write_json(path, invalid)
                before = path.read_bytes()
                with self.assertRaises(ValueError):
                    record_accepted_bundle(self.store, job, bundle)
                self.assertEqual(path.read_bytes(), before)
                self.assertEqual(len(BrainStore(self.root).list_memories('alpha')), 2)

    def test_unaccepted_failed_unfinalized_or_wrong_identity_never_create_brain(self):
        for update in ({'status': 'awaiting_review', 'review_status': 'pending'},
                       {'status': 'rejected', 'review_status': 'rejected'},
                       {'execution_status': 'failed'}, {'finalized_at': None}):
            with self.subTest(update=update):
                job = self.task(**update)
                with patch('memory_bundle.BrainStore', side_effect=AssertionError('No memory initialization')), \
                        self.assertRaises(ValueError):
                    record_accepted_bundle(self.store, job, self.bundle())
        job = self.task()
        write_json(self.store.directory(job) / 'result.json', dict(self.canonical(job), job_id='f' * 32))
        with self.assertRaises(ValueError):
            record_accepted_bundle(self.store, job, self.bundle())
        self.assertFalse((self.root / 'runtime/brain').exists())

    def test_invalid_second_node_leaves_no_nodes_changes_or_search_hits(self):
        job, bundle = self.task(), self.bundle()
        bundle['memories'][1]['kind'] = 'permission'
        with self.assertRaises(ValueError):
            record_accepted_bundle(self.store, job, bundle)
        brain = BrainStore(self.root)
        self.assertEqual(brain.list_memories('alpha'), [])
        self.assertEqual(brain.changes('alpha')['changes'], [])
        self.assertEqual(brain.search('zebra', 'alpha')['results'], [])
        self.assertEqual(self.receipt(job)['status'], 'error')
        self.assertTrue(self.receipt(job)['retryable'])

    def test_missing_existing_link_endpoint_rolls_back_inserted_nodes(self):
        job, bundle = self.task(), self.bundle()
        bundle['relations'][0]['to'] = 'f' * 32
        with self.assertRaisesRegex(ValueError, 'not found'):
            record_accepted_bundle(self.store, job, bundle)
        brain = BrainStore(self.root)
        self.assertEqual(brain.list_memories('alpha'), [])
        self.assertEqual(brain.changes('alpha')['changes'], [])
        self.assertEqual(brain.search('zebra', 'alpha')['results'], [])

    def test_foreign_project_user_or_expired_endpoint_rolls_back_new_nodes(self):
        cases = ({'project_id': 'beta'}, {'user_id': 'other-user'},
                 {'valid_from': '2020-01-01T00:00:00Z', 'valid_to': '2021-01-01T00:00:00Z'})
        for update in cases:
            with self.subTest(update=update):
                existing = self.active(**update)
                job, bundle = self.task(), self.bundle()
                bundle['relations'][0]['to'] = existing['id']
                before = BrainStore(self.root).list_memories('alpha')
                with self.assertRaises(ValueError):
                    record_accepted_bundle(self.store, job, bundle)
                self.assertEqual(BrainStore(self.root).list_memories('alpha'), before)

    def test_top_level_and_atomic_scopes_cannot_override_exact_source(self):
        for layer, field, value in [('bundle', 'project_id', 'beta'), ('node', 'project_id', 'beta'),
                                    ('node', 'user_id', 'someone-else')]:
            with self.subTest(layer=layer, field=field):
                bundle = self.bundle()
                (bundle if layer == 'bundle' else bundle['memories'][1])[field] = value
                with patch('memory_bundle.BrainStore', side_effect=AssertionError('No foreign memory write')), \
                        self.assertRaises(ValueError):
                    record_accepted_bundle(self.store, self.task(), bundle)

    def test_bundle_relationship_requires_an_endpoint_with_this_capture_provenance(self):
        first = self.active(title='First existing memory')
        second = self.active(title='Second existing memory')
        job, bundle = self.task(), self.bundle()
        bundle['relations'] = [{'from': first['id'], 'to': second['id'], 'relation': 'supports',
                                'reason': 'This relationship has neither endpoint in the reviewed capture.'}]
        with self.assertRaises(ValueError):
            record_accepted_bundle(self.store, job, bundle)
        self.assertEqual({row['id'] for row in BrainStore(self.root).list_memories('alpha')},
                         {first['id'], second['id']})

    def test_new_to_existing_memory_link_has_durable_explanation_on_both_ends(self):
        existing = self.active()
        job, bundle = self.task(), self.bundle()
        bundle['relations'] = [{'from': 'recovery', 'to': existing['id'], 'relation': 'supports',
                                'reason': 'This procedure supports the existing reviewed guidance.'}]
        saved = record_accepted_bundle(self.store, job, bundle)
        for identifier in (existing['id'], saved['memory_keys']['recovery']):
            detail = BrainStore(self.root).get(identifier)
            self.assertEqual(detail['relations'][0]['evidence_note'], bundle['relations'][0]['reason'])

    def test_concurrent_identical_retries_reuse_one_atomic_capture(self):
        job, bundle = self.task(), self.bundle()
        with ThreadPoolExecutor(max_workers=3) as pool:
            receipts = list(pool.map(lambda _: record_accepted_bundle(self.store, job, bundle), range(3)))
        self.assertEqual(len({tuple(row['memory_ids']) for row in receipts}), 1)
        brain = BrainStore(self.root)
        self.assertEqual(len(brain.list_memories('alpha')), 2)
        before = brain.changes('alpha')
        self.assertTrue(record_accepted_bundle(self.store, job, bundle)['reused'])
        self.assertEqual(brain.changes('alpha'), before)

    def test_changed_bundle_or_source_preserves_original_receipt(self):
        job, bundle = self.task(), self.bundle()
        record_accepted_bundle(self.store, job, bundle)
        before = self.receipt(job)
        changed = deepcopy(bundle)
        changed['memories'][0]['content'] += ' Changed knowledge.'
        with self.assertRaisesRegex(ValueError, 'changed'):
            record_accepted_bundle(self.store, job, changed)
        self.assertEqual(self.receipt(job), before)
        original = self.canonical(job)
        for update in ({'response': 'Changed canonical response'},
                       {'review': dict(original['review'], note='A later reviewer found the original approach incomplete.')},
                       {'assignment_project_id': 'beta'}):
            with self.subTest(update=update):
                write_json(self.store.directory(job) / 'result.json', dict(original, **update))
                with self.assertRaises(ValueError):
                    record_accepted_bundle(self.store, job, bundle)
                self.assertEqual(self.receipt(job), before)
                self.assertEqual(BrainStore(self.root).search('zebra', 'alpha')['results'], [])

    def test_forget_does_not_resurrect_a_node_or_its_relationship(self):
        job, bundle = self.task(), self.bundle()
        saved = record_accepted_bundle(self.store, job, bundle)
        brain = BrainStore(self.root)
        brain.forget(saved['memory_keys']['incident'], 'Synthetic reviewer',
                     'Forget this synthetic incident after validating the receipt.')
        before = brain.changes('alpha')
        replay = record_accepted_bundle(self.store, job, bundle)
        self.assertEqual(replay['memory_ids'], saved['memory_ids'])
        self.assertEqual(brain.get(saved['memory_keys']['incident'])['status'], 'deleted')
        self.assertEqual(brain.get(saved['memory_keys']['recovery'])['relations'], [])
        self.assertEqual(brain.changes('alpha'), before)
        self.assertEqual(brain.search('zebra', 'alpha')['results'], [])

    def test_interrupted_committed_capture_keeps_writing_receipt_and_blocks_replay(self):
        import memory_bundle
        original_insert = memory_bundle._insert
        job, bundle = self.task(), self.bundle()

        def interrupted(*args):
            original_insert(*args)
            raise KeyboardInterrupt('Synthetic process stop after SQLite commit')

        with patch('memory_bundle._insert', side_effect=interrupted), self.assertRaises(KeyboardInterrupt):
            record_accepted_bundle(self.store, job, bundle)
        before = self.receipt(job)
        self.assertEqual(before['status'], 'writing')
        brain = BrainStore(self.root)
        self.assertEqual(len(brain.list_memories('alpha')), 2)
        with self.assertRaisesRegex(ValueError, 'uncertain'):
            record_accepted_bundle(self.store, job, bundle)
        self.assertEqual(self.receipt(job), before)
        self.assertEqual(len(brain.list_memories('alpha')), 2)

    def test_memory_capacity_rollback_can_retry_the_identical_bundle(self):
        job, bundle = self.task(), self.bundle()
        small = BrainStore(self.root, limits={'max_memories': 1})
        with patch('memory_bundle.BrainStore', return_value=small), self.assertRaises(StorageLimitError):
            record_accepted_bundle(self.store, job, bundle)
        self.assertEqual(small.list_memories('alpha'), [])
        self.assertEqual(small.changes('alpha')['changes'], [])
        self.assertTrue(self.receipt(job)['retryable'])
        self.assertEqual(record_accepted_bundle(self.store, job, bundle)['status'], 'remembered')
        self.assertEqual(len(BrainStore(self.root).list_memories('alpha')), 2)

    def test_relation_capacity_rolls_back_nodes_and_preceding_relationship(self):
        job, bundle = self.task(), self.bundle()
        bundle['relations'].append({'from': 'incident', 'to': 'recovery', 'relation': 'supports',
                                    'reason': 'A second explicit relation exceeds the test capacity.'})
        small = BrainStore(self.root, limits={'max_relations': 1})
        with patch('memory_bundle.BrainStore', return_value=small), self.assertRaises(StorageLimitError):
            record_accepted_bundle(self.store, job, bundle)
        self.assertEqual(small.list_memories('alpha'), [])
        self.assertEqual(small.changes('alpha')['changes'], [])
        self.assertEqual(self.receipt(job)['status'], 'error')

    def test_forgetting_all_nodes_frees_capacity_for_a_new_capture_without_replay(self):
        job, bundle = self.task(), self.bundle()
        small = BrainStore(self.root, limits={'max_memories': 2})
        with patch('memory_bundle.BrainStore', return_value=small):
            saved = record_accepted_bundle(self.store, job, bundle)
            for identifier in saved['memory_ids']:
                small.forget(identifier, 'Synthetic reviewer', 'Remove the completed fixture to free its capacity.')
            replacement = record_accepted_bundle(self.store, self.task(), bundle)
            replay = record_accepted_bundle(self.store, job, bundle)
        self.assertEqual(replacement['status'], 'remembered')
        self.assertEqual(replay['memory_ids'], saved['memory_ids'])
        self.assertEqual({row['id'] for row in small.list_memories('alpha')}, set(replacement['memory_ids']))

    def test_source_modified_between_validation_and_transaction_is_held(self):
        job, bundle = self.task(), self.bundle()
        original = BrainStore.candidate
        calls = []

        def changed(brain, payload):
            fields = original(brain, payload)
            calls.append(payload['title'])
            if len(calls) == 2:
                write_json(self.store.directory(job) / 'result.json',
                           dict(self.canonical(job), response='Changed after candidate validation'))
            return fields

        with patch.object(BrainStore, 'candidate', changed), self.assertRaises(ValueError):
            record_accepted_bundle(self.store, job, bundle)
        self.assertEqual(BrainStore(self.root).list_memories('alpha'), [])
        self.assertEqual(self.receipt(job)['status'], 'error')


class RunCaptureTests(unittest.TestCase):
    """Run ownership and imported artifact provenance."""
    bundle = MemoryBundleTests.bundle
    canonical = MemoryBundleTests.canonical
    receipt = MemoryBundleTests.receipt

    def setUp(self):
        MemoryBundleTests.setUp(self)
        self.run, self.manifest = create_run(self.root, 'bundle-test', 'Review the synthetic system', project_id='alpha')
        self.state = Coordinator(self.run).read()
        self.proof = self.run / 'review/evidence.md'
        self.proof.write_text('Synthetic checks verify recovery and saved output.\n', encoding='utf-8')

    def capture_bundle(self):
        return dict(self.bundle(), capture_id='verified-recovery', evidence=['review/evidence.md'])

    def capture(self, bundle=None, **overrides):
        args = dict(owner=self.state['owner'], session=self.state['session'], generation=self.state['generation'],
                    reviewer='Synthetic reviewer', note='Verified the two claims against the saved local evidence file.')
        args.update(overrides)
        return capture_run(self.root, self.run.name, bundle or self.capture_bundle(), **args)

    def test_capture_preserves_imported_artifact_identity_without_worker_execution(self):
        bundle = self.capture_bundle()
        with patch('subprocess.run', side_effect=AssertionError('No worker execution')), \
                patch('subprocess.Popen', side_effect=AssertionError('No background worker')):
            result = self.capture(bundle)
        self.assertTrue(result['imported_artifact'])
        self.assertEqual(result['provider_calls'], 0)
        task = self.canonical(result['job_id'])
        self.assertTrue(task['imported_completed_artifact'])
        self.assertEqual(task['artifact_origin'], 'reviewed-run-closeout')
        self.assertEqual(task['worker'], 'native-review')
        self.assertEqual(task['assignment_project_id'], 'alpha')
        self.assertEqual(task['run_id'], self.run.name)
        self.assertIsNone(task['started_at'])
        self.assertEqual(task['review_status'], 'accepted')
        self.assertEqual(task['provider_calls'], 0)
        evidence = json.loads(task['response'])
        self.assertEqual(evidence['knowledge'], bundle)
        self.assertEqual(evidence['evidence'][0]['path'], 'review/evidence.md')
        self.assertEqual(evidence['evidence'][0]['sha256'], hashlib.sha256(self.proof.read_bytes()).hexdigest())
        manifest = json.loads((self.run / 'run.json').read_text(encoding='utf-8'))
        self.assertEqual(manifest['tasks'], [{'job_id': result['job_id'], 'status': 'accepted', 'kind': 'reviewed-closeout'}])

    def test_capture_requires_current_owner_session_and_generation_before_creating_task(self):
        for update in ({'owner': 'fable'}, {'session': 'another-session'}, {'generation': self.state['generation'] + 1}):
            with self.subTest(update=update), self.assertRaises(ValueError):
                self.capture(**update)
        self.assertEqual(list((self.root / 'runs/tasks').iterdir()), [])
        self.assertFalse((self.root / 'runtime/brain').exists())

    def test_evidence_must_be_bounded_and_inside_this_exact_run(self):
        outside = self.root / '.orchestration/outside.md'
        outside.write_text('Not proof from the selected run', encoding='utf-8')
        large = self.run / 'review/large.md'
        large.write_bytes(b'x' * (2 * 1024**2 + 1))
        for evidence in (['../outside.md'], [str(self.proof)], ['review/missing.md'], ['review/large.md'], []):
            bundle = dict(self.capture_bundle(), evidence=evidence)
            with self.subTest(evidence=evidence), self.assertRaises((ValueError, OSError)):
                self.capture(bundle)
        self.assertEqual(list((self.root / 'runs/tasks').iterdir()), [])

    def test_capture_retry_reuses_original_artifact_memories_and_manifest_entry(self):
        first, second = self.capture(), self.capture()
        self.assertEqual(first['job_id'], second['job_id'])
        self.assertEqual(first['memory_outcome']['memory_ids'], second['memory_outcome']['memory_ids'])
        self.assertEqual(len(list((self.root / 'runs/tasks').iterdir())), 1)
        self.assertEqual(len(BrainStore(self.root).list_memories('alpha')), 2)
        self.assertEqual(len(json.loads((self.run / 'run.json').read_text(encoding='utf-8'))['tasks']), 1)

    def test_changed_capture_claims_evidence_or_reviewer_are_held(self):
        first = self.capture()
        journal = self.run / 'review/memory-capture-verified-recovery.json'
        original = journal.read_bytes()
        bundle = self.capture_bundle()
        bundle['memories'][0]['title'] = 'Changed claim title'
        with self.assertRaisesRegex(ValueError, 'changed'):
            self.capture(bundle)
        with self.assertRaisesRegex(ValueError, 'changed'):
            self.capture(reviewer='Another reviewer')
        self.proof.write_text('Changed review evidence', encoding='utf-8')
        with self.assertRaisesRegex(ValueError, 'changed'):
            self.capture()
        self.assertEqual(journal.read_bytes(), original)
        self.assertEqual(len(list((self.root / 'runs/tasks').iterdir())), 1)
        self.assertEqual(self.receipt(first['job_id'])['status'], 'remembered')

    def test_capture_requires_exact_project_and_stable_capture_id(self):
        for updates in ({'project_id': 'other'}, {'capture_id': '../unsafe'}, {'capture_id': ''}):
            with self.subTest(updates=updates), self.assertRaises(ValueError):
                self.capture(dict(self.capture_bundle(), **updates))
        self.assertEqual(list((self.root / 'runs/tasks').iterdir()), [])

    def test_malformed_existing_capture_journal_is_preserved_without_new_tasks(self):
        self.capture()
        path = self.run / 'review/memory-capture-verified-recovery.json'
        saved = json.loads(path.read_text(encoding='utf-8'))
        for invalid in ({}, [], None, dict(saved, job_id=None), dict(saved, job_id='../outside'),
                        dict(saved, status='unexpected')):
            with self.subTest(invalid=invalid):
                write_json(path, invalid)
                before = path.read_bytes()
                with self.assertRaises(ValueError):
                    self.capture()
                self.assertEqual(path.read_bytes(), before)
                self.assertEqual(len(list((self.root / 'runs/tasks').iterdir())), 1)
                self.assertEqual(len(BrainStore(self.root).list_memories('alpha')), 2)

    def test_capture_journal_cannot_reassign_another_runs_completed_artifact(self):
        self.capture()
        path = self.run / 'review/memory-capture-verified-recovery.json'
        saved = json.loads(path.read_text(encoding='utf-8'))
        other_run, _ = create_run(self.root, 'another-bundle', 'Another independent closeout', project_id='alpha')
        other_state = Coordinator(other_run).read()
        (other_run / 'review/evidence.md').write_bytes(self.proof.read_bytes())
        other = capture_run(self.root, other_run.name, self.capture_bundle(), owner=other_state['owner'],
            session=other_state['session'], generation=other_state['generation'], reviewer='Synthetic reviewer',
            note='Verified the two claims against the saved local evidence file.')
        saved['job_id'] = other['job_id']
        write_json(path, saved)
        before = path.read_bytes()
        with self.assertRaises(ValueError):
            self.capture()
        self.assertEqual(path.read_bytes(), before)
        manifest = json.loads((self.run / 'run.json').read_text(encoding='utf-8'))
        self.assertNotIn(other['job_id'], [item['job_id'] for item in manifest['tasks']])

    def test_imported_artifact_flag_cannot_be_guessed_from_the_capture_journal(self):
        result = self.capture()
        task = self.canonical(result['job_id'])
        task['imported_completed_artifact'] = False
        write_json(self.store.directory(result['job_id']) / 'result.json', task)
        with self.assertRaises(ValueError):
            self.capture()


if __name__ == '__main__':
    unittest.main()
