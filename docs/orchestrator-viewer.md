# Orchestrator viewer

Double-click **Open Orchestrator Viewer.cmd**, or run `python orchestrator.py viewer`.
The launcher reuses one local server per workspace. It opens a browser page; it
does not attach a console, resume a conversation or call a model.

The shared navigation at the top (**Orchestrator · Usage · Tasks · Memory ·
Contributions · Experiments · System map**) is the same on every page. Its
**Memory** link opens the memory dashboard on the selected project and run,
starting that server if needed; Memory's **Orchestrator** link comes back to
this viewer on the same run. The **Lead for new runs** chip links to the lead
switch on this page. All pages stay on `127.0.0.1`.

The **Bot readiness** panel at the top lists held bots with one plain sentence
(what holds them and what to do next) and each bot's reading age, read from the
last saved usage status. It never starts a usage check itself.

The run picker groups runs as **Current**, **Recent**, **Experiments** and
**Archived**, and names each by its title, lead and last activity. A run that is
not finished but has had no checkpoint for over 24 hours is marked "Idle since".
**Lead last active** is the newest checkpoint or saved-conversation time;
**Status checked** is only when this page last asked the provider.

## Experiment runs

Use **Experiments** in the shared navigation. Select an experiment, then one
execution: for example, solo without memory or a team with supplied memories.
Each view shows that execution's token usage by provider and worker, requested
and reported models, contestant and helper counts, timing, memory receipts,
individual prompts and responses, and initial/final checks. It refreshes every
five seconds while visible. Several calls from one contestant do not mean
several bots. Solo is one OpenAI contestant with zero helper agents; the
coordinating ASTRA conversation and fixture authors are outside its scored usage.

New benchmark runners register a separate child run, workspace and Brain scope
for each condition and repetition, then execute one condition at a time.
Normal and edge cases are **separate result sections within that execution**,
sharing its usage total. They are not additional model runs. See
[isolated experiment instructions](../benchmarks/ISOLATED_EXPERIMENTS.md) for
the cold-first protocol, failure holds, and commands that invoke real models.

The older combined pilots are available as read-only condition views. Their
recorded calls and totals are separated for inspection, but the viewer does
not claim those original executions had separate run or memory scopes. Old
checks without an explicit normal/edge tag remain **unclassified**. Original
grades stay accessible when a documented correction exists; corrected checks
cannot remove timing effects from incorrect feedback during the original run.

Missing or unreconciled measurements display **Unknown**. Known partial token
subtotals remain available, and failed or uncertain calls do not disappear from
new execution totals. Setup usage is excluded and unmeasured. Account quota,
estimated costs and token counts do not establish an additional charge or prove
that work was free. Viewing results makes no model calls.

## Recorded work

Choose a project run to see its recorded lead, saved objective, completed work,
next steps and open jobs. The viewer refreshes every five seconds while visible.
It follows changes to the selected run's coordinator, including a Fable handoff.
Run completion and provider activity are shown separately: a completed run can
still have an open provider session. A checkpoint is saved progress, not a live
execution heartbeat.

Healthy refreshes run quietly; the normal page shows **Auto-refresh on** instead
of a flashing refresh button. **Retry now** appears if a connection fails, while
automatic retries continue. A bookmarked run remains selected, including an old
completed audit. Choose the current run to see its checkpoint and conversation;
the viewer never switches the target of a conversation control silently.

**Worker activity** separates quota collection, reservation, provider execution,
result saving and quota reconciliation. It shows measured elapsed time, completed
phase durations, provider progress/retries, the configured deadline and the memory
receipt after review. No percentage is invented for work whose completion cannot
be measured. Only workers linked to the selected run appear; sharing a Brain
project does not mix activity from different runs. Older tasks can still match an
explicit job ID, a project named after the run, or an output path inside that run.
An explicit different run binding takes precedence over a legacy output path.
Completed workers are collapsed, and the deadline column appears only when a
worker has a recorded deadline. Local reviewed-artifact captures appear separately
under **Local memory saves**, with their receipt, memory count and saved time.
They are not worker executions or Jev calls; their short import time is not a
task duration. Historical tasks and evidence stay in their original runs and inbox.

Native agents show saved session spans, latest-turn durations and
update times when their exact parent and child records can be verified in local
Codex metadata. Session spans include pauses between follow-ups. Their completion
state still comes from the coordinator; saved updates do not prove current
execution. Native agents have no enforced dispatcher deadline. Missing or
unverified records have a visible reason. The panel reads bounded,
scoped metadata and does not display worker drafts or private reasoning.

Claude status collection runs in the background; a slow or unavailable CLI does
not block saved checkpoint/history reads. Memory writes are polled separately
from sibling-service discovery, with visible connection errors and request
deadlines. Automatic refresh preserves open dialogs, draft input and selection.
The navigation keeps both the exact run and its selected memory project, including
a project with no memories yet. Switching projects drops an unrelated old run.

## Conversation and interaction

The conversation panel reads saved user and assistant messages from the exact
provider session. **Load older messages** retrieves earlier pages, including
history that is no longer visible in the terminal. **Jump to latest** returns to
the newest loaded message. This view does not take over a console attached in
another window. Closing the browser leaves the provider session alone.

For a Claude lead (Opus 5.5), **Connect to lead** opens **Open Claude console**, which runs `claude attach` for the selected
background session. Send messages and answer permission or account prompts in
that console. The button is deliberate: attachment can move the session out of
another console window, and an ended session may need recovery in Claude.
Normal provider permissions and spending choices remain in effect. The viewer
does not approve requests or enable usage credits. Known waiting states appear
as an attention banner; the exact approval dialog is in the console.

If live status cannot be read, the session ended, or it no longer appears in
Claude's listing, the viewer retains locally mapped history and disables
attachment. The reason appears below the button rather than only in a tooltip.
Conflicting session/workspace metadata also disables it. A specific run never
falls back to a newer, unrelated Claude session. `Watch Fable Coordinator.cmd` (legacy name)
still opens a console directly; prefer the viewer when choosing among runs.

The **Lead orchestrator** switch at the top chooses Claude, ASTRA or Sol for new
runs (see [coordinator handoffs](coordinator-handoff.md)). A Codex lead (ASTRA or
Sol) has no console; its saved conversation can be viewed after an explicit binding:

```powershell
python orchestrator.py viewer --run RUN_NAME --bind-codex-session EXACT_CONVERSATION_UUID
python orchestrator.py viewer --run RUN_NAME
```

The binding is local and invalidates when the recorded lead identity changes.
The conversation UUID must match an existing Codex transcript. For a conversation
started in VS Code, **Open Codex conversation** opens that exact conversation in
the installed Codex extension using its verified local conversation link. It does
not submit a message or start another model turn. Replies and approvals stay in
Codex. Other Codex application sources, a missing binding, or an unsupported
installation have a visible explanation below the disabled button. Each click
rechecks the selected lead and provider identity so a handoff cannot open an
unrelated session. This viewer does not provide its own Codex chat transport.

New runs initialized from Codex save the current parent conversation ID as timing
lineage. It remains valid across lead handoffs but does not automatically bind
conversation history. Older runs can resolve native timing from a verified viewer
binding to their original parent; unlinked runs retain explained missing values.

For another project, use `--root C:\path\to\PROJECT --run RUN_NAME`. The run
must be inside that project's `.orchestration` directory. Bookmark the selected
run's URL, or reopen it using the command; the port can change after a restart.

## Storage and privacy

The service binds to `127.0.0.1`, uses per-instance API tokens and same-origin
checks, and sends no transcript data to a model or remote service. It reads
existing provider transcripts instead of copying them into another database.
Each history request reads at most 512 KiB plus a small identity check and
returns up to 40 visible messages. Oversized or malformed records have explicit
notices, and older-page cursors continue advancing. Provider retention still
controls whether old history exists.

Tool inputs/results, private reasoning and binary attachments are omitted;
images have a placeholder directing you to the original session. Message text
is rendered literally, including any HTML. This is a readable conversation
view, not a complete terminal or audit-log export. The viewer adds only a small
runtime instance record and optional per-run Codex binding. It neither launches
local models nor installs Graphiti.

## Validation

Launcher lesson, verified September 9, 2026: a button request can succeed while
VS Code never receives the conversation link. Codex extension tools inherit
`ELECTRON_RUN_AS_NODE=1`, which makes `Code.exe` reject GUI arguments. The opener
clears that variable for its child process and reports an early nonzero exit.
Tests cover both behaviors; the repair run's `review/live-codex-interaction.json`
records actual receipt of the exact URI in the installed extension log. When
changing an editor launcher, verify delivery as well as successful process creation.

Synthetic tests cover exact session resolution, ended-session history, conflicting
identities, stale attachment requests, append-safe paging, replacement/truncation,
oversized records, Unicode, HTTP access checks, and Windows junction escapes.
Browser fixtures cover direct run links, older messages, safe text rendering,
mobile layout and explicit attachment only. Live checks read the existing Fable
memory-closeout session and the current ASTRA transcript without attaching or
starting another model. The provider-specific control repair additionally checks
the Codex conversation link through the live dashboard and installed extension;
its run report distinguishes a launch request from observed URI delivery. See
each viewer run's `review/validation.md` for final
counts and limits. A new quota-triggered takeover was not forced for this test.

## Manual usage monitor

The **Background usage checks** switch in the Bot readiness panel turns
background account allowance checks on or off. It works without selecting a run. Opening the viewer
only reads its status. **Stopping** lasts until an active quota check ends; an old
saved PID is never displayed as a running monitor. Turn it off when you finish
coding. Closing the viewer leaves the current setting in effect until sign-out.
