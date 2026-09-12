import math
import os
import sys
import unittest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "app")))

from worker_progress import ClaudeProgress


class ClaudeProgressTests(unittest.TestCase):
    def test_normal_completion(self):
        p = ClaudeProgress()
        snap0 = p.snapshot()
        self.assertEqual(snap0["state"], "waiting")
        self.assertIsNone(p.terminal_result)
        self.assertEqual(p.partial_response, "")

        p.observe(
            {"type": "system", "subtype": "init", "model": "claude-3.5", "session_id": "opaque", "tools": []},
            0.1,
        )
        p.observe(
            {"type": "stream_event", "event": {"type": "content_block_delta", "delta": {"type": "text_delta", "text": "hello"}}},
            0.2,
        )
        p.observe(
            {"type": "stream_event", "event": {"type": "content_block_delta", "delta": {"type": "text_delta", "text": " world"}}},
            0.3,
        )
        terminal = {"type": "result", "is_error": False, "result": "hello world", "modelUsage": {}, "usage": {}}
        p.observe(terminal, 0.4)

        snap = p.snapshot()
        self.assertEqual(snap["state"], "finished")
        self.assertEqual(snap["event_count"], 4)
        self.assertEqual(snap["answer_chars"], 11)
        self.assertEqual(snap["first_event_s"], 0.1)
        self.assertEqual(snap["first_answer_s"], 0.2)
        self.assertEqual(snap["last_event_s"], 0.4)
        self.assertEqual(snap["retry_count"], 0)
        self.assertIsNone(snap["last_retry_status"])
        self.assertIsNone(snap["last_retry_delay_ms"])
        self.assertEqual(snap["model"], "claude-3.5")
        self.assertTrue(snap["terminal_received"])
        self.assertEqual(snap["malformed_events"], 0)
        self.assertEqual(p.partial_response, "hello world")
        self.assertIs(p.terminal_result, terminal)
        self.assertNotIn("hello world", str(snap.values()))
        self.assertNotIn("session", str(snap))
        self.assertNotIn("usage", str(snap))

    def test_retry_then_answer(self):
        p = ClaudeProgress()
        p.observe(
            {
                "type": "system",
                "subtype": "api_retry",
                "attempt": 1,
                "max_retries": 3,
                "retry_delay_ms": 1000,
                "error_status": 503,
            },
            0.05,
        )
        snap = p.snapshot()
        self.assertEqual(snap["state"], "retrying")
        self.assertEqual(snap["retry_count"], 1)
        self.assertEqual(snap["last_retry_status"], 503)
        self.assertEqual(snap["last_retry_delay_ms"], 1000.0)
        p.observe(
            {"type": "stream_event", "event": {"delta": {"type": "text_delta", "text": "ok"}}},
            1.2,
        )
        self.assertEqual(p.snapshot()["state"], "answering")
        self.assertEqual(p.partial_response, "ok")

    def test_thinking_never_retained_or_exposed(self):
        p = ClaudeProgress()
        p.observe(
            {
                "type": "stream_event",
                "event": {
                    "type": "thinking_delta",
                    "delta": {"type": "thinking_delta", "thinking": "secret chain", "text": "secret chain"},
                },
            },
            0.2,
        )
        snap = p.snapshot()
        self.assertEqual(snap["state"], "thinking")
        self.assertEqual(p.partial_response, "")
        self.assertEqual(snap["answer_chars"], 0)
        dumped = repr(snap) + repr(p.partial_response) + repr(p.terminal_result)
        self.assertNotIn("secret", dumped)
        p.observe(
            {"type": "stream_event", "event": {"delta": {"type": "text_delta", "text": "visible"}}},
            0.3,
        )
        self.assertEqual(p.partial_response, "visible")
        self.assertNotIn("secret", p.partial_response)
        self.assertNotIn("secret", str(p.snapshot()))

    def test_malformed_and_unknown_events(self):
        p = ClaudeProgress()
        p.observe(None, 0.1)
        p.observe("nope", 0.2)
        p.observe({"no_type": True}, 0.3)
        p.observe({"type": "mystery", "payload": "ignore-me"}, 0.4)
        p.observe({"type": "stream_event", "event": "not-a-dict"}, 0.5)
        snap = p.snapshot()
        self.assertGreaterEqual(snap["malformed_events"], 4)
        self.assertEqual(snap["state"], "waiting")
        self.assertNotIn("ignore-me", str(snap))
        self.assertNotIn("payload", str(snap))

    def test_invalid_timings_including_nan(self):
        p = ClaudeProgress()
        p.observe({"type": "system", "subtype": "init", "model": "ok-model"}, 0.1)
        before = p.snapshot()
        for bad in (float("nan"), float("inf"), float("-inf"), -1, math.nan, None, "0.2"):
            p.observe({"type": "stream_event", "event": {"delta": {"type": "text_delta", "text": "x"}}}, bad)
        after = p.snapshot()
        self.assertEqual(after["state"], before["state"])
        self.assertEqual(after["event_count"], before["event_count"])
        self.assertEqual(after["answer_chars"], 0)
        self.assertEqual(p.partial_response, "")
        self.assertEqual(after["malformed_events"], before["malformed_events"])

    def test_terminal_state_not_downgraded(self):
        p = ClaudeProgress()
        p.observe({"type": "result", "is_error": False, "result": "done"}, 1.0)
        self.assertEqual(p.snapshot()["state"], "finished")
        p.observe({"type": "system", "subtype": "api_retry", "error_status": 500, "retry_delay_ms": 10}, 1.1)
        p.observe({"type": "stream_event", "event": {"delta": {"type": "thinking_delta", "thinking": "nope"}}}, 1.2)
        p.observe({"type": "stream_event", "event": {"delta": {"type": "text_delta", "text": "late"}}}, 1.3)
        snap = p.snapshot()
        self.assertEqual(snap["state"], "finished")
        self.assertTrue(snap["terminal_received"])
        self.assertEqual(p.partial_response, "")

    def test_first_terminal_result_cannot_flip_outcome(self):
        p = ClaudeProgress()
        original = {"type": "result", "is_error": True}
        p.observe(original, 1.0)
        before = p.snapshot()
        p.observe({"type": "result", "is_error": False, "result": "late success"}, 2.0)
        self.assertIs(p.terminal_result, original)
        self.assertEqual(p.snapshot(), before)

    def test_invalid_event_does_not_move_successful_timing(self):
        p = ClaudeProgress()
        p.observe({"type": "system", "subtype": "init"}, 1.0)
        p.observe({"type": "stream_event", "event": "invalid"}, 4.0)
        self.assertEqual(p.snapshot()["last_event_s"], 1.0)
        self.assertEqual(p.snapshot()["event_count"], 1)

    def test_huge_and_backwards_timing_cannot_corrupt_state(self):
        p = ClaudeProgress()
        p.observe({"type": "system", "subtype": "init"}, 1.0)
        before = p.snapshot()
        for bad in (10 ** 1000, 0.5):
            p.observe({"type": "result", "is_error": False, "result": "bad timing"}, bad)
            self.assertEqual(p.snapshot(), before)
        p.observe({"type": "system", "subtype": "api_retry", "retry_delay_ms": 10 ** 1000}, 2.0)
        self.assertIsNone(p.snapshot()["last_retry_delay_ms"])

    def test_success_missing_answer_is_malformed(self):
        p = ClaudeProgress()
        p.observe({"type": "result", "is_error": False, "result": ""}, 0.5)
        p.observe({"type": "result", "is_error": False}, 0.6)
        p.observe({"type": "result", "result": "x"}, 0.7)
        snap = p.snapshot()
        self.assertEqual(snap["state"], "waiting")
        self.assertFalse(snap["terminal_received"])
        self.assertIsNone(p.terminal_result)
        self.assertGreaterEqual(snap["malformed_events"], 3)

    def test_failed_terminal_without_result(self):
        p = ClaudeProgress()
        err = {"type": "result", "is_error": True}
        p.observe(err, 0.9)
        snap = p.snapshot()
        self.assertEqual(snap["state"], "failed")
        self.assertTrue(snap["terminal_received"])
        self.assertIs(p.terminal_result, err)
        self.assertNotIn("is_error", snap)

    def test_safe_model_labels(self):
        p = ClaudeProgress()
        p.observe({"type": "system", "subtype": "init", "model": "user prompt leak"}, 0.1)
        self.assertIsNone(p.snapshot()["model"])
        p.observe({"type": "system", "subtype": "init", "model": "a" * 101}, 0.2)
        self.assertIsNone(p.snapshot()["model"])
        p.observe({"type": "system", "subtype": "init", "model": "ok_model-1.0"}, 0.3)
        self.assertEqual(p.snapshot()["model"], "ok_model-1.0")
        snap = p.snapshot()
        self.assertNotIn("prompt", str(snap))
        self.assertNotIn("user", str(snap))

    def test_does_not_infer_tokens_from_chars(self):
        p = ClaudeProgress()
        p.observe({"type": "stream_event", "event": {"delta": {"type": "text_delta", "text": "abcd"}}}, 0.1)
        snap = p.snapshot()
        self.assertEqual(snap["answer_chars"], 4)
        self.assertNotIn("token", str(snap).lower())
        self.assertNotIn("usage", snap)


if __name__ == "__main__":
    unittest.main()
