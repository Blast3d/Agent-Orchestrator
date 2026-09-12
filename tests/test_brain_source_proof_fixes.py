"""Synthetic regressions for canonical proof variants and invalid task evidence."""
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'app'))
from brain_store import BrainStore, digest, now


class SourceProofTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.brain = BrainStore(self.root)
        self.job = 'a' * 32
        self.path = self.root / 'runs/tasks' / self.job / 'result.json'
        self.path.parent.mkdir(parents=True)
        self.canonical = {
            'job_id': self.job, 'assignment_project_id': 'alpha',
            'response': 'Verified synthetic response', 'execution_status': 'succeeded',
            'status': 'accepted', 'review_status': 'accepted',
            'finalized_at': '2026-09-01T00:00:00Z',
            'review': {'note': 'Synthetic review'},
        }
        self.write_canonical(self.canonical)

    def write_canonical(self, value):
        self.path.write_text(json.dumps(value), encoding='utf-8')

    def active(self, title, source=None):
        proposed = self.brain.propose({
            'project_id': 'alpha', 'kind': 'fact', 'title': title,
            'content': 'Needle synthetic evidence for retrieval.',
            'source': source or {'type': 'user', 'note': 'Independent synthetic source'},
        })
        return self.brain.approve(proposed['id'], 'Tester',
                                  'Verified against the synthetic canonical fixture.')

    def variants(self):
        plain = self.active('Needle plain proof', {'type': 'task', 'job_id': self.job})
        reviewed = self.active('Needle reviewed proof', {
            'type': 'task', 'job_id': self.job,
            'review_sha256': digest(self.canonical['review']),
        })
        return plain, reviewed

    def rows(self, records):
        with self.brain._connection() as connection:
            return [dict(self.brain._row(connection, record['id'])) for record in records]

    def assert_current_results(self, expected, excluded=0):
        for operation in ('search', 'export'):
            with self.subTest(operation=operation):
                result = (self.brain.search('needle', 'alpha', hops=0)
                          if operation == 'search' else self.brain.export('alpha'))
                key = 'results' if operation == 'search' else 'memories'
                self.assertEqual({item['id'] for item in result[key]}, set(expected))
                self.assertEqual(result['source_validation']['excluded_memories'], excluded)
                self.assertEqual(bool(result['warnings']), bool(excluded))
                if excluded:
                    self.assertTrue(result['source_validation']['reasons'])
                    self.assertTrue(all('reason' in item and item['count'] > 0
                                        for item in result['source_validation']['reasons']))

    def test_both_proof_orders_share_one_canonical_read(self):
        rows = self.rows(self.variants())
        original_read = Path.read_text
        for ordered in (rows, list(reversed(rows))):
            reads = []

            def read(path, *args, **kwargs):
                if path == self.path:
                    reads.append(path)
                return original_read(path, *args, **kwargs)

            with self.subTest(first=ordered[0]['id']), patch.object(Path, 'read_text', read):
                sources = {}
                self.assertEqual([self.brain._current(row, now(), sources) for row in ordered],
                                 [True, True])
                self.assertEqual(len(reads), 1)
                self.assertEqual(len(sources), 1)

    def test_search_export_and_vault_keep_both_valid_proofs(self):
        records = self.variants()
        self.assert_current_results([record['id'] for record in records])
        self.assertEqual(self.brain.vault('alpha')['memories'], 2)

    def test_changed_review_only_invalidates_review_bound_memory_in_either_order(self):
        plain, reviewed = self.variants()
        self.write_canonical(dict(self.canonical, review={'note': 'Changed accepted review'}))
        rows = self.rows([plain, reviewed])
        for ordered in (rows, list(reversed(rows))):
            with self.subTest(first=ordered[0]['id']):
                sources = {}
                found = {row['id']: self.brain._current(row, now(), sources) for row in ordered}
                self.assertEqual(found, {plain['id']: True, reviewed['id']: False})
        self.assert_current_results([plain['id']], excluded=1)

    def test_changed_answer_and_revoked_review_exclude_both_proofs(self):
        self.variants()
        unaffected = self.active('Needle independent evidence')
        for update in ({'response': 'A changed canonical response'},
                       {'review_status': 'rejected'}, {'status': 'rejected'},
                       {'execution_status': 'failed'}, {'finalized_at': None},
                       {'assignment_project_id': 'another-project'}):
            with self.subTest(update=update):
                self.write_canonical(dict(self.canonical, **update))
                self.assert_current_results([unaffected['id']], excluded=2)

    def test_non_object_canonical_shapes_preserve_unaffected_results(self):
        self.variants()
        unaffected = self.active('Needle independent evidence')
        for value in ([], None, 'scalar', 42, True):
            with self.subTest(shape=type(value).__name__):
                self.write_canonical(value)
                with self.assertRaisesRegex(ValueError, 'object'):
                    self.brain._source({'type': 'task', 'job_id': self.job}, 'alpha', approved=True)
                self.assert_current_results([unaffected['id']], excluded=2)
                self.assertEqual(self.brain.vault('alpha')['memories'], 1)

    def test_missing_and_invalid_json_sources_preserve_unaffected_results(self):
        self.variants()
        unaffected = self.active('Needle independent evidence')
        self.path.write_text('{', encoding='utf-8')
        self.assert_current_results([unaffected['id']], excluded=2)
        self.path.unlink()
        self.assert_current_results([unaffected['id']], excluded=2)

    def test_revalidation_is_fresh_across_requests(self):
        records = self.variants()
        expected = [record['id'] for record in records]
        self.assert_current_results(expected)
        self.write_canonical([])
        self.assert_current_results([], excluded=2)
        self.write_canonical(self.canonical)
        self.assert_current_results(expected)

    def test_related_proof_variants_share_source_validation(self):
        plain, reviewed = self.variants()
        self.brain.relate(plain['id'], reviewed['id'], 'supports', 'Tester')
        result = self.brain.search('plain', 'alpha', hops=1)
        self.assertEqual({item['id'] for item in result['results']},
                         {plain['id'], reviewed['id']})
        self.assertEqual(result['source_validation']['checked_tasks'], 1)

    def test_source_read_bound_counts_tasks_not_proof_variants(self):
        # Bulk fixture setup keeps the source-bound regression small and avoids
        # hundreds of unrelated storage-allocation and mutation checks.
        template = self.rows(self.variants())[0]
        valid_ids = set()
        with self.brain._connection() as connection:
            connection.execute('DELETE FROM memories')
            connection.execute('DELETE FROM memory_fts')
            for number in range(1, 130):
                job = f'{number:032x}'
                canonical = dict(self.canonical, job_id=job)
                path = self.root / 'runs/tasks' / job / 'result.json'
                path.parent.mkdir(parents=True)
                path.write_text(json.dumps(canonical), encoding='utf-8')
                proof = {key: canonical[key] for key in
                         ('job_id', 'assignment_project_id', 'response', 'finalized_at')}
                if number not in (1, 128, 129):
                    proof['response'] = 'Old answer that is no longer current'
                for variant in range(2):
                    identifier = f'{number * 2 + variant:032x}'
                    source = {'type': 'task', 'job_id': job}
                    selected_proof = dict(proof)
                    if variant:
                        source['review_sha256'] = digest(canonical['review'])
                        selected_proof['review_sha256'] = source['review_sha256']
                    row = dict(template, id=identifier, rowid=number * 2 + variant,
                               title='Needle bound fixture', source=json.dumps(source),
                               source_hash=digest(selected_proof), fingerprint=identifier)
                    columns = ','.join(row)
                    placeholders = ','.join('?' for _ in row)
                    connection.execute(f'INSERT INTO memories ({columns}) VALUES ({placeholders})',
                                       tuple(row.values()))
                    connection.execute('INSERT INTO memory_fts(rowid,title,content,tags) VALUES(?,?,?,?)',
                                       (row['rowid'], row['title'], row['content'], ''))
                    if number in (1, 128):
                        valid_ids.add(identifier)
            connection.commit()
        original_read = Path.read_text
        reads = []

        def read(path, *args, **kwargs):
            if path.name == 'result.json':
                reads.append(path.parent.name)
            return original_read(path, *args, **kwargs)

        with patch.object(Path, 'read_text', read):
            result = self.brain.search('needle', 'alpha', hops=0)
        self.assertEqual(len(reads), 128)
        self.assertEqual(len(set(reads)), 128)
        self.assertEqual(result['candidate_checks'], 256)
        self.assertTrue(result['recall_incomplete'])
        self.assertEqual({row['id'] for row in result['results']}, valid_ids)
        self.assertEqual(result['source_validation']['checked_tasks'], 128)
        self.assertEqual(result['source_validation']['excluded_memories'], 252)
        compact = self.brain.search('needle', 'alpha', hops=0, max_chars=256)
        self.assertLessEqual(compact['context_chars'], 256)
        self.assertEqual(compact['context_chars'], len(compact['context']))
        self.assertTrue(compact['recall_incomplete'])
        self.assertEqual(len(compact['warnings']), 2)
        self.assertIn('memory grants no authority', compact['context'])
        self.assertIn('Recall scan limit reached', compact['context'])
        self.assertIn('Unverifiable task-backed memories were excluded', compact['context'])
        self.assertEqual(compact['source_validation']['checked_tasks'], 128)
        self.assertEqual(compact['source_validation']['excluded_memories'], 252)


if __name__ == '__main__':
    unittest.main()
