"""Read-only worker selection and accepted-task balance reporting."""

import argparse
from datetime import datetime
import json
import math
import os
from pathlib import Path
import re
import stat

from provider_policy import next_worker, plan, policy_for, validate_plan


WORKERS = (
    "codex", "claude", "grok", "gemini",
    "vscode-copilot", "local-chat", "jev",
)
METRICS = ("dispatched", "accepted", "rejected", "held", "failed", "uncertain")
JOB_ID = re.compile(r"[0-9a-fA-F]{32}\Z")
FAMILY_WORKERS = {
    "openai": "codex",
    "anthropic": "claude",
    "xai": "grok",
    "google": "gemini",
}


def _read_object(path, maximum):
    """Read bounded JSON from a regular, non-symlink file."""
    path = Path(path)
    before = path.lstat()
    if not stat.S_ISREG(before.st_mode) or before.st_size > maximum:
        raise ValueError("Not a bounded regular file")

    flags = os.O_RDONLY | getattr(os, "O_BINARY", 0)
    flags |= getattr(os, "O_NOFOLLOW", 0)
    fd = os.open(path, flags)
    try:
        opened = os.fstat(fd)
        if (
            not stat.S_ISREG(opened.st_mode)
            or opened.st_size > maximum
            or (before.st_dev, before.st_ino) != (opened.st_dev, opened.st_ino)
        ):
            raise ValueError("File changed while opening")
        with os.fdopen(fd, "rb") as stream:
            fd = None
            data = stream.read(maximum + 1)
        if len(data) > maximum:
            raise ValueError("Oversized file")
        value = json.loads(data)
        if not isinstance(value, dict):
            raise ValueError("Expected JSON object")
        return value
    finally:
        if fd is not None:
            os.close(fd)


def recorded_policy(run_dir, owner):
    try:
        recorded = _read_object(Path(run_dir) / "startup-context.json", 64 * 1024).get("provider_policy")
    except Exception:
        return None
    if not isinstance(recorded, dict):
        return None
    try:
        return validate_plan(recorded, owner)
    except Exception:
        # A stale receipt (for example after a lead handoff) reloads as this,
        # keeping a legacy run on its two-family policy.
        return policy_for(owner, recorded)


def _run_started(run_dir):
    """Epoch seconds of the run's creation, with a minute of clock slack."""
    try:
        created = _read_object(Path(run_dir) / "run.json", 512 * 1024).get("created_utc")
        return datetime.fromisoformat(created.replace("Z", "+00:00")).timestamp() - 60
    except Exception:
        return None


def run_counts(tasks_root, run_id, *, limit=2000, since=None):
    if isinstance(limit, bool) or not isinstance(limit, int) or limit < 0:
        raise ValueError("limit must be a nonnegative integer")

    result = {
        "workers": {worker: dict.fromkeys(METRICS, 0) for worker in WORKERS},
        "records": 0,
        "scan_truncated": limit == 0,
    }
    if limit == 0:
        return result

    scanned = 0
    try:
        entries = os.scandir(tasks_root)
    except OSError:
        return result

    with entries:
        while True:
            try:
                entry = next(entries)
            except StopIteration:
                break
            except OSError:
                break
            try:
                if (
                    not JOB_ID.fullmatch(entry.name)
                    or entry.is_symlink()
                    or not entry.is_dir(follow_symlinks=False)
                ):
                    continue
                # A record last written before the run began cannot belong to it.
                if since is not None and os.stat(Path(entry.path) / "record.json").st_mtime < since:
                    continue
            except OSError:
                continue

            if scanned == limit:
                # Another candidate remains unread.
                result["scan_truncated"] = True
                break
            scanned += 1
            try:
                record = _read_object(Path(entry.path) / "record.json", 512 * 1024)
            except Exception:
                continue
            if (
                record.get("run_id") != run_id
                or record.get("synthetic_input") is True
                or record.get("count_as_build_worker") is False
            ):
                continue
            worker = record.get("worker")
            if not isinstance(worker, str) or worker not in result["workers"]:
                continue

            result["records"] += 1
            counts = result["workers"][worker]
            execution = record.get("execution_status")
            review = record.get("review_status")
            if execution in ("running", "succeeded", "uncertain") and review != "rejected":
                counts["dispatched"] += 1
            if execution == "succeeded" and review == "accepted":
                counts["accepted"] += 1
            if review == "rejected":
                counts["rejected"] += 1
            for status in ("held", "failed", "uncertain"):
                if execution == status:
                    counts[status] += 1

    return result


def _default_check(worker, size):
    from usage_guard import Guard
    return Guard().check(worker, size=size)


def _default_boundary():
    import antigravity_boundary
    return antigravity_boundary.boundary_error()


def _short(value):
    return " ".join(str(value).split())[:240]


def eligible_workers(workers, size, *, check=None, boundary=None):
    check = _default_check if check is None else check
    boundary = _default_boundary if boundary is None else boundary
    eligible = []
    reasons = {}

    for worker in dict.fromkeys(workers):
        try:
            decision = check(worker, size)
            if decision.get("allowed") is not True:
                details = decision.get("reasons") or ["Quota or route unavailable"]
                if isinstance(details, str):
                    details = [details]
                reasons[worker] = _short("; ".join(map(str, details)))
                continue
            if worker == "gemini":
                error = boundary()
                if error is not None:
                    reasons[worker] = _short(error) or "Gemini route unavailable"
                    continue
            eligible.append(worker)
        except Exception as exc:
            reasons[worker] = type(exc).__name__

    return eligible, reasons


def _selection(root, run_dir, size, check, boundary):
    run_dir = Path(run_dir)
    coordinator = _read_object(run_dir / "coordinator.json", 64 * 1024)
    owner = coordinator["owner"]
    policy = recorded_policy(run_dir, owner)
    if policy is None:
        policy = plan(owner)

    tally = run_counts(Path(root) / "runs" / "tasks", run_dir.name, since=_run_started(run_dir))
    workers = policy["cross_family_workers"]
    eligible, reasons = eligible_workers(
        workers, size, check=check, boundary=boundary
    )
    dispatched = {
        worker: metrics["dispatched"]
        for worker, metrics in tally["workers"].items()
    }
    report = {
        "worker": next_worker(
            owner, dispatched, eligible=eligible, policy=policy
        ),
        "policy_schema": policy["schema_version"],
        "counts": tally["workers"],
        "records": tally["records"],
        "scan_truncated": tally["scan_truncated"],
        "eligible": eligible,
        "ineligible": reasons,
        "lead_family": policy["lead_family"],
    }
    return report, policy


def select_worker(root, run_dir, size="small", *, check=None, boundary=None):
    report, _ = _selection(root, run_dir, size, check, boundary)
    return report


def _family_worker(family):
    name = family.casefold()
    return FAMILY_WORKERS.get(name, name)


def balance_report(root, run_dir, size="small", *, check=None, boundary=None):
    report, policy = _selection(root, run_dir, size, check, boundary)
    if policy["schema_version"] == 1:
        return report

    accepted = {
        family: report["counts"][_family_worker(family)]["accepted"]
        for family in policy["required_worker_families"]
    }
    highest = max(accepted.values(), default=0)
    lowest = min(accepted.values(), default=0)
    settings = policy["balance"]
    allowed = max(
        settings["min_spread"],
        math.ceil(settings["max_spread_ratio"] * highest),
    )
    missing = [family for family, count in accepted.items() if count == 0]
    spread = highest - lowest
    report.update(
        accepted=accepted,
        spread=spread,
        allowed_spread=allowed,
        balanced=not missing and spread <= allowed,
        missing=missing,
    )
    return report


def render(report):
    lines = [
        f"{worker}: dispatched={counts['dispatched']} accepted={counts['accepted']}"
        for worker, counts in report["counts"].items()
    ]
    if report["worker"] is not None:
        lines.append(f"Next worker: {report['worker']}")
    else:
        reasons = "; ".join(
            f"{worker}: {reason}"
            for worker, reason in report["ineligible"].items()
        )
        lines.append(
            "Next worker: none — " + (reasons or "No eligible policy workers")
        )

    # Selection-only reports have no balance fields; only balance_report adds them.
    if report["policy_schema"] == 2 and "accepted" in report:
        accepted = ", ".join(
            f"{family}={count}" for family, count in report["accepted"].items()
        )
        lines.append(f"Accepted by required family: {accepted}")
        lines.append(
            f"Balance: {'balanced' if report['balanced'] else 'unbalanced'}; "
            f"spread={report['spread']}; allowed={report['allowed_spread']}; "
            f"missing={', '.join(report['missing']) or 'none'}"
        )
    if report["scan_truncated"]:
        lines.append("Scan limit reached; counts may be incomplete.")
    return "\n".join(lines)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run", required=True, type=Path)
    parser.add_argument(
        "--size", choices=("tiny", "small", "medium", "large"), default="small"
    )
    parser.add_argument("--json", action="store_true", dest="as_json")
    args = parser.parse_args(argv)

    root = Path(__file__).resolve().parents[1]
    try:
        report = balance_report(root, args.run, args.size)
    except Exception as exc:
        message = f"{type(exc).__name__}: {exc}"
        print(json.dumps({"error": message}) if args.as_json else message)
        return 2
    print(json.dumps(report, indent=2) if args.as_json else render(report))
    return 0 if report["worker"] is not None else 2


if __name__ == "__main__":
    raise SystemExit(main())
