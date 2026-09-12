"""Synthetic AGY event regressions; no CLI, accounts, models or filesystem tools."""
import copy
import json
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'app'))
from antigravity_progress import AntigravityProgress, AntigravityProtocolError
import output_limits
from output_limits import OutputLimitExceeded

MODEL = 'gemini-3.8-flash-medium'
CID = 'synthetic-conversation'


def init():
    return {'event': 'init', 'conversation_id': CID,
            'init': {'model': MODEL, 'cwd': 'synthetic-workspace',
                     'tools': ['view_file', 'call_mcp_tool'], 'permission_mode': 'request-review'}}


def step(text=None, **extra):
    body = {'conversation_id': CID, 'step_index': 1, 'state': 'ACTIVE', 'step_type': 'agent_response'}
    if text is not None:
        body['text_delta'] = text
    body.update(extra)
    return {'event': 'step_update', 'step_update': body}


def result(status='SUCCESS', **extra):
    body = {'conversation_id': CID, 'status': status, 'response': 'Seven items.',
            'duration_seconds': 1.1, 'num_turns': 1,
            'usage': {'input_tokens': 10, 'output_tokens': 5, 'thinking_tokens': 2,
                      'cache_read_tokens': 0, 'total_tokens': 15}}
    body.update(extra)
    return {'event': 'result', 'result': body}


class AntigravityProgressTests(unittest.TestCase):
    def ready(self):
        parser = AntigravityProgress(expected_model=MODEL)
        parser.observe(init(), 0.1)
        return parser

    def assertRejected(self, event, cause, parser=None):
        parser = parser or self.ready()
        with self.assertRaises(AntigravityProtocolError) as caught:
            parser.observe(event, 0.3)
        self.assertEqual(caught.exception.cause, cause)
        self.assertEqual(parser.snapshot()['protocol_error'], cause)
        self.assertIsNone(parser.terminal_result)
        return parser

    def test_observed_text_turn_shape_and_usage_are_preserved(self):
        parser = self.ready()
        parser.observe(step(step_type='user_input', state='DONE', step_index=0), 0.15)
        parser.observe(step('Seven '), 0.2)
        parser.observe(step('items.', state='DONE'), 0.3)
        payload = result()
        parser.observe(payload, 0.4)
        self.assertEqual(parser.partial_response, 'Seven items.')
        self.assertEqual(parser.terminal_result['usage'], payload['result']['usage'])
        payload['result']['usage']['input_tokens'] = 999
        self.assertEqual(parser.terminal_result['usage']['input_tokens'], 10)
        self.assertEqual(parser.terminal_result['model'], MODEL)
        self.assertFalse(parser.terminal_result['is_error'])
        snapshot = parser.snapshot()
        self.assertEqual(snapshot['state'], 'finished')
        self.assertEqual(snapshot['event_count'], 5)
        self.assertEqual(snapshot['first_answer_s'], 0.2)
        self.assertEqual(snapshot['last_event_s'], 0.4)
        self.assertTrue(snapshot['terminal_received'])
        for secret in ('Seven items.', CID, 'synthetic-workspace'):
            self.assertNotIn(secret, json.dumps(snapshot))

    def test_failed_terminal_statuses_are_completion_evidence_not_success(self):
        for status in ('ERROR', 'CANCELED', 'INVALID'):
            with self.subTest(status=status):
                parser = self.ready()
                parser.observe(result(status, response=None), 0.3)
                self.assertTrue(parser.terminal_result['is_error'])
                self.assertEqual(parser.terminal_result['status'], status)
                self.assertTrue(parser.snapshot()['terminal_received'])
                self.assertEqual(parser.snapshot()['state'], 'failed')

    def test_provider_error_fields_remain_available_for_failure_classification(self):
        for error in ('quota_exhausted', {'code': 429, 'message': 'Quota exhausted', 'thought': 'secret-body'}):
            parser = self.ready()
            parser.observe(result('ERROR', error=error), 0.2)
            self.assertIn('error', parser.terminal_result)
            self.assertNotIn('secret-body', repr(parser.terminal_result))
            self.assertTrue(parser.terminal_result['is_error'])

    def test_thinking_bodies_are_never_retained(self):
        parser = self.ready()
        parser.observe(step(thinking_delta='secret-body', thought='secret-body'), 0.2)
        self.assertEqual(parser.snapshot()['state'], 'thinking')
        self.assertEqual(parser.partial_response, '')
        parser.observe(result(thought='secret-body', thinking={'text': 'secret-body'}), 0.3)
        self.assertNotIn('secret-body', repr(parser.__dict__))
        self.assertEqual(parser.terminal_result['usage']['thinking_tokens'], 2)

    def test_tool_and_delegation_metadata_are_rejected_regardless_of_step_label(self):
        for fields in ({'step_type': 'tool'}, {'step_type': 'subagent'},
                       {'tool_info': {}}, {'subagent_info': None}, {'tool_name': ''},
                       {'nested': {'tool_info': {'name': 'harmless'}}}):
            with self.subTest(fields=fields):
                parser = self.assertRejected(step(**fields), 'antigravity_tool_event')
                self.assertEqual(parser.snapshot()['tool_event_count'], 1)

    def test_top_level_tool_marker_and_post_terminal_tool_are_rejected(self):
        event = result()
        event['tool_info'] = {}
        self.assertRejected(event, 'antigravity_tool_event')
        parser = self.ready()
        parser.observe(result(), 0.2)
        self.assertRejected(step(tool_name='write_to_file'), 'antigravity_tool_event', parser)

    def test_init_required_and_cannot_be_repeated(self):
        self.assertRejected(result(), 'antigravity_missing_init', AntigravityProgress(MODEL))
        self.assertRejected(init(), 'antigravity_duplicate_init')
        parser = self.ready()
        self.assertFalse(parser.snapshot()['terminal_received'])

    def test_duplicate_terminal_and_later_text_are_rejected(self):
        for event in (result(), step('late'), init()):
            with self.subTest(event=event['event']):
                parser = self.ready()
                parser.observe(result(), 0.2)
                self.assertRejected(event, 'antigravity_event_after_terminal', parser)

    def test_conversation_mismatch_missing_and_dual_identifiers(self):
        for event in (step(conversation_id='different'), result(conversation_id='different')):
            self.assertRejected(event, 'antigravity_conversation_mismatch')
        missing = step()
        missing['step_update'].pop('conversation_id')
        self.assertRejected(missing, 'antigravity_missing_conversation')
        conflict = init()
        conflict['init']['conversation_id'] = 'different'
        self.assertRejected(conflict, 'antigravity_conversation_mismatch', AntigravityProgress(MODEL))

    def test_wrong_model_missing_manifest_and_permission_mode_are_rejected(self):
        for key, value, cause in (
            ('model', 'unexpected-model', 'antigravity_model_mismatch'),
            ('model', None, 'antigravity_model_mismatch'),
            ('tools', None, 'antigravity_invalid_tool_manifest'),
            ('tools', [1], 'antigravity_invalid_tool_manifest'),
            ('permission_mode', 'always-proceed', 'antigravity_permission_mode_mismatch')):
            event = init()
            event['init'][key] = value
            self.assertRejected(event, cause, AntigravityProgress(MODEL))
        self.assertRejected(result(model='changed-model'), 'antigravity_model_mismatch')

    def test_unknown_or_malformed_event_never_becomes_success(self):
        for event, cause in ((None, 'antigravity_malformed_event'),
                             ({'event': 'new_capability'}, 'antigravity_unknown_event'),
                             ({'event': 'step_update', 'step_update': []}, 'antigravity_malformed_event'),
                             (step(step_type='new_capability'), 'antigravity_unknown_step'),
                             (step(state='NEW'), 'antigravity_unknown_step_state'),
                             (step(step_index=True), 'antigravity_invalid_step_index'),
                             (step(text_delta=[]), 'antigravity_invalid_answer_delta')):
            self.assertRejected(event, cause)

    def test_invalid_terminal_fields_fail_closed(self):
        for fields, cause in (({'status': 'success'}, 'antigravity_invalid_terminal_status'),
                              ({'status': []}, 'antigravity_invalid_terminal_status'),
                              ({'response': ' '}, 'antigravity_invalid_terminal_answer'),
                              ({'usage': None}, 'antigravity_invalid_terminal_usage'),
                              ({'usage': {'input_tokens': -1}}, 'antigravity_invalid_terminal_usage'),
                              ({'usage': {'input_tokens': True}}, 'antigravity_invalid_terminal_usage'),
                              ({'duration_seconds': -1}, 'antigravity_invalid_terminal_metadata')):
            self.assertRejected(result(**fields), cause)

    def test_partial_preview_is_utf8_bounded_but_terminal_remains_complete(self):
        parser = self.ready()
        with patch.object(output_limits, 'PARTIAL_PREVIEW_MAX', 5):
            parser.observe(step('ééé'), 0.2)
            parser.observe(step('tail'), 0.3)
        self.assertEqual(parser.partial_response, 'éé')
        self.assertEqual(parser.snapshot()['observed_answer_chars'], 7)
        self.assertTrue(parser.snapshot()['preview_truncated'])
        parser.observe(result(response='ééétail'), 0.4)
        self.assertEqual(parser.terminal_result['response'], 'ééétail')

    def test_event_and_terminal_limits_are_hard_failures(self):
        for attribute, event in (('EVENT_MAX', step('x' * 300)), ('TERMINAL_MAX', result(response='x' * 300))):
            parser = self.ready()
            with patch.object(output_limits, attribute, 100):
                with self.assertRaises(OutputLimitExceeded):
                    parser.observe(event, 0.2)
            self.assertIsNone(parser.terminal_result)

    def test_invalid_time_and_nonfinite_payload_are_rejected(self):
        for elapsed in (True, -1, float('inf'), float('nan'), 0.01):
            parser = self.ready()
            with self.assertRaisesRegex(AntigravityProtocolError, 'antigravity_invalid_event_time'):
                parser.observe(step('text'), elapsed)
        self.assertRejected(result(usage={'input_tokens': float('nan')}), 'antigravity_malformed_event')

    def zero_turn_error(self):
        return result('ERROR', conversation_id='', response='', duration_seconds=0, num_turns=0,
                      error=(f'invalid model selection (--model "{MODEL}" --effort "low"): '
                             f'--model {MODEL} conflicts with --effort=low'),
                      usage={key: 0 for key in ('input_tokens', 'output_tokens', 'thinking_tokens',
                                                'cache_read_tokens', 'total_tokens')})

    def test_exact_zero_turn_config_error_is_a_proven_failed_terminal(self):
        parser = AntigravityProgress(MODEL)
        event = self.zero_turn_error()
        parser.observe(event, 0.1)
        self.assertTrue(parser.terminal_result['is_error'])
        self.assertTrue(parser.terminal_result['pre_inference_rejection'])
        self.assertEqual(parser.terminal_result['error'], event['result']['error'])
        self.assertEqual(parser.terminal_result['usage'], event['result']['usage'])
        self.assertEqual(parser.snapshot()['state'], 'failed')
        self.assertTrue(parser.snapshot()['terminal_received'])
        self.assertFalse(parser.snapshot()['init_received'])
        self.assertIsNone(parser.snapshot()['model'])
        self.assertEqual(parser.partial_response, '')
        self.assertRejected(event, 'antigravity_event_after_terminal', parser)

    def test_zero_usage_alone_does_not_prove_no_inference(self):
        baseline = self.zero_turn_error()
        alterations = [
            ('status', 'SUCCESS'), ('error', 'Something failed'), ('error', baseline['result']['error'] + ' extra'),
            ('conversation_id', CID), ('duration_seconds', 0.01), ('duration_seconds', False),
            ('num_turns', 1), ('num_turns', False), ('response', 'A returned answer'),
            ('usage', {}), ('usage', dict(baseline['result']['usage'], input_tokens=1)),
            ('usage', dict(baseline['result']['usage'], input_tokens=False))]
        for key, value in alterations:
            with self.subTest(key=key, value=value):
                event = copy.deepcopy(baseline)
                event['result'][key] = value
                parser = AntigravityProgress(MODEL)
                with self.assertRaises(AntigravityProtocolError):
                    parser.observe(event, 0.1)
                self.assertIsNone(parser.terminal_result)
                self.assertFalse(parser.snapshot()['pre_inference_rejection'])
        for parser in (AntigravityProgress(), self.ready()):
            with self.assertRaises(AntigravityProtocolError):
                parser.observe(baseline, 0.2)
            self.assertIsNone(parser.terminal_result)

    def test_protocol_failure_is_sticky(self):
        parser = self.assertRejected(step(tool_name='run_command'), 'antigravity_tool_event')
        with self.assertRaisesRegex(AntigravityProtocolError, 'antigravity_tool_event'):
            parser.observe(result(), 0.4)
        self.assertIsNone(parser.terminal_result)


if __name__ == '__main__':
    unittest.main()
