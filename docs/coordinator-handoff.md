# Orchestrator-run ASTRA to Claude handoff

Current model choice (2026-09-11): **Claude Opus**. The user paused Fable until
further notice. `config/workers.json` now controls worker and coordinator
models and blocks paused model families before launches. New handoffs save
their selected model; old Fable handoffs and console resumes remain blocked.
The legacy `fable` owner/target key identifies the Claude coordinator slot;
it does not select the Fable model. Existing records retain their history.

At **5% or less remaining**, the current ASTRA orchestrator can handle the handoff
commands itself. You do not need to stop ASTRA manually or type the takeover
command. The check runs at work milestones; it is not a new background watcher.
The quota value is the lowest usable account reading, without subtracting worker
reservations. In this host's advisory mode, the lead uses cached readings and
queues collection without waiting for it. Missing, partial or past-reset usage
cannot prove that the lead has reached 5%. Receiving Claude admission also uses
the saved reading; collection timeouts do not block it. The **20% worker-start
threshold** remains separate from the **5% leadership-transfer threshold**.

## What happens

1. ASTRA updates its checkpoint with the objective, completed work, decisions,
   approvals, uncertain jobs, evidence and next steps.
2. `lead transfer` verifies the current owner/session/generation, confirms the
   near-zero source reading and checks/reserves Claude's included-account allowance.
3. It saves the checkpoint and launch intent atomically, records ASTRA's explicit
   yield, then starts the installed Claude CLI once with `opus`.
4. The receiving session uses its pinned identity to claim that handoff before
   coordinating the run. ASTRA becomes a worker and stops coordinating.
5. Claude reconciles existing files, jobs, reviews and reservations and continues
   within the user's recorded authorization. It keeps ASTRA on bounded project
   assignments while ASTRA's provider still accepts requests.

## Watching the switchover

`--bg` starts Claude under Claude's background daemon, which shows nothing on its
own. After a successful submission, `lead transfer` resolves the daemon id and
opens a separate console running `claude attach ID`, so the switchover and
Claude's output appear in that window. Permission prompts ("Do you want to
proceed?") appear there and wait for your answer; nothing answers them for you.
The launch receipt records `background_id` and `viewer` (`opened`, `unresolved`
or `failed:*`); a viewer problem never changes the submission status.

Closing that window or pressing Ctrl+Z detaches; the session keeps running.
Reopen it any time with **Watch Fable Coordinator.cmd** or:

```powershell
python orchestrator.py watch                 # newest running Claude coordinator
python orchestrator.py watch --run $runPath  # the session recorded in that run
python orchestrator.py watch --list          # background sessions, no attach
claude logs ID                               # recent output without attaching
```

`lead status` and `lead prompt` show the same `background_id`.

## The outgoing lead keeps working

Leadership transfer preserves the outgoing session as a **worker**. After the new
lead claims the run, it reconciles existing assignments and assigns bounded work
with explicit file ownership. The outgoing worker reports its results to the new
lead; it cannot independently assign other bots, alter lead checkpoints or integrate
shared deliverables. Existing running jobs are preserved, not restarted.

The application saves worker identities in `coordinator.json`. Any participating
session can check its duties using its own actual identity:

```powershell
python orchestrator.py lead role --run RUN --owner astra --session ACTUAL_SESSION
```

The result identifies the current lead and whether the session may continue assigned
work. That flag is a role permission, not a task assignment or a quota reading.
Pending claims pause new work; the current lead must reconcile assignments after
claim. Unknown sessions receive no worker authority. Worker identities survive
later transfers, and only an explicit claim restores leadership to a worker.

At 5% the outgoing interactive session may still finish scoped work. At actual
exhaustion it pauses until allowance returns, then resumes as a worker after checking
the current lead and assignment. Normal admission still applies to new hosted jobs;
this does not lower worker safety floors or create another account budget. The role
record and prompts do not automatically message or resume an existing conversation.
Both sessions must be active and use their available task/result channel.

The legacy `previous_lead_stopped` field means leadership activity stopped for a
self-yield; `outgoing_session_stopped: false` explicitly records that the session
was not stopped. Operator recovery after an actual stop records that stop separately.

Future handoffs launch Claude with **Auto permission mode** (`--permission-mode
auto`), as authorized by the user on 2026-09-11. Auto reviews routine actions;
explicit ask/deny rules and remaining permission prompts still apply. If Auto is
unavailable for the account or installation, Claude may fall back to Manual.
See [Claude's permission modes](https://code.claude.com/docs/en/permission-modes).

Session-only `permissions.additionalDirectories` supplies the Orchestrator
application, installed `~/.claude/skills/multi-model-orchestrator` folder, and
existing absolute `source_workspace` / `implementation_workspace` directories
recorded in `run.json`. The launch workspace already covers its run directory.
Missing, invalid, duplicate or already-covered paths are omitted; the launcher
does not widen a missing path to its parent or grant the home directory or drive
root. Keep these explicit run fields current when moving the implementation to
another checkout. [Additional-directory permissions](https://code.claude.com/docs/en/permissions)
use the session's permission mode.

This changes future launches, without changing global Claude settings or an
existing session. The manual-only `/orchestrator-takeover` skill remains available
for user-initiated recovery and its invocation guard is unchanged.

## Command for the current orchestrator

The agent maintains the complete checkpoint JSON, then runs this sequence with
its actual run and identity. The checkpoint file contains only the `checkpoint`
object, not the whole coordinator record.

```powershell
$orchestratorEntry = Join-Path $env:USERPROFILE 'Documents/Agent-Orchestrator/orchestrator.py'
$runPath = 'C:\path\to\PROJECT\.orchestration\RUN_NAME'
$coordinatorState = python $orchestratorEntry lead status --run $runPath | ConvertFrom-Json
python $orchestratorEntry lead transfer --run $runPath --owner astra --session $coordinatorState.session --generation $coordinatorState.generation --file (Join-Path $runPath 'checkpoint-next.json')
```

The default threshold is 5%; `--threshold-pct 0` requires an exhausted reading.
Use `--manual` only for an explicitly requested transfer independent of quota.
Unknown/stale source quota or insufficient receiving allowance holds the transfer
and leaves ownership unchanged. A saved launch intent is never automatically
retried, even after a timeout or crash. The receiver's claim confirms takeover;
a successful launcher exit only confirms submission.

Any current lead can relinquish its own record with `lead prepare --to TARGET
--reason near-zero --owner OWNER --session SESSION --generation N --yield-lead`
after verifying the target is ready. Claude can use this to yield back to ASTRA,
but this application currently launches only Claude; an existing ready Codex
session must claim a return handoff.

## Approval for code review

The user explicitly authorized sending this handoff implementation and its tests
to Claude for this code review, excluding credentials, private transcripts and
unrelated files. That scoped review succeeded. Record this permission in the run
and reuse it for this review; it is not blanket approval for other projects.

An earlier automatic approval review rejected an attempt to enable Auto mode and
remove the manual-only recovery guard. On 2026-09-11 the user explicitly approved
Auto mode and scoped directory access to reduce repeated Claude prompts. The
launcher now uses that authorization; the recovery guard remains in place.

## Persistent user viewer

**Open Orchestrator Viewer.cmd** or `python orchestrator.py viewer --run RUN_NAME`
shows the exact lead, checkpoint, saved conversation and known waiting state.
Opening the browser does not attach or invoke a model. Use **Open interactive
console** to reply or answer approvals in the selected Claude session. Older saved
messages remain readable after terminal output scrolls away. Exact-run selection
never falls through to another Claude. See [the viewer guide](orchestrator-viewer.md)
for ASTRA bindings and unavailable-session recovery.

## Recovery and limits

If a limit stops ASTRA before its next milestone, use `/model opus` and then
`/orchestrator-takeover EXACT_RUN_PATH` in Claude. Checkpoints contain saved facts,
not unsaved conversation memory. Inspect real task records before retrying any
uncertain job. A quota reset never returns leadership automatically.

The launch receipt records the receiving session and reservation. Keep that
reservation while the provider session may still be running. After verifying
its actual end, use the existing usage-guard `finish` command for that specific
reservation; do not release it just because the launcher returned or claim
succeeded. Other job reservations and accepted work remain untouched.

The background session uses a session-only existing-checkout setting so dirty
work is available to its successor. Permission checks still apply. This follows
[Claude's documented background-session behavior](https://code.claude.com/docs/en/agent-view),
including its `worktree.bgIsolation` setting. The installed CLI help verified
`--bg`, `--session-id` and the explicit Claude model.

Tests use real temporary quota stores and fake process launches to verify
self-yield, single launch, competing claims, quota holds, worker continuity and
crash recovery, Auto launch arguments and scoped directory grants. Tests cover both coordinator identities, later transfers, unknown
sessions and failed claims without restoring stale leadership.
The Auto-mode change is verified with fake launches; it is not a live demonstration
that every action can proceed without a prompt.
A live background takeover was observed on 2026-09-08 (run
`brain-audit-eight-20260909T040051Z-a3d6c55f`): the claim succeeded, then the
session waited invisibly on a permission prompt. The attached console above is
the response. The persistent viewer was subsequently checked against this exact
live daemon and its saved transcript without a fresh takeover or attachment.
