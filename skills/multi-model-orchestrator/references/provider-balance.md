# Distribute useful work across providers

The user's October 21, 2026 requirement replaces the earlier two-family minimum:
every orchestration run gives accepted work to **every non-lead family**, and those
families share the accepted worker tasks **near-equally**. With Claude leading,
the workers are Codex (OpenAI), Grok (xAI) and Gemini (Google). With ASTRA or Sol
leading, they are Claude (Anthropic), Grok and Gemini. Gemini runs on Google's
best verified tier for this account's Antigravity route (`gemini-3.8-flash-high`
as of October 21, 2026). Different worker names, roles or interfaces do not
establish different families. Copilot counts by its observed model. JEV is
advisory routing and does not count as a contributing worker family.

The lead's own family is excluded from the balance. Lead-family work, including
same-family native helpers, is optional and never fills a worker family's share.
ASTRA and Sol share OpenAI; Claude uses Anthropic. The legacy `fable` owner key
denotes the Claude slot; the Fable model remains paused. Use actual execution
metadata for hosted model identity.

## Build the roster before dispatch

Plan at least one bounded assignment per non-lead family before launching any
worker, then keep their counts level as the run grows. `orchestrator.py team`
shows a near-equal allocation for the selected lead, `orchestrator.py balance
--run RUN` shows each family's dispatched and accepted counts and the next worker,
and `orchestrator.py run auto --run RUN ...` dispatches to the least-used eligible
family, preferring Gemini on ties. Record each assignment's provider, role,
deliverable and acceptance check before launch.

Assign review responsibility at the same time. The lead owns acceptance and
integration; a producer never accepts its own deliverable. Cross-family review
is an efficient way to keep shares level: each family reviews another family's
output. A small edit performed as a run uses the lead plus one review from each
non-lead family; a self-contained question need not start a run.

Gemini accepts briefs up to the Windows command-line limit (about 30 KB) and its
agent deadline follows task size, so it can take the same review and drafting
work as the others. Split work by independent scope, not by provider limits.

## Balance rule and its limits

Closeout counts accepted hosted tasks per required family. The spread between the
most and least used family may not exceed `max(1, ceil(0.15 x highest))`: 3-3-2
and 10-10-8 pass, 3-3-1 and 10-10-7 hold. A rejected answer does not count, so a
family whose work was rejected needs another accepted assignment.

Balance never justifies filler. Do not pad jobs, split one task artificially,
invent contributions, relabel past workers or credit discarded drafts. Size
assignments so each family gets real work: research, implementation, tests,
review or documentation. Quota, route availability, deadlines and authorization
can delay a family; they do not waive it. If an authorized family is unusable,
save the blocker with its evidence and keep the run held; report it to the user.
A timed-out or stale allowance check is not provider unavailability: start from
saved evidence and refresh in the background. For quota-based rerouting, prefer
another provider at or below the configured 20% available threshold or after a
confirmed rejection; every primary has two configured alternates and Gemini is
always one of them for Claude, Codex and Grok. All reservation and
uncertain-job rules apply.

Runs started before October 21, 2026 recorded the older two-family policy at
startup. Closeout honors the policy recorded in each run's startup receipt.

## Match the model and worker count to the work

Use a capable model for decomposition, difficult implementation and final review.
Use an adequate lighter model or lower effort for bounded routine subtasks when
that option is supported by the verified route. Check the actual configured model
and record the returned model, or unknown; a role name does not choose a model.

Workers may propose a bounded subtask split. The recorded lead approves the
scope and accounts for the whole team, including proposed child workers, before
any extra dispatch. The guarded supplied-text routes return answers; they do not
create autonomous child-agent teams. Dispatch additional assignments through the
maintained dispatcher, with their own task IDs, provider/model evidence and quota
admission. Never claim proposed or unobserved bots ran.

Preserve the existing no-new-billable-fallback rule and the user's no-local-model
constraint for the Orchestrator/Brain workflow. Provider balance does not
authorize additional usage credits, unrelated exports or model installs.

## Measure what actually happened

Startup records `provider_policy` (schema 2: required worker families and the
balance rule). Run-linked dispatch verifies that binding even with `--no-memory`.
The `provider_family_coverage` closeout check counts only positively credited
accepted work: hosted jobs must match canonical successful task/model evidence,
and native lead work must have a reviewed capture from the exact run. Arbitrary
foreign native labels, in-progress work, rejected outputs, zero credit, synthetic
checks, JEV advice and imported memories do not establish a family. The app does
not intercept native tools or schedule native workers.

Keep planned, started, completed and accepted work distinct. Report named worker
count and distinct provider count separately, including the lead consistently.
Summarize accepted work by provider as well as by agent. Credit providers only
for work actually retained; rejected, held and unused attempts remain visible.
When attaching a per-task contribution ledger, reuse that task's canonical
worker and reviewer IDs, such as `worker:grok`. Label work-share percentages as
reviewed estimates. Usage remaining, token counts, elapsed time and charges are
separate measurements; unknowns stay unknown.
