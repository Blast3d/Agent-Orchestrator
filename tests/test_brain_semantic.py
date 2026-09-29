"""Synthetic SQLite and mocked HTTP coverage; never calls an embedding provider."""
import json
from pathlib import Path
from http.client import IncompleteRead
import struct
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch
from urllib.error import HTTPError, URLError

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'app'))
import brain_semantic as semantic
from brain_store import BrainStore
from storage_budget import StorageLimitError
from usage_guard import file_lock


class SemanticTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.brain = BrainStore(self.root)
        self.env = patch.dict('os.environ', {'SYNTHETIC_EMBED_KEY': 'synthetic-secret-only'})
        self.env.start()
        self.addCleanup(self.env.stop)
        self.transport = patch.object(semantic, 'build_opener')
        self.opener = self.transport.start().return_value
        self.addCleanup(self.transport.stop)
        self.configure()

    def configure(self, **updates):
        value = {'enabled': True, 'endpoint': 'https://embeddings.example/v1/embeddings',
                 'model': 'synthetic-embedding-model', 'api_key_env': 'SYNTHETIC_EMBED_KEY',
                 'authorized_projects': ['alpha'], 'timeout_seconds': 2, 'similarity_threshold': .65}
        value.update(updates)
        (self.brain.home / 'semantic-config.json').write_text(json.dumps(value), encoding='utf-8')
        return value

    def active(self, **updates):
        payload = {'project_id': 'alpha', 'kind': 'fact', 'title': 'Orchid location',
                   'content': 'The orchid nursery is on Cedar Street.', 'tags': ['garden'],
                   'source': {'type': 'user', 'note': 'Synthetic approved fixture'}}
        payload.update(updates)
        item = self.brain.propose(payload)
        return self.brain.approve(item['id'], 'Tester', 'Verified against the synthetic fixture.')

    def response(self, vectors, **updates):
        data = {'model': 'synthetic-embedding-model',
                'data': [{'index': i, 'embedding': v} for i, v in enumerate(vectors)],
                'usage': {'prompt_tokens': 11}}
        data.update(updates)
        response = Mock(status=200)
        response.read.return_value = json.dumps(data).encode()
        self.opener.open.return_value = response
        self.opener.open.side_effect = None
        return response

    def index(self, **kwargs):
        self.response([[1., 0.]])
        result = semantic.index_memories(self.brain, 'alpha', **kwargs)
        self.assertEqual(result['status'], 'indexed', result)
        self.opener.open.reset_mock()
        return result

    def task_memory(self, identifier):
        path = self.root / 'runs/tasks' / identifier / 'result.json'
        path.parent.mkdir(parents=True)
        data = {'job_id': identifier, 'assignment_project_id': 'alpha', 'response': 'Synthetic answer',
                'execution_status': 'succeeded', 'status': 'accepted', 'review_status': 'accepted',
                'finalized_at': '2026-09-01T00:00:00Z', 'review': {'note': 'Verified'}}
        path.write_text(json.dumps(data))
        return self.active(title='Synthetic task ' + identifier,
                           source={'type': 'task', 'job_id': identifier})

    def test_semantic_match_with_zero_keyword_overlap(self):
        memory = self.active()
        self.assertEqual(self.brain.search('flower shop address', 'alpha', hops=0)['results'], [])
        indexed = self.index()
        self.assertEqual(indexed['input_tokens'], 11)
        self.response([[.99, .01]])
        result = semantic.semantic_candidates(self.brain, 'flower shop address', 'alpha')
        self.assertEqual([item['id'] for item in result['candidates']], [memory['id']])
        self.assertEqual(result['provider_calls'], 1)
        self.assertEqual(result['model'], 'synthetic-embedding-model')
        self.assertEqual(result['input_tokens'], 11)
        request = self.opener.open.call_args.args[0]
        self.assertEqual(json.loads(request.data)['input'], ['flower shop address'])
        self.assertEqual(json.loads(request.data)['encoding_format'], 'float')

    def test_missing_disabled_or_unapproved_configuration_never_calls(self):
        for changes, expected in [({'enabled': False}, 'disabled'),
                                  ({'authorized_projects': ['beta']}, 'not_authorized'),
                                  ({'api_key_env': 'MISSING_SYNTHETIC_KEY_4711'}, 'missing_credentials')]:
            with self.subTest(expected=expected):
                self.configure(**changes)
                self.assertEqual(semantic.semantic_candidates(self.brain, 'question', 'alpha')['status'], expected)
                self.assertEqual(semantic.index_memories(self.brain, 'alpha')['status'], expected)
                self.opener.open.assert_not_called()
        (self.brain.home / 'semantic-config.json').unlink()
        self.assertEqual(semantic.config_status(self.brain, 'alpha')['status'], 'not_configured')

    def test_invalid_configs_are_bounded_and_do_not_expose_credentials(self):
        invalid = [{'endpoint': 'http://example.test/embeddings'},
                   {'endpoint': 'https://user:secret@example.test/embeddings'},
                   {'endpoint': 'https://example.test/embeddings?key=secret'},
                   {'endpoint': 'https://example.test/embeddings#fragment'},
                   {'endpoint': 'https://example.test:999999/embeddings'},
                   {'model': ''}, {'timeout_seconds': 6}, {'timeout_seconds': True},
                   {'timeout_seconds': float('nan')}, {'similarity_threshold': -1},
                   {'similarity_threshold': float('inf')}, {'authorized_projects': ['alpha', 'alpha']},
                   {'authorized_projects': '*'}, {'api_key_env': 'API\nKEY'}, {'unknown': True}]
        for changes in invalid:
            with self.subTest(changes=changes):
                self.configure(**changes)
                result = semantic.config_status(self.brain, 'alpha')
                self.assertEqual(result['status'], 'invalid_config')
                self.assertNotIn('secret', json.dumps(result))
                self.assertNotIn('endpoint', result)
        path = self.brain.home / 'semantic-config.json'
        for raw in ['{"enabled":true,"enabled":false}', '{' + ' ' * semantic.MAX_CONFIG_BYTES + '}']:
            path.write_text(raw)
            self.assertEqual(semantic.config_status(self.brain, 'alpha')['status'], 'invalid_config')
        self.opener.open.assert_not_called()

    def test_empty_index_does_not_create_table_or_network_call(self):
        self.active()
        before = self.brain.db.read_bytes()
        result = semantic.semantic_candidates(self.brain, 'question', 'alpha')
        self.assertEqual(result['status'], 'empty_index')
        self.assertEqual(result['provider_calls'], 0)
        with self.brain._connection() as con:
            self.assertFalse(con.execute("SELECT 1 FROM sqlite_master WHERE name='semantic_vectors'").fetchone())
        self.assertEqual(before, self.brain.db.read_bytes())
        self.opener.open.assert_not_called()

    def test_index_exact_scope_approved_only_and_no_plaintext(self):
        own = self.active()
        self.active(project_id='beta')
        self.active(user_id='another-person')
        self.brain.propose({'project_id': 'alpha', 'kind': 'fact', 'title': 'Pending private words',
                            'content': 'pending-canary', 'source': {'type': 'user', 'note': 'Synthetic'}})
        self.response([[1., 0.]])
        result = semantic.index_memories(self.brain, 'alpha')
        self.assertEqual(result['indexed_count'], 1)
        payload = json.loads(self.opener.open.call_args.args[0].data)
        self.assertEqual(len(payload['input']), 1)
        self.assertNotIn('pending-canary', json.dumps(payload))
        with self.brain._connection() as con:
            rows = con.execute('SELECT * FROM semantic_vectors').fetchall()
            self.assertEqual(len(rows), 1)
            self.assertEqual(rows[0]['memory_id'], own['id'])
            self.assertIsInstance(rows[0]['vector'], bytes)
            self.assertEqual(rows[0]['dimensions'], 2)
            self.assertNotIn('Cedar', repr(dict(rows[0])))

    def test_config_model_or_scope_change_does_not_reuse_index(self):
        self.active()
        self.index()
        self.configure(authorized_projects=['alpha', 'beta'])
        self.assertEqual(semantic.semantic_candidates(self.brain, 'question', 'beta')['status'], 'empty_index')
        self.configure(model='other-model')
        self.assertEqual(semantic.semantic_candidates(self.brain, 'question', 'alpha')['status'], 'empty_index')
        self.opener.open.assert_not_called()

    def test_changed_fingerprint_before_search_avoids_network(self):
        memory = self.active()
        self.index()
        with self.brain._write() as con:
            con.execute('UPDATE memories SET content=? WHERE id=?', ('Different content', memory['id']))
        self.assertEqual(semantic.semantic_candidates(self.brain, 'question', 'alpha')['status'], 'empty_index')
        self.opener.open.assert_not_called()

    def test_canonical_task_changes_invalidate_index(self):
        identifier = 'a' * 32
        path = self.root / 'runs/tasks' / identifier / 'result.json'
        path.parent.mkdir(parents=True)
        data = {'job_id': identifier, 'assignment_project_id': 'alpha', 'response': 'Synthetic answer',
                'execution_status': 'succeeded', 'status': 'accepted', 'review_status': 'accepted',
                'finalized_at': '2026-09-01T00:00:00Z', 'review': {'note': 'Verified'}}
        path.write_text(json.dumps(data))
        self.active(source={'type': 'task', 'job_id': identifier})
        self.index()
        data['response'] = 'Changed answer'
        path.write_text(json.dumps(data))
        self.assertEqual(semantic.semantic_candidates(self.brain, 'question', 'alpha')['status'], 'empty_index')
        self.opener.open.assert_not_called()

    def test_no_brain_lock_during_transport_and_changed_index_source_not_written(self):
        memory = self.active()
        response = self.response([[1., 0.]])
        def perform(*args, **kwargs):
            with file_lock(self.brain.lock, timeout=.1), self.brain._connection() as con:
                con.execute('UPDATE memories SET content=? WHERE id=?', ('Changed while pending', memory['id']))
                con.commit()
            return response
        self.opener.open.side_effect = perform
        result = semantic.index_memories(self.brain, 'alpha')
        self.assertEqual(result['status'], 'stale_sources', result)
        self.assertEqual(result['indexed_count'], 0)
        self.assertEqual(result['provider_calls'], 1)

    def test_query_forget_during_transport_drops_candidate(self):
        memory = self.active()
        self.index()
        response = self.response([[1., 0.]])
        def perform(*args, **kwargs):
            self.brain.forget(memory['id'], 'Tester', 'Explicit fixture deletion during query')
            return response
        self.opener.open.side_effect = perform
        result = semantic.semantic_candidates(self.brain, 'question', 'alpha')
        self.assertEqual(result['status'], 'ok', result)
        self.assertEqual(result['candidates'], [])

    def test_no_retry_for_timeout_http_redirect_or_connection_failure(self):
        self.active()
        self.index()
        errors = [(TimeoutError('private detail'), 'timeout'),
                  (URLError(TimeoutError('private detail')), 'timeout'),
                  (HTTPError('https://secret.example', 302, 'private detail', {}, None), 'unavailable'),
                  (IncompleteRead(b'private detail'), 'unavailable'),
                  (URLError('private detail'), 'unavailable')]
        for error, expected in errors:
            with self.subTest(expected=expected):
                self.opener.open.reset_mock()
                self.opener.open.side_effect = error
                result = semantic.semantic_candidates(self.brain, 'question', 'alpha')
                self.assertEqual(result['status'], expected, result)
                self.assertEqual(result['provider_calls'], 1)
                self.assertIsNone(result['input_tokens'])
                self.assertNotIn('private', json.dumps(result))
                self.opener.open.assert_called_once()
                self.assertEqual(self.opener.open.call_args.kwargs['timeout'], 2)
        self.assertIsNone(semantic._NoRedirect().redirect_request(None, None, 302, '', {}, 'https://other.test'))

    def test_invalid_response_model_vector_and_index_rejected(self):
        self.active()
        self.index()
        cases = [{'model': 'wrong-model'}, {'data': []},
                 {'data': [{'index': 1, 'embedding': [1, 0]}]},
                 {'data': [{'index': True, 'embedding': [1, 0]}]},
                 {'data': [{'index': 0, 'embedding': [0, 0]}]},
                 {'data': [{'index': 0, 'embedding': [True, 1]}]},
                 {'data': [{'index': 0, 'embedding': [float('nan'), 1]}]},
                 {'data': [{'index': 0, 'embedding': [1] * 3073}]}]
        for updates in cases:
            with self.subTest(updates=str(updates)[:80]):
                self.response([[1, 0]], **updates)
                result = semantic.semantic_candidates(self.brain, 'question', 'alpha')
                self.assertEqual(result['status'], 'invalid_response', result)
                self.assertEqual(result['candidates'], [])
        response = self.response([[1, 0]])
        response.read.return_value = b'x' * (semantic.MAX_RESPONSE_BYTES + 1)
        result = semantic.semantic_candidates(self.brain, 'question', 'alpha')
        self.assertEqual(result['status'], 'invalid_response')
        response.read.assert_called_once_with(semantic.MAX_RESPONSE_BYTES + 1)

    def test_response_order_is_indexed_and_mixed_dimensions_rejected(self):
        config = semantic._load_config(self.brain, 'alpha')
        self.response([], data=[{'index': 1, 'embedding': [0, 1]}, {'index': 0, 'embedding': [1, 0]}])
        self.assertEqual(semantic._embed(config, ['a', 'b'])['vectors'], [[1, 0], [0, 1]])
        for data in [[{'index': 0, 'embedding': [1, 0]}, {'index': 0, 'embedding': [1, 0]}],
                     [{'index': 0, 'embedding': [1]}, {'index': 1, 'embedding': [1, 0]}]]:
            self.response([], data=data)
            with self.assertRaises(semantic._Unavailable):
                semantic._embed(config, ['a', 'b'])

    def test_query_dimension_mismatch_does_not_compare_vectors(self):
        self.active()
        self.index()
        self.response([[1, 0, 0]])
        self.assertEqual(semantic.semantic_candidates(self.brain, 'question', 'alpha')['candidates'], [])

    def test_cosine_is_finite_validated_and_scale_independent(self):
        self.assertAlmostEqual(semantic.cosine([1e308, 1e308], [1, 1]), 1)
        self.assertEqual(semantic.cosine([1, 0], [-1, 0]), -1)
        for left, right in [([], []), ([0], [1]), ([1], [1, 2]), ([float('inf')], [1]),
                            ([True], [1]), ([1] * 3073, [1] * 3073), ([10**999], [1])]:
            with self.assertRaises(ValueError):
                semantic.cosine(left, right)

    def test_current_indexing_is_idempotent_and_batch_limit_checked(self):
        self.active()
        self.index()
        result = semantic.index_memories(self.brain, 'alpha')
        self.assertEqual(result['provider_calls'], 0)
        self.opener.open.assert_not_called()
        for limit in (0, 101, True, '2'):
            self.assertEqual(semantic.index_memories(self.brain, 'alpha', limit=limit)['status'], 'input_limit')

    def test_storage_denial_occurs_before_network_or_table_creation(self):
        self.active()
        with patch.object(self.brain.budget, 'allocation', side_effect=StorageLimitError('Synthetic budget full')):
            result = semantic.index_memories(self.brain, 'alpha')
        self.assertEqual(result['status'], 'storage_limited')
        self.assertEqual(result['provider_calls'], 0)
        self.opener.open.assert_not_called()
        with self.brain._connection() as con:
            self.assertFalse(con.execute("SELECT 1 FROM sqlite_master WHERE name='semantic_vectors'").fetchone())

    def test_configuration_revoked_in_flight_never_saves(self):
        self.active()
        response = self.response([[1, 0]])
        def perform(*args, **kwargs):
            self.configure(enabled=False)
            return response
        self.opener.open.side_effect = perform
        result = semantic.index_memories(self.brain, 'alpha')
        self.assertEqual(result['status'], 'disabled')
        self.assertEqual(result['provider_calls'], 1)
        self.assertEqual(result['indexed_count'], 0)

    def test_invalid_local_index_does_not_trigger_embedding_request(self):
        memory = self.active()
        self.index()
        for dimensions, vector, model in [(2, b'x', 'synthetic-embedding-model'),
                                          (2, struct.pack('<ff', float('nan'), 1), 'synthetic-embedding-model'),
                                          (2, struct.pack('<ff', 1, 0), 'wrong-model'),
                                          (3073, b'x' * 12292, 'synthetic-embedding-model')]:
            with self.brain._write() as con:
                con.execute('UPDATE semantic_vectors SET dimensions=?,vector=?,model=? WHERE memory_id=?',
                            (dimensions, vector, model, memory['id']))
            self.assertEqual(semantic.semantic_candidates(self.brain, 'question', 'alpha')['status'], 'empty_index')
        self.opener.open.assert_not_called()
        self.response([[1, 0]])
        self.assertEqual(semantic.index_memories(self.brain, 'alpha')['indexed_count'], 1)

    def test_missing_or_invalid_usage_stays_unknown(self):
        self.active()
        self.index()
        for usage in (None, {}, {'prompt_tokens': True}, {'prompt_tokens': -1}):
            self.response([[1, 0]], usage=usage)
            result = semantic.semantic_candidates(self.brain, 'question', 'alpha')
            self.assertIsNone(result['input_tokens'])
            self.assertEqual(result['provider_calls'], 1)

    def test_response_and_request_limits_are_enforced(self):
        config = semantic._load_config(self.brain, 'alpha')
        for texts in (['x'] * 101, ['x' * (semantic.MAX_REQUEST_BYTES + 1)]):
            with self.assertRaises(semantic._Unavailable):
                semantic._embed(config, texts)
        self.opener.open.assert_not_called()

    def test_fingerprint_covers_exact_embedded_fields_and_source_hash(self):
        memory = self.active()
        with self.brain._connection() as con:
            row = dict(con.execute('SELECT * FROM memories WHERE id=?', (memory['id'],)).fetchone())
        expected = semantic.fingerprint(row)
        parsed = dict(row, tags=json.loads(row['tags']), episode=json.loads(row['episode']))
        self.assertEqual(semantic.fingerprint(parsed), expected)
        for field, replacement in [('source_hash', 'a' * 64), ('title', 'Different title'),
                                   ('content', 'Different content'), ('tags', '["different"]'),
                                   ('episode', '{"summary":"Different episode"}')]:
            self.assertNotEqual(semantic.fingerprint(dict(row, **{field: replacement})), expected)

    def test_explicit_batches_progress_and_search_caps_candidates_at_sixty(self):
        memories = [self.active(title=f'Synthetic location {i}') for i in range(63)]
        for count in (32, 31):
            self.response([[1, 0]] * count)
            result = semantic.index_memories(self.brain, 'alpha', limit=32)
            self.assertEqual(result['indexed_count'], count, result)
        self.response([[1, 0]])
        result = semantic.semantic_candidates(self.brain, 'unrelated wording', 'alpha')
        self.assertEqual(len(result['candidates']), 60)
        self.assertEqual(result['indexed_count'], 63)
        self.assertEqual([item['id'] for item in result['candidates']], sorted(item['id'] for item in memories)[:60])

    def test_distinct_task_source_verification_is_bounded(self):
        row = {'source': json.dumps({'type': 'task', 'job_id': 'f' * 32}), 'project_id': 'alpha'}
        sources = {(f'{i:032x}', 'alpha'): {} for i in range(128)}
        with patch.object(self.brain, '_current') as current:
            self.assertFalse(semantic._current(self.brain, row, '2026-09-01T00:00:00Z', sources))
            current.assert_not_called()
        row['source'] = json.dumps({'type': 'task', 'job_id': f'{1:032x}'})
        with patch.object(self.brain, '_current', return_value=True) as current:
            self.assertTrue(semantic._current(self.brain, row, '2026-09-01T00:00:00Z', sources))
            current.assert_called_once()

    def test_lookup_consumes_shared_source_budget_and_revalidates_existing_keys(self):
        memories = {self.task_memory(job)['id']: job for job in ('a' * 32, 'b' * 32)}
        self.response([[1, 0], [1, 0]])
        self.assertEqual(semantic.index_memories(self.brain, 'alpha')['indexed_count'], 2)
        self.response([[1, 0]])
        keys = {(f'{i:032x}', 'alpha') for i in range(127)}
        with patch.object(self.brain, '_task_source_proofs', wraps=self.brain._task_source_proofs) as proofs:
            result = semantic.semantic_candidates(self.brain, 'question', 'alpha', source_keys=keys)
        self.assertEqual(len(keys), 128)
        self.assertTrue(result['scan_limited'])
        self.assertEqual(len(result['candidates']), 1)
        allowed_job = memories[result['candidates'][0]['id']]
        self.assertIn((allowed_job, 'alpha'), keys)
        self.assertEqual([call.args[0] for call in proofs.call_args_list], [allowed_job, allowed_job])
        self.assertIn('additional', result['reason'])

    def test_exhausted_shared_budget_reports_incomplete_without_network(self):
        self.task_memory('a' * 32)
        self.index()
        keys = {(f'{i:032x}', 'alpha') for i in range(128)}
        with patch.object(self.brain, '_task_source_proofs') as proofs:
            result = semantic.semantic_candidates(self.brain, 'question', 'alpha', source_keys=keys)
        self.assertEqual(result['provider_calls'], 0)
        self.assertTrue(result['scan_limited'])
        self.assertEqual(result['candidates'], [])
        self.assertIn('bound', result['reason'])
        self.opener.open.assert_not_called()
        proofs.assert_not_called()
        self.assertEqual(len(keys), 128)

    def test_changed_task_after_network_cannot_reset_shared_budget(self):
        memory = self.task_memory('a' * 32)
        self.index()
        response = self.response([[1, 0]])
        keys = {(f'{i:032x}', 'alpha') for i in range(127)}
        def perform(*args, **kwargs):
            with self.brain._write() as con:
                con.execute('UPDATE memories SET source=? WHERE id=?',
                            (json.dumps({'type': 'task', 'job_id': 'b' * 32}), memory['id']))
            return response
        self.opener.open.side_effect = perform
        with patch.object(self.brain, '_task_source_proofs', wraps=self.brain._task_source_proofs) as proofs:
            result = semantic.semantic_candidates(self.brain, 'question', 'alpha', source_keys=keys)
        self.assertEqual(len(keys), 128)
        self.assertIn(('a' * 32, 'alpha'), keys)
        self.assertNotIn(('b' * 32, 'alpha'), keys)
        self.assertEqual(result['provider_calls'], 1)
        self.assertTrue(result['scan_limited'])
        self.assertEqual(result['candidates'], [])
        proofs.assert_called_once()

    def test_invalid_shared_budget_is_rejected_before_network(self):
        self.active()
        self.index()
        for keys in ([], set(range(3)), {(f'{i:032x}', 'alpha') for i in range(129)}):
            result = semantic.semantic_candidates(self.brain, 'question', 'alpha', source_keys=keys)
            self.assertEqual(result['status'], 'input_limit')
            self.assertEqual(result['provider_calls'], 0)
        self.opener.open.assert_not_called()


if __name__ == '__main__':
    unittest.main()
