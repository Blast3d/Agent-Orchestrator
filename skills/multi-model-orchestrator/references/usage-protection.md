# Usage protection

Codex must check quota before assigning hosted work. On the configured Windows
host, `scripts/usage_guard.py` reads official quota reports, keeps shared pools
and reservations in `~/Documents/Agent-Orchestrator/runtime`, and warns through a local dashboard
and silent Windows notifications. The settings are user-editable in `policy.json`.

This host explicitly enables `quota_admission_mode: advisory` and
`worker_start_threshold_pct: 20`. Start from the last recorded reading without
waiting for collection. At or below 20% available after pending reservations,
prefer an eligible alternate. Stale, failed-refresh and missing readings are
warnings, not holds. A reading whose exact reset has passed is historical;
current allowance is unknown until measured again. Unknown is neither zero nor
100%. Confirmed current rejection cooldowns still hold that provider.

Tasks reserve
1/3/8/15 percentage points for tiny/small/medium/large work. These are conservative
initial estimates, not predictions of provider cost. Split large assignments into
bounded checkpoints and calibrate estimates from observed usage. Never assume a
2% remainder can finish a task. Advisory mode does not additionally subtract the
new task estimate and legacy 10% floor when deciding startup. Legacy strict mode
is retained for installations that explicitly want fresh-only admission; it is
not this host's active policy. Do not load local models for Orchestrator/Brain work.

For supplied-text Grok, Gemini, Claude, or local tasks, use
`scripts/dispatch_worker.py <worker> --prompt-file <file> --output <new-json-file>
--task <label> --size small`. It checks saved cloud quota, atomically reserves headroom,
executes one bounded task, records the result, and queues background collection
before and after execution. Collection is coalesced per provider and hidden; its
timeout or launch failure cannot cancel an admitted task. It does not
enable billable fallback. This helper is for answers; use a separately scoped CLI
for file edits, with the same quota reservation around it.

For direct CLI, native, or MCP handoffs outside the helper:

1. Read the saved provider evidence; queue a background refresh without waiting
   when needed. Use `check` for
   planning; `reserve <worker> --task <label> --size <size>` atomically grants a
   reservation under the configured advisory policy. A reader timeout alone is
   never a reason to skip the bot or silently fill its seat from the lead's provider.
2. Record the returned reservation ID before starting. Each parallel assignment
   needs its own reservation; models in one shared pool are not separate budgets.
3. After the actual job finishes, call `finish <id> --status completed|failed`.
   A completed estimate stays held until a newer provider reading arrives.
4. On a rate-limit rejection, use `block <worker>` and stop new jobs/retries on
   that pool. Preserve partial output and consider a verified available worker
   within the authorized cost/data scope. Never silently downgrade or spend more.

The CLI automatically tries the configured Claude/Grok alternate once after a
confirmed, safely finalized quota rejection or recorded low allowance. Explicit
`--fallback-worker` order takes precedence; `--no-auto-fallback` disables defaults.
Use stable project/assignment IDs for deliberate retries. Unkeyed requests get a
new receipt identity; outside the maintained workspace they use an unscoped
project with memory disabled. Reuse its recorded IDs to resume the same request.
Never infer quota exhaustion from a permission error or uncertain execution timeout.

`python orchestrator.py status` and `runtime/usage-status.json` expose the same
evidence as the usage page: raw remaining percentage, reserved estimate, available
estimate, original observation/reset times and collection failures. Snapshot
generation time is not an observation timestamp. Keep stale readings visibly cached.

Antigravity exposes quota-free `/usage` JSON with group/bucket IDs and reset times.
Grok's official terminal `/usage` panel exposes subscription allowance; a
terminal adapter must reject cached/loading/error displays. Per-job tokens or
reported dollar cost are not remaining allowance or proof of an extra charge.
Claude subscription windows and NotebookLM feature limits must be read through a
supported current interface. Missing data is unknown, not zero usage. For a manual
account reading, `record` requires its source, observed UTC timestamp, remaining
percentage, and optional exact reset timestamp; never invent a reset date.

The monitor is advisory for other applications: it cannot intercept a user using
Grok/Claude elsewhere or every possible direct tool call. Codex enforces this skill
at handoff; the dispatch helper enforces the reservation in code. Notifications
depend on Windows notification settings, and quota estimates cannot guarantee
completion of an unbounded task. Keep long work checkpointed.

See the local `Documents/Agent-Orchestrator/docs/usage-research.md` for dated source evidence.

A successful worker execution is `awaiting_review`; record the independent decision with
`python orchestrator.py review JOB_ID --decision accepted|rejected --reviewer Codex --note "Evidence of validation"`.
A timeout/interruption with uncertain provider completion retains its reservation as
`recovery_required`; inspect and reconcile before releasing or retrying. Automatic
Gemini supplied-text dispatch now uses the verified per-job deny-hook adapter;
AGY plan mode alone is not an isolation mechanism. Missing or changed boundary
evidence holds the route before inference.

## Interrupted usage-state recovery

On 2026-09-08, the saved usage state contained only zero bytes while canonical
task results remained readable. The cause was not established. Never replace a
corrupt usage state with an empty ready account or release uncertain jobs just
because the application restarted. Preserve the damaged bytes, compare retained
canonical reservation evidence, document missing history and reconstructed
estimates, and require fresh official readings before hosted work. Keep uncertain
provider jobs held. A completed canonical answer can be recovered separately from
an unfinished export without repeating inference; finalization and quota cleanup
still need explicit reconciliation.

The maintained usage writer now flushes and syncs its temporary file before
replacement. Failure-injection tests cover old-state preservation and fail-closed
corruption handling; they do not prove survival of every power-loss scenario.

## Monitor and quota-reader diagnosis, September 8, 2026

A prior invalid-JSON failure stopped the monitor. Recovering the usage file did
not restart that process: verify the recorded monitor PID and a completed fresh
refresh cycle, then use `python orchestrator.py monitor --restart` when needed.
An installation doctor pass is not proof that the monitor or a provider works.
The current helper does not provide automatic crash recovery.

Quota-reader failure is not evidence of exhausted allowance or failed login.
The guard now preserves recognized collector causes (timeout, setup/trust,
unverified refresh, or missing executable), while suppressing arbitrary terminal
output. Failed reads invalidate freshness but advisory mode permits work from
the saved allowance. A successful
retry confirms that reading only; it does not repair an intermittent reader.
Keep prior uncertain reservations until reconciliation.

The `status` command reports quota eligibility. Check the worker registry and
dispatcher restrictions separately: fresh Antigravity allowance alone does not
satisfy its verified-boundary checks. Grok Build and Grok Bot have separate budgets.
