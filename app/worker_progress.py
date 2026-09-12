"""Safe progress summaries for Claude CLI streaming events."""

from __future__ import annotations

import json
import math
import re
from typing import Any, Optional

import output_limits
from output_limits import OutputLimitExceeded

_SAFE_MODEL = re.compile(r"^[A-Za-z0-9._-]{1,100}$")
_STATES = (
    "starting",
    "waiting",
    "thinking",
    "answering",
    "retrying",
    "finished",
    "failed",
)
_TERMINAL = frozenset({"finished", "failed"})


def _is_finite_number(value: Any) -> bool:
    try:
        return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)
    except OverflowError:
        return False


def _safe_model(value: Any) -> Optional[str]:
    if isinstance(value, str) and _SAFE_MODEL.fullmatch(value):
        return value
    return None


def _payload_size(value: Any) -> Optional[int]:
    try:
        encoded = json.dumps(value, ensure_ascii=False).encode("utf-8")
    except (TypeError, ValueError, OverflowError):
        return None
    return len(encoded)


def _clip_utf8(text: str, max_bytes: int) -> str:
    if max_bytes <= 0:
        return ""
    data = text.encode("utf-8")
    if len(data) <= max_bytes:
        return text
    return data[:max_bytes].decode("utf-8", errors="ignore")


class ClaudeProgress:
    """snapshot() is safe metadata; raw answer fields are private task artifacts."""
    def __init__(self) -> None:
        self._state = "waiting"
        self._event_count = 0
        self._answer_chars = 0
        self._observed_answer_chars = 0
        self._retained_answer_chars = 0
        self._preview_truncated = False
        self._first_event_s: Optional[float] = None
        self._first_answer_s: Optional[float] = None
        self._last_event_s: Optional[float] = None
        self._retry_count = 0
        self._last_retry_status: Optional[int] = None
        self._last_retry_delay_ms: Optional[float] = None
        self._model: Optional[str] = None
        self._terminal_received = False
        self._malformed_events = 0
        self.terminal_result: Optional[dict] = None
        self.partial_response = ""

    def observe(self, event: Any, elapsed_s: Any) -> None:
        if not _is_finite_number(elapsed_s) or elapsed_s < 0:
            return
        if self._terminal_received:
            return
        if self._last_event_s is not None and elapsed_s < self._last_event_s:
            return
        if not isinstance(event, dict):
            self._malformed_events += 1
            return

        etype = event.get("type")
        if not isinstance(etype, str):
            self._malformed_events += 1
            return

        handled = False
        if etype == "system":
            handled = self._handle_system(event)
        elif etype == "stream_event":
            handled = self._handle_stream(event, float(elapsed_s))
        elif etype == "result":
            handled = self._handle_result(event)
        else:
            handled = True

        if not handled:
            self._malformed_events += 1
            return
        self._event_count += 1
        if self._first_event_s is None:
            self._first_event_s = float(elapsed_s)
        self._last_event_s = float(elapsed_s)

    def snapshot(self) -> dict:
        return {
            "state": self._state,
            "event_count": self._event_count,
            "answer_chars": self._answer_chars,
            "observed_answer_chars": self._observed_answer_chars,
            "retained_answer_chars": self._retained_answer_chars,
            "preview_truncated": self._preview_truncated,
            "first_event_s": self._first_event_s,
            "first_answer_s": self._first_answer_s,
            "last_event_s": self._last_event_s,
            "retry_count": self._retry_count,
            "last_retry_status": self._last_retry_status,
            "last_retry_delay_ms": self._last_retry_delay_ms,
            "model": self._model,
            "terminal_received": self._terminal_received,
            "malformed_events": self._malformed_events,
        }

    def _set_state(self, state: str) -> None:
        if self._state in _TERMINAL:
            return
        if state in _STATES:
            self._state = state

    def _handle_system(self, event: dict) -> bool:
        subtype = event.get("subtype")
        if subtype == "init":
            model = _safe_model(event.get("model"))
            if model is not None:
                self._model = model
            self._set_state("starting")
            return True
        if subtype == "api_retry":
            self._retry_count += 1
            status = event.get("error_status")
            if isinstance(status, int) and not isinstance(status, bool):
                self._last_retry_status = status
            delay = event.get("retry_delay_ms")
            if _is_finite_number(delay) and delay >= 0:
                self._last_retry_delay_ms = float(delay)
            self._set_state("retrying")
            return True
        return True

    def _handle_stream(self, event: dict, elapsed_s: float) -> bool:
        inner = event.get("event")
        if not isinstance(inner, dict):
            return False
        itype = inner.get("type")
        delta = inner.get("delta")
        if not isinstance(delta, dict):
            return True
        dtype = delta.get("type")
        if dtype == "thinking_delta" or itype == "thinking_delta":
            self._set_state("thinking")
            return True
        if dtype == "text_delta":
            text = delta.get("text")
            if isinstance(text, str):
                self._observed_answer_chars += len(text)
                used = len(self.partial_response.encode("utf-8"))
                room = output_limits.PARTIAL_PREVIEW_MAX - used
                if room > 0 and not self._preview_truncated:
                    self.partial_response += _clip_utf8(text, room)
                added_bytes = len(text.encode("utf-8"))
                if added_bytes > max(room, 0):
                    self._preview_truncated = True
                self._retained_answer_chars = len(self.partial_response)
                self._answer_chars = self._retained_answer_chars
                if self._first_answer_s is None and text:
                    self._first_answer_s = elapsed_s
            self._set_state("answering")
            return True
        return True

    def _handle_result(self, event: dict) -> bool:
        size = _payload_size(event)
        if size is None:
            return False
        if size > output_limits.TERMINAL_MAX:
            raise OutputLimitExceeded("terminal")
        is_error = event.get("is_error")
        if not isinstance(is_error, bool):
            return False
        if is_error:
            self.terminal_result = event
            self._terminal_received = True
            self._set_state("failed")
            return True
        result = event.get("result")
        if not isinstance(result, str) or not result.strip():
            return False
        self.terminal_result = event
        self._terminal_received = True
        self._set_state("finished")
        return True
