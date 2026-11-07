# Worker CLI updates

Worker CLIs (Antigravity `agy`, Grok Build, Claude Code, Codex) may update. The
orchestrator notices updates and verifies them instead of silently breaking or
holding work.

```powershell
python orchestrator.py updates status            # installed vs last verified, all CLIs
python orchestrator.py updates verify antigravity  # re-verify the Gemini tool boundary
python orchestrator.py updates accept grok --evidence "task 1234... accepted on grok 0.9"
```

`updates status` runs each CLI's `--version` with update suppression
(`AGY_CLI_DISABLE_AUTO_UPDATE=true`, `--no-auto-update`, `DISABLE_AUTOUPDATER=1`)
and hashes the executable. It makes no model calls. States: `current`,
`updated` (the binary changed since it was verified), `unverified`, `missing`,
`error`.

## Antigravity (Gemini)

Gemini runs through the Antigravity CLI as a supplied-text worker whose tools are
denied by a per-job wildcard PreToolUse hook. `config/antigravity-boundary.json`
pins the verified `agy.exe`, handler and Python hashes; any change holds Gemini
before inference. When a Gemini dispatch (or `run auto`) finds the executable
changed, the dispatcher runs the verification once for that binary:

1. zero-usage local inspections (`/hooks`, `/config`), then four synthetic probes
   with the production launch flags: legacy tool names, the model's own tool per
   capability, a fail-closed hook timeout, and a configured local MCP server
   (the global MCP config is swapped and restored byte for byte);
2. every attempted call must be rejected, each hook denial correlated one to one
   with the handler log, no marker or canary change, and the MCP counter fixture
   proven to work when called directly;
3. only when every check passes is the new proof published (the old one is kept
   with the evidence under `runtime/antigravity-verification/`).

The same check runs before `run auto` and `balance` pick a worker, and run startup
reports a changed binary in the packet. Turn the automatic step off with
`"policy": {"auto_verify_cli_updates": false}` in `config/workers.json`; Gemini then
stays held until `updates verify antigravity` succeeds.

A failed verification is recorded and not retried automatically for that binary;
Gemini stays held and `updates status` shows why. `--force` reruns it. Each
verification costs four synthetic Gemini sessions (about 150,000 input tokens on
1.5.3) and is labeled synthetic, so it never counts as worker participation.

## Other CLIs

Grok, Claude Code and Codex have no hash-pinned boundary; their tool limits come
from launch flags. After an update, `updates status` shows `updated`; once a real
task on the new version is reviewed, record it with `updates accept NAME
--evidence ...`.

## Manual commands

Never run a worker CLI by hand without update suppression. A plain `agy models`
updated Antigravity from 1.2.16 to 1.5.3 on November 7, 2026.
