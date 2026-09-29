"""Real SQLite reference lifecycle, scope, source, Forget and provider-race tests."""
from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'app'))
from brain_links import create_reference
from brain_store import BrainStore, digest
from storage_budget import StorageLimitError


class BrainLinkTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.brain = BrainStore(self.root)
        guard = patch('jev_openrouter._post', side_effect=AssertionError('Unexpected provider call'))
        guard.start()
        self.addCleanup(guard.stop)

    def memory(self, project='origin', **extra):
        payload = {'project_id': project, 'kind': 'fact', 'title': 'Retry memory',
                   'content': 'Retry with a bounded deadline and recorded timing.',
                   'tags': ['recovery'], 'source': {'type': 'user', 'note': 'Reviewed synthetic source.'}}
        payload.update(extra)
        row = self.brain.propose(payload)
        return self.brain.approve(row['id'], 'Tester', 'Checked the complete synthetic evidence.')

    def link(self, original, destination='destination', local=None, **extra):
        options = {'project_id': destination, 'target_project_id': original['project_id'],
                   'target_id': original['id'], 'actor': 'Tester',
                   'note': 'Reviewed selected source and approved destination reuse.',
                   'memory_id': local['id'] if local else None}
        options.update(extra)
        return create_reference(self.brain, **options)

    def recall(self, project='destination'):
        with patch('jev_openrouter.load_config', return_value={'status': 'disabled'}):
            return self.brain.search('retry', project, strategy='keyword', record_trace=False)

    def task_memory(self):
        job = 'a' * 32
        path = self.root / 'runs/tasks' / job / 'result.json'
        path.parent.mkdir(parents=True)
        path.write_text(json.dumps({'job_id': job, 'assignment_project_id': 'origin',
            'execution_status': 'succeeded', 'status': 'accepted', 'review_status': 'accepted',
            'finalized_at': '2026-09-01T00:00:00Z', 'response': 'Reviewed retry evidence'}))
        return self.memory(source={'type': 'task', 'job_id': job}), path

    def test_selected_reference_recalled_and_only_destination_is_shared(self):
        original = self.memory()
        foreign = self.memory(title='Unselected retry memory', content='Unselected foreign secret.')
        result = self.link(original)
        reference = result['reference']
        self.assertIsNone(result['relation'])
        self.assertEqual(reference['project_id'], 'destination')
        self.assertEqual(reference['source']['memory_id'], original['id'])
        self.assertEqual(reference['source']['consent'], 'reuse_in_destination')
        recall = self.recall()
        self.assertEqual([m['id'] for m in recall['results']], [reference['id']])
        self.assertNotIn(foreign['content'], recall['context'])
        self.assertEqual(self.recall('unrelated')['results'], [])
        self.assertEqual(BrainStore(self.root).get(reference['id'])['content'], original['content'])

    def test_local_edge_and_reference_are_atomic(self):
        original = self.memory()
        local = self.memory('destination', title='Destination incident')
        result = self.link(original, local=local, relation='solves')
        self.assertEqual(result['relation']['source_id'], local['id'])
        self.assertEqual(result['relation']['target_id'], result['reference']['id'])
        self.assertEqual(result['relation']['relation'], 'solves')
        with self.assertRaises(ValueError):
            self.brain.relate(local['id'], original['id'], 'solves', 'Tester')

    def test_full_relation_budget_rolls_back_new_reference(self):
        self.brain.limits['max_relations'] = 1
        local = self.memory('destination')
        peer = self.memory('destination', title='Other memory')
        self.brain.relate(local['id'], peer['id'], 'supports', 'Tester')
        original = self.memory()
        before = self.brain.export('destination')
        with self.assertRaises(StorageLimitError):
            self.link(original, local=local)
        after = self.brain.export('destination')
        self.assertEqual(before['memories'], after['memories'])
        self.assertEqual(before['change_cursor'], after['change_cursor'])

    def test_wrong_scope_and_cross_user_rejected_without_mutation(self):
        original = self.memory()
        stranger = self.memory('destination', user_id='stranger')
        for extra in ({'target_project_id': 'wrong'}, {'user_id': 'stranger'},
                      {'project_id': 'origin'}, {'memory_id': stranger['id']}):
            with self.subTest(extra=extra), self.assertRaises(ValueError):
                self.link(original, **extra)
        self.assertEqual(self.brain.list_memories('destination'), [])

    def test_reference_candidates_and_reference_chains_are_rejected(self):
        original = self.memory()
        reference = self.link(original)['reference']
        with self.assertRaisesRegex(ValueError, 'Reference chains'):
            self.link(reference, destination='third')
        with self.assertRaisesRegex(ValueError, 'explicit reviewed link'):
            self.brain.propose({'project_id': 'destination', 'kind': 'fact', 'title': 'Forged',
                                'content': original['content'], 'source': reference['source']})

    def test_duplicate_concurrent_imports_share_one_reference_and_edge(self):
        original = self.memory()
        local = self.memory('destination')
        with ThreadPoolExecutor(max_workers=4) as pool:
            results = list(pool.map(lambda _: self.link(original, local=local), range(4)))
        self.assertEqual(len({r['reference']['id'] for r in results}), 1)
        self.assertEqual(len({r['relation']['id'] for r in results}), 1)
        self.assertEqual(sum(not r['reference']['duplicate'] for r in results), 1)

    def test_identical_user_note_is_not_reused_as_reference(self):
        original = self.memory()
        existing = self.memory('destination')
        reference = self.link(original)['reference']
        self.assertNotEqual(reference['id'], existing['id'])
        self.assertEqual(existing['source']['type'], 'user')

    def test_pending_expired_and_future_originals_rejected(self):
        for kwargs in ({'valid_from': '2000-01-01T00:00:00Z', 'valid_to': '2001-01-01T00:00:00Z'},
                       {'valid_from': '2100-01-01T00:00:00Z'}):
            with self.subTest(kwargs=kwargs), self.assertRaisesRegex(ValueError, 'current'):
                self.link(self.memory(**kwargs))
        pending = self.brain.propose({'project_id': 'origin', 'kind': 'fact', 'title': 'Pending',
            'content': 'Unreviewed retry', 'source': {'type': 'user', 'note': 'Synthetic pending'}})
        with self.assertRaisesRegex(ValueError, 'current'):
            self.link(pending)

    def test_original_task_change_excludes_reference_from_recall_export_and_vault(self):
        original, path = self.task_memory()
        reference = self.link(original)['reference']
        self.assertEqual(len(self.recall()['results']), 1)
        task = json.loads(path.read_text())
        task['response'] = 'Changed evidence requiring fresh review'
        path.write_text(json.dumps(task))
        result = self.recall()
        self.assertEqual(result['results'], [])
        self.assertEqual(result['source_validation']['checked_tasks'], 1)
        self.assertEqual(result['source_validation']['excluded_memories'], 1)
        self.assertNotIn(original['id'], json.dumps(result['source_validation']))
        self.assertEqual(self.brain.export('destination')['memories'], [])
        self.assertNotIn(reference['content'], Path(self.brain.vault('destination')['path']).read_text())

    def test_source_checks_survive_automatic_semantic_fallback(self):
        original, path = self.task_memory()
        self.link(original)
        task = json.loads(path.read_text())
        task['response'] = 'Changed original source'
        path.write_text(json.dumps(task))
        with patch('jev_openrouter.load_config', return_value={'status': 'disabled'}):
            result = self.brain.search('retry', 'destination', record_trace=False)
        self.assertEqual(result['source_validation']['checked_tasks'], 1)
        self.assertEqual(result['source_validation']['excluded_memories'], 1)
        self.assertEqual(result['results'], [])

    def test_original_row_or_review_change_excludes_reference(self):
        for field, value in (('content', 'Tampered original'), ('review_note', 'Changed review'),
                             ('valid_to', '2001-01-01T00:00:00Z')):
            original = self.memory(title='Retry ' + field)
            reference = self.link(original)['reference']
            with self.brain._connection() as con:
                con.execute('UPDATE memories SET ' + field + '=? WHERE id=?', (value, original['id']))
                con.commit()
            self.assertNotIn(reference['id'], [m['id'] for m in self.recall()['results']])

    def test_original_supersession_does_not_silently_follow_new_version(self):
        original = self.memory()
        reference = self.link(original)['reference']
        replacement = self.memory(title='New retry', content='New version needs explicit approval.')
        self.brain.supersede(original['id'], replacement['id'], 'Tester', 'Newer reviewed source replaces old.')
        self.assertEqual(self.recall()['results'], [])
        self.assertEqual(self.brain.get(reference['id'])['status'], 'expired')
        self.assertTrue(any(item['operation'] == 'reference_invalidated' for item in self.brain.changes('destination')['changes']))
        fresh = self.link(replacement)['reference']
        self.assertNotEqual(fresh['id'], reference['id'])
        self.assertEqual([r['id'] for r in self.recall()['results']], [fresh['id']])

    def test_reference_copy_tamper_is_excluded(self):
        reference = self.link(self.memory())['reference']
        with self.brain._connection() as con:
            con.execute('UPDATE memories SET content=? WHERE id=?', ('Injected copy content', reference['id']))
            con.commit()
        self.assertEqual(self.recall()['results'], [])

    def test_unlink_forgets_only_destination_copy(self):
        original = self.memory()
        reference = self.link(original)['reference']
        self.brain.forget(reference['id'], 'Tester', 'Revoke this destination reuse only.')
        self.assertEqual(self.brain.get(reference['id'])['content'], '')
        self.assertEqual(self.brain.get(original['id'])['status'], 'active')
        self.assertEqual(self.recall()['results'], [])
        fresh = self.link(original)['reference']
        self.assertNotEqual(reference['id'], fresh['id'])

    def test_forget_original_securely_clears_derived_copies_traces_vectors_and_exports(self):
        secret = 'uniquelinkedsecretcanaryabcdefghij'
        original = self.memory(content=secret)
        local = self.memory('destination', title='Independent node', content='Retain independent evidence')
        reference = self.link(original, local=local)['reference']
        other = self.link(original, destination='another')['reference']
        vault = Path(self.brain.vault('destination')['path'])
        self.brain.search('retry', 'destination')
        with self.brain._connection() as con:
            from brain_semantic import SCHEMA
            con.executescript(SCHEMA)
            for row in (reference, other):
                con.execute('INSERT INTO semantic_vectors VALUES(?,?,?,?,?,?,?,?,?)',
                    (row['id'], row['project_id'], 'local', 'test', 'test', 'f', 1, b'0000', '2026-09-01'))
            con.commit()
        self.brain.forget(original['id'], 'Tester', 'Erase original and all dependent evidence copies.')
        for row in (original, reference, other):
            self.assertEqual(self.brain.get(row['id'])['content'], '')
            self.assertEqual(self.brain.get(row['id'])['status'], 'deleted')
        self.assertFalse(vault.exists())
        self.assertEqual(self.brain.get(local['id'])['relations'], [])
        self.assertEqual(self.brain.snapshot('destination')['traces'], [])
        with self.brain._connection() as con:
            self.assertEqual(con.execute('SELECT count(*) FROM semantic_vectors').fetchone()[0], 0)
        for file in self.brain.home.glob('memory.sqlite*'):
            self.assertNotIn(secret.encode(), file.read_bytes())
        changes = self.brain.changes('destination')['changes']
        self.assertTrue(any(row['operation'] == 'reference_forgotten' for row in changes))

    def test_reference_task_uses_existing_distinct_source_budget(self):
        from brain_recall import _source_room
        from brain_semantic import _current
        original, _ = self.task_memory()
        reference = self.link(original)['reference']
        with self.brain._connection() as con:
            row = self.brain._row(con, reference['id'])
            keys = {(format(index, '032x'), 'unrelated') for index in range(128)}
            self.assertFalse(_source_room(row, keys))
            evidence = {}
            with patch.object(self.brain, '_task_source_proofs') as read:
                self.assertFalse(_current(self.brain, row, '2026-10-01T00:00:00+00:00', {}, keys, evidence))
            read.assert_not_called()
            self.assertTrue(evidence['scan_limited'])
            keys = {('a' * 32, 'origin')}
            self.assertTrue(_source_room(row, keys))
            self.assertEqual(len(keys), 1)

    def test_jev_workflow_accepts_current_reference_and_rejects_revoked(self):
        from jev_workflows import _snapshot
        original = self.memory()
        reference = self.link(original)['reference']
        records, proofs = _snapshot(self.brain, 'destination', [reference['id']])
        self.assertEqual(records[0]['content'], original['content'])
        self.assertIn(reference['id'], proofs)
        self.brain.forget(original['id'], 'Tester', 'Revoke source before workflow evidence read.')
        with self.assertRaises(ValueError):
            _snapshot(self.brain, 'destination', [reference['id']])

    def test_original_forget_during_jev_call_never_returns_reference(self):
        original = self.memory()
        reference = self.link(original)['reference']
        config = {'status': 'ready', 'key_present': True, 'authorized_projects': ['destination'],
                  'purposes': ['memory_rank'], 'min_confidence': .8}
        def provider(*args, **kwargs):
            self.brain.forget(original['id'], 'Tester', 'Forget original during external scoring.')
            return {'status': 'ok', 'provider_calls': 0, 'cache_hit': True, 'answers':
                    {'memory_0': {'type': 'score', 'score': 3, 'confidence': 1}}}
        with patch('jev_openrouter.load_config', return_value=config), patch('jev_openrouter.evaluate', side_effect=provider) as call:
            result = self.brain.search('retry', 'destination')
        call.assert_called_once()
        self.assertEqual(result['results'], [])
        self.assertNotIn(reference['content'], result['context'])

    def test_changed_original_before_jev_cached_rerank_is_not_transmitted(self):
        from brain_jev import maybe_rank
        original = self.memory()
        reference = self.link(original)['reference']
        baseline = self.recall()
        self.brain.forget(original['id'], 'Tester', 'Forget source before delayed scoring begins.')
        config = {'status': 'ready', 'key_present': True, 'authorized_projects': ['destination'],
                  'purposes': ['memory_rank'], 'min_confidence': .8}
        with patch('jev_openrouter.load_config', return_value=config), patch('jev_openrouter.evaluate') as call:
            result = maybe_rank(self.brain, baseline, record_trace=False)
        call.assert_not_called()
        self.assertEqual(result['results'], [])
        self.assertNotIn(reference['content'], result['context'])

    def test_schema_upgrade_is_repeatable_and_retains_originals(self):
        original = self.memory()
        with self.brain._connection() as con:
            con.execute('DROP INDEX reference_target')
            con.execute('PRAGMA user_version=3')
            con.commit()
        for _ in range(2):
            store = BrainStore(self.root)
            self.assertEqual(store.get(original['id'])['content'], original['content'])
            with store._connection() as con:
                self.assertEqual(con.execute('PRAGMA user_version').fetchone()[0], 4)
                self.assertIsNotNone(con.execute("SELECT 1 FROM sqlite_master WHERE name='reference_target'").fetchone())


if __name__ == '__main__':
    unittest.main()
