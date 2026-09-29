"""Answer-only progress for `codex exec --json` events.

The Codex worker runs with its shell, code-mode host, MCP, apps, plugins,
skills, memories and web search turned off, in a read-only sandbox on an empty
folder. Only answer, reasoning and notice items are expected. Any other item is
attempted tool use and stops the task. Unknown event or item types, malformed
lines once the turn has started, and error items during the turn (a failed tool
attempt looks like one) also stop it, so a CLI upgrade needs this adapter checked.
"""
import json
import math
import re

import output_limits
from output_limits import OutputLimitExceeded

_THREAD = re.compile(r'^[A-Za-z0-9-]{1,100}$')
_ITEM_EVENTS = frozenset(('item.started', 'item.updated', 'item.completed'))
_EVENTS = _ITEM_EVENTS | {'thread.started', 'turn.started', 'turn.completed', 'turn.failed', 'error'}
# The one startup notice these flags produce (codex-cli 0.155): code mode fails closed.
_KNOWN_NOTICE = re.compile(r'^Code Mode is unavailable because code-mode host is disabled\b')
# Codex reports a spent plan allowance as a failed turn; treat only these phrasings
# as a confirmed quota rejection (they authorize a fallback worker).
_USAGE_LIMIT = re.compile(r"\busage limit\b|\brate limit(?:ed| reached| exceeded)\b|"
                          r"\btoo many requests\b|\b429\b", re.I)
_USAGE_KEYS = ('input_tokens', 'cached_input_tokens', 'cache_write_input_tokens',
               'output_tokens', 'reasoning_output_tokens')
_MESSAGE_MAX = 2000


class CodexProtocolError(ValueError):
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


def _clip(text, limit):
    data = text.encode('utf-8')
    return text if len(data) <= limit else data[:limit].decode('utf-8', errors='ignore')


def _message(value):
    if isinstance(value, dict):
        value = value.get('message')
    return _clip(value, _MESSAGE_MAX) if isinstance(value, str) else ''


class CodexProgress:
    """ClaudeProgress-compatible API: observe(), snapshot(), terminal_result."""
    def __init__(self, expected_model=None):
        self.expected_model = expected_model
        self.terminal_result = None
        self.partial_response = ''
        self._answer = None
        self._state = 'waiting'
        self._thread_id = None
        self._turn_started = False
        self._events = 0
        self._ignored_events = 0
        self._malformed = 0
        self._notices = 0
        self._retries = 0
        self._last_error = ''
        self._observed_chars = 0
        self._preview_truncated = False
        self._first_event = self._first_answer = self._last_event = None

    def observe(self, event, elapsed_s):
        if not _number(elapsed_s) or (self._last_event is not None and elapsed_s < self._last_event):
            return
        if not isinstance(event, dict) or not isinstance(event.get('type'), str):
            if self._turn_started:
                raise CodexProtocolError('codex_malformed_event')
            self._malformed += 1
            return
        etype = event['type']
        if etype not in _EVENTS:
            raise CodexProtocolError('codex_unknown_event')
        if self.terminal_result is not None:
            if etype in _ITEM_EVENTS:
                raise CodexProtocolError('codex_event_after_result')
            self._ignored_events += 1
            return
        if etype == 'thread.started':
            thread = event.get('thread_id')
            self._thread_id = thread if isinstance(thread, str) and _THREAD.fullmatch(thread) else None
            self._state = 'starting'
        elif etype == 'turn.started':
            self._turn_started = True
            self._state = 'thinking'
        elif etype in _ITEM_EVENTS:
            if not self._item(event, etype, float(elapsed_s)):
                self._malformed += 1
                return
        elif etype == 'turn.completed':
            self._completed(event)
        elif etype == 'turn.failed':
            self._failed(_message(event.get('error')) or self._last_error or 'Codex reported a failed turn')
        elif etype == 'error':
            # Reconnect notices and fatal errors both arrive here; a fatal one is
            # followed by turn.failed or a nonzero exit.
            self._last_error = _message(event) or self._last_error
            self._retries += 1
            self._state = 'retrying'
        self._events += 1
        if self._first_event is None:
            self._first_event = float(elapsed_s)
        self._last_event = float(elapsed_s)

    def _item(self, event, etype, elapsed):
        item = event.get('item')
        if not isinstance(item, dict) or not isinstance(item.get('type'), str):
            return False
        itype = item['type']
        if itype == 'agent_message':
            text = item.get('text')
            if etype == 'item.completed':
                if not isinstance(text, str):
                    return False
                # The last completed message is the answer; earlier ones are commentary.
                self._answer = text
                self._observed_chars += len(text)
                preview = _clip(text, output_limits.PARTIAL_PREVIEW_MAX)
                self._preview_truncated = self._preview_truncated or preview != text
                self.partial_response = preview
                if self._first_answer is None and text:
                    self._first_answer = elapsed
            self._state = 'answering'
            return True
        if itype == 'reasoning':
            self._state = 'thinking'
            return True
        if itype == 'error':
            # Startup notices are not fatal; an error item during the turn may be a failed tool call.
            message = item.get('message')
            if self._turn_started and not (isinstance(message, str) and _KNOWN_NOTICE.match(message)):
                raise CodexProtocolError('codex_error_item')
            self._notices += 1
            return True
        raise CodexProtocolError('codex_tool_use')

    def _completed(self, event):
        usage = event.get('usage')
        usage = {key: usage[key] for key in _USAGE_KEYS
                 if isinstance(usage, dict) and _number(usage.get(key))}
        if not isinstance(self._answer, str) or not self._answer.strip():
            self._failed('Codex finished the turn without an answer', usage)
            return
        self._terminal({'type': 'result', 'is_error': False, 'result': self._answer,
                        'model': None, 'configured_model': self.expected_model,
                        'thread_id': self._thread_id, 'usage': usage or None})
        self._state = 'finished'

    def _failed(self, message, usage=None):
        error = {'message': message}
        if _USAGE_LIMIT.search(message):
            error['status'] = 429
        self._terminal({'type': 'result', 'is_error': True, 'error': error, 'model': None,
                        'configured_model': self.expected_model, 'thread_id': self._thread_id,
                        'usage': usage or None})
        self._state = 'failed'

    def _terminal(self, result):
        try:
            size = len(json.dumps(result, ensure_ascii=False).encode('utf-8'))
        except (TypeError, ValueError):
            raise CodexProtocolError('codex_invalid_result')
        if size > output_limits.TERMINAL_MAX:
            raise OutputLimitExceeded('terminal')
        self.terminal_result = result

    def snapshot(self):
        return {'state': self._state, 'event_count': self._events,
                'answer_chars': len(self.partial_response), 'observed_answer_chars': self._observed_chars,
                'retained_answer_chars': len(self.partial_response), 'preview_truncated': self._preview_truncated,
                'first_event_s': self._first_event, 'first_answer_s': self._first_answer,
                'last_event_s': self._last_event, 'retry_count': self._retries,
                'notice_count': self._notices, 'ignored_events': self._ignored_events,
                'model': None, 'configured_model': self.expected_model,
                'turn_started': self._turn_started,
                'terminal_received': self.terminal_result is not None,
                'malformed_events': self._malformed}
