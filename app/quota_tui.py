"""Read account allowance using official Grok/Claude CLI /usage (no inference).

Only the minimal-mode fetched summary is accepted. The fullscreen usage modal
may show an old balance even while a refresh is loading or has failed.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import os
import re
import select
import subprocess
import sys
import time

from paths import VENDOR, WORKSPACES
sys.path.insert(0, str(VENDOR))
VERIFIED_GROK_VERSIONS = {"1.0.13"}
# Enabled only after a real /usage display and its refresh transition are tested.
VERIFIED_CLAUDE_VERSIONS: set[str] = {"2.1.263"}


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def parse_grok_screen(display: str, observed_at: str | None = None) -> dict:
    """Parse a new no-inference minimal session, never a generic percentage.

    Official credit_bar.rs floors usage_pct, so e.g. 0% used means [0, 1)%
    used. Subtract one full display unit for a conservative remaining bound.
    The provider prints a local wall clock without year/time zone: keep it as
    display text instead of inventing an exact reset instant.
    """
    version_match = re.search(r"Grok Build\s+v(\d+\.\d+\.\d+)\b", display)
    if not version_match or version_match[1] not in VERIFIED_GROK_VERSIONS:
        raise ValueError("Grok CLI version is unverified; revalidate the usage adapter after updates.")
    if "Context usage  Usage limit  Session info" in display:
        raise ValueError("The fullscreen usage modal can display cached data; it is not accepted.")
    marker = "Session usage: no model calls yet in this session."
    if display.count(marker) != 1:
        raise ValueError("Expected one fresh session with no model calls.")
    tail = display.split(marker, 1)[1]
    if re.search(r"couldn't|could not|failed|cached|loading|billing error", tail, re.I):
        raise ValueError("The official usage fetch is incomplete or failed.")
    matches = list(re.finditer(
        r"^\s*Weekly limit: (?P<used>\d{1,3})%\s*\n"
        r"\s*Next reset: (?P<reset>(?:January|February|March|April|May|June|July|August|"
        r"September|October|November|December) (?:[1-9]|[12]\d|3[01]), "
        r"(?:[01]\d|2[0-3]):[0-5]\d)\s*(?:\n|$)", tail, re.M,
    ))
    if len(matches) != 1:
        raise ValueError("No unique complete weekly usage summary was returned by Grok.")
    used = int(matches[0]["used"])
    if not 0 <= used <= 100:
        raise ValueError("The displayed weekly usage is outside 0 to 100 percent.")
    observed = observed_at or utc_now()
    return {
        "provider": "grok", "status": "ok", "cli_version": version_match[1],
        "observed_at": observed, "no_model_calls": True,
        "freshness": "successful /usage summary from a newly launched minimal-mode session",
        "windows": [{
            "id": "grok-weekly", "remaining_pct": max(0, 99 - used),
            "displayed_used_pct": used, "remaining_is_lower_bound": True,
            "reset_at": None, "reset_display": matches[0]["reset"],
            "source": "official Grok CLI /usage", "observed_at": observed,
            "max_age_seconds": 600,
        }],
        "evidence": [marker, f"Weekly limit: {used}%",
                     f"Next reset: {matches[0]['reset']}"],
    }


def run_grok(timeout: float = 30.0) -> dict:
    import pyte
    from winpty import Backend, PtyProcess

    executable = Path.home() / ".grok" / "bin" / "grok.exe"
    if not executable.is_file():
        raise RuntimeError("The official Grok CLI is not installed at the configured location.")
    env = dict(os.environ)
    env.update(TERM="xterm-256color", COLORTERM="truecolor")
    proc = PtyProcess.spawn(
        [str(executable), "--no-auto-update", "--no-alt-screen", "--minimal",
         "--no-subagents", "--disable-web-search", "--tools=",
         "--permission-mode", "dontAsk"],
        cwd=str(WORKSPACES / "grok"), env=env,
        dimensions=(60, 160), backend=Backend.ConPTY,
    )

    class Screen(pyte.Screen):
        def write_process_input(self, data: str) -> None:
            proc.write(data)

    screen = Screen(160, 60)
    stream = pyte.Stream(screen)
    start = time.monotonic()
    sent = False
    last_display = ""
    try:
        while time.monotonic() - start < min(timeout, 30):
            ready, _, _ = select.select([proc.fileobj], [], [], 0.15)
            if ready:
                try:
                    stream.feed(proc.read(65536))
                except EOFError:
                    break
                last_display = "\n".join(line.rstrip() for line in screen.display).strip()
            elapsed = time.monotonic() - start
            if not sent and elapsed >= 2 and "/help for commands" in last_display:
                proc.write("/usage\r")
                sent = True
            if sent and "Weekly limit:" in last_display and "Next reset:" in last_display:
                try:
                    return parse_grok_screen(last_display)
                except ValueError:
                    # An ANSI update may split across reads. Keep waiting for a
                    # complete rendered summary; never accept partial output.
                    pass
            if not proc.isalive():
                break
        raise RuntimeError("No complete Grok usage display appeared within the time limit.")
    finally:
        try:
            proc.write("\x1b")
            proc.write("\x03")
            proc.write("\x03")
        except Exception:
            pass
        proc.close(force=True)


def parse_claude_screen(display: str, *, cli_version: str,
                        observed_at: str | None = None) -> dict:
    """Parse labeled plan bars, excluding context percentages and dollar totals.

    The collector owns freshness. Parser fixtures can exercise unknown UI
    versions, but run_claude will not accept a version until it is live-verified.
    """
    if re.search(r"couldn't|could not|failed|cached|loading|refreshing|error fetching|unavailable", display, re.I):
        raise ValueError("Claude usage is incomplete, unavailable, or cached.")
    # Terminal decoration is ignored; labels and explicit 'used' orientation are
    # required. Context bars and dollar amounts can never be parsed as quotas.
    lines = [re.sub(r"^[\s\u2502]+|[\s\u2502]+$", "", x) for x in display.splitlines()]
    # Local session analytics and promotional/credit panels are not account
    # quota windows. Their independent percentages must not become allowances.
    for index, line in enumerate(lines):
        if line.startswith(("What's contributing to your limits usage?", "Usage credits")):
            lines = lines[:index]
            break
    headers: list[tuple[int, str, str | None]] = []
    for index, line in enumerate(lines):
        if line == "Current session":
            headers.append((index, "claude-five-hour", None))
        elif line == "Current week (all models)":
            headers.append((index, "claude-seven-day", None))
        else:
            extra = re.fullmatch(r"Current week \(([A-Za-z][A-Za-z0-9 .-]{0,35}) only\)", line)
            if extra:
                family = re.sub(r"[^a-z0-9]+", "-", extra[1].lower()).strip("-")
                headers.append((index, f"claude-seven-day-{family}", family))
            elif re.match(r"Current (?:session|week|day|month)\b", line):
                raise ValueError("Claude displayed an unrecognized allowance window; review the adapter.")
    ids = [item[1] for item in headers]
    if len(ids) != len(set(ids)):
        raise ValueError("Claude usage contains duplicate allowance windows.")
    if not {"claude-five-hour", "claude-seven-day"}.issubset(ids):
        raise ValueError("Both Claude account-wide session and weekly allowances are required.")
    observed = observed_at or utc_now()
    windows = []
    evidence = []
    for position, (index, window_id, family) in enumerate(headers):
        end = headers[position + 1][0] if position + 1 < len(headers) else len(lines)
        block = lines[index + 1:end]
        percentages = [re.search(r"(?<![\d.])(\d{1,3})% used\b", line) for line in block]
        percentages = [match for match in percentages if match]
        if len(percentages) != 1:
            raise ValueError("Claude allowance must contain exactly one labeled used percentage.")
        used = int(percentages[0][1])
        if not 0 <= used <= 100:
            raise ValueError("Claude used percentage is outside 0 to 100.")
        resets = [line for line in block if re.match(r"Resets?\s", line)]
        if len(resets) != 1 or len(resets[0]) > 160:
            raise ValueError("Claude allowance reset display is missing or ambiguous.")
        window = {
            "id": window_id, "remaining_pct": max(0, 99 - used),
            "displayed_used_pct": used, "remaining_is_lower_bound": True,
            "reset_at": None, "reset_display": re.sub(r"^Resets?\s+", "", resets[0]),
            "source": "official Claude Code /usage", "observed_at": observed,
            "max_age_seconds": 600,
        }
        if family:
            window["model_family"] = family
        windows.append(window)
        evidence.extend([lines[index], f"{used}% used", resets[0]])
    return {
        "provider": "claude", "status": "ok", "cli_version": cli_version,
        "observed_at": observed, "no_model_calls": True,
        "windows": windows, "evidence": evidence,
    }


def capture_claude_usage(timeout: float = 30.0) -> tuple[str, str, bool]:
    """Launch a new safe-mode TUI and request only its built-in usage panel.

    Returns a rendered screen internally, not a saved terminal transcript. Any
    onboarding, login or folder-trust prompt is a stop condition, not an answer
    the monitor supplies. A loaded panel is accepted only after its loading
    state was observed in this new process; there is no startup-cache fallback.
    """
    import pyte
    from winpty import Backend, PtyProcess

    executable = Path.home() / "AppData/Roaming/npm/node_modules/@anthropic-ai/claude-code/bin/claude.exe"
    if not executable.is_file():
        raise RuntimeError("Claude Code is not installed at its configured location.")
    version_result = subprocess.run([str(executable), "--version"], capture_output=True,
                                    text=True, timeout=5, creationflags=subprocess.CREATE_NO_WINDOW)
    match = re.fullmatch(r"(\d+\.\d+\.\d+) \(Claude Code\)\s*", version_result.stdout)
    if version_result.returncode or not match:
        raise RuntimeError("Could not verify the installed Claude Code version.")
    version = match[1]
    workspace = WORKSPACES / "claude"
    if not workspace.is_dir():
        raise RuntimeError("Claude's isolated worker folder has not been prepared.")
    env = dict(os.environ)
    env.update(TERM="xterm-256color", COLORTERM="truecolor", DISABLE_AUTOUPDATER="1")
    # This monitor-added umbrella flag prevented /usage fetching in 2.1.263.
    # Restore the normal authenticated CLI path for this child, not global settings.
    env.pop("CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC", None)
    env.pop("ANTHROPIC_API_KEY", None)
    proc = PtyProcess.spawn(
        [str(executable), "--safe-mode", "--no-chrome", "--strict-mcp-config",
         "--tools=", "--permission-mode", "dontAsk", "--ax-screen-reader",
         "--settings", '{"remoteControlAtStartup":false}'],
        cwd=str(workspace), env=env, dimensions=(85, 180), backend=Backend.ConPTY,
    )

    class Screen(pyte.Screen):
        def write_process_input(self, data: str) -> None:
            proc.write(data)

    screen = Screen(180, 85)
    stream = pyte.Stream(screen)
    start = time.monotonic()
    sent = False
    saw_loading = False
    last_display = ""
    candidate_since: float | None = None
    try:
        while time.monotonic() - start < min(timeout, 30):
            ready, _, _ = select.select([proc.fileobj], [], [], 0.05)
            if ready:
                try:
                    stream.feed(proc.read(65536))
                except EOFError:
                    break
                last_display = "\n".join(line.rstrip() for line in screen.display).strip()
            # No action is taken in any setup/consent dialog.
            if re.search(r"Choose the text style|Select login method:|Browser didn't open\?|"
                         r"Do you trust|trust the files|trust this folder|trust this directory", last_display, re.I):
                raise RuntimeError("Claude Code interactive onboarding, authorization or isolated-folder trust is incomplete.")
            if not sent and time.monotonic() - start >= 2 and (
                "? for shortcuts" in last_display or "/help for" in last_display
                or (f"Claude Code v{version}" in last_display and
                    re.search(r"^\$\s*$", last_display, re.M))
            ):
                proc.write("/usage\r")
                sent = True
            if sent:
                if re.search(r"loading(?: your)? (?:usage|limits|plan)|loading\.\.\.|refreshing[.\u2026]", last_display, re.I):
                    saw_loading = True
                try:
                    parse_claude_screen(last_display, cli_version=version)
                    candidate_since = candidate_since or time.monotonic()
                except ValueError:
                    candidate_since = None
                if candidate_since is not None and time.monotonic() - candidate_since >= 0.4:
                    if not re.search(r"Usage:\s+0 input, 0 output, 0 cache read, 0 cache write", last_display):
                        raise RuntimeError("The quota-only session did not verify zero model tokens.")
                    return last_display, version, saw_loading
            if not proc.isalive():
                break
        raise RuntimeError("No complete Claude Code /usage panel appeared within the time limit.")
    finally:
        try:
            proc.write("\x1b\x03\x03")
        except Exception:
            pass
        proc.close(force=True)


def run_claude(timeout: float = 30.0) -> dict:
    display, version, saw_loading = capture_claude_usage(timeout)
    if version not in VERIFIED_CLAUDE_VERSIONS:
        raise RuntimeError("Claude's current usage display has not yet been live-validated; quota remains unknown.")
    if not saw_loading:
        raise RuntimeError("Claude usage refresh could not be distinguished from cached data; quota remains unknown.")
    result = parse_claude_screen(display, cli_version=version)
    result["freshness"] = "new safe-mode TUI /usage loading-to-loaded transition"
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("provider", nargs="?", choices=["grok", "claude"])
    parser.add_argument("--provider", dest="provider_flag", choices=["grok", "claude"])
    parser.add_argument("--timeout", type=float, default=30)
    parser.add_argument("--output", type=Path, help="Also save the normalized reading (no terminal transcript).")
    args = parser.parse_args()
    provider = args.provider_flag or args.provider
    if provider is None or (args.provider_flag and args.provider and args.provider_flag != args.provider):
        parser.error("Specify exactly one provider: grok or claude")
    try:
        if not 1 <= args.timeout <= 30:
            raise ValueError("timeout must be between 1 and 30 seconds")
        result = run_claude(args.timeout) if provider == "claude" else run_grok(args.timeout)
    except Exception as exc:
        result = {"provider": provider, "status": "unknown", "windows": [],
                  "error": str(exc), "observed_at": utc_now()}
    serialized = json.dumps(result, ensure_ascii=True, indent=2)
    if args.output:
        args.output.write_text(serialized + "\n", encoding="utf-8")
    print(serialized)
    return 0 if result.get("status") != "unknown" else 2


if __name__ == "__main__":
    raise SystemExit(main())
