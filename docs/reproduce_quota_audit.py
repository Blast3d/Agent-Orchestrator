"""Offline audit probes. All quota state lives in temporary directories.

No provider subprocesses, credentials, live model calls, or .codex state are used.
Run with: python docs/reproduce_quota_audit.py
"""
from datetime import timedelta
import json
from pathlib import Path
import re
import sys
import tempfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "app"))
from usage_guard import Guard, now, stamp


def window(identity, remaining, observed, max_age=600):
    return {"id": identity, "remaining_pct": remaining,
            "observed_at": stamp(observed), "max_age_seconds": max_age,
            "source": "offline audit fixture"}


def stale_snapshot_reinstatement():
    with tempfile.TemporaryDirectory() as folder:
        guard = Guard(folder)
        current = now()
        ids = ["claude-five-hour", "claude-seven-day"]
        guard.observe([window(k, 80, current - timedelta(seconds=20)) for k in ids],
                      "claude", complete=True, memberships={"claude": ids})
        # Newer complete read is missing a required window: admission must hold.
        guard.observe([window(ids[0], 70, current)], "claude", complete=True,
                      memberships={"claude": [ids[0]]})
        before = guard.check("claude")["allowed"]
        # Older concurrent request finishes late and reports the former window.
        guard.observe([window(k, 80, current - timedelta(seconds=10)) for k in ids],
                      "claude", complete=True, memberships={"claude": ids})
        after = guard.check("claude")["allowed"]
        return {"held_after_newer_partial_snapshot": not before,
                "incorrectly_reopened_by_older_snapshot": after}


def duplicate_windows():
    with tempfile.TemporaryDirectory() as folder:
        guard = Guard(folder)
        observed = now()
        try:
            guard.observe([window("agy:gemini-weekly", 2, observed),
                           window("agy:gemini-weekly", 100, observed)],
                          "antigravity", complete=True,
                          memberships={"gemini": ["agy:gemini-weekly"]})
        except (ValueError, TypeError):
            return {"ambiguous_duplicate_rejected": True}
        checked = guard.check("gemini", "large")
        return {"ambiguous_duplicate_rejected": False,
                "large_task_allowed_despite_conflicting_2_percent_window": checked["allowed"],
                "effective_remaining_pct": checked["windows"][0]["remaining_pct"]}


def dashboard_expiry():
    with tempfile.TemporaryDirectory() as folder:
        guard = Guard(folder)
        observed = now()
        guard.observe([window("notebooklm-chat-daily", 80, observed, 30)], "manual")
        guard.dashboard()
        page = (Path(folder) / "usage-dashboard.html").read_text(encoding="utf-8")
        # Locate the NotebookLM chat row independent of label punctuation.
        row = next(row for row in re.findall(r"<tr .*?</tr>", page)
                   if "NotebookLM" in row and "chat" in row)
        expiry = float(re.search(r'data-valid-until="([0-9.]+)"', row)[1])
        return {"configured_validity_seconds": 30,
                "dashboard_validity_seconds": round(expiry - observed.timestamp())}


def invalid_policy_budget():
    with tempfile.TemporaryDirectory() as folder:
        guard = Guard(folder)
        guard.observe([window("agy:gemini-weekly", 2, now())], "antigravity")
        guard.policy["estimates_pct"]["large"] = -15
        try:
            result = guard.check("gemini", "large", True, "invalid-policy probe")
        except (ValueError, TypeError):
            return {"invalid_policy_rejected": True}
        return {"invalid_policy_rejected": False,
                "large_task_admitted_at_2_percent": result["allowed"],
                "reserved_estimate_pct": result["estimate_pct"]}


if __name__ == "__main__":
    print(json.dumps({
        "older_complete_snapshot": stale_snapshot_reinstatement(),
        "duplicate_window_ids": duplicate_windows(),
        "dashboard_expiry": dashboard_expiry(),
        "invalid_policy": invalid_policy_budget(),
    }, indent=2))
