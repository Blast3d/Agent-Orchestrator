# Agent Orchestrator

**Portable Windows app:** download the Windows x64 ZIP from this repository's
Releases, extract it and run **Setup.cmd**. It includes Python, dashboard code,
launchers and the VS Code bot extension. See [packaging and setup](docs/packaging.md).
The source ZIP is available alongside it. Provider applications and logins are
configured separately; personal tasks, memory and credentials are excluded.

Claude routing now uses **Opus** for workers and backup coordination. Fable is
paused until the user resumes it (2026-09-11). See [coordinator handoffs](docs/coordinator-handoff.md)
for the saved model policy, existing-session limits and recovery.

Your local orchestration application. **ASTRA leads by default**. At 5% remaining, ASTRA can run `lead transfer` to checkpoint, yield and launch Fable itself, keeping normal permissions. Fable claims the saved run and continues orchestration. ASTRA remains a worker on assigned project tasks while quota permits; use `lead role` to check session duties. The recorded lead assigns bounded tasks, checks answers and integrates the final result. `lead transfer` opens a console attached to the new Fable session so you can watch the switchover and answer its permission prompts; **Watch Fable Coordinator.cmd** reopens it. See [Fable takeover](docs/coordinator-handoff.md).

The thirteen-agent experiment remains available: **Open Panel Results.cmd** shows its vote, implemented winners and estimated contribution shares.

**Open Orchestrator Viewer.cmd** shows the selected run's lead, progress and saved
conversation, with older-message paging and an explicit **Open interactive console**
button for Fable. Viewing does not steal an attached console or start a model.
Use `python orchestrator.py viewer --run RUN_NAME` for a specific run. ASTRA history
is supported through an exact Codex conversation binding; replies stay in Codex.
See [the viewer guide](docs/orchestrator-viewer.md).

**Open Brain Dashboard.cmd** opens the new project memory dashboard: search reviewed facts, preferences and episodes, inspect source evidence, link relationships, supersede outdated facts and forget unwanted memories. It picks up memories that agents write from other sessions on its own, and its **Orchestrator** crumb jumps to the Orchestrator viewer for the same run (the viewer's **MEMORY** crumb jumps back). It uses SQLite, with no local model or Graphiti installation. Participating writes use bounded storage admission. See [Orchestrator memory](docs/brain.md) for commands, worker integration and limits.

Double-click **Open Project Maps.cmd** to see who helped on each project and compare their shares visually. **Open Dashboard.cmd** shows quota. **Open Project.cmd** opens this folder. **Check Setup.cmd** checks the installation without calling a model. See [Project Maps](docs/project-maps.md) for the visual guide.

The **Orchestrator / Memory / Provider usage / Contribution maps** navigation at
the top of all four pages connects them directly and preserves the project and
run when you return. From a live dashboard, reports open through its local web
address. The existing HTML files also keep these links; start the dashboards
from **AI-Workspace** if their links report that a service is unavailable.
Provider usage shows account allowance; contribution maps show reviewed shares
of work kept in each project.

Double-click **Open Task Inbox.cmd** for a searchable view of every saved task, its answer, review explanation and next step. This offline snapshot is refreshed each time you reopen the shortcut. It keeps uncertain work and rejected answers visible for deliberate follow-up.

The current update adds saved progress, deadlines and worker handoffs to the inbox, bounds worker output, and provides explicit summary inspection and repair. The selected changes passed the recorded integration checks; see each run for exact test coverage and remaining live-service limits. See [Task progress and summary repair](docs/task-progress-and-repair.md) for the daily workflow and `python orchestrator.py summary inspect JOB_ID`. Partial previews remain incomplete; uncertain interruptions retain their allowance reservations.

This is a Windows application built from Python scripts, local dashboards, and an optional globally available agent skill. It has durable local task records and a portable Windows ZIP distribution.

## Where everything lives

| Folder | Contents |
|---|---|
| `app/` | The maintained dispatcher, quota guard, provider readers, local-model helper and task review code |
| `config/` | This computer's worker roster and Ollama profile |
| `runtime/` | Current quota readings, `policy.json`, reservations, monitor logs and dashboard |
| `runs/tasks/` | One folder per new dispatched task: durable metadata, result and review decision |
| `runs/assignments/` | Project receipts that keep a repeated assignment from starting again |
| `runs/handoffs/` | Frozen handoff plans and brief snapshots for explicitly selected alternatives |
| `.orchestration/` | Project briefs, audit input snapshots, Claude's report and integration decisions |
| `docs/` | Audit, roadmap, architecture and dated provider research |
| `tests/` | Behavior checks that run against the maintained application code |
| `skills/multi-model-orchestrator/` | Maintained global skill source; its helpers forward into `app/` |
| `launchers/` | Manual provider chat and monitor shortcuts |
| `vendor/quota/` | Existing pinned dependencies for terminal quota readers |
| `archive/` | Historical setup evidence and the pre-migration backup |

The installed skill stays in `.codex/skills` so Codex can discover it from any project. `.codex/model-workers.json` is a discovery mirror of `config/workers.json`. Run `python scripts/install_global.py` after changing the maintained skill or worker registry. `doctor` detects differences.

Provider authentication remains in each provider's normal account folders. Model weights remain on `X:\AI-Models\Ollama`, and the Ollama executable remains on `X:\AI-Tools\Ollama`. Old `Local-AI-Setup` shortcuts redirect here. The already-running Ollama service may keep its old log open until its next normal restart; new starts write into `runtime/`. OpenWhispr's dictation service is separate.

## Daily use

Ask Codex to use the orchestrator for a project. You do not need to launch all providers yourself. Useful manual commands from this folder:

```powershell
python orchestrator.py doctor
python orchestrator.py status
python orchestrator.py refresh --provider claude
python orchestrator.py dashboard
python orchestrator.py inbox --open
python orchestrator.py tasks
python orchestrator.py monitor
python orchestrator.py stop-monitor
```

A supplied-text worker task needs a UTF-8 brief containing the objective, relevant inputs, required output and acceptance criteria:

```powershell
python orchestrator.py run claude --prompt-file brief.txt --output answer.json --task code-review --size small
```

Output paths must be new and their parent directory must exist. The application claims the output before calling a model and keeps a separate durable result under `runs/tasks/`. A successful response is **awaiting_review**, not accepted work. Inspect the response against the brief and evidence, then record the decision:

```powershell
python orchestrator.py review JOB_ID --decision accepted --reviewer Codex --note "Verified each finding against source and regression results."
```

The review record is stored in the canonical task folder; exported JSON is an execution snapshot. The reviewer remains responsible for the truth of the validation note. A minimum note length is not an automated quality evaluator.

Accepted project-scoped task reviews now automatically save a compact review-evidence episode to the Brain, without another model call. Use `--remember-file` for a curated solution instead. Memory errors are separate from task acceptance; `python orchestrator.py remember JOB_ID` retries only the memory write. A task without a project is explicitly skipped.

Use a stable memory project across related runs. The maintained workspace now
sets `agent-orchestrator` in `.orchestration/project.json`; new runs inherit it.
`init_run.py --project ID` explicitly selects another scope. Run IDs remain unique,
and old task sources keep their original project. The memory dashboard refreshes
saved recall activity as well as memory changes. Healthy refreshes are quiet;
manual controls appear for an error or updates deferred while reading/editing.

Use `python orchestrator.py team --type audit --independent-workstreams 6 --cap 3` for a read-only team plan based on independent work, saved quota and measured task timings. The cap counts simultaneous workers; the lead is separate. The viewer's **Worker activity** panel shows dispatch phases, elapsed time, provider progress, deadlines and memory receipts. See [team sizing and latency](docs/team-sizing.md).

Every finalized worker task now saves a **contribution audit**, and Codex includes a combined audit when an orchestrated task finishes. It shows estimated accepted-work percentages by agent and category, alongside separate delegation and reported-token usage. Rejected drafts remain visible as usage. Unknown measurements remain unknown. See [contribution audits](docs/contribution-audits.md) for evidence rules and commands.

## Clear briefs and remembered assignments

Check a brief without contacting a model:

```powershell
python orchestrator.py brief-check brief.txt
```

Use headings or labeled lines for **Goal**, **Inputs**, **Output** and **Checks**. JSON briefs also work. The checker finds missing sections, obvious placeholders and file problems; it does not judge whether the instructions are sensible. Add `--require-brief-check` when dispatching to hold an incomplete brief before using allowance. Existing freeform briefs still work, with the check saved for review.

Give repeatable work a project and assignment name:

```powershell
python orchestrator.py run claude --prompt-file brief.txt --output answer.json --task review --project my-project --assignment-id review-v1 --require-brief-check
```

Repeating that assignment returns its original saved task and latest review, without another model call or another exported answer. Changing its brief or worker requires a new assignment name. For a deliberate revision, add `--revision-of PREVIOUS_JOB_ID`; unresolved work remains held. Separate assignment names keep independent opinions independent.

## Handoffs when allowance runs low

Codex can choose suitable, already-approved alternatives before starting. For example, append `--fallback-worker grok` to the keyed command above. A confirmed quota hold or an exited rate-limit rejection hands the brief to Grok through the same allowance guard. Each attempt has its own result and a linked handoff record. Repeating the command reuses those attempts.

This host configures Claude and Grok as each other's automatic alternate. Explicit `--fallback-worker` order takes precedence; `--no-auto-fallback` disables defaults for a task with narrower provider authorization. Unkeyed requests get a generated assignment ID in the receipt; use explicit project/assignment IDs to resume a known request. No local models are used for Orchestrator/Brain work, and no new billable route is enabled. Unknown allowance does not prevent startup. Missing permissions, incomplete briefs and uncertain execution still require inspection. Codex includes accepted progress and remaining steps when preparing a continuation brief for work that has already made progress.

Local handoff plans require a tiny or small task in general chat, formatting, extraction, summarization or classification. Other sizes and categories are held before the first worker starts. Handoff plans freeze the brief and chosen worker order so a file edit or repeated command cannot quietly change the work midway.

## Current worker controls

**VS Code chat integrations can receive orchestration assignments.** Codex and
Claude reuse their existing worker routes. The new `vscode-copilot` worker uses
VS Code's Copilot model API through the local Agent Orchestrator Bots extension.
Enable it and choose a model once in VS Code, then check
`python orchestrator.py vscode-bots`. See [VS Code bots](docs/vscode-bots.md) for
setup, dispatch and validation limits.

| Worker | Current orchestration route |
|---|---|
| Claude | Included account, native Claude executable, tools disabled; Sonnet/medium default; actual returned model recorded |
| VS Code Copilot | Supplied-text assignments through the selected Copilot model; local bridge, no model tools; requires VS Code activation/model access; allowance and actual tokens unknown |
| Grok | Included SuperGrok account, official Grok Build, tools/web/subagents disabled |
| Grok Bot | Atlas in the official Windows app; supervised tasks and a separate, manually refreshed weekly allowance |
| Local Qwen | Small supplied-text jobs through loopback Ollama; pinned local model, 4K context, one active request |
| Gemini | Antigravity `agy`: automatic supplied-text assignments verified, with a per-job deny hook, pinned binary/handler, quota guard and strict stream parser. See [validation and limits](docs/ANTIGRAVITY-AUTOMATIC-ASSIGNMENTS.md). |
| NotebookLM | Feature-specific jobs use saved/manual allowance evidence; no verified automatic consumer quota reader |
| Codex | Lead and integrator; official account quota reader also supports reservations for native subagents |

Quota availability, working authentication, enforced tool boundaries and a correct answer are separate checks. A dashboard label of **QUOTA READY** only answers the first question.

## Quota protection and recovery

This host uses advisory quota admission: collection timeouts, stale readings and missing readings do not block task startup. The dispatcher reads saved allowance and collects updates in the background. At **20% available or below**, after pending reservations, prefer an eligible alternate. Confirmed quota rejection also permits replacement after the first attempt safely finishes. A past reset makes an old reading historical, with current allowance unknown. Tasks still reserve 1/3/8/15 percentage points for tiny/small/medium/large work. These estimates cannot guarantee completion or account for every simultaneous manual chat. Legacy strict admission remains available in `runtime/policy.json`; its extra 10% floor is inactive in advisory mode.

The dashboard shows last-observed allowance, observation time, reservation estimates and collection failures. The lead can inspect the same local evidence with `python orchestrator.py status` or `runtime/usage-status.json`. Snapshot generation time is separate from when usage was measured.

The hidden monitor refreshes every five minutes and starts at Windows sign-in. It does not load local models. Silent Windows warnings depend on notification settings; the dashboard retains quota state.

Timeouts and interrupted execution retain a reservation when completion is uncertain. Inspect the task record and provider session before retrying or releasing that reservation. Do not assume a closed terminal means remote inference stopped. Automatic replacement is limited to confirmed quota conditions; there is no automatic retry of uncertain execution or new paid fallback. Ordinary failures are recorded, and a failed export does not erase a saved canonical result.

Hosted jobs now have deadlines of 2/7.5/15/22.5 minutes for tiny/small/medium/large
assignments. Claude reports live progress; unfinished answer text and local error
evidence survive an interruption. A progress report is not a completed answer.
Use `--timeout-seconds 1200` for an explicit hosted-worker deadline of up to
30 minutes. Changing it requires a new assignment identity; it does not retry
an uncertain task. See [the eight-worker brain audit](docs/brain-audit-2026-09-08.md).
See [the Claude diagnosis and work-sharing changes](docs/CLAUDE-RECOVERY.md).

## Validation and next work

Run `python -m unittest discover -s tests -p "test_*.py"` for local behavior checks. These do not consume model quota. See [the audit](docs/AUDIT.md), [the architecture](docs/ARCHITECTURE.md), and [the roadmap](docs/ROADMAP.md). Historical setup results are dated evidence, not current authentication or performance guarantees.

This working folder can contain private task prompts/results and machine configuration. The `.gitignore` and packaging source selection exclude those from the source repository and distributable ZIPs. No credentials are bundled for another computer.


The private [three-project report](https://github.com/Blast3d/agent-project-reports) contains the votes, completed improvements, validation and estimated contributor shares. Download its files and open index.html for the interactive map at another computer. Reports publish separately from application source.

## Interactive system map and measured memory

Open `Open System Map.cmd` or the System map link in either dashboard. The
[map guide](docs/system-map.md) explains the offline artifact and current gaps.
[Brain documentation](docs/brain.md) covers atomic reviewed closeouts, default
scoped worker recall, and explicit usefulness reviews. No Graphiti installation
or new model is needed.
