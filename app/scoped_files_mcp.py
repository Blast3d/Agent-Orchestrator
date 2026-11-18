"""Stdio MCP server exposing scoped file tools for one assignment folder."""

from __future__ import annotations

import argparse
import json
import sys

from scoped_files import READ_TOOLS, TOOLS, WRITE_TOOLS, Scope

MAX_LINE_BYTES = 1024 * 1024
MAX_RESPONSE_BYTES = 1024 * 1024
DRAIN_CHUNKS = 256  # discard at most 16 MiB of an overlong line
SUPPORTED_VERSIONS = ("2025-06-18", "2025-03-26", "2024-11-05")
SERVER_INFO = {"name": "orchestrator-files", "version": "1.0.0"}
READ_ANNOTATIONS = {
    "readOnlyHint": True, "destructiveHint": False,
    "idempotentHint": True, "openWorldHint": False,
}
WRITE_ANNOTATIONS = {
    "readOnlyHint": False, "destructiveHint": False,
    "idempotentHint": False, "openWorldHint": False,
}


def _encode(message):
    return json.dumps(message, separators=(",", ":"), ensure_ascii=False).encode("utf-8")


def _send(stdout, message):
    raw = _encode(message)
    if len(raw) > MAX_RESPONSE_BYTES:
        raw = _encode({"jsonrpc": "2.0", "id": message.get("id"),
                       "result": _tool_error("Output was too large")})
    stdout.write(raw + b"\n")
    stdout.flush()


def _error(request_id, code, message):
    return {"jsonrpc": "2.0", "id": request_id, "error": {"code": code, "message": message}}


def _tool_error(text):
    return {"content": [{"type": "text", "text": text}], "isError": True}


def _read_line(stdin):
    """Return (line, overlong); line is None at end of input."""
    chunk = stdin.readline(MAX_LINE_BYTES + 1)
    if not chunk:
        return None, False
    if len(chunk) <= MAX_LINE_BYTES:
        return chunk.rstrip(b"\r\n"), False
    # Bound the discard too; a client that never ends the line ends the session.
    for _ in range(DRAIN_CHUNKS):
        if not chunk or chunk.endswith(b"\n"):
            return b"", True
        chunk = stdin.readline(65536)
    return None, False


def tool_list(read_only=False):
    return [
        {
            "name": tool["name"],
            "description": tool["description"],
            "inputSchema": tool["input_schema"],
            "annotations": READ_ANNOTATIONS if tool["name"] in READ_TOOLS else WRITE_ANNOTATIONS,
        }
        for tool in TOOLS
        if not (read_only and tool["name"] in WRITE_TOOLS)
    ]


def _call(scope, params, read_only):
    name = params["name"]
    arguments = params.get("arguments", {})
    if not isinstance(arguments, dict):
        return _tool_error("Arguments must be an object.")
    if name not in READ_TOOLS | WRITE_TOOLS:
        return _tool_error("Unknown tool.")
    if read_only and name in WRITE_TOOLS:
        return _tool_error("This assignment is read-only.")
    try:
        result = scope.call(name, arguments)
    except Exception as exc:
        # The exception text may carry file contents or outside paths.
        print(f"scope.call failed: {type(exc).__name__}", file=sys.stderr, flush=True)
        return _tool_error("Tool execution failed")
    return {"content": [{"type": "text", "text": json.dumps(result, separators=(",", ":"),
                                                            ensure_ascii=False)}],
            "isError": not result.get("ok")}


def _reply(message, scope, read_only, initialized):
    request_id = message["id"]
    method = message.get("method")
    params = message.get("params")
    if message.get("jsonrpc") != "2.0" or not isinstance(method, str):
        return _error(request_id, -32600, "Invalid request.")
    if method == "initialize":
        requested = params.get("protocolVersion") if isinstance(params, dict) else None
        return {"jsonrpc": "2.0", "id": request_id, "result": {
            "protocolVersion": requested if requested in SUPPORTED_VERSIONS else SUPPORTED_VERSIONS[0],
            "capabilities": {"tools": {"listChanged": False}},
            "serverInfo": SERVER_INFO,
            "instructions": "These tools work only inside the assigned folder.",
        }}
    if method == "ping":
        return {"jsonrpc": "2.0", "id": request_id, "result": {}}
    if not initialized:
        return _error(request_id, -32002, "Server not initialized.")
    if method == "tools/list":
        return {"jsonrpc": "2.0", "id": request_id, "result": {"tools": tool_list(read_only)}}
    if method == "tools/call":
        if not isinstance(params, dict) or not isinstance(params.get("name"), str):
            return _error(request_id, -32602, "Invalid params.")
        return {"jsonrpc": "2.0", "id": request_id, "result": _call(scope, params, read_only)}
    return _error(request_id, -32601, "Method not found.")


def serve(stdin, stdout, scope, read_only=None):
    """Serve newline-delimited JSON-RPC on binary streams until end of input."""
    if read_only is None:
        read_only = getattr(scope, "read_only", False)
    initialized = False
    while True:
        line, overlong = _read_line(stdin)
        if line is None:
            return
        if overlong:
            _send(stdout, _error(None, -32600, "Line exceeds 1 MiB."))
            continue
        if not line.strip():
            continue
        try:
            message = json.loads(line.decode("utf-8"))
        except (ValueError, RecursionError):
            _send(stdout, _error(None, -32700, "Parse error."))
            continue
        if not isinstance(message, dict):
            _send(stdout, _error(None, -32600, "Only single request objects are supported."))
            continue
        if "id" not in message:
            continue  # notifications, including notifications/cancelled
        reply = _reply(message, scope, read_only, initialized)
        initialized = initialized or (message.get("method") == "initialize" and "result" in reply)
        _send(stdout, reply)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", required=True)
    parser.add_argument("--audit", required=True)
    parser.add_argument("--read-only", action="store_true")
    args = parser.parse_args(argv)
    try:
        scope = Scope(args.root, audit_path=args.audit, read_only=args.read_only)
    except (ValueError, OSError):
        print("scoped_files_mcp: the assigned folder is unavailable.", file=sys.stderr)
        return 2
    serve(sys.stdin.buffer, sys.stdout.buffer, scope)
    return 0


if __name__ == "__main__":
    sys.exit(main())
