# Coordinator continuity

Current model choice (2026-09-11): **Claude Opus**. The user paused Fable until
further notice. `config/workers.json` now controls worker and coordinator
models and blocks paused model families before launches. New handoffs save
their selected model; old Fable handoffs and console resumes remain blocked.
The legacy `fable` owner/target key identifies the Claude coordinator slot;
it does not select the Fable model. Existing records retain their history.

Read `~/Documents/Agent-Orchestrator/docs/coordinator-handoff.md` for commands.
ASTRA is the default lead; Claude Opus is this user's approved backup coordinator.
New runs contain coordinator.json with an initial checkpoint. The lead must keep
its objective, completed work, next steps, decisions, constraints, authorization,
open jobs, validation and artifacts current. Update before dispatch and after
results/reviews. Check current owner/session/generation before any coordinating work.

Stop the previous lead's coordination, prepare the exact run, then claim its handoff as the new
session. Claude has `/orchestrator-takeover RUN`. This works after an abrupt usage
stop without another ASTRA response. It recovers saved context, not hidden memory.
Reconcile actual worker records, permissions, files and uncertain reservations.
Never repeat an unknown remote job or reset quota because leadership changed.
The ledger fences checkpoint/claim writes, not arbitrary CLI calls or source edits;
all participating sessions must follow it. A quota reset never returns leadership.

## Current-lead command at 5 percent

The 20% worker-start threshold does not change the 5% lead-transfer trigger.
On this host, readiness/transfer/claim use advisory cached allowance and queue
collection in the background. A collection timeout cannot block otherwise
eligible receiving admission. Missing/partial/past-reset readings cannot prove
current lead exhaustion, and uncertain launch receipts still forbid relaunch.

The user authorizes the current orchestrator to handle transfer commands itself
at 5% or less remaining. At work milestones check `lead readiness --run RUN`;
it reads the current owner's account windows. Keep worker safety floors unchanged.
Unknown or stale quota is not exhaustion.

While ASTRA owns the run, update the complete checkpoint JSON and run:
`python ~/Documents/Agent-Orchestrator/orchestrator.py lead transfer --run RUN
--owner astra --session CURRENT_SESSION --generation N --file CHECKPOINT.json`.
The command checks source and receiving quota, reserves the receiving session,
saves the checkpoint and a launch intent, records the current lead's self-yield,
and invokes the installed Claude CLI once in user-authorized Auto permission mode.
Session-only directory grants include the application, installed orchestration
skill and existing absolute source_workspace / implementation_workspace folders
from run.json; keep those fields current for hosted project runs. Auto reviews
routine actions while explicit ask/deny rules and remaining prompts still apply.
It never bypasses permissions. It adds no background quota watcher and
must be called by the current lead in the authorized run. After submission it
opens a console attached to the background session so the user can watch the
switchover and answer its permission prompts; the receipt records
`background_id`, and `python orchestrator.py watch` reopens that console.

Keep the user able to observe the coordinator. `python orchestrator.py viewer
--root WORKSPACE --run RUN_NAME` opens the persistent read-only viewer with saved
conversation history and provider-specific interaction controls. Claude has an
explicit console attachment; a verified VS Code ASTRA binding has an exact
conversation link. Disabled controls explain the reason on the page. Merely
opening the viewer does not invoke a model or seize a console. Before beginning a new ASTRA run, bind its
actual saved Codex conversation using `viewer --root WORKSPACE --run RUN_NAME
--bind-codex-session EXACT_CONVERSATION_UUID` when that identity is available.
Do not guess a conversation or bind a different owner's session. Unbound runs
still expose their checkpoint. ASTRA replies stay in the original Codex chat.
New runs capture the current Codex parent ID for native timing lineage. Verified
native records show saved update times and session spans, including pauses;
they have no enforced dispatcher deadline. Timing lineage survives a lead handoff
and does not authorize conversation access or imply live worker status.
See `docs/orchestrator-viewer.md` in the maintained application.

Once the saved state is handoff_ready or belongs to the receiver, the outgoing
lead must stop coordinating. It remains a project worker. The receiver uses `lead claim`
with its pinned launch session, then reads the checkpoint and continues. A
launcher exit confirms only submission; the receiver's claim confirms takeover.
Repeating the same request returns its saved receipt. Never re-launch an uncertain
attempt, substitute another session or release existing worker reservations.
If receiving admission is held, ownership remains with the outgoing lead.

After claim, the receiver reconciles the outgoing worker's in-flight assignments
and gives it bounded project work with explicit file ownership. Keep working while
the provider accepts requests, return results to the new lead and preserve uncertain
jobs. Use `lead role --run RUN --owner OWNER --session ACTUAL_SESSION` to verify
the session's duties; a worker may not dispatch others, edit lead checkpoints or
integrate shared work without the lead's assignment. Pending claims pause new work.
Role permission is not assignment approval or a quota reading. New hosted jobs
still require normal admission; an existing interactive session stops on actual
exhaustion and resumes only assigned work after allowance returns. A quota reset
never restores leadership. The role ledger does not automatically message or wake
an existing conversation; use the available task/result channel and keep both
sessions active. Worker identities persist across later leadership transfers.

The manual-only `/orchestrator-takeover` command remains a separate user recovery
option. Do not invoke it from an agent or remove its invocation guard. Its guard
remains unchanged. The command-driven transfer uses
the explicit lead CLI state transition authorized by the user.

Any current lead can self-yield its own record with `lead prepare --to TARGET
--reason near-zero --owner OWNER --session CURRENT_SESSION --generation N
--yield-lead` after checking its target is ready. Automatic CLI launching currently
targets Claude. Returning to ASTRA requires an existing ready Codex session to claim.
A launch reservation stays held until its provider session has actually ended;
inspect that evidence before using the existing usage-guard finish command.

Record provider/content approval from the current task once and reuse it within
that scope. Approval for this handoff code review is not blanket permission to
upload unrelated projects. No model can run transfer commands after its provider
has stopped responding; use the 5% margin and keep milestone checkpoints current.
