"""Decision cache and transport reuse tests; no live provider."""
from copy import deepcopy
import json
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import patch
from urllib.error import HTTPError, URLError
from io import BytesIO

import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'app'))
import jev_openrouter as jev
import jev_cache
from storage_budget import StorageLimitError


QUESTIONS = {
    'team': {'type': 'choice', 'instructions': 'Which team?',
             'criteria': {'alpha': 'First team', 'beta': 'Second team'}},
    'relevance': {'type': 'score', 'instructions': 'How relevant?',
                  'criteria': ['Irrelevant', 'Related', 'Directly useful']},
    'supported': {'type': 'noul', 'instructions': 'Is this supported?'},
}
RESPONSE = {
    'model': 'typesafe/jev-1.13-20260917', 'provider': 'TypeSafe', 'id': 'gen-dec-fixture-1',
    'answers': {
        'team': {'type': 'choice', 'choice': 'alpha',
                 'probabilities': {'alpha': .9, 'beta': .1}, 'confidence': .8},
        'relevance': {'type': 'score', 'score': 1.8,
                      'probabilities': {'0': 0, '1': .2, '2': .8}, 'confidence': .7,
                      'legend': {'0': 'Irrelevant', '1': 'Related', '2': 'Directly useful'}},
        'supported': {'type': 'noul', 'noul': .96},
    },
    'usage': {'input_tokens': 357, 'output_tokens': 38, 'cost': .000014994},
}
STATE = {'task': 'rank memories', 'candidates': ['note-a', 'note-b']}


def _config(**overrides):
    base = {
        'status': 'ready',
        'reason': '',
        'authorized_projects': ['agent-orchestrator', 'openwhispr'],
        'purposes': ['memory_rank', 'route'],
        'min_confidence': 0.8,
        'cache_enabled': True,
        'cache_ttl_seconds': 3600,
        'timeout_seconds': 30,
        'max_requests_per_day': 100,
    }
    base.update(overrides)
    return base


class JevCacheTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        (self.root / 'runtime').mkdir()
        self.posts = []

        def fake_post(raw, api_key, timeout):
            self.posts.append({'raw': raw, 'key': api_key, 'timeout': timeout})
            return json.dumps(RESPONSE).encode()

        self._post_cm = patch.object(jev, '_post', side_effect=fake_post)
        self._key_cm = patch.object(jev, '_key', return_value='test-not-a-real-key')
        self._cfg_cm = patch.object(jev, 'load_config', return_value=_config())
        self._rsv_cm = patch.object(jev, '_reserve_request')
        self._post_cm.start()
        self._key_cm.start()
        self._cfg_cm.start()
        self._rsv_cm.start()

    def tearDown(self):
        self._post_cm.stop()
        self._key_cm.stop()
        self._cfg_cm.stop()
        self._rsv_cm.stop()
        self._tmp.cleanup()

    def _evaluate(self, **kwargs):
        args = {
            'root': self.root,
            'project_id': 'agent-orchestrator',
            'purpose': 'memory_rank',
            'state': STATE,
            'questions': QUESTIONS,
            'user_id': 'local',
            'cache_context': {'shortlist': ['id-1', 'id-2']},
        }
        args.update(kwargs)
        return jev.evaluate(**args)

    def test_identical_inputs_reuse_validated_decision_without_new_usage(self):
        first = self._evaluate()
        self.assertEqual(first['status'], 'ok')
        self.assertEqual(first['cache'].get('status'), 'miss')
        self.assertTrue(first['cache'].get('stored'))
        self.assertEqual(first['provider_calls'], 1)
        self.assertEqual(first['input_tokens'], 357)
        self.assertEqual(first['output_tokens'], 38)
        self.assertEqual(first['cost_usd'], 0.000014994)
        self.assertEqual(len(self.posts), 1)

        second = self._evaluate()
        self.assertEqual(second['status'], 'ok')
        self.assertEqual(second['cache']['status'], 'hit')
        self.assertEqual(second['provider_calls'], 0)
        self.assertEqual(second['input_tokens'], 0)
        self.assertEqual(second['output_tokens'], 0)
        self.assertEqual(second['cost_usd'], 0)
        self.assertEqual(second['request_id'], None)
        origin = second['cache']['origin_usage']
        self.assertEqual(origin['input_tokens'], 357)
        self.assertEqual(origin['output_tokens'], 38)
        self.assertEqual(origin['cost_usd'], 0.000014994)
        self.assertEqual(origin['request_id'], 'gen-dec-fixture-1')
        self.assertIn('caller must revalidate source evidence', second['reason'])
        self.assertEqual(second['answers']['team']['choice'], 'alpha')
        self.assertEqual(len(self.posts), 1)

    def test_changed_project_user_purpose_questions_context_or_min_confidence_miss(self):
        self.assertEqual(self._evaluate()['cache']['status'], 'miss')
        self.assertEqual(len(self.posts), 1)

        other_project = self._evaluate(project_id='openwhispr')
        self.assertEqual(other_project['cache']['status'], 'miss')
        self.assertEqual(other_project['provider_calls'], 1)

        other_user = self._evaluate(user_id='reviewer')
        self.assertEqual(other_user['cache']['status'], 'miss')

        other_purpose = self._evaluate(purpose='route')
        self.assertEqual(other_purpose['cache']['status'], 'miss')

        q = deepcopy(QUESTIONS)
        q['team']['instructions'] = 'Which team owns this?'
        other_q = self._evaluate(questions=q)
        self.assertEqual(other_q['cache']['status'], 'miss')

        other_ctx = self._evaluate(cache_context={'shortlist': ['id-9']})
        self.assertEqual(other_ctx['cache']['status'], 'miss')

        self._cfg_cm.stop()
        with patch.object(jev, 'load_config', return_value=_config(min_confidence=0.5)):
            other_policy = self._evaluate()
        self._cfg_cm = patch.object(jev, 'load_config', return_value=_config())
        self._cfg_cm.start()
        self.assertEqual(other_policy['cache']['status'], 'miss')
        self.assertEqual(len(self.posts), 7)

    def test_unauthorized_project_or_purpose_never_reads_or_writes_cache(self):
        seeded = self._evaluate()
        self.assertEqual(seeded['status'], 'ok')
        posts_after_seed = len(self.posts)

        denied = self._evaluate(project_id='not-authorized')
        self.assertEqual(denied['status'], 'unavailable')
        self.assertEqual(denied['cache'], {'status': 'disabled'})
        self.assertEqual(denied['provider_calls'], 0)
        self.assertEqual(len(self.posts), posts_after_seed)

        denied_purpose = self._evaluate(purpose='not-a-purpose')
        self.assertEqual(denied_purpose['status'], 'unavailable')
        self.assertEqual(denied_purpose['cache'], {'status': 'disabled'})
        self.assertEqual(len(self.posts), posts_after_seed)

    def test_disabled_or_missing_cache_flag_skips_reuse(self):
        self._evaluate()
        self.assertEqual(len(self.posts), 1)
        self._cfg_cm.stop()
        with patch.object(jev, 'load_config', return_value=_config(cache_enabled=False)):
            disabled = self._evaluate()
        self.assertEqual(disabled['cache'], {'status': 'disabled'})
        self.assertEqual(disabled['provider_calls'], 1)
        self.assertEqual(len(self.posts), 2)

        cfg = _config()
        del cfg['cache_enabled']
        with patch.object(jev, 'load_config', return_value=cfg):
            missing = self._evaluate()
        self.assertEqual(missing['cache'], {'status': 'disabled'})
        self.assertEqual(missing['provider_calls'], 1)
        self.assertEqual(len(self.posts), 3)
        self._cfg_cm = patch.object(jev, 'load_config', return_value=_config())
        self._cfg_cm.start()

    def test_malformed_cache_entry_is_ignored_and_does_not_leak_payload_text(self):
        secret = 'UNTRUSTED-EVIDENCE-SHOULD-NOT-SURFACE'
        cache_path = self.root / 'runtime' / 'jev-cache.json'
        cache_path.write_text(json.dumps({
            'schema_version': 1,
            'entries': {'not-a-row': secret},
        }), encoding='utf-8')
        result = self._evaluate()
        # Invalid entries are dropped one by one (audit B07); the file stays usable.
        self.assertEqual(result['cache']['status'], 'miss')
        self.assertNotIn(secret, json.dumps(result))
        self.assertEqual(result['status'], 'ok')
        self.assertEqual(len(self.posts), 1)

    def test_invalid_schema_or_oversize_entry_count_fails_closed(self):
        bloated = {str(i): {'created': time.time(), 'response': RESPONSE} for i in range(jev_cache.MAX_ENTRIES + 1)}
        (self.root / 'runtime' / 'jev-cache.json').write_text(json.dumps({
            'schema_version': 1, 'entries': bloated,
        }), encoding='utf-8')
        result = self._evaluate()
        self.assertEqual(result['cache']['status'], 'unavailable')

        (self.root / 'runtime' / 'jev-cache.json').write_text(json.dumps({
            'schema_version': 99, 'entries': {},
        }), encoding='utf-8')
        result = self._evaluate()
        self.assertEqual(result['cache']['status'], 'unavailable')

    def test_ttl_expiration_forces_new_provider_call(self):
        first = self._evaluate()
        self.assertEqual(first['cache']['status'], 'miss')
        path = self.root / 'runtime' / 'jev-cache.json'
        payload = json.loads(path.read_text(encoding='utf-8'))
        for row in payload['entries'].values():
            row['created'] = time.time() - 10
        path.write_text(json.dumps(payload), encoding='utf-8')

        self._cfg_cm.stop()
        with patch.object(jev, 'load_config', return_value=_config(cache_ttl_seconds=1)):
            expired = self._evaluate()
        self._cfg_cm = patch.object(jev, 'load_config', return_value=_config())
        self._cfg_cm.start()
        self.assertEqual(expired['cache']['status'], 'miss')
        self.assertEqual(expired['provider_calls'], 1)
        self.assertEqual(len(self.posts), 2)

    def test_put_evicts_oldest_beyond_max_entries(self):
        ttl = 3600
        at = time.time()
        entries = {}
        for i in range(jev_cache.MAX_ENTRIES):
            entries[f'k{i:03d}'] = {'created': at - (jev_cache.MAX_ENTRIES - i), 'response': {'id': f'old-{i}'}}
        (self.root / 'runtime' / 'jev-cache.json').write_text(json.dumps({
            'schema_version': jev_cache.REVISION, 'entries': entries,
        }), encoding='utf-8')
        jev_cache.put(self.root, 'fresh-key', RESPONSE, ttl)
        stored = json.loads((self.root / 'runtime' / 'jev-cache.json').read_text(encoding='utf-8'))['entries']
        self.assertLessEqual(len(stored), jev_cache.MAX_ENTRIES)
        self.assertIn('fresh-key', stored)
        self.assertNotIn('k000', stored)

    def test_persisted_cache_contains_no_request_state_or_evidence(self):
        evidence = 'quoted memory body that must not be stored'
        self._evaluate(state={'task': 'rank', 'excerpt': evidence}, cache_context={'note': evidence})
        raw = (self.root / 'runtime' / 'jev-cache.json').read_text(encoding='utf-8')
        self.assertNotIn(evidence, raw)
        self.assertNotIn('excerpt', raw)
        blob = json.loads(raw)
        self.assertEqual(set(blob), {'schema_version', 'entries'})
        for row in blob['entries'].values():
            self.assertEqual(set(row), {'created', 'response'})
            self.assertNotIn('request', row)
            self.assertNotIn('state', row)

    def test_http_and_transport_failures_are_not_cached(self):
        def boom(raw, api_key, timeout):
            raise HTTPError('https://example.invalid', 502, 'bad', hdrs=None, fp=BytesIO())

        with patch.object(jev, '_post', side_effect=boom):
            failed = self._evaluate()
        self.assertEqual(failed['status'], 'error')
        self.assertEqual(failed['http_status'], 502)
        self.assertFalse((self.root / 'runtime' / 'jev-cache.json').exists())

        with patch.object(jev, '_post', side_effect=URLError('offline')):
            failed2 = self._evaluate()
        self.assertEqual(failed2['status'], 'error')
        self.assertFalse((self.root / 'runtime' / 'jev-cache.json').exists())

    def test_invalid_provider_envelope_is_not_cached(self):
        with patch.object(jev, '_post', return_value=b'{"not":"a decision"}'):
            bad = self._evaluate()
        self.assertEqual(bad['status'], 'invalid_response')
        self.assertFalse((self.root / 'runtime' / 'jev-cache.json').exists())

    def test_sensitive_input_blocks_before_network_and_cache(self):
        blocked = {
            'blocked': True,
            'findings': [{'kind': 'secret', 'path': 'state.token'}],
        }
        with patch('jev_prechecks.scan_sensitive', return_value=blocked):
            result = self._evaluate(state={'token': 'sk-live-fixture'})
        self.assertEqual(result['status'], 'sensitive_input')
        self.assertEqual(result['sensitivity']['blocked'], True)
        self.assertEqual(len(self.posts), 0)
        self.assertFalse((self.root / 'runtime' / 'jev-cache.json').exists())
        self.assertEqual(result['cache'], {'status': 'disabled'})

    def test_identity_hashes_request_bytes_not_plaintext_payload(self):
        raw = jev._request(STATE, QUESTIONS)
        key = jev_cache.identity(self.root, 'agent-orchestrator', 'local', 'memory_rank',
                                 raw, {'c': 1}, {'min_confidence': 0.8})
        self.assertEqual(len(key), 64)
        self.assertNotIn('rank memories', key)

    def test_cache_hit_does_not_claim_sqlite_or_source_revalidation(self):
        self._evaluate()
        hit = self._evaluate()
        dumped = json.dumps(hit)
        self.assertNotIn('sqlite', dumped.lower())
        self.assertIn('caller must revalidate source evidence', hit['reason'])

    def test_storage_limit_on_put_records_not_stored(self):
        def exploding_put(*args, **kwargs):
            raise StorageLimitError('full')

        with patch.object(jev_cache, 'put', side_effect=exploding_put):
            # First call still posts; put failure is non-fatal.
            result = self._evaluate()
        self.assertEqual(result['status'], 'ok')
        self.assertEqual(result['cache']['status'], 'miss')
        self.assertIs(result['cache'].get('stored'), False)


if __name__ == '__main__':
    unittest.main()
