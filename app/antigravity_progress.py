"""Strict, answer-only progress for AGY's observed headless JSON event schema.

This validates a supplied-text contract; it does not replace a pre-tool gate.
Unknown events fail closed so a CLI upgrade requires adapter validation.
"""
import copy
import json
import math
import re

import output_limits
from output_limits import OutputLimitExceeded

_MODEL = re.compile(r'^[A-Za-z0-9._-]{1,100}$')
_TERMINALS = frozenset(('SUCCESS', 'ERROR', 'CANCELED', 'INVALID'))
_TOOL_KEYS = frozenset(('tool_info', 'subagent_info', 'tool_name'))
_STEP_STATES = frozenset(('ACTIVE', 'DONE', 'ERROR', 'CANCELED'))
_ZERO_USAGE_KEYS = frozenset(('input_tokens', 'output_tokens', 'thinking_tokens',
                              'cache_read_tokens', 'total_tokens'))


class AntigravityProtocolError(ValueError):
    """Safe local cause only; never include an untrusted event body."""
    def __init__(self, cause):
        self.cause = cause
        super().__init__(cause)


def _number(value):
    try:
        return (isinstance(value, (int, float)) and not isinstance(value, bool)
                and math.isfinite(value) and value >= 0)
    except OverflowError:
        return False


def _has_tool_marker(event):
    pending = [event]
    while pending:
        item = pending.pop()
        if isinstance(item, dict):
            if _TOOL_KEYS.intersection(item):
                return True
            if item.get('step_type') in ('tool', 'subagent'):
                return True
            pending.extend(item.values())
        elif isinstance(item, list):
            pending.extend(item)
    return False


class AntigravityProgress:
    """ClaudeProgress-compatible API with AGY-specific validation.

    observe() raises on protocol/tool-contract violations, including events after
    a terminal. Callers must stop execution and preserve uncertain reservations.
    terminal_result contains only selected provider fields plus model/is_error.
    """
    def __init__(self, expected_model=None, expected_permission_mode='request-review'):
        if expected_model is not None and (not isinstance(expected_model, str)
                                           or not _MODEL.fullmatch(expected_model)):
            raise ValueError('Invalid expected Antigravity model')
        if expected_permission_mode != 'request-review':
            raise ValueError('Only the verified request-review mode is supported')
        self.expected_model = expected_model
        self.expected_permission_mode = expected_permission_mode
        self.terminal_result = None
        self.partial_response = ''
        self._conversation = None
        self._initialized = False
        self._model = None
        self._state = 'waiting'
        self._events = 0
        self._malformed = 0
        self._tools = 0
        self._first_event = self._first_answer = self._last_event = None
        self._observed_chars = 0
        self._preview_truncated = False
        self._protocol_error = None

    def _reject(self, cause):
        self._malformed += 1
        self._protocol_error = cause
        self._state = 'failed'
        self.terminal_result = None
        raise AntigravityProtocolError(cause)

    def _conversation_id(self, event, body):
        values = [value for value in (event.get('conversation_id'), body.get('conversation_id'))
                  if value is not None]
        if not values or any(not isinstance(value, str) or not value.strip()
                             or len(value) > 256 for value in values):
            self._reject('antigravity_missing_conversation')
        if any(value != values[0] for value in values):
            self._reject('antigravity_conversation_mismatch')
        if self._conversation is not None and values[0] != self._conversation:
            self._reject('antigravity_conversation_mismatch')
        return values[0]

    def observe(self, event, elapsed_s):
        if self._protocol_error is not None:
            raise AntigravityProtocolError(self._protocol_error)
        if not _number(elapsed_s) or (self._last_event is not None and elapsed_s < self._last_event):
            self._reject('antigravity_invalid_event_time')
        if not isinstance(event, dict):
            self._reject('antigravity_malformed_event')
        try:
            size = len(json.dumps(event, ensure_ascii=False, allow_nan=False).encode('utf-8'))
        except (ValueError, TypeError, OverflowError, RecursionError):
            self._reject('antigravity_malformed_event')
        if size > output_limits.EVENT_MAX:
            raise OutputLimitExceeded('stdout_line')
        if _has_tool_marker(event):
            self._tools += 1
            self._reject('antigravity_tool_event')
        if self.terminal_result is not None:
            self._reject('antigravity_event_after_terminal')
        kind = event.get('event')
        if kind not in ('init', 'step_update', 'result'):
            self._reject('antigravity_unknown_event')
        body = event.get(kind)
        if not isinstance(body, dict):
            self._reject('antigravity_malformed_event')
        if (kind == 'result' and not self._initialized
                and self._zero_turn_config_error(event, body, size)):
            self._events += 1
            self._first_event = self._last_event = float(elapsed_s)
            return
        conversation = self._conversation_id(event, body)
        if kind == 'init':
            if self._initialized:
                self._reject('antigravity_duplicate_init')
            model = body.get('model')
            if (not isinstance(model, str) or not _MODEL.fullmatch(model)
                    or (self.expected_model is not None and model != self.expected_model)):
                self._reject('antigravity_model_mismatch')
            if body.get('permission_mode') != self.expected_permission_mode:
                self._reject('antigravity_permission_mode_mismatch')
            if not isinstance(body.get('tools'), list) or any(not isinstance(tool, str) for tool in body['tools']):
                self._reject('antigravity_invalid_tool_manifest')
            self._conversation, self._model = conversation, model
            self._initialized = True
            self._state = 'starting'
        else:
            if not self._initialized:
                self._reject('antigravity_missing_init')
            if kind == 'step_update':
                self._step(body, elapsed_s)
            else:
                self._result(body, size)
        self._events += 1
        if self._first_event is None:
            self._first_event = float(elapsed_s)
        self._last_event = float(elapsed_s)

    def _zero_turn_config_error(self, event, body, size):
        """Recognize only the exact locally observed model/effort CLI rejection.

        A zero token count alone does not prove no inference. This exception
        additionally requires the known config diagnostic, no conversation,
        zero duration/turns and the complete observed zero-usage schema.
        """
        if self.expected_model is None or self._events:
            return False
        expected_error = (f'invalid model selection (--model "{self.expected_model}" --effort "low"): '
                          f'--model {self.expected_model} conflicts with --effort=low')
        usage = body.get('usage')
        if (body.get('status') != 'ERROR' or body.get('error') != expected_error
                or body.get('conversation_id') != '' or event.get('conversation_id', '') != ''
                or body.get('response') != ''
                or not _number(body.get('duration_seconds')) or body['duration_seconds'] != 0
                or not isinstance(body.get('num_turns'), int) or isinstance(body['num_turns'], bool)
                or body['num_turns'] != 0 or not isinstance(usage, dict)
                or set(usage) != _ZERO_USAGE_KEYS
                or any(not _number(value) or value != 0 for value in usage.values())):
            return False
        if size > output_limits.TERMINAL_MAX:
            raise OutputLimitExceeded('terminal')
        self.terminal_result = {key: copy.deepcopy(body[key]) for key in
                                ('conversation_id', 'status', 'response', 'error',
                                 'duration_seconds', 'num_turns', 'usage')}
        self.terminal_result.update(is_error=True, pre_inference_rejection=True)
        self._state = 'failed'
        return True

    def _step(self, body, elapsed_s):
        if body.get('step_type') not in ('user_input', 'agent_response'):
            self._reject('antigravity_unknown_step')
        if not isinstance(body.get('state'), str) or body['state'] not in _STEP_STATES:
            self._reject('antigravity_unknown_step_state')
        index = body.get('step_index')
        if not isinstance(index, int) or isinstance(index, bool) or index < 0:
            self._reject('antigravity_invalid_step_index')
        if body['step_type'] == 'user_input':
            return
        text = body.get('text_delta')
        if text is not None and not isinstance(text, str):
            self._reject('antigravity_invalid_answer_delta')
        if text:
            self._observed_chars += len(text)
            room = max(0, output_limits.PARTIAL_PREVIEW_MAX - len(self.partial_response.encode('utf-8')))
            encoded = text.encode('utf-8')
            if not self._preview_truncated:
                self.partial_response += encoded[:room].decode('utf-8', errors='ignore')
            if len(encoded) > room:
                self._preview_truncated = True
            if self._first_answer is None:
                self._first_answer = float(elapsed_s)
            self._state = 'answering'
        elif any(key in body for key in ('thought', 'thought_delta', 'thinking', 'thinking_delta')):
            self._state = 'thinking'

    def _result(self, body, size):
        if size > output_limits.TERMINAL_MAX:
            raise OutputLimitExceeded('terminal')
        status = body.get('status')
        if not isinstance(status, str) or status not in _TERMINALS:
            self._reject('antigravity_invalid_terminal_status')
        response = body.get('response')
        if ((status == 'SUCCESS' and (not isinstance(response, str) or not response.strip()))
                or (response is not None and not isinstance(response, str))):
            self._reject('antigravity_invalid_terminal_answer')
        usage = body.get('usage')
        if not isinstance(usage, dict) or any(not _number(value) for value in usage.values()):
            self._reject('antigravity_invalid_terminal_usage')
        if 'model' in body and body['model'] != self._model:
            self._reject('antigravity_model_mismatch')
        terminal = {key: copy.deepcopy(body[key]) for key in
                    ('conversation_id', 'status', 'response', 'usage') if key in body}
        if 'error' in body:
            error = body['error']
            if isinstance(error, str):
                terminal['error'] = error
            elif isinstance(error, dict):
                terminal['error'] = {key: value for key, value in error.items()
                                     if key in ('code', 'status', 'type', 'message')
                                     and isinstance(value, (str, int, float)) and not isinstance(value, bool)}
            elif error is not None:
                self._reject('antigravity_invalid_terminal_error')
        for key in ('duration_seconds', 'num_turns'):
            if key in body:
                if not _number(body[key]):
                    self._reject('antigravity_invalid_terminal_metadata')
                terminal[key] = body[key]
        terminal.update(conversation_id=self._conversation, model=self._model, is_error=status != 'SUCCESS')
        self.terminal_result = terminal
        self._state = 'finished' if status == 'SUCCESS' else 'failed'

    def snapshot(self):
        return {'state': self._state, 'event_count': self._events,
                'answer_chars': len(self.partial_response), 'observed_answer_chars': self._observed_chars,
                'retained_answer_chars': len(self.partial_response), 'preview_truncated': self._preview_truncated,
                'first_event_s': self._first_event, 'first_answer_s': self._first_answer,
                'last_event_s': self._last_event, 'retry_count': 0,
                'last_retry_status': None, 'last_retry_delay_ms': None, 'model': self._model,
                'terminal_received': self.terminal_result is not None, 'malformed_events': self._malformed,
                'init_received': self._initialized, 'tool_event_count': self._tools,
                'protocol_error': self._protocol_error,
                'pre_inference_rejection': bool(self.terminal_result and
                                               self.terminal_result.get('pre_inference_rejection'))}
