# Agent Orchestrator

Coordinate AI coding assistants, track their work, and keep reviewed project
memory on your Windows PC. **Codex is the default lead**, called **ASTRA** in the
workflow documentation.

## Requirements — start here

For the usual coding workflow, you need **Windows x64, VS Code, and the OpenAI
Codex extension signed in to an account with Codex access**. Choose the portable
Windows ZIP if you want Python and the application dependencies included.

| Component | When you need it | What to install or prepare |
| --- | --- | --- |
| **Windows x64** | Running this application's Windows launchers and provider adapters | Extract the app to a writable local folder. The packaged runtime and native adapters target Windows x64. |
| **[Visual Studio Code](https://code.visualstudio.com/download)** | The documented editor workflow, Codex conversations, and optional Copilot bots | Install a current stable Windows release and open your project folder. |
| **[OpenAI Codex extension](https://developers.openai.com/codex/ide/)** | Using Codex/ASTRA as the lead in VS Code | Install the official OpenAI extension and sign in. The Codex quota reader can use its bundled executable; a separate Codex CLI on `PATH` is also supported. |
| **Python** | Running the application | **Included in the portable Windows ZIP**: Python 3.13.15. For a source checkout, install [Python for Windows](https://www.python.org/downloads/windows/) with pip; **Python 3.13 x64 is the build target and recommended source environment**. |
| **Web browser** | Viewing the Orchestrator, memory, and usage dashboards | Use a current browser; the dashboards open on a local loopback address. |
| **Internet and provider account access** | Installing tools, signing in, checking account allowances, and running hosted AI work | Sign in separately to each provider you intend to use. Accounts, subscriptions, and credits are supplied by those providers. Saved reports and local memory can be viewed offline. |

The Python checks require **3.11 or newer** and **SQLite 3.42+ with FTS5 secure
deletion**. SQLite comes with Python; the portable runtime supplies the required
version. The terminal readers use the pinned `pywinpty`, `pyte`, and `wcwidth`
packages in [requirements.txt](requirements.txt), all included in the Windows ZIP.

### Optional tools and additional workers

| Tool or integration | Required only when… | Setup notes |
| --- | --- | --- |
| **Agent Orchestrator Bots VS Code extension** | You want Copilot assignments or the VS Code **Usage monitor** button | Install the app's `.vsix` with **Extensions: Install from VSIX**. The monitor button needs **0.1.1+**; older ZIPs may include an earlier extension. For Copilot, also enable the bot, select an available model, and grant VS Code's model-access consent. See [VS Code setup](docs/vscode-bots.md). |
| **Claude Code** | You want Claude workers or backup coordination | Install and authenticate Claude Code. This repo's current Windows adapter expects the **npm installation** under `%APPDATA%\npm\node_modules\@anthropic-ai\claude-code\bin\claude.exe`; a standalone installation at another location requires adapter changes. See [provider setup](docs/packaging.md). |
| **[Node.js and npm](https://nodejs.org/en/download)** | You install Claude through npm, or run this repo's JavaScript tests | Claude's current npm installer requires **Node.js 22+**; see [Anthropic's npm instructions](https://code.claude.com/docs/en/setup#install-with-npm). The Python dashboards use the Python runtime. |
| **Grok Build CLI** | You want Grok workers | Install the official CLI and authenticate your account. The current adapter expects `%USERPROFILE%\.grok\bin\grok.exe`. Provider CLI updates may need quota-reader revalidation. |
| **Git** | You clone the repository, contribute changes, or want Git tools in coding sessions | Install Git for Windows for that workflow; downloaded ZIPs can be extracted directly. |
| **Other integrations** | You choose their worker routes | Gemini/Antigravity, NotebookLM, and local-model routes have separate setup. Consult [current worker controls](#current-worker-controls) before enabling them. |

### First run

1. Install VS Code and the official Codex extension, then sign in to Codex for the
   lead workflow.
2. Download the **Windows x64 ZIP** from this repository's **Releases**, extract
   the entire archive, and run **Setup.cmd**. This checks the bundled runtime and
   initializes missing local settings. Provider logins remain a separate step.
3. Open **Open Orchestrator Viewer.cmd**. Install the included VSIX if you want
   the VS Code controls or Copilot assignments.
4. Choose the provider workers you want, complete their login/setup, and use
   **Check Setup.cmd** to inspect application and integration status. Executable
   detection does not itself verify a provider login or a successful AI request.
5. If you want Codex to discover this app from other projects, follow the
   [optional global skill registration](docs/packaging.md). Turn the **Usage
   monitor** on for coding sessions and off when finished; opening the viewer or
   VS Code does not enable it.

**Running a source checkout instead?** Install Python 3.13 x64 with pip and make
`python` available in your terminal. From this repository's folder in PowerShell:

```powershell
python -m pip install --target vendor/quota -r requirements.txt
.\Setup.cmd
& '.\Open Orchestrator Viewer.cmd'
```

The source checkout contains the extension source; follow
[VS Code extension packaging](docs/vscode-bots.md#local-maintenance) to create its
VSIX. See [packaging and setup](docs/packaging.md) for build and upgrade details.

## Overview

**Portable Windows app:** download the Windows x64 ZIP from this repository's
Releases, extract it and run **Setup.cmd**. It includes Python, dashboard code,
launchers and the VS Code bot extension. See [packaging and setup](docs/packaging.md).
The source ZIP is available alongside it. Provider applications and logins are
configured separately; personal tasks, memory and credentials are excluded.

Claude routing now uses **Opus** for workers and backup coordination. Fable is
paused until the user resumes it (2026-09-11). See [coordinator handoffs](docs/coordinator-handoff.md)
for the saved model policy, existing-session limits and recovery.

The recorded lead assigns bounded tasks, checks answers, and integrates the final
result. Use `lead role` to inspect session duties and the
[coordinator handoff guide](docs/coordinator-handoff.md) for supported transfers
and recovery. Historical Fable records and launcher names remain for compatibility;
the current paused-model policy still applies.

The thirteen-agent experiment remains available: **Open Panel Results.cmd** shows its vote, implemented winners and estimated contribution shares.

**Open Orchestrator Viewer.cmd** shows the selected run's lead, progress and saved
conversation, with older-message paging and a provider-specific **Open Codex
conversation** or **Open Claude console** control when available. Viewing does
not steal an attached console or start a model.
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

After global registration, Codex and Claude receive startup instructions for
substantial project work automatically. The lead uses **start** to load the
operating guide and scoped Brain recall before assigning workers. The dispatcher
includes the guide in every new worker request; project recall remains scoped to
the assignment. **closeout** checks reviewed task captures and contribution maps
before recording completion. See [startup and closeout](docs/startup-and-closeout.md).
You do not need to launch all providers yourself. Useful manual commands:

```powershell
python orchestrator.py doctor
python orchestrator.py status
python orchestrator.py refresh --provider claude
python orchestrator.py dashboard
python orchestrator.py inbox --open
python orchestrator.py tasks
python orchestrator.py monitor
python orchestrator.py monitor --status
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
| Claude | Included account, native Claude executable, tools disabled; Opus/medium default; actual returned model recorded |
| VS Code Copilot | Supplied-text assignments through the selected Copilot model; local bridge, no model tools; requires VS Code activation/model access; allowance and actual tokens unknown |
| Grok | Included SuperGrok account, official Grok Build, tools/web/subagents disabled |
| Grok Bot | Atlas in the official Windows app; supervised tasks and a separate, manually refreshed weekly allowance |
| Local Qwen | Small supplied-text jobs through loopback Ollama; pinned local model, 4K context, one active request |
| Gemini | Optional Antigravity `agy` supplied-text route; requires separate CLI authentication and validated local boundary configuration. See the [boundary implementation](app/antigravity_boundary.py). |
| NotebookLM | Feature-specific jobs use saved/manual allowance evidence; no verified automatic consumer quota reader |
| Codex | Lead and integrator; official account quota reader also supports reservations for native subagents |

Quota availability, working authentication, enforced tool boundaries and a correct answer are separate checks. A dashboard label of **QUOTA READY** only answers the first question.

## Quota protection and recovery

This host uses advisory quota admission: collection timeouts, stale readings and missing readings do not block task startup. The dispatcher reads saved allowance and collects updates in the background. At **20% available or below**, after pending reservations, prefer an eligible alternate. Confirmed quota rejection also permits replacement after the first attempt safely finishes. A past reset makes an old reading historical, with current allowance unknown. Tasks still reserve 1/3/8/15 percentage points for tiny/small/medium/large work. These estimates cannot guarantee completion or account for every simultaneous manual chat. Legacy strict admission remains available in `runtime/policy.json`; its extra 10% floor is inactive in advisory mode.

The dashboard shows last-observed allowance, observation time, reservation estimates and collection failures. The lead can inspect the same local evidence with `python orchestrator.py status` or `runtime/usage-status.json`. Snapshot generation time is separate from when usage was measured.

The usage monitor is manual. Turn it on with the **Usage monitor** switch near the
top of the Orchestrator viewer, or click **Usage monitor** in VS Code's status bar
and choose **Turn on usage monitor**. The Command Palette also has **Agent
Orchestrator: Usage Monitor On/Off**. Turn it off when finished coding. Opening
the viewer or VS Code does not start the monitor. Closing either app does not stop
an enabled monitor; it runs until you switch it off or sign out.

While on, it refreshes account allowances about every five minutes. **Stopping**
means the current quota check is ending. Windows sign-in startup is disabled on
this installation; `python orchestrator.py monitor --unregister-startup` removes
a startup entry from an older setup without starting the monitor. Start/Stop
shortcuts remain in `launchers/`. No local models are loaded. Silent Windows
warnings depend on notification settings; the dashboard retains quota state.

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
