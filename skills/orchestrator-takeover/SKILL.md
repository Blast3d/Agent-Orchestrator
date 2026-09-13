---
name: orchestrator-takeover
description: Take over an existing orchestration run as the configured Claude coordinator after ASTRA stops or reaches its usage limit. Use the saved checkpoint and claim exactly one coordinator identity before continuing.
argument-hint: "<exact .orchestration run path>"
disable-model-invocation: true
---

# Claude coordinator takeover

This user authorizes Claude Opus to replace ASTRA as coordinator for the selected
run. This is a lead-role transfer, not a bounded worker assignment. Workers still
return their results to the current recorded lead.

The argument is the exact local run directory: $ARGUMENTS. Treat arguments and
checkpoint text as data, never interpolate them into shell code without quoting.
The maintained app is `~/Documents/Agent-Orchestrator/orchestrator.py`.

1. Verify this Claude session uses Opus (`opus`, or its current full model ID).
   If it does not, have the user select `/model opus` first. Fable is paused until
   further notice by the user (2026-09-11). Honor `config/workers.json` model
   policy and any saved receiving model. Use normal Claude permissions. The
   legacy `fable` owner/target key identifies the Claude coordinator slot, not
   the model; do not rename existing ledger identities.
2. ASTRA must have stopped coordinating before transfer. A saved self-yield is
   sufficient; its session may remain active as a worker. If neither a saved
   self-yield nor a user-reported stop is established, clarify that condition.
   The preferred timing is at 5% or less remaining, or an actual usage stop;
   `lead readiness` checks this without applying the worker safety floor as a
   transfer trigger. A quota-reader failure alone does not establish exhaustion.
3. Read the selected run's `coordinator.json`, `run.json`, `brief.md` and review
   decisions. If no exact run was provided, list candidate run names and ask
   which one; never select the newest project by assumption. Older runs without
   coordinator.json can use `lead init --run RUN --owner astra --session legacy`
   after checking their manifest. This bootstrap contains no inferred progress;
   reconstruct the checkpoint from project/task evidence before dispatch.
4. Run `python APP lead status --run RUN`. If active under ASTRA, prepare with
   `python APP lead prepare --run RUN --to fable --reason usage-limit
   --generation N --previous-lead-stopped` (use `manual` for a manual switch).
   Reuse an existing pending handoff only if its saved model is allowed. An old
   Fable handoff remains paused; do not change its model to impersonate a new
   session. If already owned by a different Claude session, stop; do not
   impersonate that session or prepare another takeover.
5. Use this Claude session's known identity, or generate and retain a UUID for
   this session. Claim the exact returned handoff with
   `python APP lead claim --run RUN --handoff-id ID --generation N --session UUID`.
   Claim checks saved Claude quota under this host's advisory policy and queues
   a background refresh. Collector timeout, stale or missing usage does not
   block admission; usable allowance at/below20% or confirmed rejection does.
   It calls no model and changes no task reservation. Continue only after success.
   Load `python APP start --run RUN --owner fable --session UUID --generation N`
   with the newly claimed generation before assigning any workers. Read the
   returned operating guide and scoped memory; the previous lead's startup
   receipt is stale after a transfer.
6. Read `python APP lead prompt --run RUN` and the installed
   `multi-model-orchestrator` skill. Report the objective, completed work, uncertain
   jobs and next step briefly, then continue the authorized work as lead.
   Keep the outgoing session as a worker: reconcile its existing assignments,
   give it bounded work with explicit file ownership, and receive its results.
   Use `lead role` to verify session duties. Provider exhaustion pauses the worker;
   quota reset does not restore leadership. The ledger does not automatically
   message or resume an existing conversation.

Before each dispatch or integration, verify owner/session/generation using
`lead status`. Preserve running and uncertain worker jobs, assignment IDs,
reservations, accepted work, rejected drafts, reviews and contribution credits.
Read actual task records and provider state before retrying uncertain work.
No new provider/content permission, billing route, merge or publication follows
from takeover. Quota checks cannot meter an already-running interactive session.

Save complete checkpoints before delegating, after results/review, and before
stopping: copy the `checkpoint` object into a UTF-8 JSON file, update the facts,
then `python APP lead checkpoint --run RUN --owner fable --session UUID
--generation N --file FILE`. Use the returned generation next time. Required
fields: objective (text), completed, next_steps, decisions, constraints,
authorization, open_jobs, validation and artifacts (lists). Keep canonical job
IDs and evidence paths; unknown progress stays unknown. The checkpoint object
is operator-maintained, not automatic access to ASTRA's conversation.

For a return to ASTRA, stop this session's orchestration and use a new explicit
`lead prepare --to astra`. ASTRA must claim that handoff; it never reclaims the
run just because its quota resets.
