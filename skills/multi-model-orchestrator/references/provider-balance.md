# Distribute useful work across providers

The user's October 5, 2026 requirement is a hard minimum of **two contributing AI
families** per orchestration run, with **three preferred**. Different worker
names, roles or interfaces do not establish different families. OpenAI/Codex,
Anthropic/Claude, xAI/Grok and Google/Gemini through Antigravity are distinct
families. Copilot counts by its observed model. JEV is advisory routing and does
not count as a contributing worker family.

Count the lead only for positively credited, retained work backed by a genuine
reviewed native capture from the exact run. ASTRA and Sol share OpenAI; Claude
uses Anthropic. The legacy `fable` owner key denotes the Claude slot; the Fable
model remains paused. Use actual execution metadata for hosted model identity.

## Build the roster before dispatch

Plan useful work from another family before assigning same-family native
workers. With an OpenAI lead, Claude, Grok or Gemini can provide that assignment;
with Claude leading, use Codex, Grok or Gemini as appropriate. Target a third
family when there is concrete research, production or review work it can retain.
Record each assignment's provider, role, deliverable and acceptance check before
launch.
Assign review responsibility at the same time. The lead owns acceptance and
integration; a producer never accepts its own deliverable. Schedule an
independent review for work whose complexity or risk warrants it.
For larger teams, distribute additional jobs across the ready providers rather
than filling every remaining seat with native workers from the lead's provider.

Use the smallest team that meets the minimum and helps. A self-contained question
need not start orchestration. A small edit performed as a run can use the lead
plus a later cross-family reviewer. Local access, quota, route availability,
suitability, deadlines and authorization can change which families participate;
they do not waive the two-family threshold. If no authorized alternate is usable,
save the blocker and keep the run held. Do not silently substitute same-family
agents for cross-family work. A timed-out or stale allowance check is not
provider unavailability: start from saved evidence and refresh in the background.
For quota-based rerouting, prefer another provider at or below the configured 20%
available threshold or after a confirmed rejection. Family policy applies
independently. All reservation and uncertain-job rules apply.

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

Startup records `provider_policy` with minimum two and preferred three families.
Run-linked dispatch verifies that binding even with `--no-memory`. The
`provider_family_coverage` closeout check counts only positively credited accepted
work: hosted jobs must match canonical successful task/model evidence, and native
lead work must have a reviewed capture from the exact run. Arbitrary foreign
native labels, in-progress work, rejected outputs, zero credit, synthetic checks,
JEV advice and imported memories do not establish another family. Two families
pass with an encouragement to use three; three satisfy the preferred target.
The app does not intercept native tools or schedule workers automatically.

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
