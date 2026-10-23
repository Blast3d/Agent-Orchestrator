"""Worker completion readback: loopback-only, bounded, and silent on every failure."""
from __future__ import annotations

import json
import socket
import sys
import tempfile
import threading
import time
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from uuid import uuid4

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "app"))
import worker_readback  # noqa: E402

SESSION = "e3ea58e0-8f81-478b-921d-ca1e26d19220"
JOB_ID = "a" * 32
TOKEN = "test-token-value"


def _ok_result(**overrides):
    base = {
        "job_id": JOB_ID,
        "worker": "codex",
        "status": "awaiting_review",
        "execution_status": "succeeded",
        "response": "Worker finished the task.",
        "assignment_reused": False,
        "run_id": "run-1",
    }
    base.update(overrides)
    return base


def _env(**overrides):
    env = {
        "CLAUDE_CODE_SESSION_ID": SESSION,
        "AGENT_ORCHESTRATOR_WORKER_READBACK": "1",
    }
    env.update(overrides)
    return env


class _CaptureHandler(BaseHTTPRequestHandler):
    captured = None
    response_status = 202
    response_body = b'{"data": {"accepted": true}}'
    delay = 0.0
    hang = False

    def log_message(self, format, *args):
        return

    def do_POST(self):
        if self.hang:
            time.sleep(3)
            return
        if self.delay:
            time.sleep(self.delay)
        length = int(self.headers.get("Content-Length", "0"))
        body = self.rfile.read(length)
        type(self).captured = {
            "path": self.path,
            "headers": {k.lower(): v for k, v in self.headers.items()},
            "body": body,
        }
        self.send_response(self.response_status)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(self.response_body)


def _serve(handler_cls):
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler_cls)
    # A deliberately hanging handler must not block shutdown.
    server.daemon_threads = True
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server, server.server_address[1]


class WorkerReadbackTests(unittest.TestCase):
    def _bridge(self, tmp: Path, port: int, token: str = TOKEN, extra=None):
        path = tmp / "cli-bridge.json"
        data = {"port": port, "token": token}
        if extra:
            data.update(extra)
        path.write_text(json.dumps(data), encoding="utf-8")
        return path

    def test_sent_exact_body_and_bearer(self):
        class H(_CaptureHandler):
            captured = None
            response_status = 202
            response_body = b'{"data": {"accepted": true}}'
            hang = False
            delay = 0.0

        server, port = _serve(H)
        try:
            tmp = Path(self._tmp())
            bridge = self._bridge(tmp, port)
            receipt = worker_readback.announce_worker_result(
                _ok_result(),
                environ=_env(),
                bridge_path=bridge,
                cwd="/workspace/demo",
            )
            self.assertEqual(receipt["status"], "sent")
            self.assertEqual(receipt["http_status"], 202)
            self.assertNotIn(TOKEN, json.dumps(receipt))
            self.assertNotIn("Worker finished the task.", json.dumps(receipt))
            cap = H.captured
            self.assertIsNotNone(cap)
            self.assertEqual(cap["path"], "/v1/readback/claude-code-helper")
            self.assertEqual(cap["headers"].get("authorization"), f"Bearer {TOKEN}")
            self.assertEqual(cap["headers"].get("content-type"), "application/json")
            body = json.loads(cap["body"].decode("utf-8"))
            self.assertEqual(
                body,
                {
                    "text": "Worker finished the task.",
                    "sessionId": SESSION,
                    "agentId": JOB_ID,
                    "agentType": "orchestrator-worker",
                    "description": "Codex worker",
                    "project": "/workspace/demo",
                },
            )
        finally:
            server.shutdown()
            server.server_close()

    def test_codex_lead_no_session_skipped(self):
        env = {"AGENT_ORCHESTRATOR_WORKER_READBACK": "1"}
        receipt = worker_readback.announce_worker_result(_ok_result(), environ=env, bridge_path="/no/such")
        self.assertEqual(receipt["status"], "skipped")
        self.assertEqual(receipt["reason"], "not_claude_code_lead")

    def test_opt_out_skipped(self):
        for val in ("0", "OFF", "False", "no"):
            receipt = worker_readback.announce_worker_result(
                _ok_result(),
                environ=_env(AGENT_ORCHESTRATOR_WORKER_READBACK=val),
                bridge_path="/no/such",
            )
            self.assertEqual(receipt["status"], "skipped")
            self.assertEqual(receipt["reason"], "opt_out")

    def test_held_failed_uncertain_reused_skipped(self):
        cases = [
            _ok_result(status="held"),
            _ok_result(execution_status="failed"),
            _ok_result(execution_status="uncertain"),
            _ok_result(assignment_reused=True),
        ]
        for result in cases:
            receipt = worker_readback.announce_worker_result(result, environ=_env(), bridge_path="/no/such")
            self.assertEqual(receipt["status"], "skipped")

    def test_malformed_bridges_skipped(self):
        tmp = Path(self._tmp())
        env = _env()
        result = _ok_result()

        missing = tmp / "missing.json"
        self.assertEqual(
            worker_readback.announce_worker_result(result, environ=env, bridge_path=missing)["status"],
            "skipped",
        )

        bad_json = tmp / "bad.json"
        bad_json.write_text("{not json", encoding="utf-8")
        self.assertEqual(
            worker_readback.announce_worker_result(result, environ=env, bridge_path=bad_json)["reason"],
            "bridge_invalid_json",
        )

        as_list = tmp / "list.json"
        as_list.write_text("[1, 2]", encoding="utf-8")
        self.assertEqual(
            worker_readback.announce_worker_result(result, environ=env, bridge_path=as_list)["reason"],
            "bridge_not_object",
        )

        bad_port = tmp / "port.json"
        bad_port.write_text(json.dumps({"port": 80, "token": TOKEN}), encoding="utf-8")
        self.assertEqual(
            worker_readback.announce_worker_result(result, environ=env, bridge_path=bad_port)["reason"],
            "bridge_bad_port",
        )

        empty_token = tmp / "token.json"
        empty_token.write_text(json.dumps({"port": 8200, "token": ""}), encoding="utf-8")
        self.assertEqual(
            worker_readback.announce_worker_result(result, environ=env, bridge_path=empty_token)["reason"],
            "bridge_bad_token",
        )

        oversized = tmp / "big.json"
        oversized.write_bytes(b"{" + (b"x" * 5000) + b"}")
        self.assertEqual(
            worker_readback.announce_worker_result(result, environ=env, bridge_path=oversized)["reason"],
            "bridge_oversized",
        )

    def test_app_disabled_202_skipped_with_reason(self):
        class H(_CaptureHandler):
            captured = None
            response_status = 202
            response_body = b'{"data": {"accepted": false, "reason": "helper_readback_disabled"}}'
            hang = False
            delay = 0.0

        server, port = _serve(H)
        try:
            bridge = self._bridge(Path(self._tmp()), port)
            receipt = worker_readback.announce_worker_result(_ok_result(), environ=_env(), bridge_path=bridge)
            self.assertEqual(receipt["status"], "skipped")
            self.assertEqual(receipt["reason"], "helper_readback_disabled")
            self.assertEqual(receipt["http_status"], 202)
        finally:
            server.shutdown()
            server.server_close()

    def test_404_and_500_failed_with_http_status(self):
        for code in (404, 500):

            class H(_CaptureHandler):
                captured = None
                response_status = code
                response_body = b"{}"
                hang = False
                delay = 0.0

            server, port = _serve(H)
            try:
                bridge = self._bridge(Path(self._tmp()), port)
                receipt = worker_readback.announce_worker_result(_ok_result(), environ=_env(), bridge_path=bridge)
                self.assertEqual(receipt["status"], "failed")
                self.assertEqual(receipt["http_status"], code)
                self.assertNotIn(TOKEN, json.dumps(receipt))
            finally:
                server.shutdown()
                server.server_close()

    def test_connection_refused_failed(self):
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
        sock.close()
        bridge = self._bridge(Path(self._tmp()), port)
        receipt = worker_readback.announce_worker_result(_ok_result(), environ=_env(), bridge_path=bridge)
        self.assertEqual(receipt["status"], "failed")
        # Windows retries a refused loopback SYN for about two seconds, so the
        # bounded call may report its timeout instead of the refusal.
        self.assertIn(receipt["reason"], ("connection_error", "timeout"))
        self.assertNotIn("http_status", receipt)

    def test_redirect_is_refused_and_the_token_never_leaves(self):
        class Target(_CaptureHandler):
            captured = None
            response_status = 202
            response_body = b'{"data": {"accepted": true}}'
            hang = False
            delay = 0.0

            def do_GET(self):
                type(self).captured = {"headers": {k.lower(): v for k, v in self.headers.items()}}
                self.send_response(200)
                self.end_headers()

        target, target_port = _serve(Target)

        class Redirect(_CaptureHandler):
            def do_POST(self):
                self.send_response(302)
                self.send_header("Location", f"http://127.0.0.1:{target_port}/collect")
                self.end_headers()

        redirect, port = _serve(Redirect)
        try:
            bridge = self._bridge(Path(self._tmp()), port)
            receipt = worker_readback.announce_worker_result(_ok_result(), environ=_env(), bridge_path=bridge)
            self.assertEqual(receipt["status"], "failed")
            self.assertEqual(receipt["http_status"], 302)
            self.assertIsNone(Target.captured, "the redirect target must receive nothing")
        finally:
            for server in (redirect, target):
                server.shutdown()
                server.server_close()

    def test_hanging_server_fails_within_timeout(self):
        class H(_CaptureHandler):
            captured = None
            hang = True
            delay = 0.0
            response_status = 202
            response_body = b'{"data": {"accepted": true}}'

        server, port = _serve(H)
        try:
            bridge = self._bridge(Path(self._tmp()), port)
            start = time.monotonic()
            receipt = worker_readback.announce_worker_result(
                _ok_result(),
                environ=_env(),
                bridge_path=bridge,
                timeout=0.4,
            )
            elapsed = time.monotonic() - start
            self.assertEqual(receipt["status"], "failed")
            self.assertLess(elapsed, 2.0)
        finally:
            server.shutdown()
            server.server_close()

    def test_text_truncated_to_16000(self):
        class H(_CaptureHandler):
            captured = None
            response_status = 202
            response_body = b'{"data": {"accepted": true}}'
            hang = False
            delay = 0.0

        server, port = _serve(H)
        try:
            bridge = self._bridge(Path(self._tmp()), port)
            long_text = "x" * 20000
            receipt = worker_readback.announce_worker_result(
                _ok_result(response=long_text),
                environ=_env(),
                bridge_path=bridge,
            )
            self.assertEqual(receipt["status"], "sent")
            body = json.loads(H.captured["body"].decode("utf-8"))
            self.assertEqual(len(body["text"]), 16000)
            dumped = json.dumps(receipt)
            self.assertNotIn(long_text, dumped)
            self.assertNotIn(TOKEN, dumped)
        finally:
            server.shutdown()
            server.server_close()

    def test_invalid_result_and_job_id(self):
        self.assertEqual(
            worker_readback.announce_worker_result("nope", environ=_env(), bridge_path="/x")["reason"],
            "invalid_result",
        )
        self.assertEqual(
            worker_readback.announce_worker_result(
                _ok_result(job_id="ZZ"),
                environ=_env(),
                bridge_path="/x",
            )["reason"],
            "invalid_job_id",
        )

    def test_worker_labels(self):
        labels = {
            "codex": "Codex worker",
            "grok": "Grok worker",
            "gemini": "Gemini worker",
            "claude": "Claude worker",
            "vscode-copilot": "Copilot worker",
            "local-chat": "Local Qwen worker",
            "other": "Worker",
        }
        for worker, desc in labels.items():

            class H(_CaptureHandler):
                captured = None
                response_status = 202
                response_body = b'{"data": {"accepted": true}}'
                hang = False
                delay = 0.0

            server, port = _serve(H)
            try:
                bridge = self._bridge(Path(self._tmp()), port)
                worker_readback.announce_worker_result(
                    _ok_result(worker=worker),
                    environ=_env(),
                    bridge_path=bridge,
                )
                body = json.loads(H.captured["body"].decode("utf-8"))
                self.assertEqual(body["description"], desc)
            finally:
                server.shutdown()
                server.server_close()

    def _tmp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        return directory.name


if __name__ == "__main__":
    unittest.main()
