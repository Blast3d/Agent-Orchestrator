"""Synthetic SQLite races and routing checks; no live provider requests."""
import contextlib
import io
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'app'))
from brain_store import BrainStore
from brain_retrieval_metadata import public_retrieval
from jev_routing import advise
from jev_cli import main


class JevIntegrationTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.brain = BrainStore(self.root)
        self.config = {'status': 'ready', 'key_present': True, 'authorized_projects': ['alpha'],
                       'purposes': ['memory_rank', 'route'], 'min_confidence': .8}
        mocked = patch('jev_openrouter.load_config', return_value=self.config)
        mocked.start()
        self.addCleanup(mocked.stop)

    def memory(self, content='Retry policy uses bounded backoff.', **extra):
        candidate = dict(project_id='alpha', kind='fact', title='Retry policy', content=content,
                         source={'type': 'user', 'note': 'Synthetic test source.'})
        candidate.update(extra)
        item = self.brain.propose(candidate)
        return self.brain.approve(item['id'], 'Tester', 'Checked synthetic source.')

    def response(self, count=2, confidence=1):
        return {'status': 'ok', 'reason': 'Synthetic scores.',
                'answers': {'memory_' + str(i): {'type': 'score', 'score': i + 1,
                           'confidence': confidence} for i in range(count)},
                'model': 'synthetic', 'provider': 'TypeSafe', 'request_id': 'test-request',
                'input_tokens': 10, 'output_tokens': 5, 'cost_usd': .001,
                'provider_calls': 1, 'elapsed_ms': 1}

    def test_disabled_scope_missing_key_and_empty_do_not_call(self):
        with patch('jev_openrouter.evaluate') as evaluate:
            self.brain.search('retry', 'alpha')
            self.memory()
            for change in ({'status': 'disabled'}, {'key_present': False},
                           {'authorized_projects': ['other']}, {'purposes': ['route']}):
                with patch('jev_openrouter.load_config', return_value=dict(self.config, **change)):
                    self.assertTrue(self.brain.search('retry', 'alpha')['results'])
        evaluate.assert_not_called()

    def test_ranks_existing_memories_and_updates_trace(self):
        self.memory()
        self.memory('Retry policy explains recovery choices.')
        seen = []
        def provider(root, project, purpose, state, questions, **kwargs):
            seen.extend(state['candidates'])
            self.assertEqual(purpose, 'memory_rank')
            self.assertEqual(project, 'alpha')
            self.assertEqual(set(state), {'query', 'candidates', 'profile', 'role_needs'})
            return self.response()
        with patch('jev_openrouter.evaluate', side_effect=provider):
            result = self.brain.search('retry', 'alpha')
        ids = [r['id'] for r in result['results']]
        self.assertEqual(ids, [seen[1]['id'], seen[0]['id']])
        self.assertTrue(result['retrieval']['jev']['applied'])
        self.assertLessEqual(result['context_chars'], 8000)
        with self.brain._connection() as con:
            row = con.execute('SELECT * FROM traces WHERE id=?', (result['trace_id'],)).fetchone()
            saved = con.execute('SELECT detail FROM retrieval_traces WHERE trace_id=?', (result['trace_id'],)).fetchone()
        self.assertEqual(json.loads(row['memory_ids']), ids)
        self.assertTrue(json.loads(saved['detail'])['jev']['applied'])
        self.assertEqual(public_retrieval(result['retrieval'])['jev']['cost_usd'], .001)

    def test_low_confidence_and_failure_preserve_order(self):
        self.memory()
        self.memory('Retry policy explains recovery choices.')
        with patch('jev_openrouter.load_config', return_value={'status': 'disabled'}):
            baseline = self.brain.search('retry', 'alpha', record_trace=False)
        for response in (self.response(confidence=.3), {'status': 'error', 'reason': 'Unavailable.',
                                                       'answers': {}, 'provider_calls': 1}):
            with patch('jev_openrouter.evaluate', return_value=response):
                result = self.brain.search('retry', 'alpha', record_trace=False)
            self.assertEqual([r['id'] for r in baseline['results']], [r['id'] for r in result['results']])
            self.assertFalse(result['retrieval']['jev']['applied'])

    def test_forget_during_call_is_not_locked_and_never_returns_content(self):
        item = self.memory()
        def provider(*args, **kwargs):
            self.brain.forget(item['id'], 'Tester', 'Synthetic concurrent forgetting.')
            return self.response(count=1)
        with patch('jev_openrouter.evaluate', side_effect=provider):
            result = self.brain.search('retry', 'alpha')
        self.assertEqual(result['results'], [])
        self.assertNotIn(item['content'], result['context'])

    def test_content_scope_or_validity_change_during_request_excludes(self):
        for field, value in [('content', 'Changed after scoring.'), ('project_id', 'beta'),
                             ('valid_to', '2020-01-01T00:00:00Z')]:
            item = self.memory('Retry ' + field)
            def provider(*args, **kwargs):
                with self.brain._connection() as con:
                    con.execute('UPDATE memories SET ' + field + '=? WHERE id=?', (value, item['id']))
                    con.commit()
                return self.response(count=1)
            with patch('jev_openrouter.evaluate', side_effect=provider):
                result = self.brain.search(field, 'alpha', record_trace=False)
            self.assertNotIn(item['id'], [r['id'] for r in result['results']])

    def test_truncated_valid_content_can_be_scored(self):
        self.memory('Retry policy. ' * 180)
        with patch('jev_openrouter.evaluate', return_value=self.response(count=1)) as evaluate:
            result = self.brain.search('retry', 'alpha', max_chars=1200)
        evaluate.assert_called_once()
        self.assertEqual(len(result['results']), 1)
        self.assertLessEqual(result['context_chars'], 1200)

    def test_route_is_advisory_and_only_named_worker_is_recommended(self):
        response = {'status': 'ok', 'answers': {'worker': {'choice': 'reviewer', 'confidence': .9}}}
        with patch('jev_openrouter.evaluate', return_value=response):
            result = advise(self.root, 'alpha', 'Review a patch.', {'reviewer': 'Inspect code.'})
        self.assertEqual(result['recommended_worker'], 'reviewer')
        self.assertTrue(result['advisory_only'])
        for choice, confidence in [('reviewer', .2), ('unknown', 1), ('defer', 1)]:
            response['answers']['worker'].update(choice=choice, confidence=confidence)
            with patch('jev_openrouter.evaluate', return_value=response):
                result = advise(self.root, 'alpha', 'Review a patch.', {'reviewer': 'Inspect code.'})
            self.assertIsNone(result['recommended_worker'])

    def test_forgotten_seed_cannot_leave_orphaned_graph_result(self):
        seed = self.memory()
        linked = self.memory('Independent supporting detail.', title='Neighbor')
        self.brain.relate(seed['id'], linked['id'], 'supports', 'Tester')
        def provider(*args, **kwargs):
            self.brain.forget(seed['id'], 'Tester', 'Synthetic graph seed forgotten.')
            return self.response(count=2)
        with patch('jev_openrouter.evaluate', side_effect=provider):
            result = self.brain.search('retry', 'alpha', strategy='graph')
        self.assertEqual(result['results'], [])
        self.assertNotIn(seed['id'], result['context'])

    def test_original_omission_evidence_survives_reranking(self):
        from brain_jev import maybe_rank
        self.memory()
        with patch('jev_openrouter.load_config', return_value={'status': 'disabled'}):
            baseline = self.brain.search('retry', 'alpha', record_trace=False)
        baseline['context_omitted_ids'] = ['previously-omitted']
        with patch('jev_openrouter.evaluate', return_value=self.response(count=1)):
            result = maybe_rank(self.brain, baseline, record_trace=False)
        self.assertIn('previously-omitted', result['context_omitted_ids'])

    def test_invalid_config_does_not_replace_valid_file(self):
        target = self.root / 'runtime/jev-config.json'
        target.write_text('{"schema_version":1,"enabled":false}', encoding='utf-8')
        original = target.read_bytes()
        with contextlib.redirect_stdout(io.StringIO()):
            result = main(['--root', str(self.root), 'configure', '--project', 'alpha',
                          '--purpose', 'route', '--enable', '--env-file', '../outside.env'])
        self.assertEqual(result, 2)
        self.assertEqual(target.read_bytes(), original)

    def test_route_rejects_bad_input_before_network(self):
        with patch('jev_openrouter.evaluate') as evaluate:
            for workers in ({}, {'defer': 'Reserved'}, {'a': ''}, ['a']):
                with self.assertRaises(ValueError):
                    advise(self.root, 'alpha', 'Review.', workers)
        evaluate.assert_not_called()

    def test_configure_stays_disabled_unless_explicitly_enabled(self):
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(main(['--root', str(self.root), 'configure', '--project', 'alpha',
                                   '--purpose', 'memory_rank']), 0)
        saved = json.loads((self.root / 'runtime/jev-config.json').read_text())
        self.assertFalse(saved['enabled'])
        self.assertNotIn('api_key', saved)
        self.assertEqual(saved['authorized_projects'], ['alpha'])


class LocalOnlySearchTests(unittest.TestCase):
    def test_explicit_local_only_search_skips_jev_ranking(self):
        import tempfile
        from unittest.mock import patch
        from brain_store import BrainStore
        with tempfile.TemporaryDirectory() as folder:
            store = BrainStore(Path(folder))
            with patch('brain_jev.maybe_rank', side_effect=AssertionError('a local-only search must not call Jev')):
                result = store.search('anything', 'alpha', strategy='keyword', record_trace=False, jev=False)
            self.assertEqual(result['jev_ranking'], {'status': 'skipped', 'reason': 'local-only search requested'})
            # Keyword retrieval is only the candidate method; Jev still ranks it unless opted out.
            with patch('brain_jev.maybe_rank', side_effect=lambda store, result, **kw: dict(result, ranked=True)) as rank:
                self.assertTrue(store.search('anything', 'alpha', strategy='keyword', record_trace=False)['ranked'])
            rank.assert_called_once()


if __name__ == '__main__':
    unittest.main()
