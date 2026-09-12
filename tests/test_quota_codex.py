from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "app"))
"""Safety regressions for read-only Codex quota observation; no inference."""
import contextlib
import io
import json
import time
import unittest
from types import SimpleNamespace
from unittest.mock import patch

import quota_codex as q

OBSERVED = "2026-09-07T19:46:47Z"


def window(used=25, minutes=300):
    return {"usedPercent": used, "windowDurationMins": minutes, "resetsAt": 1788840000}


class QuotaParserTests(unittest.TestCase):
    def test_legacy_windows_and_remaining_conversion(self):
        result = q.parse_rate_limits({"rateLimits": {
            "primary": window(98), "secondary": window(100, 10080)
        }}, OBSERVED)
        self.assertEqual([w["id"] for w in result["windows"]], ["codex-five-hour", "codex-weekly", "codex-provider-block"])
        self.assertEqual([w["remaining_pct"] for w in result["windows"]], [2, 0, 100])
        self.assertTrue(result["no_model_calls"])

    def test_multi_meter_replaces_legacy_mirror_and_preserves_all_windows(self):
        result = q.parse_rate_limits({"accountId": "private-account", "rateLimits": {
            "primary": window(2)
        }, "rateLimitsByLimitId": {
            "codex": {"secondary": window(30, 10080)},
            "private-meter-name": {"primary": window(70), "secondary": window(90, 10080)},
        }}, OBSERVED)
        self.assertEqual([w["remaining_pct"] for w in result["windows"]], [70, 100, 30, 10, 100])
        self.assertNotIn("private-account", json.dumps(result))
        self.assertNotIn("private-meter-name", json.dumps(result))

    def test_spend_control_and_explicit_denial_close_gate(self):
        for addition in ({"spendControlReached": True}, {"rateLimitReachedType": "new-denial-type"}):
            result = q.parse_rate_limits({"rateLimits": {"primary": window(0), **addition}})
            self.assertEqual(result["windows"][-1]["remaining_pct"], 0)
        result = q.parse_rate_limits({"rateLimits": {"individualLimit": {
            "remainingPercent": 1, "resetsAt": 1788840000, "limit": "100", "used": "99"
        }}})
        self.assertEqual(result["windows"][0]["remaining_pct"], 1)

    def test_recovery_explicitly_clears_every_bucket_without_losing_required_ids(self):
        for denial in ({"spendControlReached": True}, {"rateLimitReachedType": "rate_limit_reached"}):
            buckets = {"codex": {"primary": window(20)}, "additional-meter": {"secondary": window(30, 10080)}}
            blocked = q.parse_rate_limits({"rateLimitsByLimitId": {
                key: {**value, **denial} for key, value in buckets.items()
            }}, OBSERVED)
            recovered = q.parse_rate_limits({"rateLimitsByLimitId": buckets}, "2026-09-07T19:47:47Z")
            prior = {item["id"]: item for item in blocked["windows"]}
            fresh = {item["id"]: item for item in recovered["windows"]}
            self.assertEqual(prior.keys(), fresh.keys())
            required_pools = set(prior) | set(fresh)
            self.assertFalse(required_pools - fresh.keys())
            for identity in required_pools:
                if identity.endswith("-provider-block"):
                    self.assertEqual(prior[identity]["remaining_pct"], 0)
                    self.assertEqual(fresh[identity]["remaining_pct"], 100)
                else:
                    self.assertEqual(prior[identity]["remaining_pct"], fresh[identity]["remaining_pct"])

    def test_clear_sentinel_never_replaces_missing_real_allowance(self):
        with self.assertRaises(q.QuotaError):
            q.parse_rate_limits({"rateLimits": {"spendControlReached": False, "rateLimitReachedType": None}})

    def test_unknown_or_malformed_bucket_invalidates_entire_read(self):
        for invalid in ({}, {"primary": {}}, {"primary": {"usedPercent": True}},
                        {"primary": window(-1)}, {"primary": window(float("nan"))},
                        {"primary": window("25")}, {"spendControlReached": "false"},
                        {"primary": window(2), "tertiary": window(98, 1440)},
                        {"primary": {"usedPercent": 1, "resetsAt": "tomorrow"}},
                        {"primary": window(1), "secondary": window(2)}):
            with self.subTest(invalid=invalid), self.assertRaises(q.QuotaError):
                q.parse_rate_limits({"rateLimitsByLimitId": {"codex": {"primary": window()}, "other": invalid}})

    def test_credits_alone_do_not_count_as_included_quota(self):
        with self.assertRaises(q.QuotaError):
            q.parse_rate_limits({"rateLimits": {"credits": {"unlimited": True, "hasCredits": True}}})

    def test_absent_timestamp_is_not_invented_and_overlimit_closes(self):
        result = q.parse_rate_limits({"rateLimits": {"primary": {"usedPercent": 102}}})
        self.assertIsNone(result["windows"][0]["reset_at"])
        self.assertEqual(result["windows"][0]["remaining_pct"], 0)


class ProtocolTests(unittest.TestCase):
    def client(self, messages):
        wire = b"".join(json.dumps(message).encode() + b"\n" for message in messages)
        proc = SimpleNamespace(stdout=io.BytesIO(wire), stdin=io.BytesIO())
        return q.StdioProtocol(proc), proc

    def test_notifications_and_server_requests_never_execute_tools(self):
        client, proc = self.client([
            {"method": "quota/notification", "params": {"private": "discard"}},
            {"method": "tools/execute", "id": "server-request", "params": {}},
            {"id": 99, "result": {"unrelated": True}},
            {"id": 1, "result": {"rateLimits": {"primary": window()}}},
        ])
        response = client.request("account/rateLimits/read", None, time.monotonic() + 2)
        self.assertIn("rateLimits", response)
        written = [json.loads(line) for line in proc.stdin.getvalue().splitlines()]
        self.assertEqual(written[0]["method"], "account/rateLimits/read")
        self.assertEqual(written[1]["error"]["code"], -32601)
        self.assertNotIn("discard", str(written))

    def test_rpc_error_is_redacted(self):
        client, _ = self.client([{"id": 1, "error": {"message": "private@example.com secret-token"}}])
        with self.assertRaises(q.QuotaError) as raised:
            client.request("account/rateLimits/read", None, time.monotonic() + 2)
        self.assertNotIn("private", str(raised.exception))
        self.assertNotIn("secret", str(raised.exception))

    def test_incomplete_protocol_and_deadline_fail_closed(self):
        client, _ = self.client([{"id": 1}])
        with self.assertRaises(q.QuotaError):
            client.request("account/rateLimits/read", None, time.monotonic() + 2)
        client, _ = self.client([])
        with self.assertRaises(q.QuotaError):
            client.request("account/rateLimits/read", None, time.monotonic() - 1)

    def test_cli_errors_emit_unknown_and_no_arbitrary_error_details(self):
        output = io.StringIO()
        with patch("sys.argv", ["quota_codex.py"]), patch.object(q, "run_codex", side_effect=OSError("private-token")), contextlib.redirect_stdout(output):
            exit_code = q.main()
        result = json.loads(output.getvalue())
        self.assertEqual(exit_code, 1)
        self.assertEqual(result["status"], "unknown")
        self.assertEqual(result["windows"], [])
        self.assertNotIn("private-token", output.getvalue())


if __name__ == "__main__":
    unittest.main()
