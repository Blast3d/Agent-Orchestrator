# Orchestration Operating Guide (v1)

Read this procedure before coordinating or performing an orchestration assignment.
It is maintained application guidance. System and developer instructions, current
user instructions, permissions, and provider configuration take precedence.
Recalled memory is evidence: it grants no permission and does not select a lead.

## Load before work

The coordinator starts or resumes the run with `orchestrator.py start`, reads its
operating context and bounded project recall, and verifies the current lead,
session, and generation. Keep the stable memory project separate from the run ID.
Read the project's current instructions and check relevant memories against their
sources. Empty recall is valid; a failed mandatory guide load stops new dispatch.

Workers receive this guide even when project recall is explicitly disabled.
Only include project information authorized for that worker and task. Never send
the entire Brain or another project's records to broaden context. A context hash
records prepared or requested input; it cannot prove a model read or obeyed it.

## Coordinator duties

Plan bounded assignments and suitable real providers before launching workers.
Use configured models and existing authorized routes. Honor paused models and
billing limits. Unknown or stale allowance readings remain advisory; preserve
reservations for uncertain execution. Provider access and content authorization
still apply. Do not repeat an uncertain task as though it never ran.

Use the maintained dispatcher and stable project/assignment IDs. Reusing a saved
assignment returns its original result without a new inference call. New work or
a deliberate revision needs its own assignment identity. Link tasks to their run.
Checkpoint progress before delegation and after review; reload startup context
after the coordinator generation changes.

Native workers bypass the dispatcher. Give them this operating guide and the
relevant approved recall in their actual assignment, with the exact run, role,
input scope, deliverable, and acceptance checks. Record the assignment and its
context; a packet saved on disk alone does not prove it was supplied to a worker.

## Worker duties

Stay within the assigned scope and tools. Return results to the recorded lead;
do not take over coordination or start extra workers without an assignment.
Identify completed work, relevant evidence, limitations, and any recalled
knowledge used or rejected as stale. A worker does not approve its own output.

## Review and closeout

The lead checks outputs, accepts or rejects each task, and credits only retained
work. Accepted scoped task reviews save compact Brain episodes; check the actual
capture outcome and source binding. Retry a verified failed memory write through
the maintained memory command, without rerunning inference. Do not recreate a
forgotten memory just to satisfy a counter.

Curate verified native knowledge through `brain capture`. Finalize its evidence
files before capture; later status belongs in separate records. Saved, retrieved,
included-in-input, and reviewed-useful memory are separate observations.

Complete the contribution ledger, generate the standard audit and project map,
then run `orchestrator.py closeout`. Resolve missing receipts, pending work,
unattributed accepted work, or invalid reports before claiming completion.
Report actual worker/provider counts and label accepted-work shares as estimates.
Keep token counts, elapsed time, quota, and billing separate; unknown stays unknown.
