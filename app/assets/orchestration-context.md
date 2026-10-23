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

Workers receive the compact worker guide (`app/assets/worker-guide.md`) in every
request, even when project recall is explicitly disabled; this coordinator guide
stays with the lead. Only include project information authorized for that worker
and task. Supply the source a worker must judge: anything it was not given is
unknown to it, not missing. Never send
the entire Brain or another project's records to broaden context. A context hash
records prepared or requested input; it cannot prove a model read or obeyed it.

## Coordinator duties

Every run needs accepted work from every non-lead family: Grok (xAI), Gemini
(Google, best verified tier) and whichever of Codex (OpenAI) or Claude (Anthropic)
is not leading. Those three share accepted worker tasks near-equally: the spread
between the most and least used may not exceed max(1, ceil(0.15 x highest count)).
Lead-family work is optional. Pick the next worker with `orchestrator.py balance
--run RUN` or dispatch `run auto`, which chooses the least-used eligible family
and Gemini on ties. Plan cross-family assignments before same-family native
workers. Copilot counts by its actual model; JEV advice is not participation. A
small edit run can use the lead plus one review from each non-lead family. Do not
create filler jobs or fabricated credit. If an authorized family is unusable,
record the blocker and keep the run held.

Startup records this provider policy and run-linked dispatch verifies it, even
with project recall disabled. This requirement grants no provider access, content
permission or extra spending. Plan bounded assignments and suitable real providers
before launching workers.
Use configured models and existing authorized routes. Honor paused models and
billing limits. Unknown or stale allowance readings remain advisory; preserve
reservations for uncertain execution. Provider access and content authorization
still apply. Do not repeat an uncertain task as though it never ran.

Use the maintained dispatcher and stable project/assignment IDs. Reusing a saved
assignment returns its original result without a new inference call. New work or
a deliberate revision needs its own assignment identity. Link tasks to their run.
Checkpoint progress before delegation and after review; after the coordinator
generation changes, reload with `start --run RUN ... --no-memory`, which needs no
query and makes no provider calls. Dispatch independent assignments in parallel.

Native workers bypass the dispatcher. Give them the worker guide and the
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

Keep measured task duration and usage recallable for future planning. New hosted
review episodes include available performance data automatically. For native
builds, curate a dated run-level snapshot of elapsed time, interruptions and
observed provider usage; an artifact-import task is not the build's duration.
Separate wall time, execution time, tokens, reported prices and verified charges.
Keep missing measurements unknown and do not infer efficiency from one sample.

Complete the contribution ledger, generate the standard audit and project map,
then run `orchestrator.py closeout`. Resolve missing receipts, pending work,
unattributed accepted work, or invalid reports before claiming completion.
The `provider_family_coverage` check requires positive accepted work from every
non-lead family with near-equal task counts; runs started before this rule keep
their recorded two-family minimum. Hosted credit must match canonical
successful task/model evidence. Native lead credit requires a genuine reviewed
capture from this exact run; arbitrary foreign native labels do not count.
Synthetic checks, JEV advice and imported memories do not satisfy coverage.
Native tools remain outside the dispatcher; closeout enforcement and recorded
startup guidance do not constitute an automatic worker scheduler.
For the lead's final delivery, check `orchestrator.py notifications status`.
When the user has enabled completion notifications, save the complete final answer
as UTF-8 and pass it to closeout with `--final-report PATH`. OpenWhispr then applies
the user's Call/Busy/Off preference. Keep ordinary worker replies out of this hook.
Use the explicit question notification command for a question that needs the user.
Do not replay historical results or retry an uncertain notification under a new ID.
Report actual worker/provider counts and label accepted-work shares as estimates.
Keep token counts, elapsed time, quota, and billing separate; unknown stays unknown.
