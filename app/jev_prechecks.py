from __future__ import annotations

import math
import re
from datetime import datetime, timezone
from typing import Any

_MAX_BYTES = 65536
_MAX_DEPTH = 8
_PK = re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----")
_BEARER = re.compile(r"\bBearer\s+[A-Za-z0-9\-._~+/]+=*", re.I)
_SK = re.compile(r"\bsk-(?:(?:or-v1-|proj-|ant-)[A-Za-z0-9_-]+|[A-Za-z0-9_-]{20,})")
_AWS = re.compile(r"\bAKIA[0-9A-Z]{16}\b")
_CRED = re.compile(
    r"\b(?:api[_-]?key|password|(?:access|refresh)[_-]?token|client[_-]?secret|secret[_-]?key|private[_-]?key)\b[\"']?\s*[:=]\s*(?:['\"][^'\"\r\n]+['\"]|[^\s,;}\]\"']+)",
    re.I,
)


def _cats(text: str) -> list[str]:
    found: list[str] = []
    if _PK.search(text):
        found.append("privatekeyheaders")
    if _BEARER.search(text):
        found.append("bearer")
    if _SK.search(text):
        found.append("provider_key")
    if _AWS.search(text):
        found.append("aws_access_key")
    if _CRED.search(text):
        found.append("credential_assignment")
    return found


def _walk(value: Any, depth: int, seen: int) -> tuple[list[str], int, bool]:
    cats: list[str] = []
    if seen > _MAX_BYTES or depth > _MAX_DEPTH:
        return [], seen, True
    if isinstance(value, str):
        seen += len(value.encode("utf-8"))
        if seen > _MAX_BYTES:
            return [], seen, True
        cats.extend(_cats(value))
        return cats, seen, False
    if isinstance(value, dict):
        if len(value) > 2048:
            return [], seen, True
        for k, v in value.items():
            if not isinstance(k, str):
                return [], seen, True
            seen += len(k.encode('utf-8')) + 4
            cats.extend(_cats(k))
            if (re.fullmatch(r'api[_-]?key|password|(?:access|refresh)[_-]?token|client[_-]?secret|secret[_-]?key|private[_-]?key', k, re.I)
                    and v is not None and (not isinstance(v, str) or v.strip())):
                cats.append('credential_assignment')
            nested, seen, bad = _walk(v, depth + 1, seen)
            cats.extend(nested)
            if bad:
                return cats, seen, True
        return cats, seen, False
    if isinstance(value, list):
        if len(value) > 2048:
            return [], seen, True
        for item in value:
            nested, seen, bad = _walk(item, depth + 1, seen)
            cats.extend(nested)
            if bad:
                return cats, seen, True
        return cats, seen, False
    if value is None or type(value) in (bool, int) or (type(value) is float and math.isfinite(value)):
        seen += len(str(value)) + 1
        return cats, seen, seen > _MAX_BYTES
    return [], seen, True


def scan_sensitive(value: Any) -> dict[str, Any]:
    try:
        cats, _, bad = _walk(value, 0, 0)
    except (ValueError, TypeError, UnicodeError, RecursionError):
        cats, bad = [], True
    if bad:
        return {"blocked": True, "categories": ["input_unverifiable"]}
    uniq = list(dict.fromkeys(cats))
    return {"blocked": bool(uniq), "categories": uniq}


def _mem_map(memories: Any) -> tuple[dict[str, str] | None, str | None]:
    if not isinstance(memories, list) or len(memories) > 16:
        return None, "unsupported_shape"
    out: dict[str, str] = {}
    for m in memories:
        if not isinstance(m, dict) or "id" not in m:
            return None, "unsupported_shape"
        mid = m["id"]
        if not isinstance(mid, str) or not mid.strip() or len(mid) > 128:
            return None, "unsupported_shape"
        if mid in out:
            return None, "duplicate_id"
        content = m.get("content", "")
        if not isinstance(content, str) or len(content) > 3000:
            return None, 'unsupported_shape'
        out[mid] = content
    return out, None


def citation_precheck(citations: Any, memories: Any) -> dict[str, Any]:
    mmap, err = _mem_map(memories)
    checks: list[dict[str, Any]] = []
    if err or mmap is None:
        return {"valid": False, "checks": [{"memory_id": "", "quote_exists": False, "reason": err}]}
    if not isinstance(citations, list) or not 1 <= len(citations) <= 16:
        return {"valid": False, "checks": [{"memory_id": "", "quote_exists": False, "reason": "unsupported_shape"}]}
    seen: set[str] = set()
    ok = True
    for c in citations:
        if not isinstance(c, dict):
            checks.append({"memory_id": "", "quote_exists": False, "reason": "unsupported_shape"})
            ok = False
            continue
        mid = c.get("memory_id")
        quote = c.get("quote")
        if not isinstance(mid, str) or not mid:
            checks.append({"memory_id": mid if isinstance(mid, str) else "", "quote_exists": False, "reason": "missing_id"})
            ok = False
            continue
        if mid in seen:
            checks.append({"memory_id": mid, "quote_exists": False, "reason": "duplicate_id"})
            ok = False
            continue
        seen.add(mid)
        if mid not in mmap:
            checks.append({"memory_id": mid, "quote_exists": False, "reason": "unknown_id"})
            ok = False
            continue
        if not isinstance(quote, str) or not quote.strip() or len(quote) > 3000:
            checks.append({"memory_id": mid, "quote_exists": False, "reason": "missing_quote"})
            ok = False
            continue
        exists = quote in mmap[mid]
        checks.append({"memory_id": mid, "quote_exists": exists, "reason": "ok" if exists else "quote_not_found"})
        if not exists:
            ok = False
    return {"valid": ok, "checks": checks}


def _parse_dt(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if dt.tzinfo is None:
        return None
    return dt.astimezone(timezone.utc)


def stale_facts(memories: Any, as_of: Any, current_versions: Any = None) -> list[dict[str, Any]]:
    as_dt = _parse_dt(as_of)
    vers = current_versions if isinstance(current_versions, dict) else {}
    out: list[dict[str, Any]] = []
    if not isinstance(memories, list) or as_dt is None:
        return out
    for m in memories[:64]:
        if not isinstance(m, dict):
            continue
        mid = m.get("id")
        if not isinstance(mid, str) or not mid:
            continue
        vf = _parse_dt(m.get("valid_from"))
        vt = m.get("valid_to")
        expired: bool | None
        if vt in (None, ""):
            expired = False
        else:
            vt_dt = _parse_dt(vt)
            expired = None if vt_dt is None else vt_dt <= as_dt
        age: int | None = None if vf is None else max(0, (as_dt - vf).days)
        raw_v = m.get("version")
        cur = vers.get(mid) if mid in vers else None
        if raw_v is None or cur is None:
            vmatch: bool | None = None
        else:
            vmatch = type(raw_v) is type(cur) and raw_v == cur
        out.append({"id": mid, "expired": expired, "age_days": age, "version_matches": vmatch})
    return out
