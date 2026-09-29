"""Offline Jev contract, scope, accounting, and transport tests; no live API."""
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from io import BytesIO
import json
import os
from pathlib import Path
import stat
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import MagicMock, patch
from urllib.error import HTTPError, URLError
from urllib.request import Request

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'app'))
import jev_openrouter as jev
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


class JevTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.runtime = self.root / 'runtime'
        self.runtime.mkdir()
        env = patch.dict('os.environ', {'OPENROUTER_API_KEY': 'synthetic-secret'}, clear=True)
        env.start()
        self.addCleanup(env.stop)
        self.network_patch = patch.object(jev, '_post', return_value=json.dumps(RESPONSE).encode())
        self.post = self.network_patch.start()
        self.addCleanup(self.network_patch.stop)

    def configure(self, **updates):
        value = {'schema_version': 1, 'enabled': True, 'model': jev.MODEL,
                 'authorized_projects': ['alpha'], 'purposes': ['memory_rank', 'route']}
        value.update(updates)
        (self.runtime / 'jev-config.json').write_text(json.dumps(value), encoding='utf-8')
        return value

    def evaluate(self, **updates):
        args = {'root': self.root, 'project_id': 'alpha', 'purpose': 'memory_rank',
                'state': {'text': 'Synthetic authorized material'}, 'questions': deepcopy(QUESTIONS)}
        args.update(updates)
        return jev.evaluate(**args)

    def reply(self, value):
        self.post.return_value = json.dumps(value).encode()

    def assert_held(self, result, status=None):
        if status:
            self.assertEqual(result['status'], status)
        self.assertNotEqual(result['status'], 'ok')
        self.assertEqual(result['provider_calls'], 0)
        self.assertEqual(result['answers'], {})
        self.post.assert_not_called()

    def test_unconfigured_is_disabled_without_creating_files(self):
        self.assert_held(self.evaluate(), 'disabled')
        self.assertEqual(list(self.runtime.iterdir()), [])

    def test_disabled_never_reads_secret_or_contacts_provider(self):
        self.configure(enabled=False)
        with patch.object(jev, '_key', side_effect=AssertionError('No secret lookup')):
            self.assert_held(self.evaluate(), 'disabled')

    def test_missing_credentials_are_unavailable(self):
        self.configure()
        with patch.dict('os.environ', {}, clear=True):
            self.assert_held(self.evaluate(), 'unavailable')
        self.assertFalse((self.runtime / 'jev-usage.json').exists())

    def test_config_status_contains_no_secret(self):
        self.configure()
        config = jev.load_config(self.root)
        self.assertEqual(config['status'], 'ready')
        self.assertEqual(config['max_requests_per_day'], 100)
        self.assertEqual(config['min_confidence'], .8)
        self.assertTrue(config['key_present'])
        self.assertNotIn('synthetic-secret', json.dumps(config))

    def test_literal_dotenv_with_bom_quotes_comments_and_no_environment_mutation(self):
        self.configure()
        for literal in ('OPENROUTER_API_KEY=fixture-dotenv',
                        '  OPENROUTER_API_KEY = fixture-dotenv  # comment',
                        'OPENROUTER_API_KEY="fixture-dotenv" # comment',
                        "OPENROUTER_API_KEY='fixture-dotenv'",):
            with self.subTest(literal=literal):
                (self.root / '.env').write_text('# local settings\nOTHER=value\n' + literal,
                                               encoding='utf-8-sig')
                with patch.dict('os.environ', {}, clear=True):
                    config = jev.load_config(self.root)
                    result = self.evaluate()
                    self.assertNotIn('OPENROUTER_API_KEY', os.environ)
                self.assertTrue(config['key_present'])
                self.assertEqual(result['status'], 'ok')
                self.assertEqual(self.post.call_args.args[1], 'fixture-dotenv')
                self.assertNotIn('fixture-dotenv', json.dumps(config))
                self.assertNotIn('fixture-dotenv', json.dumps(result))

    def test_process_credential_takes_precedence_without_reading_dotenv(self):
        self.configure()
        (self.root / '.env').write_text('OPENROUTER_API_KEY=fixture-dotenv', encoding='utf-8')
        with patch.object(jev, '_dotenv_key', side_effect=AssertionError('No file lookup')):
            self.assertEqual(self.evaluate()['status'], 'ok')
        self.assertEqual(self.post.call_args.args[1], 'synthetic-secret')

    def test_dotenv_is_literal_and_reads_only_configured_key(self):
        self.configure(api_key_env='CUSTOM_JEV_KEY')
        (self.root / '.env').write_text('OPENROUTER_API_KEY=wrong\n'
                                       'UNRELATED=$(do-not-execute)\n'
                                       'CUSTOM_JEV_KEY=$LITERAL-$(not-executed)\n', encoding='utf-8')
        with patch.dict('os.environ', {}, clear=True):
            self.assertEqual(self.evaluate()['status'], 'ok')
            self.assertEqual(self.post.call_args.args[1], '$LITERAL-$(not-executed)')
            self.assertEqual(dict(os.environ), {})

    def test_empty_process_key_uses_dotenv(self):
        self.configure()
        (self.root / '.env').write_text('OPENROUTER_API_KEY=fixture-dotenv', encoding='utf-8')
        with patch.dict('os.environ', {'OPENROUTER_API_KEY': ''}, clear=True):
            self.assertEqual(self.evaluate()['status'], 'ok')
        self.assertEqual(self.post.call_args.args[1], 'fixture-dotenv')

    def test_invalid_nonempty_process_key_does_not_fall_back(self):
        self.configure()
        (self.root / '.env').write_text('OPENROUTER_API_KEY=fixture-dotenv', encoding='utf-8')
        with patch.dict('os.environ', {'OPENROUTER_API_KEY': 'bad\nkey'}, clear=True):
            self.assert_held(self.evaluate(), 'unavailable')

    def test_malformed_duplicate_control_and_oversized_dotenv_are_rejected(self):
        self.configure()
        cases = ('OPENROUTER_API_KEY="unclosed',
                 'OPENROUTER_API_KEY="key" trailing',
                 'OPENROUTER_API_KEY=key\nOPENROUTER_API_KEY=key',
                 'OPENROUTER_API_KEY key',
                 'OPENROUTER_API_KEY="line\nbreak"',
                 'OPENROUTER_API_KEY=bad\x00key',
                 'OPENROUTER_API_KEY=bad\x0bkey',
                 'OPENROUTER_API_KEY=',
                 '#' * (jev.MAX_CONFIG_BYTES + 1))
        for content in cases:
            with self.subTest(content=repr(content[:30])):
                (self.root / '.env').write_text(content, encoding='utf-8')
                with patch.dict('os.environ', {}, clear=True):
                    self.assert_held(self.evaluate(), 'unavailable')

    def test_dotenv_link_is_rejected(self):
        self.configure()
        external = self.root / 'external-secret'
        external.write_text('OPENROUTER_API_KEY=fixture-dotenv', encoding='utf-8')
        try:
            (self.root / '.env').symlink_to(external)
        except OSError:
            self.skipTest('Symlinks unavailable to this Windows account')
        with patch.dict('os.environ', {}, clear=True):
            self.assert_held(self.evaluate(), 'unavailable')

    def test_dotenv_windows_reparse_point_is_rejected_before_opening(self):
        self.configure()
        env_path = self.root / '.env'
        original = Path.lstat

        def file_info(path, *args, **kwargs):
            if path == env_path:
                return SimpleNamespace(st_mode=stat.S_IFREG, st_file_attributes=0x400)
            return original(path, *args, **kwargs)

        with patch.dict('os.environ', {}, clear=True), patch.object(Path, 'lstat', file_info):
            self.assert_held(self.evaluate(), 'unavailable')

    def test_explicit_external_dotenv_reads_only_designated_credential(self):
        with tempfile.TemporaryDirectory() as other_dir:
            external = Path(other_dir) / '.env'
            external.write_text('OPENROUTER_API_KEY=external-fixture\nOTHER=private-other', encoding='utf-8')
            self.configure(env_file=str(external))
            with patch.dict('os.environ', {}, clear=True):
                result = self.evaluate()
                self.assertEqual(result['status'], 'ok')
                self.assertEqual(dict(os.environ), {})
            self.assertEqual(self.post.call_args.args[1], 'external-fixture')
            self.assertNotIn('external-fixture', json.dumps(result))
            self.assertNotIn('private-other', json.dumps(result))
            self.assertFalse((self.root / '.env').exists())

    def test_no_fallback_to_unselected_external_or_default_dotenv(self):
        with tempfile.TemporaryDirectory() as other_dir:
            external = Path(other_dir) / '.env'
            external.write_text('OPENROUTER_API_KEY=external-fixture', encoding='utf-8')
            self.configure()
            with patch.dict('os.environ', {}, clear=True):
                self.assert_held(self.evaluate(), 'unavailable')
            (self.root / '.env').write_text('OPENROUTER_API_KEY=local-fixture', encoding='utf-8')
            self.configure(env_file=str(Path(other_dir) / 'absent.env'))
            with patch.dict('os.environ', {}, clear=True):
                self.assert_held(self.evaluate(), 'unavailable')

    def test_relative_dotenv_under_root_is_supported(self):
        folder = self.root / 'settings'
        folder.mkdir()
        (folder / 'jev.env').write_text('OPENROUTER_API_KEY=relative-fixture', encoding='utf-8')
        self.configure(env_file='settings/jev.env')
        with patch.dict('os.environ', {}, clear=True):
            self.assertEqual(self.evaluate()['status'], 'ok')
        self.assertEqual(self.post.call_args.args[1], 'relative-fixture')

    def test_unsafe_env_file_configuration_is_rejected(self):
        for env_file in ('../outside.env', 'bad\x00name', 'bad\nname', 'bad\x85name', '',
                         True, 42, 'x' * 2049):
            with self.subTest(env_file=repr(env_file)[:35]):
                self.configure(env_file=env_file)
                self.assert_held(self.evaluate(), 'unavailable')

    def test_dotenv_parent_junction_is_rejected(self):
        folder = self.root / 'settings'
        folder.mkdir()
        self.configure(env_file='settings/jev.env')
        original = Path.lstat

        def file_info(path, *args, **kwargs):
            if path == folder:
                return SimpleNamespace(st_mode=stat.S_IFDIR, st_file_attributes=0x400)
            return original(path, *args, **kwargs)

        with patch.dict('os.environ', {}, clear=True), patch.object(Path, 'lstat', file_info):
            self.assert_held(self.evaluate(), 'unavailable')

    def test_scope_and_purpose_are_exact(self):
        self.configure(purposes=['memory_rank'])
        for updates in ({'project_id': 'Alpha'}, {'project_id': '*'}, {'project_id': 'alpha '},
                        {'purpose': 'route'}, {'purpose': 'memory_rank '}, {'purpose': []}):
            with self.subTest(updates=updates):
                self.assert_held(self.evaluate(**updates), 'unavailable')

    def test_malformed_configuration_fails_closed(self):
        for updates in ({'enabled': 1}, {'schema_version': True}, {'model': 'other/model'},
                        {'authorized_projects': ['alpha', 'alpha']}, {'purposes': ['anything']},
                        {'timeout_seconds': 16}, {'timeout_seconds': True},
                        {'max_requests_per_day': True}, {'max_requests_per_day': 1001},
                        {'max_requests_per_day': 0}, {'min_confidence': float('nan')},
                        {'api_key_env': 'BAD\nNAME'}, {'endpoint': 'https://other.invalid'}):
            with self.subTest(updates=updates):
                self.configure(**updates)
                self.assert_held(self.evaluate(), 'unavailable')

    def test_duplicate_and_oversized_config_are_rejected(self):
        path = self.runtime / 'jev-config.json'
        for raw in ('{"schema_version":1,"enabled":false,"enabled":true}',
                    ' ' * (jev.MAX_CONFIG_BYTES + 1)):
            path.write_text(raw, encoding='utf-8')
            self.assert_held(self.evaluate(), 'unavailable')

    def test_request_shape_and_valid_typed_answer(self):
        self.configure()
        result = self.evaluate()
        self.assertEqual(result['status'], 'ok')
        self.assertEqual(result['answers'], RESPONSE['answers'])
        self.assertEqual(result['input_tokens'], 357)
        self.assertEqual(result['cost_usd'], .000014994)
        self.assertEqual(result['provider_calls'], 1)
        raw, key, timeout = self.post.call_args.args
        self.assertEqual(json.loads(raw), {'model': jev.MODEL,
                                         'state': {'text': 'Synthetic authorized material'},
                                         'questions': QUESTIONS})
        self.assertEqual(key, 'synthetic-secret')
        self.assertEqual(timeout, 10)
        self.assertNotIn(key, json.dumps(result))
        usage = json.loads((self.runtime / 'jev-usage.json').read_text())
        self.assertEqual(usage['attempts'], 1)
        self.assertNotIn('Synthetic', json.dumps(usage))

    def test_missing_usage_stays_unknown(self):
        self.configure()
        response = deepcopy(RESPONSE)
        del response['usage']
        self.reply(response)
        result = self.evaluate()
        self.assertEqual(result['status'], 'ok')
        self.assertIsNone(result['input_tokens'])
        self.assertIsNone(result['output_tokens'])
        self.assertIsNone(result['cost_usd'])

    def test_zero_usage_stays_zero(self):
        self.configure()
        response = deepcopy(RESPONSE)
        response['usage'] = {'input_tokens': 0, 'output_tokens': 0, 'cost': 0}
        self.reply(response)
        result = self.evaluate()
        self.assertEqual(result['status'], 'ok')
        self.assertEqual(result['cost_usd'], 0)

    def test_invalid_requests_never_reserve_or_send(self):
        self.configure()
        invalid = [{}, {'q': {'type': 'choice', 'instructions': 'Pick', 'criteria': ['a', 'b']}},
                   {'q': {'type': 'score', 'instructions': 'Rank', 'criteria': ['same', 'same']}},
                   {'q': {'type': 'noul', 'instructions': 'Check', 'extra': True}},
                   {'q': {'type': 'unknown', 'instructions': 'Check'}}]
        for questions in invalid:
            with self.subTest(questions=questions):
                self.assert_held(self.evaluate(questions=questions), 'invalid_request')
        for state in ('a' * jev.MAX_REQUEST_BYTES, {'a': float('inf')}, {1: 'nonstring key'}):
            with self.subTest(state_type=type(state)):
                self.assert_held(self.evaluate(state=state), 'invalid_request')
        self.assertFalse((self.runtime / 'jev-usage.json').exists())

    def test_choice_answer_must_match_exact_options_and_distribution(self):
        self.configure()
        for updates in ({'choice': 'foreign'}, {'choice': True}, {'choice': 'beta'},
                        {'confidence': True}, {'confidence': -1},
                        {'probabilities': {'alpha': .9}},
                        {'probabilities': {'alpha': .9, 'beta': .1, 'foreign': 0}},
                        {'probabilities': {'alpha': True, 'beta': 0}},
                        {'probabilities': {'alpha': .2, 'beta': .1}}):
            with self.subTest(updates=updates):
                response = deepcopy(RESPONSE)
                response['answers']['team'].update(updates)
                self.reply(response)
                self.assert_invalid_response()

    def assert_invalid_response(self):
        result = self.evaluate()
        self.assertEqual(result['status'], 'invalid_response')
        self.assertEqual(result['provider_calls'], 1)
        self.assertEqual(result['answers'], {})
        self.assertIsNone(result['input_tokens'])
        self.assertIsNone(result['request_id'])
        self.assertNotIn('synthetic-secret', json.dumps(result))

    def test_score_requires_valid_weighted_score_and_exact_legend(self):
        self.configure()
        for updates in ({'score': True}, {'score': float('inf')}, {'score': .2}, {'score': 3},
                        {'legend': {'0': 'Other', '1': 'Related', '2': 'Directly useful'}},
                        {'probabilities': {'0': 0, '1': .2, '2': .8, '3': 0}},
                        {'confidence': .9, 'unexpected': 'data'}):
            with self.subTest(updates=updates):
                response = deepcopy(RESPONSE)
                response['answers']['relevance'].update(updates)
                self.reply(response)
                self.assert_invalid_response()

    def test_noul_rejects_boolean_nonfinite_or_out_of_range(self):
        self.configure()
        for value in (True, float('nan'), float('inf'), -0.1, 1.1, '0.8', None):
            with self.subTest(value=value):
                response = deepcopy(RESPONSE)
                response['answers']['supported']['noul'] = value
                self.reply(response)
                self.assert_invalid_response()

    def test_response_requires_exact_questions(self):
        self.configure()
        for operation in ('missing', 'extra', 'wrong_type'):
            response = deepcopy(RESPONSE)
            if operation == 'missing':
                del response['answers']['team']
            elif operation == 'extra':
                response['answers']['unknown'] = {'type': 'noul', 'noul': 1}
            else:
                response['answers']['team']['type'] = 'noul'
            self.reply(response)
            self.assert_invalid_response()

    def test_invalid_response_metadata_and_usage_are_not_returned(self):
        self.configure()
        for updates in ({'model': 'other/model'}, {'provider': 'other'}, {'id': 'bad\nid'},
                        {'id': 'x' * 201}, {'usage': {'input_tokens': True}},
                        {'usage': {'cost': float('inf')}}, {'usage': {'output_tokens': -1}}):
            response = deepcopy(RESPONSE)
            response.update(updates)
            self.reply(response)
            self.assert_invalid_response()

    def test_duplicate_json_and_oversized_response_are_rejected(self):
        self.configure()
        for raw in (b'{"answers":{},"answers":{}}', b' ' * (jev.MAX_RESPONSE_BYTES + 1),
                    b'not JSON', b'\xff'):
            self.post.return_value = raw
            self.assert_invalid_response()

    def test_uncertain_failure_consumes_slot_without_retry_or_leaking_body(self):
        self.configure(max_requests_per_day=1)
        self.post.side_effect = URLError('synthetic-secret and private state')
        result = self.evaluate()
        self.assertEqual(result['status'], 'error')
        self.assertEqual(result['provider_calls'], 1)
        self.assertIsNone(result['cost_usd'])
        self.assertNotIn('synthetic-secret', json.dumps(result))
        second = self.evaluate()
        self.assertEqual(second['status'], 'rate_limited')
        self.assertEqual(second['provider_calls'], 0)
        self.assertEqual(self.post.call_count, 1)

    def test_http_error_body_is_closed_and_not_returned(self):
        self.configure()
        body = BytesIO(b'private state and synthetic-secret')
        self.post.side_effect = HTTPError(jev.ENDPOINT, 401, 'synthetic-secret', {}, body)
        result = self.evaluate()
        self.assertEqual(result['status'], 'error')
        self.assertEqual(result['http_status'], 401)
        self.assertTrue(body.closed)
        self.assertNotIn('synthetic-secret', json.dumps(result))

    def test_http_status_metadata_is_bounded_and_never_includes_provider_body(self):
        self.configure()
        for status in (402, 429, 503, 'private-error', True, 999):
            self.post.side_effect = HTTPError(jev.ENDPOINT, status, 'synthetic-secret', {},
                                             BytesIO(b'private error body'))
            result = self.evaluate()
            self.assertEqual(result['status'], 'error')
            self.assertEqual(result['http_status'], status if type(status) is int and status < 600 else None)
            self.assertNotIn('private', json.dumps(result))
            self.assertNotIn('synthetic-secret', json.dumps(result))

    def test_http_status_is_unknown_without_network_and_200_for_valid_response(self):
        self.assertIsNone(self.evaluate()['http_status'])
        self.configure()
        self.assertEqual(self.evaluate()['http_status'], 200)

    def test_corrupt_or_future_usage_holds_request(self):
        self.configure()
        today = datetime.now(timezone.utc).date()
        values = ['not json', {'schema_version': 1, 'day': today.isoformat(), 'attempts': True},
                  {'schema_version': 1, 'day': (today + timedelta(days=1)).isoformat(), 'attempts': 0},
                  {'schema_version': 1, 'day': today.isoformat(), 'attempts': -1}]
        for value in values:
            (self.runtime / 'jev-usage.json').write_text(json.dumps(value), encoding='utf-8')
            self.assert_held(self.evaluate(), 'unavailable')

    def test_yesterday_counter_rolls_over(self):
        self.configure(max_requests_per_day=1)
        yesterday = datetime.now(timezone.utc).date() - timedelta(days=1)
        (self.runtime / 'jev-usage.json').write_text(json.dumps(
            {'schema_version': 1, 'day': yesterday.isoformat(), 'attempts': 1}), encoding='utf-8')
        self.assertEqual(self.evaluate()['status'], 'ok')
        self.assertEqual(self.evaluate()['status'], 'rate_limited')
        self.assertEqual(self.post.call_count, 1)

    def test_storage_hold_and_atomic_write_failure_prevent_request(self):
        self.configure()
        with patch.object(jev.StorageBudget, 'reserve', side_effect=StorageLimitError('private path')):
            self.assert_held(self.evaluate(), 'unavailable')
        with patch.object(jev, 'write_json', side_effect=OSError('private path')):
            self.assert_held(self.evaluate(), 'unavailable')

    def test_concurrent_calls_reserve_exact_daily_cap(self):
        self.configure(max_requests_per_day=3)
        with ThreadPoolExecutor(max_workers=6) as executor:
            results = list(executor.map(lambda _: self.evaluate(), range(8)))
        self.assertEqual(sum(r['status'] == 'ok' for r in results), 3)
        self.assertEqual(sum(r['status'] == 'rate_limited' for r in results), 5)
        self.assertEqual(self.post.call_count, 3)
        self.assertEqual(json.loads((self.runtime / 'jev-usage.json').read_text())['attempts'], 3)
        reservations = json.loads((self.runtime / 'storage-reservations.json').read_text())
        self.assertEqual(reservations['reservations'], {})

    def test_transport_exact_url_auth_timeout_and_bounded_read(self):
        # Temporarily restore real transport but intercept urllib before any I/O.
        self.network_patch.stop()
        response = MagicMock()
        response.__enter__.return_value = response
        response.status = 200
        response.read.return_value = b'{}'
        with patch.object(jev, 'build_opener') as build:
            build.return_value.open.return_value = response
            raw = jev._post(b'{"model":"typesafe/jev-1.13"}', 'fixture-key', 2)
            self.assertEqual(raw, b'{}')
            request = build.return_value.open.call_args.args[0]
            self.assertEqual(request.full_url, jev.ENDPOINT)
            self.assertEqual(request.method, 'POST')
            self.assertEqual(request.get_header('Authorization'), 'Bearer fixture-key')
            self.assertEqual(request.get_header('Content-type'), 'application/json')
            self.assertEqual(build.return_value.open.call_args.kwargs, {'timeout': 2})
            self.assertIsInstance(build.call_args.args[0], jev._NoRedirect)
            response.read.assert_called_once_with(jev.MAX_RESPONSE_BYTES + 1)

    def test_redirect_handler_never_reissues_request(self):
        request = Request(jev.ENDPOINT, data=b'{}', headers={'Authorization': 'Bearer fixture-key'})
        for code in (301, 302, 303, 307, 308):
            with self.assertRaises(HTTPError) as error:
                jev._NoRedirect().redirect_request(request, None, code, 'redirect', {},
                                                   'https://other.invalid')
            self.assertEqual(error.exception.code, code)


if __name__ == '__main__':
    unittest.main()
