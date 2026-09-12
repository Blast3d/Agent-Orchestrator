"""Read-only candidate discovery; no model execution, network, or secret values."""
import argparse
import json
import os
from pathlib import Path
import shutil
import sys


def inventory():
    windows = os.name == "nt"
    names = {
        "codex": ["codex.exe", "codex.cmd", "codex"],
        "claude-code": ["claude.cmd", "claude.exe", "claude"],
        "gemini-cli": ["gemini.cmd", "gemini.exe", "gemini"],
        "antigravity": ["antigravity.cmd", "antigravity.exe", "antigravity"],
        "antigravity-cli": ["agy.exe", "agy"],
        "grok-build": ["grok.exe", "grok.cmd", "grok"],
        "ollama": ["ollama.exe", "ollama"],
        "lm-studio-cli": ["lms.exe", "lms.cmd", "lms"],
        "opencode": ["opencode.exe", "opencode.cmd", "opencode"],
        "aider": ["aider.exe", "aider"],
        "notebooklm-cli": ["nlm.exe", "nlm"],
        "github-copilot-cli": ["copilot.exe", "copilot.cmd", "copilot"],
        "cursor": ["cursor.cmd", "cursor.exe", "cursor"],
    }
    home = Path.home()
    codex_base = Path(os.environ.get("CODEX_HOME", home / ".codex"))
    worker_paths = {}
    disabled_routes = {}
    registry_status = "not-found"
    try:
        registry = codex_base / "model-workers.json"
        if registry.is_file():
            data = json.loads(registry.read_text(encoding="utf-8-sig"))
            for worker in data.get("workers", []):
                if isinstance(worker, dict) and worker.get("tool") in names:
                    executable = worker.get("executable")
                    if isinstance(executable, str) and Path(executable).is_absolute():
                        worker_paths.setdefault(worker["tool"], []).append(Path(executable))
            for route in data.get("disabled_routes", []):
                if isinstance(route, dict) and route.get("tool") in names:
                    disabled_routes[route["tool"]] = {
                        key: route[key] for key in ("scope", "reason", "replacement_tool")
                        if isinstance(route.get(key), str)
                    }
            registry_status = "read"
    except (OSError, ValueError, TypeError):
        registry_status = "unreadable-or-invalid"
    search_path = os.environ.get("PATH", "")
    if windows:
        try:
            import winreg
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, "Environment") as key:
                user_path = winreg.QueryValueEx(key, "Path")[0]
                search_path += os.pathsep + os.path.expandvars(user_path)
        except OSError:
            pass
    known = {}
    if windows:
        local = Path(os.environ.get("LOCALAPPDATA", home / "AppData" / "Local"))
        roaming = Path(os.environ.get("APPDATA", home / "AppData" / "Roaming"))
        known = {
            "antigravity": [local / "Programs/Antigravity/Antigravity.exe"],
            "antigravity-cli": [local / "agy/bin/agy.exe"],
            "grok-build": [home / ".grok/bin/grok.exe"],
            "ollama": [local / "Programs/Ollama/ollama.exe"],
            "lm-studio-app": [local / "Programs/LM Studio/LM Studio.exe"],
            "gemini-cli": [roaming / "npm/gemini.cmd"],
            "opencode": [home / ".opencode/bin/opencode.exe"],
        }
    result = []
    for tool, paths in worker_paths.items():
        known.setdefault(tool, []).extend(paths)
    for tool in sorted(set(names) | set(known)):
        found = next((n for n in names.get(tool, []) if shutil.which(n, path=search_path)), None)
        conventional = any(p.is_file() for p in known.get(tool, []))
        result.append({"tool": tool, "discovery": "on-path" if found else "known-location" if conventional else "not-found-in-checked-locations", "readiness": "not-probed"})

    for candidate in result:
        if candidate["tool"] in disabled_routes:
            candidate["routing"] = "disabled-for-configured-worker"
            candidate["route_note"] = disabled_routes[candidate["tool"]]

    servers = []
    config_status = "not-found"
    config = codex_base / "config.toml"
    try:
        import tomllib
        if config.is_file():
            config_status = "read"
            data = tomllib.loads(config.read_text(encoding="utf-8-sig"))
            for name, value in data.get("mcp_servers", {}).items():
                if isinstance(value, dict):
                    servers.append({"name": name, "enabled": value.get("enabled", True), "transport": "http" if value.get("url") else "stdio", "readiness": "not-probed"})
    except ImportError:
        config_status = "requires-python-3.11-for-toml"
    except (OSError, ValueError):
        config_status = "unreadable-or-invalid"
    from vscode_bots import status as vscode_status
    return {"local_worker_registry": registry_status, "candidates": result, "codex_mcp_config": config_status, "configured_mcp": servers,
            "vscode_chat_integrations": vscode_status()['integrations'],
            "notes": ["No model, login, quota or external network probe was performed. VS Code bridge discovery checks authenticated loopback status only.", "Missing PATH entries do not prove an application is uninstalled.", "Inspect tools exposed by the current host for callable MCP capabilities."]}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, help="Save JSON to a new file; never overwrite")
    args = parser.parse_args()
    payload = json.dumps(inventory(), indent=2) + "\n"
    if args.output:
        try:
            with args.output.open("x", encoding="utf-8") as handle:
                handle.write(payload)
        except OSError as exc:
            parser.exit(1, f"Cannot save inventory ({type(exc).__name__}); destination must be writable and new.\n")
    else:
        sys.stdout.write(payload)


if __name__ == "__main__":
    main()
