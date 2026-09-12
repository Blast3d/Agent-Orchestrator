# Provider routing and execution

Read the installed version's help and the live tool schema before executing a
command. These are routing instructions, not a claim that every listed tool is
installed, authenticated, free, or available in every host.

## Current host discovery

Run `python <skill-directory>/scripts/inventory_agents.py` for a redacted local
candidate inventory. It checks PATH, a few conventional install locations, and
MCP server names without invoking models or contacting services. Inspect current
exposed MCP tools separately. Do not infer absence of an MCP capability from a
missing command on PATH, or readiness from an enabled config entry.

On the original Windows host, Claude Code CLI and the Antigravity application
were found, and NotebookLM MCP reported configured authentication on September 7,
2026. These are a dated observation to recheck, not required dependencies.

## Built-in subagents and Codex

Use native delegation for bounded local jobs when available and useful. Respect
the parent host's concurrency and model-selection rules. Give workers disjoint
writes. A second native worker may still be the same model as the coordinator.

When a separate CLI process is useful, inspect `codex exec --help` and use a
specific working directory and sandbox. Do not start it from the whole home
directory to work on a small project. Codex's local CLI also advertises
`--oss --local-provider ollama|lmstudio`; this requires a compatible local server
and model, not merely the CLI flag. Existing user configuration determines the
actual backend. Do not bypass sandbox or hook trust to automate execution.

## Claude Code / Anthropic

- Prompt-only comparison: use the exposed `aiPrompts.ask_claude` equivalent when
  available. Its supplied-text isolation avoids implicitly sending a repository.
- File creation: use the official Claude Code CLI in the explicitly selected
  output folder/worktree. Check `claude --help` first. On Windows, use
  `claude.cmd` when `.ps1` shims are blocked.
- The original workflow used noninteractive print/JSON output, a clean context,
  normal permissions, and only Read/Write/Edit for narrative files. When supported,
  `--safe-mode` prevents unrelated customizations/hooks from entering a worker;
  `--tools Read,Write,Edit` limits a content-making worker. Select an appropriate
  normal permission mode for already-authorized isolated edits. Never use
  `--dangerously-skip-permissions` as the orchestration default.
- `--bare` and `--safe-mode` are not interchangeable: the installed help says
  bare mode changes authentication behavior. Preserve working login unless the
  task specifically calls for an API-key environment.
- Feed lengthy prompts through stdin or a UTF-8 task file using an API argument
  array or safe shell quoting. Do not interpolate Markdown into shell commands.

Claude Code subscriptions, Anthropic API access, and another app's Claude access
are distinct entitlements. This skill does not make Claude API calls free.

September 7/8, 2026 execution lesson: the old dispatcher killed every hosted job
at 180 seconds and discarded timeout output. A replay of the original Claude
coding contract on Code 2.1.263 / Sonnet 5 began answering after 372 seconds and
completed in 472 seconds with no observed retries. A smaller low-effort policy
module completed in 15.6 seconds. These are dated task observations, not latency
guarantees or proof of the old backend's exact state. Use task-sized hard deadlines
and `--output-format stream-json --verbose --include-partial-messages` for Claude.
The maintained Windows runner reads pipes concurrently: `communicate()` timeout
polling buffered events until EOF on this host. Persist safe progress metadata and
answer deltas, never thinking text. Require a valid terminal result and normal
process completion before review; CLI silence is not evidence of quota exhaustion.
Official: [streaming and terminal events](https://code.claude.com/docs/en/headless).

## Antigravity

Use an exposed prompt connector for supplied-text design or architecture input.
The original `aiPrompts.ask_antigravity` wrapper ran a fresh conversation in an
empty folder in plan mode, with no project tools. That is suitable for an answer,
not proof of repository edits. If actual editing is requested, discover the
installed official interface and its permissions. Record the selected model if
returned; Antigravity may be a different interface to an already-used model.

## NotebookLM

Use available notebook tools for source-grounded synthesis, mind maps, alternate
slides, and audio/video overviews. The MCP connector is an adapter; tool names do
not establish a general public Google API or an account's feature entitlement.

1. Check available tools/auth status. Reuse the intended notebook and its sources
   if the current task calls for it; otherwise create a task-specific notebook.
2. Supply the reviewed source brief and only content authorized for Google.
   Do not bulk-upload arbitrary project directories.
3. Ask grounded questions or create the requested Studio artifact. If a schema
   requires `confirm=true`, use existing explicit authorization for that action
   where it applies; do not assume the skill itself supplies authorization.
4. Save notebook and artifact/job IDs immediately. Poll the returned job's status;
   after a timeout, check for an existing artifact before another create request.
5. Download and inspect completed files. If only a sharing URL is returned, check
   whether the intended recipient can access it. For portable delivery, export
   local artifacts and include a local mind-map representation where feasible.

A generated discussion can introduce unsupported claims even from a good brief.
Review its transcript; revise, regenerate with a narrower brief, or label it as a
draft with corrections. Keep exact-script narration as a separate deliverable
when fidelity matters. Refresh expired login through the connector's supported
login flow; never extract or display browser cookies to force access.

## Gemini through Antigravity CLI

For consumer Google accounts, use the supported Antigravity CLI (`agy`). Google
ended Gemini CLI's consumer Code Assist route on June 18, 2026; successful Google
sign-in to that old client does not establish working inference. API-key and
enterprise routes have separate entitlements; do not silently switch billing.

Check `agy models`, then pin a returned model ID. On the original Windows host,
`agy` was installed under `%LOCALAPPDATA%/agy/bin/agy.exe`, even when absent from
the current process PATH. A supplied-text test with `gemini-3.8-flash-medium`
succeeded on September 7, 2026. Use headless JSON output, an explicit working
directory, and normal tool permissions; plan mode alone is not a filesystem
sandbox. Verify the returned answer/artifact as well as the process exit code.

Use supported account authentication. Preserve `useG1Credits: false` in
`~/.gemini/antigravity-cli/settings.json` for included-quota-only operation.
Do not set `modelProvider: "gemini"` for this account route: that selects API-key
authentication. Never export CLI OAuth tokens into third-party clients.

Official: [consumer shutdown](https://developers.google.com/gemini-code-assist/docs/deprecations),
[headless use](https://antigravity.google/docs/cli/headless/),
[credit controls](https://antigravity.google/docs/cli/credits/).

The September 7 thirteen-agent panel completed three supervised Antigravity
turns with `gemini-3.8-flash-medium` and no observed tool events. Its exact scratch
workspace used a trusted wildcard PreToolUse deny hook, explicit `--add-dir`,
the default agent and captured streams. Cwd alone did not discover the hook;
an empty custom-agent tools list was not sufficient. File, command, web,
delegation, messaging and scheduling denials were exercised. MCP and hook-timeout
boundaries were not fully tested then. That original project-specific decision
was superseded by the reusable adapter validation described below.

## Grok from xAI

Grok and Groq are different services. Prefer the official Grok Build CLI for
bounded scripted work when the account is eligible. Inspect `grok models` and
pin a model from xAI; custom CLI backends can use other providers. Record returned
model metadata, or `unknown` when not exposed; a model's self-description is not
identity verification. Use `--no-auto-update`, headless JSON, a turn cap, and
scoped tool permissions. Disable nested subagents for a simple assigned task;
Codex owns integration. A browser login alone does not prove quota or task success.

On this Windows installation, Grok Build 1.0.13 needs `--verbatim` to retain long
supplied prompts: otherwise they can become invalid file references. Empty
`--tools=` and `dontAsk` do not establish zero tools; read-only tools may be
auto-allowed. Use the maintained dispatcher's nonempty allowlist followed by
explicit exclusions, disable subagents and web search, and deny all file,
command and MCP permission categories. A September 7 canary task returned
`NO_TOOLS`, exposed no canary content, created no marker and showed no tool
execution in its matching session. This is CLI-level evidence, not an OS sandbox.
Recheck these semantics after CLI upgrades. A failed file read with
`FileNotFound` is not evidence that permissions blocked it.

The same 180-second cutoff interrupted a Grok coding job while its session still
reported reasoning. A separate bounded parser assignment with explicit low
reasoning effort completed in 61 seconds on September 7/8. The dispatcher now
pins low effort for this supplied-text route and records its fixed configuration.
Grok's JSON route buffers its answer; elapsed time and output counts are available,
but it does not claim Claude-style live thinking/answer events. The successful
parser job reported `grok-4.6-build` in `modelUsage`, although its top-level model
field was empty. Check all structured identity evidence; do not rely on a model's
self-description or confuse a requested ID with a returned one.

Grok Bot is a separate app with persistent computers and cooperating named Bots.
No supported external Bot job-control endpoint was found in the September 2026
documentation review. Its model is managed by the service; do not claim each Bot
is a distinct model. Grok-to-Cursor account linking is permanent, so it is not a
routine reversible setup step. Confirm exact account entitlement and any enabled
on-demand spending before relying on Bots. The xAI docs MCP searches documentation;
it does not invoke inference. API-key access also needs verified usable quota.

The Windows Grok Bot app was installed under Program Files and Atlas completed
a bounded proposal, shared discussion and final ballot on September 7, 2026. Its underlying model was not
reported. Supervised UI work is available; do not describe Grok Build CLI seats
as Bot-app agents. The app has its own `grok-bot` worker and `grokbot-weekly`
pool. Read Settings > Usage & Billing, record the observed remaining percentage
and timestamp, then reserve before dispatch. A CLI allowance is not a Bot reading.
There is no verified automatic Bot quota reader: manual readings expire after
ten minutes, hold new work, and trigger the existing monitor's warning path.
Check spending controls without enabling overage or linking accounts as a side
effect. Prefer all-users installation when the official installer supports it,
as requested by this user; per-user installation does not prevent orchestration.

Official: [CLI](https://docs.x.ai/build/cli/headless-scripting),
[Bot plans and linking](https://cursor.com/help/grok-bot/plans),
[Bot collaboration](https://docs.x.ai/grok-bot/bots).

## Local models: Ollama or LM Studio

These are runtimes, not extra model families. Inspect installed/loaded models and
hardware before choosing a task. Favor small structured extraction, classification,
or second-draft jobs that the selected model handles well; evaluate quality before
using a local model as the sole technical reviewer.

Use an existing loopback API, or an explicit supported CLI. Keep a local-only job
on downloaded local models; Ollama also offers cloud routing. Do not expose the
server on the LAN or pull multi-gigabyte models just to perform discovery.

For this Windows setup, consult the local worker registry and its profile before
starting Ollama. The tested small chat model is explicitly
`qwen3:4b-instruct-2507-q4_K_M` behind `codex-chat-light`; the generic `qwen3:4b`
tag resolved to a thinking variant during setup. Preserve the working template
and pinned tag. Basic chat and prompted JSON extraction passed; validate parsed
values in Codex. Native schema-constrained output failed this setup's small test
and is not a verified capability. The profile limits one loaded model, one request,
4K context, and two-minute residency. GPU scheduler overhead is an allowance,
not a hard VRAM reservation. Check current GPU use and avoid overlapping heavy
local jobs with active dictation. Do not stop or reconfigure OpenWhispr's separate
llama.cpp service as part of unloading the Ollama worker.

## Source-panel observations, September 7/8, 2026

The pinned local Qwen worker returned three unsupported or already-implemented
KM UI suggestions despite narrowed excerpts. Those attempts were rejected and
its seat was reassigned to a native source auditor. Basic chat capability remains
separate; this model is not currently a reliable independent code reviewer.

For Grok Bot GUI prompts, set UTF-8 explicitly at both clipboard write and read
boundaries. Two mismatched console encodings can appear to round-trip while the
Bot sees garbled characters. Inspect representative visible text as well as the
copied prompt. Transport corruption is not an application rendering defect.

Claude's official quota TUI was intermittently unavailable during this run. Two
coding attempts were held before inference; later source audits succeeded using
the unchanged guarded route. A fresh direct reading is not a permanent fix.
Keep telemetry failure distinct from confirmed quota exhaustion.

## Multi-provider frontends and API gateways

OpenCode and Aider can provide a common coding interface to configured models.
OpenRouter and Groq provide hosted inference. A client/frontend is not itself an
independent model. Read current provider model IDs, free limits, and routing/data
policies before selection. Use free-only routes for a free-only task, disable
billable fallbacks, and record the actual returned model/provider when known.

Use supported account/API authentication and existing secret storage. A model
listing is not proof an account can call it. First validate a small, nonsensitive
task through the intended interface if that integration is being set up.

## Readiness diagnosis, September 8, 2026

Separate non-selection, preflight holds, provider execution failures, and rejected
answers in reports. The KM workstation skills package used native helpers by
coordinator choice; no external worker failed on that packaging task. Earlier
source audits and building tasks did produce accepted Claude and Grok outputs.

The later Claude OpenWhispr service attempt hit its 600-second deadline with
805 streamed events, ongoing thinking, and no answer text. Local execution ended
but provider completion remained uncertain. A tiny, no-tools, low-effort Claude
task subsequently returned the exact expected answer in 2.34 seconds. Prefer
bounded deliverables and appropriate effort; this small check does not prove
complex coding readiness. Keep the original timeout evidence and reservation.

Grok Build and local Ollama also passed that same synthetic response check. The
Ollama worker had been stopped and was restarted on its existing pinned model;
the dispatcher can start this service itself. Past rejected local source-audit
suggestions were output-quality failures, not proof of a missing runtime.

The prior Grok Bot OpenWhispr handoff recorded launcher exit without a usable
window, before any provider request. Its current separate allowance is unknown.
The tested Build CLI is not evidence that the Bot desktop adapter is working.
At that diagnosis, Antigravity quota reads worked while automatic dispatch was
held. The later verified adapter below supersedes that hold; preserve the dated
distinction instead of treating the historical restriction as current.

## Verified automatic Antigravity assignments, September 8, 2026

Use `python orchestrator.py run gemini` through the maintained app. The exact
installed executable, Python interpreter and fixed deny handler are fingerprinted
in `config/antigravity-boundary.json`. Each new assignment gets a fresh scratch
folder, explicit `--add-dir`, the default agent, plan mode and sandbox flag. Local
`/hooks` and `/config` responses must prove zero model usage, the exact wildcard
PreToolUse deny hook, request-review permissions, account-backed model routing,
and disabled credit overage. Global MCP configuration must remain empty. Settings
and validation fingerprints are checked again after quota reservation. Unknown
or changed state holds the assignment; never remove the checks to force a run.

Live synthetic tests observed real denials for file reading/writing, shell, web,
MCP execution, subagents, messaging and scheduling. A hook that sleeps longer than
its timeout also blocked its file-write attempt. A normal supplied-text task then
returned exact expected JSON through the main dispatcher on
`gemini-3.8-flash-medium`, with zero tool events. This validates the shared deny
gate on the exercised paths; it is not proof of an OS sandbox or every possible
future tool. The startup manifest still lists tools; the hook blocks execution.

The AGY stream has its own strict parser; it is not Claude's event format. It
checks model, workspace, conversation identity and terminal status, rejects tool
or delegation events, bounds output and excludes thinking bodies from progress.
Protocol errors and uncertain timeouts keep protected reservations. Normal
answers await Codex's independent review. Supplied briefs are capped at 12 KB and
the agent has a fixed 90-second deadline; larger work needs smaller assignments.

Do not combine a model ID ending in `-medium` with `--effort low`; the installed
CLI rejected that conflict before inference. The fixed route uses the model's
encoded effort. The legacy MCP bridge must forward to this guarded dispatcher;
an already running bridge loads its update only after the Codex client reloads.
See `docs/ANTIGRAVITY-AUTOMATIC-ASSIGNMENTS.md` for dated evidence and test scope.
