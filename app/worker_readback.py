"""Tell a running OpenWhispr that a dispatched worker finished; never affects the task."""
from __future__ import annotations

import json
import os
import re
import uuid
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import HTTPRedirectHandler, ProxyHandler, Request, build_opener

_OPT_OUT = frozenset({"0", "off", "false", "no"})
_JOB_ID_RE = re.compile(r"^[0-9a-f]{32}$")
_MAX_TEXT = 16000
_MAX_BRIDGE_BYTES = 4096
_MAX_TOKEN_LEN = 512
_MIN_PORT = 1024
_MAX_PORT = 65535
_AGENT_TYPE = "orchestrator-worker"
_MAX_RESPONSE_BYTES = 64 * 1024


class _RefuseRedirects(HTTPRedirectHandler):
    # urllib keeps the Authorization header across redirects; a 3xx is a failure here.
    def redirect_request(self, *args, **kwargs):
        return None


# Loopback only: never route the bearer token through a proxy or a redirect.
_OPENER = build_opener(ProxyHandler({}), _RefuseRedirects)
_WORKER_LABELS = {
    "codex": "Codex worker",
    "grok": "Grok worker",
    "gemini": "Gemini worker",
    "claude": "Claude worker",
    "vscode-copilot": "Copilot worker",
    "local-chat": "Local Qwen worker",
}


def announce_worker_result(
    result: Any,
    *,
    environ: dict[str, str] | None = None,
    bridge_path: str | os.PathLike[str] | None = None,
    timeout: float = 2.0,
    cwd: str | os.PathLike[str] | None = None,
) -> dict[str, Any]:
    try:
        return _announce(result, environ=environ, bridge_path=bridge_path, timeout=timeout, cwd=cwd)
    except Exception:
        return {"status": "failed", "reason": "unexpected_error"}


def _announce(
    result: Any,
    *,
    environ: dict[str, str] | None,
    bridge_path: str | os.PathLike[str] | None,
    timeout: float,
    cwd: str | os.PathLike[str] | None,
) -> dict[str, Any]:
    env = os.environ if environ is None else environ
    flag = str(env.get("AGENT_ORCHESTRATOR_WORKER_READBACK", "")).strip().lower()
    if flag in _OPT_OUT:
        return {"status": "skipped", "reason": "opt_out"}

    session_raw = env.get("CLAUDE_CODE_SESSION_ID")
    if not session_raw or not isinstance(session_raw, str):
        return {"status": "skipped", "reason": "not_claude_code_lead"}
    try:
        session_id = str(uuid.UUID(session_raw))
        if session_raw != session_id:
            return {"status": "skipped", "reason": "invalid_session_id"}
    except (ValueError, AttributeError, TypeError):
        return {"status": "skipped", "reason": "invalid_session_id"}

    if not isinstance(result, dict):
        return {"status": "skipped", "reason": "invalid_result"}
    if result.get("status") != "awaiting_review":
        return {"status": "skipped", "reason": "not_awaiting_review"}
    if result.get("execution_status") != "succeeded":
        return {"status": "skipped", "reason": "execution_not_succeeded"}
    if result.get("assignment_reused") is True:
        return {"status": "skipped", "reason": "assignment_reused"}

    job_id = result.get("job_id")
    if not isinstance(job_id, str) or not _JOB_ID_RE.fullmatch(job_id):
        return {"status": "skipped", "reason": "invalid_job_id"}

    response = result.get("response")
    if not isinstance(response, str) or not response:
        return {"status": "skipped", "reason": "empty_response"}

    path = Path(bridge_path) if bridge_path is not None else Path.home() / ".openwhispr" / "cli-bridge.json"
    bridge = _load_bridge(path)
    if isinstance(bridge, dict) and "status" in bridge:
        return bridge
    port, token = bridge  # type: ignore[misc]

    text = response[:_MAX_TEXT]
    worker = result.get("worker")
    description = _WORKER_LABELS.get(worker if isinstance(worker, str) else "", "Worker")
    project = str(cwd if cwd is not None else os.getcwd())
    body = {
        "text": text,
        "sessionId": session_id,
        "agentId": job_id,
        "agentType": _AGENT_TYPE,
        "description": description,
        "project": project,
    }
    payload = json.dumps(body).encode("utf-8")
    url = f"http://127.0.0.1:{int(port)}/v1/readback/claude-code-helper"
    req = Request(
        url,
        data=payload,
        method="POST",
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        },
    )
    try:
        with _OPENER.open(req, timeout=timeout) as resp:
            http_status = int(resp.status)
            raw = resp.read(_MAX_RESPONSE_BYTES)
    except HTTPError as exc:
        http_status = int(exc.code)
        return {"status": "failed", "reason": "http_error", "http_status": http_status}
    except TimeoutError:
        return {"status": "failed", "reason": "timeout"}
    except URLError as exc:
        reason = exc.reason
        if isinstance(reason, TimeoutError) or "timed out" in str(reason).lower():
            return {"status": "failed", "reason": "timeout"}
        return {"status": "failed", "reason": "connection_error"}
    except Exception:
        return {"status": "failed", "reason": "request_error"}

    if http_status != 202:
        return {"status": "failed", "reason": "unexpected_status", "http_status": http_status}

    try:
        parsed = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError, AttributeError):
        return {"status": "failed", "reason": "invalid_json", "http_status": http_status}

    if not isinstance(parsed, dict):
        return {"status": "failed", "reason": "invalid_json", "http_status": http_status}
    data = parsed.get("data")
    if not isinstance(data, dict):
        return {"status": "failed", "reason": "invalid_json", "http_status": http_status}
    if data.get("accepted") is True:
        return {"status": "sent", "reason": "accepted", "http_status": http_status}
    if data.get("accepted") is False:
        app_reason = data.get("reason")
        reason = app_reason if isinstance(app_reason, str) and app_reason else "helper_readback_disabled"
        return {"status": "skipped", "reason": reason, "http_status": http_status}
    return {"status": "failed", "reason": "not_accepted", "http_status": http_status}


def _load_bridge(path: Path) -> tuple[int, str] | dict[str, Any]:
    try:
        if not path.is_file():
            return {"status": "skipped", "reason": "bridge_missing"}
        size = path.stat().st_size
        if size > _MAX_BRIDGE_BYTES:
            return {"status": "skipped", "reason": "bridge_oversized"}
        raw = path.read_bytes()
    except OSError:
        return {"status": "skipped", "reason": "bridge_unreadable"}
    if len(raw) > _MAX_BRIDGE_BYTES:
        return {"status": "skipped", "reason": "bridge_oversized"}
    try:
        data = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return {"status": "skipped", "reason": "bridge_invalid_json"}
    if not isinstance(data, dict):
        return {"status": "skipped", "reason": "bridge_not_object"}
    port = data.get("port")
    token = data.get("token")
    if not isinstance(port, int) or isinstance(port, bool) or not (_MIN_PORT <= port <= _MAX_PORT):
        return {"status": "skipped", "reason": "bridge_bad_port"}
    if not isinstance(token, str) or not token or len(token) > _MAX_TOKEN_LEN:
        return {"status": "skipped", "reason": "bridge_bad_token"}
    return port, token
