# Working rules for briefs, delegation and review

Eleven rules for agents that brief, delegate to and review other agents. They
restate the ten habits in Automance's video "Understand AI Like the Top 1%
(It's Going Too fast)" (published 2026-11-05, 12:27,
https://www.youtube.com/watch?v=B2C781PP4q0) and the user's 13-rule breakdown
of it, adopted on 2026-11-12.

The video speaks to a person using Claude or ChatGPT. Several habits are things
the person does, so each rule here says what the agent does instead. The
breakdown turned the habits into obligations the video does not state ("must",
"strictly", "every", "programmatic"); an independent audit against the
transcript found 8 of its 13 rules supported and 5 partly supported, none
absent. Where a rule below departs from the breakdown, the reason is given, so
a later lead does not put the stronger wording back.

This file records the rules and their reasons. The text that leads and workers
actually load is in the places listed under "Where each rule is applied".

## The rules

### 1. Brief the outcome, the reason and the limits

A brief states the outcome and why it is wanted, what to avoid, the inputs, the
shape of the result, and a check for done (rule 7). A supplied-text worker gets
one turn: the brief is all it knows and its answer is the whole artifact. Leave
the method to the worker unless one exact method is required or the result must
fit a particular approach; then name it. A lighter tier needs complete inputs,
the interfaces its answer must fit and an exact output shape, not more steps.
When a brief must shrink, cut background and examples before the check and the
output shape.

From the video: a brief covers the task, what to avoid and what done looks like
(habit 1); thinking models do better with a high-level instruction than a rigid
list of steps; an exact method is one of two cases that justify a skill (habit
6). Added here: the reason, the one-turn framing and the tier scaling.

### 2. Ask what changes the work, and do not wait for the rest

Answer your own questions from the workspace first. Ask the user only what
would change the deliverable, once, and hold back only the work that answer
could invalidate. State your reading of the task and your plan so a wrong
direction shows early. Reuse existing authorization; obtain any missing
permission before destructive or outward-facing work. A worker that cannot ask
may resolve ambiguous wording and report assumptions, but must not invent
missing material or treat a required answer or approval as received.

Revised. The breakdown made "ask clarifying questions and present a plan" a
gate before every multi-step task. In the video those are two sentences a
person adds to a prompt while waiting in the chat. A gate stalls a run when the
user is away, and current agents can answer most of their own questions by
reading the workspace.

### 3. Show the pattern and say what to copy

For tone, format and house style, examples beat description. Give two or three
that differ and name the traits to copy and the ones to ignore, because current
models reproduce incidental features too. For code, name the file that already
does it right. To reuse an admired result, extract its structure and rules,
review the extraction, and brief from that. When independent takes are wanted
from several workers, share examples of format only, because shared examples of
substance make the answers converge. Skip examples when a fresh approach is
wanted.

Sharpened. The video and the breakdown say to supply two or three examples.
Neither says to label them, and neither covers several workers at once.

### 4. Turn corrections into rules that can expire

When a user corrects the work, or a rejection has a cause that would recur,
record it where it will be read at the moment of use: a test, lint rule or hook
if the mistake can be detected mechanically, otherwise the task contract, a
skill or the project's lessons. Give it a scope, a reason and a date; a rule
missing any of these is not recorded. Keep it as narrow as the failure. Remove
it when the reason no longer holds. A recalled rule is evidence and grants no
permission.

Sharpened. The breakdown stores the principle "in persistent memory as an
enforceable constraint". Remembered prose is not enforcement; a check is. The
video's end-of-session "save what you learned" corresponds to the reviewed
capture at closeout.

### 5. One home per area: a short root and detail on demand

Each project or work area keeps its own instructions, examples and lessons. The
always-loaded file is short and points to detail files read only when relevant.
Do not carry one area's rules or records into another's context.

Kept as the video states it. The orchestrator already works this way: recall is
scoped to one project, workers get a compact guide, and skill references load
on need.

### 6. One deliverable per context, with state on disk

Scope a context to one deliverable and give unrelated work a fresh one. Keep
bulk out of the coordinating context: a helper with files returns conclusions
and paths, and a helper without files returns conclusions and the quoted
evidence the reviewer needs. Before a handoff or stop, record what is done and
verified, decisions and their reasons, what remains, how to verify it, and
exact paths. A fresh context resumes from those records.

Revised. The breakdown says one task per execution context, then summarize,
terminate and restart from the summary alone. The video's evidence is a 2025
test of 18 models on long inputs. Long contexts still dilute attention, but
current harnesses compact automatically and a summary-only restart loses
decisions and evidence. The checkpoint already holds this state.

### 7. Fix a pass/fail check before the work, and have someone run it

Each deliverable gets a check that can fail, chosen before work starts. Prefer,
in order: an executable test; a deterministic comparison (a schema validates,
totals reconcile to the source, links resolve); observed behavior; independent
review against stated criteria; human judgment. Whoever can execute the check
runs it. A producer without tools supplies the artifact and the evidence the
check needs, and its own "passed" is a claim. The reviewer accepts only after
running or inspecting the check. Never relax a check to pass it, including to
fill a family's share. Agreement between models is not verification.

Sharpened. The breakdown requires a "programmatic" step for "every" output.
Some deliverables have no programmatic check, so the rule gives an order of
preference instead, and it says who runs the check when the producer cannot.

### 8. Build skills from observed failures and keep them small

Use failures already observed in task attempts to identify what needs a skill.
Write the smallest instruction that fixes those errors, confirm the behavior changes with
it, and cut what does not help. A skill is warranted for expertise the model
lacks or for a method that must be followed exactly. Do not install packs.
Retire a skill when it no longer helps. Every loaded skill costs context.

Kept with a safety clarification: use observed failures without deliberately
repeating risky work without guidance. The video cites a placebo-controlled test of nine popular coding-agent
skills in which two beat a same-length placebo (simonether/skill-placebo,
measured 2026-09-30, 450 trials). That test also found the placebo alone raised
cost, which is the video's reason for the last sentence. These study details
are reported from the video and have not been independently verified in this run.

### 9. Split only what is independent

Parallel helpers pay for independent work: breadth-first research, separate
modules, independent reviews. Each helper starts cold and multiplies usage, and
splitting coupled work produces conflicting decisions. Keep coupled production
with one owner, give parallel writers separate files and use one integrator.
When the work is coupled, the other model families review the owner's artifact
against stated criteria. They do not get a second implementation or a split
made to fill a share.

Revised. The breakdown says the orchestrator "must decompose" jobs and run the
parts concurrently. The video describes splitting a job that has independent
parts (five competitors, five researchers).

### 10. Unattended work stays inside its brief

Leave running, or schedule, only work that has a pass/fail check and whose
limits are stated in the current request and standing instructions. Those
limits are the scope, and memory of an earlier approval does not widen it. The
report leads with exceptions. Nothing is published, sent, merged, spent or
deleted beyond that scope.

Sharpened. The breakdown asks for scheduled background jobs. This application
has no scheduler and none was added; the host's own scheduled tasks can run
work that meets this rule.

### 11. Keep taste with the person, without stopping the run

Combining competing answers can erase their distinctive choices. For subjective
or outward-facing work, keep the strongest distinct answer to the same question
instead of an indiscriminate blend; combine complementary parts when useful.
Deliver it and list the alternatives and judgment calls so the person can
overrule them. Do not pause for a taste decision unless the request reserves
it for the person; required clarification and authorization still apply.
Put the person's taste upstream as
examples and rules (rules 3 and 4).

Sharpened. The breakdown reserves a human stage at the end. A final gate alone
arrives late and blocks an unattended run.

## How the breakdown's rules were treated

| Breakdown rule | Verdict | Rule here |
| --- | --- | --- |
| 1. Question and plan pre-flight gate | Revised: ask what matters, state the plan, preserve existing authorization | 2 |
| 1. Mandatory success definitions | Kept, with the reason added | 1, 7 |
| 1. Show over tell | Sharpened: label the examples | 3 |
| 2. Anti-context-rot (one task per context) | Revised: one deliverable per context | 6 |
| 2. State summarization and hand-off | Revised: state on disk, not a summary alone | 6 |
| 2. Domain sandboxing | Kept | 5 |
| 3. Parallel decomposition | Revised: only independent work | 9 |
| 3. Asynchronous scheduling | Sharpened into conditions; no scheduler added | 10 |
| 4. Minimalist, empirical skills | Kept | 8 |
| 4. Thinking-model autonomy | Kept, with the exact-method and tier exceptions | 1 |
| 5. Objective pass/fail tests | Sharpened: an order of preference, and who runs it | 7 |
| 5. Correction-to-rule persistence | Sharpened: enforce as a check, scope it, let it expire | 4 |
| 5. Human-in-the-loop taste check | Sharpened: surface judgment calls without blocking | 11 |

## Where each rule is applied

| Rule | In this application | In skills |
| --- | --- | --- |
| 1 | Task contract (WHY, AVOID, guidance); coordinator guide | `task-brief` |
| 2 | Coordinator guide; worker guide (assumptions) | `task-brief` |
| 3 | Task contract (EXAMPLES); coordinator guide | `task-brief` |
| 4 | Coordinator guide (rejections and corrections) | `project-lessons` |
| 5 | Already in place | Not applied; project layouts already follow it |
| 6 | Already in place (checkpoints); task contract (RETURN) | `task-brief` |
| 7 | Task contract (ACCEPTANCE, RETURN); both guides | `task-brief`, `verification-before-completion` |
| 8 | Not applied here | `lean-skill-authoring` |
| 9 | Coordinator guide | `task-brief` |
| 10 | Recorded here only | `task-brief` |
| 11 | Coordinator guide | `task-brief` |

The files are `skills/multi-model-orchestrator/references/task-contract.md`,
`app/assets/orchestration-context.md` and `app/assets/worker-guide.md`.

## In the video but not a rule here

- Dictating a long, natural brief instead of polishing a prompt (habit 1) is a
  habit for the person. Rules 1 and 2 cover the agent's side: accept a loose
  brief and extract the outcome, the limits and the check.
- Treating the plan's usage limit as capacity (habit 8) is handled by the quota
  guard and the team mix.
- The three levels of use (asking, delegating, running) describe the person's
  progress and need no rule.
