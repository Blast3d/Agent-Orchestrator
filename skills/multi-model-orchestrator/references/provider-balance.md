# Distribute useful work across providers

The user's September 9, 2026 correction concerns provider allocation. Earlier
projects had different worker names but still assigned nearly all work to
OpenAI. A role such as voice auditor or memory worker does not establish a
different provider. The requested change is more real work for Claude and Grok,
with shared guidance for ASTRA and Fable.

Count the lead in the mix: ASTRA is the OpenAI lead; Fable is a Claude lead and
already contributes Anthropic participation. Fable and Claude are not separate
providers. Use actual execution metadata for each worker's model identity.

## Build the roster before dispatch

For substantial work with at least two suitable independent worker assignments,
prefer useful participation from OpenAI, Anthropic and xAI when all three are
ready and the supplied context is authorized. With ASTRA leading, start with a
Claude producer and a Grok producer or independent reviewer. When Fable leads,
prefer Grok and an available OpenAI worker for suitable assignments. Record each
assignment's provider, role, deliverable and acceptance check before launch.
Assign review responsibility at the same time. The lead owns acceptance and
integration; a producer never accepts its own deliverable. Schedule an
independent review for work whose complexity or risk warrants it.
For larger teams, distribute additional jobs across the ready providers rather
than filling every remaining seat with native workers from the lead's provider.

Use the smallest team that helps. A single edit, lookup, or tightly coupled local
fix need not involve three providers. Local-access requirements, current quota,
route availability, demonstrated suitability, deadlines and content authorization
can justify a different mix. Record the concrete reason and use the ready routes
for the work they can do. Do not silently substitute same-provider agents for a
planned Claude or Grok assignment. A timed-out or stale allowance check is not
provider unavailability: start from saved evidence and refresh in the background.
Prefer another provider only at or below the configured 20% available threshold
or a confirmed quota rejection. All reservation and uncertain-job rules apply.

## Match the model and worker count to the work

Use a capable model for decomposition, difficult implementation and final review.
Use an adequate lighter model or lower effort for bounded routine subtasks when
that option is supported by the verified route. Do not select the most expensive
model merely because it is available. Check the actual configured model and
record the returned model, or unknown; a role name does not choose a model.

Workers may propose a bounded subtask split. The recorded lead approves the
scope and accounts for the whole team, including proposed child workers, before
any extra dispatch. Use provider-native children only when that execution route
actually supports and permits them. The current guarded supplied-text Claude
and Grok routes return answers; they do not create autonomous child-agent teams.
The lead can dispatch additional Claude/Grok assignments through the maintained
dispatcher, with their own task IDs, provider/model evidence and quota admission.
Record the parent assignment when applicable and keep the total within the
chosen concurrency and budget. Never claim proposed or unobserved bots ran.

Preserve the existing no-new-billable-fallback rule and the user's no-local-model
constraint for the Orchestrator/Brain workflow. A desire for provider balance
does not authorize additional usage credits, unrelated exports or model installs.

## Measure what actually happened

Keep planned, started, completed and accepted work distinct. Report named worker
count and distinct provider count separately, including the lead consistently.
Summarize accepted work by provider as well as by agent. Credit providers only
for work actually retained; rejected, held and unused attempts remain visible.
When attaching a per-task contribution ledger, reuse that task's canonical
worker and reviewer IDs, such as `worker:grok`. A separate attribution ID for the
same worker can create a duplicate graph row when recorded activity is merged.
Label work-share percentages as reviewed estimates. Usage remaining, token
counts, elapsed time and charges are separate measurements; unknowns stay unknown.

The user's examples of 50% and 25% described earlier reports. They are not a
fixed future allocation. Do not pad jobs, invent contributions, relabel past
OpenAI workers, or force equal percentages to make a chart look balanced. Explain
an observed skew and use it when choosing suitable workers for the next project.
