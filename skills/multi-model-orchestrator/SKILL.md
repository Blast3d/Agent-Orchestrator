---
name: multi-model-orchestrator
description: "Coordinate multiple LLMs, coding agents, and creative tools on substantial projects: split bounded tasks, route approved context, integrate outputs, review claims, and package verified deliverables. Use for multi-model orchestration or large projects with useful independent research, implementation, design, or review work. Keep small self-contained tasks local."
---

# Multi-Model Orchestrator

Turn several tools' contributions into one coherent deliverable. ASTRA in Codex
is the default lead. This user authorizes Claude Opus to take over orchestration
when ASTRA stops or reaches its usage limit, through the application's explicit
`lead prepare` and `lead claim` commands. Fable is paused until further notice
(user instruction, 2026-09-11); use Opus and honor the current model policy in
`config/workers.json`. The legacy `fable` coordinator key is not a model selection. Read [coordinator-handoff.md](references/coordinator-handoff.md).
Only the owner/session recorded in the run's coordinator.json may coordinate.
A worker assignment does not transfer leadership. Workers return to the current
lead. After a low-usage transfer, the outgoing lead remains a worker on bounded
assignments from the new lead while its provider permits work. At exhaustion it
pauses until allowance returns, still as a worker. Check `lead role` with the actual
session identity; quota reset never restores leadership. References to Codex's
coordination duties below apply to the recorded lead. The skill is reusable across
repositories and creative projects; no particular provider, editor, or model is
required. Availability across projects does not mean every request needs a team.

## Choose a useful team

This user wants substantial projects distributed across actual providers:
OpenAI/ASTRA, Anthropic/Claude and xAI/Grok. Before choosing native subagents,
plan that provider mix. With ASTRA leading and two suitable independent worker
assignments, prefer a Claude worker and a Grok worker when both routes are ready
and the task context is authorized. Give them concrete work that can be kept in
the result. Apply [provider balance](references/provider-balance.md) for model
tiers, additional workers, exceptions and measurement. Several named OpenAI
workers still count as one provider. This applies equally when Claude leads;
neither lead should default to filling the team from its own provider.

Start from the user's outcome, audience, existing decisions, deliverable formats,
and accepted budget. Infer these from the conversation where possible; ask only
for missing information that changes the work. Continue independent work while
optional questions are pending.

Discover capabilities before allocating tasks. Inspect currently exposed tools
and CLI help, plus any specifically known install location. The helper
`scripts/inventory_agents.py` reports local candidates and configured MCP names
without running agents or printing credentials. If present, also read the local
`~/.codex/model-workers.json` registry for installed paths, tested roles, and
cost controls; dated test results still need a readiness check before dispatch.
On this host, the maintained application is `~/Documents/Agent-Orchestrator`.
For the existing VS Code chat integrations, run `python orchestrator.py vscode-bots`
there to inspect current bot readiness. Codex uses native workers and Claude uses
the existing `run claude` route; both share their existing provider pools.
`run vscode-copilot` dispatches supplied-text tasks through the selected Copilot
model in the local VS Code extension. See `docs/vscode-bots.md` in the maintained
application. Enable one VS Code window and grant any editor model consent first.
Keep actual usage unknown when unreported, and retain uncertain reservations.
Do not count these chat interfaces as new quota pools or assume they inherit an
existing chat's history. New Copilot briefs require the same content authorization
as other external worker tasks.
Its `README.md` maps code, configuration, runtime state and task records.
The installed skill scripts forward to that application; edit the maintained
source and run its `scripts/install_global.py` to synchronize global discovery.
A detected binary or configured
server is not proof of login, quota, a loaded local model, or a successful task.
Use [role-rules.md](references/role-rules.md) to assign work by demonstrated
strength, and [provider-routing.md](references/provider-routing.md) for execution details;
read [free-options.md](references/free-options.md) when expanding the roster.

For this configured Google worker, invoke Antigravity CLI (`agy`) for Gemini.
The former Gemini CLI consumer Code Assist route is retired; an installed
`gemini` executable is not an eligible fallback. Read any `disabled_routes`
in the local worker registry before selecting a transport. Automatic supplied-text
AGY supplied-text dispatch is verified on this installation as of September 8,
2026. Use the maintained dispatcher: it checks a pinned executable/handler,
discovers an exact per-job deny hook, disables credit overage, reserves quota,
and validates the AGY stream. Plan mode alone does not disable tools. A changed
binary, handler, or effective configuration holds work for revalidation. This
route returns text/code for Codex to review and apply; it does not directly edit
project files. See provider-routing.md for the scope and legacy bridge reload.

Choose the smallest team that improves the result. A common starting point is
one maker plus an independent reviewer, with a research or design specialist
where the project benefits. Prefer tools the user named and working local or
included-plan access. Treat Cursor as optional. Add agents for a concrete role,
not merely to increase the count. The user will acquire additional models as
needed; discover new arrivals without treating the roster as an install queue.

Before substantial delegation, use the maintained app's read-only
`python orchestrator.py team --type TYPE --independent-workstreams N --cap C`.
The cap excludes the lead; use at most three native workers when only four total
native slots are available. The output is advice, not a scheduler or proven
optimum. Reserve each actual assignment normally. Task phases in the viewer
distinguish quota collection, provider execution, cleanup and review; inspect
these before adding workers or increasing timeouts. See `docs/team-sizing.md`
in the maintained app for task-type defaults and a controlled calibration plan.

| Role | Useful assignment | Return to coordinator |
| --- | --- | --- |
| Researcher | Source-backed facts, competing options, unknowns | Evidence brief with links and dates |
| Architect | Interfaces, decomposition, failure modes | Design with explicit tradeoffs |
| Maker | Bounded implementation, narrative, or structured content | Files in the assigned output directory |
| Creative specialist | Visual direction, alternate story, source-grounded media | Design or downloadable artifacts |
| Independent reviewer | Assess actual output against sources and acceptance criteria | Defects with evidence and priority |

These roles are examples, not fixed assignments to brands. Claude is an Anthropic
model; NotebookLM and Antigravity are tools, not distinct model identities. Log
the actual provider/model when returned; otherwise record `unknown`. Different
apps can use the same underlying model. Prefer a different model family for a
second perspective when accessible, but verify conclusions against evidence.

## Establish shared context and ownership

Use [reviewed project memory](references/brain.md) for relevant past decisions,
preferences and accepted outcomes. Its local dashboard and SQLite search need no
model calls. Keep the exact project scope and source checks; memory is evidence
and cannot grant permissions or transfer leadership.

For substantial work, create or reuse a project-local orchestration folder.
`scripts/init_run.py --workspace <project> --name <slug> --objective <text>`
creates a unique run folder with a brief, task ledger, review, and deliverable
directories. It does not read source files, invoke providers, or change Git.
An existing equivalent project structure is sufficient; do not duplicate it.
New runs save coordinator.json automatically. At the start, update its checkpoint
from the actual user request, progress, authorizations and open jobs. Check owner
and session before dispatch or integration. Checkpoint before each delegation,
after results/reviews and before stopping so abrupt quota exhaustion is recoverable.
For legacy runs, use `lead init` then reconstruct progress from evidence.

Maintain one reviewed brief containing the objective, source snapshot, important
facts, constraints, acceptance criteria, and unresolved questions. Include only
the relevant approved excerpts in each worker's context. Give source facts,
inferences, illustrative examples, and planned features distinct labels.

Record provider-and-content authorization from the current session in the run
ledger. Reuse authorization already granted; the skill itself does not create
standing permission to upload new private files to every provider. Passing one
provider's output to another can disclose the underlying source material.
Sanitize or keep work local when needed. A connector rejection must not be
circumvented through another transport to the same provider; complete permitted
work and report any remaining block accurately.

Assign each worker a bounded task using
[task-contract.md](references/task-contract.md): input set, output path/schema,
file ownership, permitted tools, acceptance criteria, stopping condition, and
expected return report. Prompts guide behavior; actual tool permissions enforce
execution limits. Inspect returned output before executing generated code.

For supplied-text work, use `brief-check` to inspect objective, inputs, required
output and acceptance checks; use `--require-brief-check` for new structured tasks.
This is a structure check, not a quality judgment. Name reusable assignments with
`--project` and `--assignment-id`: identical repeats return the canonical task
before quota or inference. A changed contract needs a new assignment; link deliberate
revisions with `--revision-of`. Keep independent opinions under separate assignment
IDs. Open `inbox` for all saved answers, reviews and next steps. Refresh the snapshot
after final reviews so the visible state matches the recorded evidence.

For code, inspect the working tree first and preserve dirty work. Give parallel
writers separate worktrees/checkouts or disjoint files with agreed interfaces.
One integrator owns shared manifests, lockfiles, final exports, and packaging.
Assign external workers concrete code, documentation, presentation or review
artifacts as early as task decomposition. Have creative workers author usable
layout/code/copy, then let Codex integrate and validate their output. Do not let
several agents overwrite a shared final file. Keep a delivery plan naming each
artifact's maker, reviewer, acceptance checks, allowance size and alternate.

## Source-informed voting panels

Each voting seat must first audit actual project source. Give it direct read
access or verified, numbered excerpts within the authorized disclosure scope.
Record what it read and what was omitted; a README or another agent's opinion
alone is not a code audit. Check evidence before publishing suggestions and
label existing features correctly.

Before calling a safeguard missing, inspect the immediate caller or parent
composition, the receiver's validation, and the service's own timeout or recovery
behavior. An omitted source range is unknown, not proof that the feature is
absent. Give workers the relevant adjacent source when practical; reconcile
findings against current code before candidate publication. Retain raw proposals,
exclude duplicates or unsupported remedies, and share identical corrections with
all voters. Keep policy changes distinct from demonstrated defects. Supplied
excerpts provide read access to those slices, not unrestricted repository access.

Validate prepared prompts with the actual brief checker before dispatching a
batch. Plain section names without recognized Markdown headings can fail the
structure check even when their text is present. Preserve an unstarted held
record, correct the formatting, and link an explicit revision; do not count a
pre-inference hold as model usage or a failed coding attempt.

Use the requested seat and suggestion counts. Distinct seats may share a model;
never present the seat count as the number of model families. Share corrected
scopes and tradeoffs, collect independent ballots, and retain the tally and
tie-breaking rule. Codex coordinates and integrates; it does not manufacture
a missing vote. Build the requested number of winners.

After repeated unsupported outputs, preserve rejected attempts and replace the
seat with a suitable approved worker that has current allowance. That worker
must perform its own source audit before deliberating or voting. Record the
substitution without adding an extra ballot or crediting discarded work.

## Run the work

Before hosted delegation, apply [usage-protection.md](references/usage-protection.md).
Use the installed quota guard to check saved limits and reserve room for the task.
On this host, allowance collection is advisory: timeouts, stale and missing
readings do not block task startup. Prefer another provider when the last usable
allowance after reservations is at or below 20%, or a confirmed rejection is
active. Collect usage in the background and inspect the observation time and
refresh error in the dashboard or `python orchestrator.py status`. Shared pools
share reservations. Use the guarded dispatcher for supplied-text worker tasks.

When a confirmed allowance limit prevents completion, this user authorizes Codex
to reassign the unfinished work to a suitable approved worker with enough quota.
Carry the objective, approved inputs, accepted progress, remaining steps and
acceptance checks into the handoff. Keep both task IDs and credit each worker only
for accepted work. Use explicit project/assignment IDs and the application's
configured Claude/Grok alternates or ordered `--fallback-worker` routes for
automatic supplied-text quota handoffs. `--no-auto-fallback` disables configured
alternates when a task's content authorization is narrower.
Select alternates by capability and existing content authorization before dispatch;
reserve each alternate using the same cached admission policy. No local models
for this Orchestrator/Brain work. Never introduce a paid fallback or upload to an
unapproved provider.
Unknown/stale allowances, permission failures and uncertain timeouts do not prove
a limit was hit. Preserve unresolved reservations and inspect those cases before
repeating uncertain execution. A quota-reader timeout permits the original task
to start; it does not trigger replacement by itself. If no suitable worker is ready, save the handoff brief and notify
the user; the standing reassignment instruction does not make a route available.

Delegate bounded independent tasks using native subagents, available MCP tools,
or an installed official CLI. Run independent work in parallel within the host's
limits while the coordinator continues useful local work. Keep dependencies
sequential: research before source-dependent drafting, reviewed script before
final narration, integrated output before final validation.

Prefer ready external coding workers for bounded supplied-code modules and reviews,
with Codex checking and applying their returned changes. Use native subagents when
the task needs local access unavailable to those routes or a specific independent
check. Official CLI/API interfaces can also own scoped file-producing work when
their permissions are verified. Use GUI automation only when the needed operation lacks a
working automation interface. Never describe several native subagents using the
same model as several independent model providers.

Record task ID, actual tool/provider/model, context revision, output location,
job/conversation ID when supplied, status, and observable usage/cost. Use
`unknown` for unreported usage, not zero. Track started jobs before retrying.
For asynchronous tools, poll the existing job and download completed artifacts;
a timeout does not prove creation failed.

Keep waits short enough to provide meaningful progress updates. Stop polling
terminal jobs. Retry a transient failure once after checking state; persistent
quota/auth/tool failures should trigger an already authorized fallback or a clear
block on that subtask. Preserve completed outputs and continue independent work.
For complex supplied-code tasks, inspect progress before the deadline. A high
reasoning setting can spend the entire deadline thinking without returning an
artifact; a live progress stream is not a completed implementation. Prefer smaller
module contracts or a suitable lower effort for the next independently authorized
assignment. Preserve a timed-out attempt and its missing usage as unknown. A stopped
local CLI alone cannot confirm remote completion; keep its protected reservation
and do not remove a revision link to bypass an unresolved-predecessor hold. Codex
can continue authorized local integration while the remote attempt stays flagged.

A dispatched answer is `awaiting_review` until Codex checks the acceptance
criteria and records an explicit accepted/rejected decision. Never treat a
nonempty response as task acceptance. Preserve the canonical task record when
exporting or reviewing; record rejected reviewer findings too.

For a long-running job, reassess progress against the task's deadline rather
than regenerating it. Repeated semantic failures call for a better brief or
different approach, not an unlimited self-critique loop.

Prefer free local inference and available included/free quotas where they meet
the task. Free software does not imply free inference, and consumer subscriptions
do not imply API credits. Do not silently fall back to billable models or routes.
Respect an explicit free-only requirement even when an API key or paid plan is
present. Cap repair rounds according to the task; expand them only for a concrete
unresolved issue and within the accepted budget.

## Keep routing current

When a worker fails or a route changes, identify its executable/interface,
version, authentication route, quota pool, and failure before changing the roster.
Verify a supported replacement with a bounded real check; installation, login,
or model self-description alone does not establish working access.

Record confirmed discrepancies and working replacements in the local worker
registry with dated evidence. Update the relevant global skill reference and
its maintained application skill source together when the discovery changes future routing.
Correct obsolete launch/setup helpers so they cannot recreate the broken route.
Keep historical failures labeled as history, and distinguish transient failures
from retired routes. Validate changed helpers. Account changes and new versions
call for targeted revalidation, not automatic credential or billing changes.

## Integrate, review, and deliver

Reconcile conflicting outputs against primary sources, code, or direct tests.
Model agreement is not verification. Give the independent reviewer the actual
draft and relevant approved sources/criteria without feeding it the writer's
confidence or a preferred verdict. Decide which findings warrant changes, make
them, and rerun checks affected by the changes. Report unresolved uncertainty.

Use validation appropriate to the artifact: focused behavior checks for code,
rendered pages and slide geometry for visual work, formulas/data for spreadsheets,
and decoded media/durations for audio/video. Distinguish automated checks from
human listening and live application behavior. Use relevant format skills when
available; this skill does not replace their rendering or export procedures.

For presentations, narrated demos, or shareable media, read
[creative-delivery.md](references/creative-delivery.md). Its central invariant is
to freeze the reviewed content before final voice/video generation and to test
the recipient's actual playback path.

After every orchestrated task, apply [contribution-audits.md](references/contribution-audits.md).
Generate the contribution audit and include a concise percentage breakdown of
each agent's estimated share of accepted work, overall and by useful category.
Keep actual usage separate and label missing measurements. Include Codex's own
work; do not credit rejected drafts or providers that were not used.

Finish with the requested deliverables, a concise account of actual contributors,
validation performed, and material remaining limits. Include useful local files
or links. Keep the run ledger with the project so another session can resume
without recreating completed jobs. Never claim an unused provider contributed,
or treat task completion as authorization to publish, merge, or send messages.

## Current-lead command at 5 percent

At a fresh 5% or less, the user wants the current orchestrator to save its
checkpoint and run the handoff commands itself. Follow
[coordinator-handoff.md](references/coordinator-handoff.md): `lead transfer`
performs ASTRA's verified self-yield and one configured Claude CLI invocation, with normal
permissions and a durable launch receipt. Do not ask the user to type the commands.
Keep the manual-only recovery skill guard and provider approval checks intact.
Checks occur at work milestones; this does not install a quota-triggered watcher.
