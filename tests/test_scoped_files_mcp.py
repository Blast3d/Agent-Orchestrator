"""Tests for the scoped file tools MCP server."""

import io
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

APP = Path(__file__).resolve().parents[1] / "app"
sys.path.insert(0, str(APP))

from scoped_files_mcp import (
    MAX_LINE_BYTES, MAX_RESPONSE_BYTES, READ_ANNOTATIONS, WRITE_ANNOTATIONS, serve,
)

INIT = {"jsonrpc": "2.0", "id": 1, "method": "initialize",
        "params": {"protocolVersion": "2025-06-18"}}


def line(message):
    return json.dumps(message).encode("utf-8") + b"\n"


def request(method, params=None, request_id=2):
    message = {"jsonrpc": "2.0", "id": request_id, "method": method}
    if params is not None:
        message["params"] = params
    return line(message)


class StubScope:
    def __init__(self, read_only=False):
        self.read_only = read_only
        self.calls = []
        self.responses = {}
        self.exception = None

    def call(self, name, arguments):
        self.calls.append((name, arguments))
        if self.exception is not None:
            raise self.exception
        return self.responses.get(name, {"ok": True, "result": f"stub {name}"})


class ScopedFilesMcpTests(unittest.TestCase):
    def setUp(self):
        self.scope = StubScope()

    def run_server(self, data, scope=None, read_only=None):
        stdout = io.BytesIO()
        serve(io.BytesIO(data), stdout, scope or self.scope, read_only=read_only)
        return [json.loads(raw) for raw in stdout.getvalue().splitlines() if raw.strip()]

    def test_initialize_version_echo_and_fallback(self):
        for requested, expected in (("2024-11-05", "2024-11-05"), ("1999-01-01", "2025-06-18")):
            with self.subTest(requested=requested):
                reply = self.run_server(line({**INIT, "params": {"protocolVersion": requested}}))[0]
                self.assertEqual(reply["result"]["protocolVersion"], expected)
                self.assertEqual(reply["result"]["capabilities"], {"tools": {"listChanged": False}})
                self.assertEqual(reply["result"]["serverInfo"]["name"], "orchestrator-files")
                self.assertTrue(reply["result"]["instructions"])

    def test_notifications_get_no_reply(self):
        notes = b"".join(line({"jsonrpc": "2.0", "method": method}) for method in (
            "notifications/initialized", "notifications/cancelled", "tools/call"))
        self.assertEqual(self.run_server(line(INIT) + notes), self.run_server(line(INIT)))

    def test_ping_and_requests_before_initialize(self):
        replies = self.run_server(request("ping", request_id=7) + request("tools/list"))
        self.assertEqual(replies[0], {"jsonrpc": "2.0", "id": 7, "result": {}})
        self.assertEqual(replies[1]["error"]["code"], -32002)

    def test_tools_list_annotations_and_read_only(self):
        tools = self.run_server(line(INIT) + request("tools/list"))[1]["result"]["tools"]
        self.assertEqual({tool["name"] for tool in tools}, {
            "read_file", "list_dir", "find_files", "search_text", "write_file", "edit_file"})
        for tool in tools:
            self.assertIn("inputSchema", tool)
            expected = WRITE_ANNOTATIONS if tool["name"] in ("write_file", "edit_file") else READ_ANNOTATIONS
            self.assertEqual(tool["annotations"], expected)
        read_only = self.run_server(line(INIT) + request("tools/list"), read_only=True)
        self.assertEqual({tool["name"] for tool in read_only[1]["result"]["tools"]},
                         {"read_file", "list_dir", "find_files", "search_text"})

    def test_tools_call_success_and_error(self):
        call = request("tools/call", {"name": "read_file", "arguments": {"path": "a.txt"}})
        result = self.run_server(line(INIT) + call)[1]["result"]
        self.assertFalse(result["isError"])
        self.assertEqual(json.loads(result["content"][0]["text"]), {"ok": True, "result": "stub read_file"})
        self.scope.responses["read_file"] = {"ok": False, "error": "Not found: a.txt"}
        result = self.run_server(line(INIT) + call)[1]["result"]
        self.assertTrue(result["isError"])
        self.assertEqual(json.loads(result["content"][0]["text"])["error"], "Not found: a.txt")

    def test_unknown_and_read_only_write_tools_never_reach_scope(self):
        unknown = request("tools/call", {"name": "run_shell", "arguments": {}})
        self.assertTrue(self.run_server(line(INIT) + unknown)[1]["result"]["isError"])
        write = request("tools/call", {"name": "write_file", "arguments": {"path": "a", "content": "b"}})
        self.assertTrue(self.run_server(line(INIT) + write, read_only=True)[1]["result"]["isError"])
        self.assertEqual(self.scope.calls, [])

    def test_arguments_and_params_validation(self):
        missing = self.run_server(line(INIT) + request("tools/call", {"name": "list_dir"}))
        self.assertFalse(missing[1]["result"]["isError"])
        self.assertEqual(self.scope.calls, [("list_dir", {})])
        for arguments in ("invalid", [1, 2]):
            reply = self.run_server(line(INIT) + request(
                "tools/call", {"name": "list_dir", "arguments": arguments}))
            self.assertTrue(reply[1]["result"]["isError"])
        for params in ([], {"name": 3}):
            reply = self.run_server(line(INIT) + request("tools/call", params))
            self.assertEqual(reply[1]["error"]["code"], -32602)
        self.assertEqual(len(self.scope.calls), 1)

    def test_scope_exception_is_generic(self):
        self.scope.exception = RuntimeError("secret file contents")
        stderr = io.StringIO()
        original, sys.stderr = sys.stderr, stderr
        try:
            reply = self.run_server(line(INIT) + request(
                "tools/call", {"name": "read_file", "arguments": {"path": "a"}}))
        finally:
            sys.stderr = original
        self.assertEqual(reply[1]["result"], {
            "content": [{"type": "text", "text": "Tool execution failed"}], "isError": True})
        self.assertIn("RuntimeError", stderr.getvalue())
        self.assertNotIn("secret", stderr.getvalue())

    def test_malformed_batch_nested_and_invalid_requests(self):
        replies = self.run_server(
            b"{not json\n" + b"\xff\xfe\n" + b"[" * 100000 + b"\n"
            + line([INIT]) + line(5)
            + line({"jsonrpc": "1.0", "id": 9, "method": "ping"})
            + line({"jsonrpc": "2.0", "id": 10, "method": 4})
            + line(INIT) + request("tools/everything"))
        self.assertEqual([reply.get("error", {}).get("code") for reply in replies],
                         [-32700, -32700, -32700, -32600, -32600, -32600, -32600, None, -32601])
        self.assertIsNone(replies[0]["id"])
        self.assertEqual(replies[5]["id"], 9)

    def test_overlong_line_discarded_and_next_served(self):
        replies = self.run_server(b"a" * (MAX_LINE_BYTES + 4096) + b"\n" + request("ping", request_id=88))
        self.assertEqual(replies[0]["error"]["code"], -32600)
        self.assertEqual(replies[1], {"jsonrpc": "2.0", "id": 88, "result": {}})

    def test_endless_line_ends_the_session(self):
        replies = self.run_server(b"a" * (MAX_LINE_BYTES + 17 * 1024 * 1024) + b"\n" + request("ping"))
        self.assertEqual(replies, [])

    def test_oversized_response(self):
        self.scope.responses["read_file"] = {"ok": True, "text": "x" * MAX_RESPONSE_BYTES}
        reply = self.run_server(line(INIT) + request("tools/call", {"name": "read_file"}))[1]
        self.assertEqual(reply["id"], 2)
        self.assertEqual(reply["result"]["content"][0]["text"], "Output was too large")
        self.assertTrue(reply["result"]["isError"])

    def test_clean_exit_on_eof(self):
        self.assertEqual(self.run_server(b""), [])
        self.assertEqual(len(self.run_server(line(INIT))), 1)


class ScopedFilesMcpProcessTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.base = Path(temporary.name)
        self.root = self.base / "assignment"
        self.root.mkdir()
        self.audit = self.base / "audit.ndjson"

    def launch(self, data, *extra):
        return subprocess.run(
            [sys.executable, str(APP / "scoped_files_mcp.py"),
             "--root", str(self.root), "--audit", str(self.audit), *extra],
            input=data, capture_output=True, timeout=30)

    def test_invalid_root_exits_2(self):
        self.root.rmdir()
        run = self.launch(b"")
        self.assertEqual(run.returncode, 2)
        self.assertTrue(run.stderr.strip())
        self.assertEqual(run.stdout, b"")

    def test_real_scope_end_to_end(self):
        (self.root / "inside.txt").write_text("inside text", encoding="utf-8")
        (self.root / ".git").mkdir()
        (self.base / "outside.txt").write_text("CANARY-outside", encoding="utf-8")
        calls = [
            {"name": "read_file", "arguments": {"path": "inside.txt"}},
            {"name": "read_file", "arguments": {"path": "../outside.txt"}},
            {"name": "read_file", "arguments": {"path": str(self.base / "outside.txt")}},
            {"name": "write_file", "arguments": {"path": "new.txt", "content": "made"}},
            {"name": "write_file", "arguments": {"path": ".git/config", "content": "x"}},
        ]
        data = line(INIT) + b"".join(
            request("tools/call", params, request_id=index + 2) for index, params in enumerate(calls))
        run = self.launch(data)
        self.assertEqual(run.returncode, 0, run.stderr)
        results = [json.loads(raw)["result"] for raw in run.stdout.splitlines()[1:]]
        self.assertEqual([result["isError"] for result in results], [False, True, True, False, True])
        self.assertIn("inside text", results[0]["content"][0]["text"])
        self.assertNotIn(b"CANARY", run.stdout + run.stderr)
        self.assertEqual((self.root / "new.txt").read_text(encoding="utf-8"), "made")
        self.assertEqual(len(self.audit.read_text(encoding="utf-8").splitlines()), 5)

        read_only = self.launch(line(INIT) + request(
            "tools/call", {"name": "write_file", "arguments": {"path": "b.txt", "content": "x"}}), "--read-only")
        self.assertTrue(json.loads(read_only.stdout.splitlines()[1])["result"]["isError"])
        self.assertFalse((self.root / "b.txt").exists())


if __name__ == "__main__":
    unittest.main()
