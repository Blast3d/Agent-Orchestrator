# Brain memory control pilot

New executions use [isolated child runs](../ISOLATED_EXPERIMENTS.md), one per
condition, with their own live **Experiment runs** dashboard view. Use a fresh
parent; historical combined pilots are immutable. Normal and edge checks appear
as separate sections inside each execution. `report.py` remains a historical
combined-run export; use the dashboard for new child-run comparisons.

This experiment uses real OpenAI, Anthropic and xAI calls with a compact synthetic
repository and the Orchestrator's real project-scoped Brain. File tools are
simulated by a controller that performs bounded real reads and literal searches
over that fixture. It does not give contestants arbitrary PowerShell, shell,
network or filesystem access.

`--jev-comparison` opts into six matched conditions: `solo-cold`, `team-cold`,
`solo-warm`, `team-warm`, `solo-jev-warm`, `team-jev-warm`. The first warm pair uses
ordinary Brain; the last pair uses Brain with JEV. Each has its own fresh project.
The default remains the four-condition pilot. Archived runs must remain unchanged.

For the six-condition suite, authorize only the two exact JEV child projects in the
existing guarded JEV configuration. Cold and ordinary projects must stay outside
that authorization list. The runner checks this before each new call and records
the effective policy. It never changes JEV configuration. Keep prior authorized
projects, purposes, confidence threshold and cache settings; restore the prior
project list after the suite. If `memory_passage_review` is already enabled, the
actual JEV treatment includes passage review and records that purpose explicitly.

Prepare a fresh parent through the normal orchestration startup workflow, then:

```powershell
python benchmarks/memory_control/run_experiment.py --run EXACT_PARENT --jev-comparison --phase preflight --condition solo-cold
python benchmarks/memory_control/run_experiment.py --run EXACT_PARENT --jev-comparison --phase freeze
python benchmarks/memory_control/run_experiment.py --run EXACT_PARENT --jev-comparison --phase cold
python benchmarks/memory_control/run_experiment.py --run EXACT_PARENT --jev-comparison --phase seed
python benchmarks/memory_control/run_experiment.py --run EXACT_PARENT --jev-comparison --phase ordinary
# Authorize the two JEV project IDs shown by preflight through the guarded config path.
python benchmarks/memory_control/run_experiment.py --run EXACT_PARENT --jev-comparison --phase preflight
python benchmarks/memory_control/run_experiment.py --run EXACT_PARENT --jev-comparison --phase jev
```

`preflight` only reads source and ranking policy; it does not create children,
search Brain, seed notes, refresh quotas or invoke a provider. Its readiness is
limited to those checks; provider authentication, allowance and lead identity need
their normal preflight. `--phase trial --condition NAME` runs one declared condition
and enforces the same frozen order. `freeze` creates isolated children and records
input hashes. Both cold trials must finish before `seed` can insert the same four
notes independently into all four warm projects. No contestant answers are
accepted or captured until all six conditions finish; seed captures contain only
the prewritten notes. Failed or uncertain conditions require explicit reconciliation.

Warm calls save `memory_ids`, `memory_sha256`, actual `memory_context`,
`memory_lookup_ms`, `memory_elapsed_ms`, `memory_trace_id`, `memory_trace_status`,
`memory_retrieval`, `memory_receipt`, and `memory_execution_requested` in each call
record. The full nested `memory_retrieval.jev` preserves candidate/scored counts,
status, applied/order_changed, promotions, latency, requests, cache state, tokens
and reported cost, including available batch detail. Missing values stay unknown.
An applied ranking with unchanged order is recorded as such. Startup checkpoints
always use no-memory mode; only contestant calls retrieve the treatment notes.
The contestant roster stays Astra/medium, Opus/medium and configured Grok/low;
the controller's selected model and its usage remain separate.

Offline checks for this extension:

```powershell
python benchmarks/memory_control/test_jev_comparison.py -v
```

The retained corrected grader's `--self-test` currently compares generated case
metadata (including `group`) with the older public JSON (without `group`) and fails
with `public fixture drift`. The offline extension checks public case content after
excluding only that metadata and separately checks all 280 reference cases. It
does not change the grader, fixture, seed text or contestant feedback.

The four scored conditions are one solo OpenAI run and one team run without
memory, followed by fresh solo and team runs with memory. Each team assigns
independent implementation modules to OpenAI, Claude and Grok. There is one
repetition per condition: this is a functional pilot, not a statistical ranking.

## Controls

- Both runs without memory finish before any seed is added to the exact Brain
  project. Every worker's background project recall is disabled in that phase.
- The seeds are written and hashed before the first run. They cover navigation,
  public tests, conventions and a small historical subproblem. The same facts are
  discoverable in fixture documents. They contain no complete repair or hidden
  evaluator information, and are not changed in response to cold-run answers.
- The memory phase recalls only those approved seeds through the real Brain.
  Both solo and team get the same available knowledge. Record retrieved IDs,
  actual supplied context, provider execution binding and claimed use separately.
- No contestant answer is accepted or captured into Brain until all four scored
  runs finish. Fresh independent conditions never inherit another condition's
  conversation, candidate code, tool output or test grade.
- Each worker may take up to four discovery/implementation turns, with up to six
  list/search/read operations per turn, and must read current owned modules
  before replacing them. Each receives one public-feedback audit/revision turn
  after its first submission. Hidden tests never enter prompts.
- Source, contracts, grader, prewritten seeds, model settings and experimental
  transport are frozen before inference. Pending/uncertain calls are preserved;
  completed call reuse requires identical inputs. No billable fallback is used.

## Measurements and limits

Record initial/final correctness, searches, listings, reads, successful/error
operations, model turns, provider input/cache/output, memory retrieval time and
end-to-end trial time. Team wave timing is governed by the longest concurrent
call; also retain cumulative provider time rather than presenting it as elapsed
time. Include public-test and integration time in the end-to-end measurement.

These are fresh supplied-text invocations with controller-carried conversation
history. Model-call overhead is therefore part of this simulated tool interface;
the result does not directly estimate a persistent native coding agent's latency.
Provider tokenizers and cache accounting differ. The report normalizes observed
cache components and leaves unreported identity or actual subscription charges
unknown.

The requested cold-first order prevents seeded-memory leakage, but cannot
separate treatment effects from provider load, caching or other time trends. A
memory-enabled success is not by itself proof that a particular memory helped.
Use observable actions and independently graded behavior alongside self-reported
use. All facts in curated notes must still be checked against current files.

## Reuse

The fixture is a small directory of plain source and Markdown files; copying it
does not copy the application, Brain database, account state or large datasets.
Use `fixture-spec.json` for controller ownership and `memory-seeds.json` for the
prewritten notes. Keep both outside the worker-visible fixture.

The grader runs locally without model calls. The live runner uses an exact
new authorized run, existing provider routes and normal quota admission. The
controller retains execution receipts, and ASTRA must review tasks, capture
final evidence, generate contributions and verify closeout before delivery.

```powershell
python benchmarks/memory_control/grader.py --self-test
python benchmarks/memory_control/grader.py --workspace PATH_TO_CANDIDATE --role all
python benchmarks/memory_control/report.py --run EXACT_EXISTING_RUN_DIRECTORY
python benchmarks/memory_control/run_experiment.py --help
```

The September 13 pilot retained its frozen grader and all original feedback. That
grader accidentally omitted the documented pure builtin `divmod`, falsely failing
26 allocation checks in both solo initial submissions. After all four scored runs
finished, the reusable grader added only `divmod`, and all eight initial/final
snapshots passed 280/280 on a uniform local recheck. The original grader is archived
in the run's `review/frozen-grader.py`; `review/corrected-grades.json` records both
hashes. Incorrect feedback affected the solo revision process, so correcting the
grades does not correct the observed timing. Reuse the corrected grader with a new
run and a fresh experiment project; do not modify or resume the old frozen trial.
