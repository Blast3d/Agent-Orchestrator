"""CLI update checks and verification module for Agent Orchestrator.

Provides zero-model-call detection and reporting of worker CLI versions
and executable hashes against last-verified states, ensuring updates are
surfaced and verified rather than silently breaking or holding work.
"""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import subprocess
import sys
from typing import Any, Callable, Dict, Optional, Union

import antigravity_boundary
import codex_worker
from paths import ROOT
from task_store import timestamp, write_json
from usage_guard import file_lock


def _resolve_antigravity() -> Optional[Path]:
    return Path(antigravity_boundary.EXE)


def _resolve_grok() -> Path:
    return Path.home() / ".grok" / "bin" / "grok.exe"


def _resolve_claude() -> Path:
    return (
        Path.home()
        / "AppData"
        / "Roaming"
        / "npm"
        / "node_modules"
        / "@anthropic-ai"
        / "claude-code"
        / "bin"
        / "claude.exe"
    )


def _resolve_codex() -> Optional[Path]:
    try:
        exe = codex_worker.executable()
    except Exception:  # an unreadable install reports as missing, never crashes status
        return None
    return Path(exe) if exe else None


# CLI configuration registry: name -> executable resolver, version args, and suppression env
CLIS: Dict[str, Dict[str, Any]] = {
    "antigravity": {
        "resolver": _resolve_antigravity,
        "args": ["--version"],
        "env": {"AGY_CLI_DISABLE_AUTO_UPDATE": "true"},
    },
    "grok": {
        "resolver": _resolve_grok,
        "args": ["--no-auto-update", "--version"],
        "env": {},
    },
    "claude": {
        "resolver": _resolve_claude,
        "args": ["--version"],
        "env": {"DISABLE_AUTOUPDATER": "1"},
    },
    "codex": {
        "resolver": _resolve_codex,
        "args": ["--version"],
        "env": {},
    },
}


def default_hasher(path: Union[str, Path]) -> str:
    """Compute SHA-256 hex digest of an executable file."""
    return antigravity_boundary.digest(Path(path))


def read_version(
    name: str,
    path: Union[str, Path],
    runner: Optional[Callable[..., Any]] = None,
) -> str:
    """Run worker CLI with update suppression to retrieve version string.

    Returns the first line of output, stripped and truncated to at most 120 characters.
    """
    cli_cfg = CLIS.get(name, {})
    args = cli_cfg.get("args", ["--version"])
    env_vars = cli_cfg.get("env", {})

    cmd = [str(path)] + list(args)
    env = os.environ.copy()
    env.update(env_vars)

    run_fn = runner or subprocess.run
    kwargs: Dict[str, Any] = {
        "stdout": subprocess.PIPE,
        "stderr": subprocess.PIPE,
        "text": True,
        "timeout": 20,
        "shell": False,
        "env": env,
    }
    if sys.platform == "win32" and hasattr(subprocess, "CREATE_NO_WINDOW"):
        kwargs["creationflags"] = subprocess.CREATE_NO_WINDOW

    res = run_fn(cmd, **kwargs)
    if isinstance(res, str):
        stdout = res
    else:
        if hasattr(res, "returncode") and res.returncode != 0:
            stderr = getattr(res, "stderr", "") or ""
            raise RuntimeError(
                f"{name} version command failed with exit code {res.returncode}: {stderr.strip()}"
            )
        stdout = getattr(res, "stdout", "") or ""

    lines = stdout.strip().splitlines()
    if not lines:
        raise RuntimeError(f"{name} produced no version output")

    return lines[0].strip()[:120]


def _read_antigravity_boundary(root: Optional[Union[str, Path]] = None) -> Optional[Dict[str, Any]]:
    """Read the authoritative Antigravity verified state from that installation's boundary proof."""
    if root is None:
        proof_path = Path(antigravity_boundary.VALIDATION)
    else:
        proof_path = Path(root) / "config" / "antigravity-boundary.json"

    if proof_path and proof_path.exists():
        try:
            with open(proof_path, "r", encoding="utf-8") as f:
                data = json.load(f)
            return {
                "version": data.get("cli_version"),
                "sha256": data.get("executable_sha256"),
                "verified_at": data.get("published_at") or data.get("verified_at"),
                "method": "boundary_verification",
                "evidence": str(proof_path),
            }
        except Exception:
            return None
    return None


def status(
    root: Optional[Union[str, Path]] = None,
    runner: Optional[Callable[..., Any]] = None,
    hasher: Optional[Callable[[Union[str, Path]], str]] = None,
    paths: Optional[Dict[str, Optional[Union[str, Path]]]] = None,
) -> Dict[str, Any]:
    """Inspect and report installed worker CLI states against verified records.

    Zero model calls. Returns report dictionary with per-CLI state and action.
    """
    base_root = Path(root) if root else ROOT
    runtime_dir = base_root / "runtime"
    verification_file = runtime_dir / "cli-verification.json"

    verified_store: Dict[str, Any] = {}
    if verification_file.exists():
        try:
            with open(verification_file, "r", encoding="utf-8") as f:
                loaded = json.load(f)
                if isinstance(loaded, dict):
                    verified_store = loaded.get("clis", {})
        except Exception:
            verified_store = {}

    ag_proof = _read_antigravity_boundary(base_root)
    hash_fn = hasher or default_hasher

    clis_report: Dict[str, Dict[str, Any]] = {}

    for name in CLIS:
        if paths is not None and name in paths:
            exe_val = paths[name]
            exe_path = Path(exe_val) if exe_val is not None else None
        else:
            try:
                exe_val = CLIS[name]["resolver"]()
                exe_path = Path(exe_val) if exe_val is not None else None
            except Exception:
                exe_path = None

        if name == "antigravity":
            verified_sha256 = ag_proof.get("sha256") if ag_proof else None
            verified_version = ag_proof.get("version") if ag_proof else None
        else:
            cli_v = verified_store.get(name, {})
            verified_sha256 = cli_v.get("sha256")
            verified_version = cli_v.get("version")

        is_installed = False
        if exe_path is not None:
            try:
                is_installed = exe_path.is_file()
            except OSError:
                is_installed = False

        if not is_installed:
            clis_report[name] = {
                "path": str(exe_path) if exe_path else None,
                "installed": False,
                "version": None,
                "sha256": None,
                "verified_version": verified_version,
                "verified_sha256": verified_sha256,
                "state": "missing",
                "action": f"Install {name} or configure executable path.",
            }
            continue

        sha256_val: Optional[str] = None
        version_val: Optional[str] = None
        has_error = False
        err_msg = ""

        try:
            sha256_val = hash_fn(exe_path)
        except Exception as e:
            has_error = True
            err_msg = f"hashing error ({e})"

        if not has_error:
            try:
                version_val = read_version(name, exe_path, runner=runner)
            except Exception as e:
                has_error = True
                err_msg = f"version command error ({e})"

        if has_error:
            clis_report[name] = {
                "path": str(exe_path),
                "installed": True,
                "version": None,
                "sha256": sha256_val,
                "verified_version": verified_version,
                "verified_sha256": verified_sha256,
                "state": "error",
                "action": f"Inspect {name} installation: {err_msg}.",
            }
            continue

        if not verified_sha256:
            state = "unverified"
            if name == "antigravity":
                action = (
                    "Run `orchestrator.py updates verify antigravity` "
                    "(dispatch also runs this automatically)."
                )
            else:
                action = f"Confirm one real task, then run `orchestrator.py updates accept {name}`."
        elif sha256_val != verified_sha256 or (
            verified_version and version_val != verified_version
        ):
            state = "updated"
            if name == "antigravity":
                action = (
                    "Run `orchestrator.py updates verify antigravity` "
                    "(dispatch also runs this automatically)."
                )
            else:
                action = f"Confirm one real task, then run `orchestrator.py updates accept {name}`."
        else:
            state = "current"
            action = "Up to date; no action required."

        clis_report[name] = {
            "path": str(exe_path),
            "installed": True,
            "version": version_val,
            "sha256": sha256_val,
            "verified_version": verified_version,
            "verified_sha256": verified_sha256,
            "state": state,
            "action": action,
        }

    return {
        "clis": clis_report,
        "model_calls": 0,
    }


def accept(
    name: str,
    evidence: str,
    root: Optional[Union[str, Path]] = None,
    runner: Optional[Callable[..., Any]] = None,
    hasher: Optional[Callable[[Union[str, Path]], str]] = None,
    paths: Optional[Dict[str, Optional[Union[str, Path]]]] = None,
) -> Dict[str, Any]:
    """Record the installed CLI version and hash as verified under lock.

    Antigravity updates cannot be accepted via this call; they require boundary verification.
    """
    if name == "antigravity":
        raise ValueError(
            "Antigravity updates cannot be accepted manually; "
            "only boundary verification may accept them (run `orchestrator.py updates verify antigravity`)."
        )
    if name not in CLIS:
        raise ValueError(f"Unknown CLI '{name}'. Supported CLIs: {list(CLIS.keys())}")
    if not evidence or not str(evidence).strip():
        raise ValueError("Evidence string is required to accept a CLI update.")

    base_root = Path(root) if root else ROOT
    runtime_dir = base_root / "runtime"
    runtime_dir.mkdir(parents=True, exist_ok=True)
    verification_file = runtime_dir / "cli-verification.json"
    lock_file = runtime_dir / "cli-verification.lock"

    if paths is not None and name in paths:
        exe_val = paths[name]
        exe_path = Path(exe_val) if exe_val is not None else None
    else:
        try:
            exe_val = CLIS[name]["resolver"]()
            exe_path = Path(exe_val) if exe_val is not None else None
        except Exception:
            exe_path = None

    if exe_path is None or not exe_path.is_file():
        raise FileNotFoundError(f"CLI '{name}' executable not found at {exe_path}")

    hash_fn = hasher or default_hasher
    sha256_val = hash_fn(exe_path)
    version_val = read_version(name, exe_path, runner=runner)

    with file_lock(lock_file):
        data: Dict[str, Any] = {"schema_version": 1, "clis": {}}
        if verification_file.exists():
            try:
                with open(verification_file, "r", encoding="utf-8") as f:
                    loaded = json.load(f)
                    if isinstance(loaded, dict):
                        data = loaded
                        if "clis" not in data or not isinstance(data["clis"], dict):
                            data["clis"] = {}
            except Exception:
                data = {"schema_version": 1, "clis": {}}

        entry = {
            "version": version_val,
            "sha256": sha256_val,
            "verified_at": timestamp(),
            "method": "manual_accept",
            "evidence": str(evidence).strip(),
        }
        data["schema_version"] = 1
        data["clis"][name] = entry
        write_json(verification_file, data)

    return entry


ANTIGRAVITY_DRIFT = "Antigravity executable or permission handler changed"


def auto_verify_enabled(config_path: Optional[Path] = None) -> bool:
    """The `policy.auto_verify_cli_updates` switch in config/workers.json; default on."""
    path = config_path or ROOT / "config" / "workers.json"
    try:
        policy = json.loads(Path(path).read_text(encoding="utf-8-sig")).get("policy", {})
    except (OSError, UnicodeError, ValueError, AttributeError):
        return True
    return policy.get("auto_verify_cli_updates", True) is not False


def gemini_admission_error(verify: Optional[Callable[[], Dict[str, Any]]] = None,
                           boundary: Optional[Callable[[], Optional[str]]] = None,
                           enabled: Optional[bool] = None) -> Optional[str]:
    """Offline Gemini admission. A changed agy binary is verified once, then rechecked.

    verify_and_publish runs at most once per executable hash and publishes a new proof
    only when every boundary check passes, so a failed update keeps Gemini held.
    """
    boundary = boundary or antigravity_boundary.boundary_error
    error = boundary()
    if error is None or not error.startswith(ANTIGRAVITY_DRIFT):
        return error
    if not (auto_verify_enabled() if enabled is None else enabled):
        return error + " Run `orchestrator.py updates verify antigravity`."
    if verify is None:
        from antigravity_verification import verify_and_publish as verify
    try:
        result = verify() or {}
    except Exception as exc:  # noqa: BLE001 - report, keep Gemini held
        return f"{error} Automatic verification stopped: {type(exc).__name__}: {exc}"[:400]
    after = boundary()
    if after is None:
        return None
    return (f"{after} Automatic verification result: {result.get('status', 'unknown')}; "
            "see `orchestrator.py updates status`.")[:400]


def main(argv: Optional[list[str]] = None) -> int:
    """CLI dispatcher for updates inspection, acceptance, and verification."""
    if argv is None:
        argv = sys.argv[1:]

    parser = argparse.ArgumentParser(
        prog="orchestrator.py updates",
        description="Zero-model-call worker CLI update detection and verification",
    )
    subparsers = parser.add_subparsers(dest="command")

    p_status = subparsers.add_parser("status", help="Report installed vs verified CLI versions")
    p_status.add_argument("--json", action="store_true", help="Output status report as JSON")
    p_status.add_argument("--root", default=None, help=argparse.SUPPRESS)

    p_accept = subparsers.add_parser("accept", help="Accept an updated CLI version after testing")
    p_accept.add_argument("name", help="Name of the CLI (grok, claude, codex)")
    p_accept.add_argument(
        "--evidence", required=True, help="Evidence of verification (e.g. task ID or test)"
    )
    p_accept.add_argument("--root", default=None, help=argparse.SUPPRESS)

    p_verify = subparsers.add_parser(
        "verify", help="Run verification probes on a CLI (antigravity)"
    )
    p_verify.add_argument("name", help="CLI to verify (antigravity)")
    p_verify.add_argument(
        "--force", action="store_true", help="Force verification even if already attempted"
    )
    p_verify.add_argument("--json", action="store_true", help="Output verification result as JSON")
    p_verify.add_argument("--root", default=None, help=argparse.SUPPRESS)

    try:
        args = parser.parse_args(argv)
    except SystemExit as e:
        return e.code if isinstance(e.code, int) else 2

    if not args.command:
        parser.print_help(sys.stderr)
        return 2

    if args.command == "status":
        report = status(root=args.root)
        if args.json:
            print(json.dumps(report, indent=2))
        else:
            print("Worker CLI update status:")
            for name, info in report["clis"].items():
                v = info["version"] or "none"
                vv = info["verified_version"] or "none"
                st = info["state"]
                act = info["action"]
                print(f"  {name:12}: {st:11} | installed: {v:12} | verified: {vv:12} | {act}")
        all_current = all(info["state"] == "current" for info in report["clis"].values())
        return 0 if all_current else 1

    if args.command == "accept":
        try:
            rec = accept(args.name, args.evidence, root=args.root)
            print(
                f"Accepted {args.name} update "
                f"(version: {rec['version']}, sha256: {rec['sha256'][:16]}...)"
            )
            return 0
        except ValueError as e:
            print(f"Error: {e}", file=sys.stderr)
            if "Unknown CLI" in str(e) or "Evidence string is required" in str(e):
                return 2
            return 1
        except Exception as e:
            print(f"Error: {e}", file=sys.stderr)
            return 1

    if args.command == "verify":
        if args.name != "antigravity":
            print(
                f"Error: Only 'antigravity' supports verification probes; got '{args.name}'.",
                file=sys.stderr,
            )
            return 2
        try:
            from antigravity_verification import verify_and_publish
        except ImportError as e:
            print(f"Error: Could not import antigravity_verification: {e}", file=sys.stderr)
            return 1

        try:
            res = verify_and_publish(root=args.root, force=args.force)
        except Exception as e:
            if args.json:
                print(json.dumps({"status": "error", "error": str(e)}, indent=2))
            else:
                print(f"Verification error: {e}", file=sys.stderr)
            return 1

        if args.json:
            print(json.dumps(res, indent=2))
        else:
            print(f"Antigravity verification: {res.get('status', 'unknown')}")

        if res.get("status") in ("current", "passed", "published", "ok"):
            return 0
        return 1

    return 2


if __name__ == "__main__":
    sys.exit(main())
