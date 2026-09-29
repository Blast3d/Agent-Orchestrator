"""Coverage for B05/B06/B07; never calls a live provider."""
import json
from pathlib import Path
import sqlite3
import sys
import tempfile
import time
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'app'))
import brain_semantic as semantic
import jev_cache
from brain_store import BrainStore
from usage_guard import file_lock, write_json


class SemanticCacheFixTests(unittest.TestCase):
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
        value = {'enabled': True, 'endpoint': 'https://embeddings.example/v1/embeddings',
                 'model': 'synthetic-embedding-model', 'api_key_env': 'SYNTHETIC_EMBED_KEY',
                 'authorized_projects': ['alpha'], 'timeout_seconds': 2, 'similarity_threshold': .65}
        (self.brain.home / 'semantic-config.json').write_text(json.dumps(value), encoding='utf-8')

    def _active(self, **updates):
        payload = {'project_id': 'alpha', 'kind': 'fact', 'title': 'Orchid location',
                   'content': 'The orchid nursery is on Cedar Street.', 'tags': ['garden'],
                   'source': {'type': 'user', 'note': 'Synthetic approved fixture'}}
        payload.update(updates)
        item = self.brain.propose(payload)
        return self.brain.approve(item['id'], 'Tester', 'Verified against the synthetic fixture.')

    def _response(self, vectors):
        data = {'model': 'synthetic-embedding-model',
                'data': [{'index': i, 'embedding': v} for i, v in enumerate(vectors)],
                'usage': {'prompt_tokens': 11}}
        response = Mock(status=200)
        response.read.return_value = json.dumps(data).encode()
        self.opener.open.return_value = response
        self.opener.open.side_effect = None
        return response

    def test_index_skips_sensitive_memory_without_network(self):
        self._active(content='Store this api_key=sk-live-0123456789abcdef0123 locally.')
        result = semantic.index_memories(self.brain, 'alpha')
        self.opener.open.assert_not_called()
        self.assertEqual(result['provider_calls'], 0)
        self.assertGreaterEqual(result.get('skipped_sensitive', 0), 1)
        self.assertIn(result['status'], ('empty_scope', 'ok', 'indexed'))
        self.assertNotEqual(result['status'], 'indexed')

    def test_retrieval_blocks_sensitive_query_without_network(self):
        self._active()
        self._response([[1., 0.]])
        indexed = semantic.index_memories(self.brain, 'alpha')
        self.assertEqual(indexed['status'], 'indexed')
        self.opener.open.reset_mock()
        result = semantic.semantic_candidates(
            self.brain, 'rotate api_key=sk-live-0123456789abcdef0123', 'alpha')
        self.opener.open.assert_not_called()
        self.assertEqual(result['status'], 'sensitive_input')
        self.assertEqual(result['provider_calls'], 0)
        self.assertEqual(result['candidates'], [])

    def test_index_skips_current_vector_beyond_a_thousand_scope_rows(self):
        self._active(title='Keep me')
        self._response([[1., 0.]])
        self.assertEqual(semantic.index_memories(self.brain, 'alpha')['status'], 'indexed')
        columns = ('memory_id', 'project_id', 'user_id', 'config_id', 'model', 'fingerprint',
                   'dimensions', 'vector', 'indexed_at')
        insert = ('INSERT INTO semantic_vectors(' + ','.join(columns) + ') VALUES('
                  + ','.join('?' * len(columns)) + ')')
        with file_lock(self.brain.lock), self.brain._connection() as con:
            con.execute('PRAGMA foreign_keys=OFF')
            real = dict(con.execute('SELECT * FROM semantic_vectors').fetchone())
            ghosts = [tuple(dict(real, memory_id='ghost-%04d' % i, fingerprint='stale')[c] for c in columns)
                      for i in range(1001)]
            con.executemany(insert, ghosts)
            # Put the real vector after the ghosts in scan order, beyond an unordered LIMIT 1000.
            con.execute('DELETE FROM semantic_vectors WHERE memory_id=?', (real['memory_id'],))
            con.execute(insert, tuple(real[c] for c in columns))
            con.commit()
        self.opener.open.reset_mock()
        self._active(title='Second note', content='Another garden fact.')
        self._response([[0., 1.]])
        result = semantic.index_memories(self.brain, 'alpha')
        self.assertEqual(result['status'], 'indexed', result)
        self.assertEqual((result['selected_count'], result['indexed_count']), (1, 1))

    def test_future_cache_entry_does_not_disable_put_or_get(self):
        from jev_openrouter import _path
        path = _path(self.root, 'jev-cache.json')
        good_key = 'a' * 64
        bad_key = 'b' * 64
        now = time.time()
        write_json(path, {'schema_version': 1, 'entries': {
            good_key: {'created': now - 10, 'response': {'model': 'x'}},
            bad_key: {'created': now + 3600, 'response': {'model': 'y'}},
        }})
        entries = jev_cache._entries(self.root)
        self.assertIn(good_key, entries)
        self.assertNotIn(bad_key, entries)
        jev_cache.put(self.root, 'c' * 64, {'model': 'z'}, ttl=3600)
        rebuilt = jev_cache._entries(self.root)
        self.assertIn(good_key, rebuilt)
        self.assertIn('c' * 64, rebuilt)
        self.assertNotIn(bad_key, rebuilt)
