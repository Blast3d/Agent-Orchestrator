# Medium task: no memory versus Jev recall

This is a real-provider, two-arm experiment requested on September 25, 2026.
There is **no ordinary Brain recall condition**. It measures the combined effect
of adding reviewed Brain memory and the production Jev scoring step.

Each of four independent Claude Opus sessions repairs the same dependency-aware
Python scheduler. The public contract covers whole-input validation, graph cycles,
completed jobs, budget feasibility, priorities, deterministic ordering, boundary
cases and input preservation. The frozen local grader contains 106 checks. These
checks share a single implementation; they are not 106 independent model trials.

The order is two no-memory runs, then two Jev-assisted runs. Each gets one response
at medium effort, no repair turn, no native tools and no model fallback. The
dispatcher supplies the same operating guide and public task text to all four.
Only the warm runs receive reviewed memory. Each condition has a separate run and
Brain project, and uses the maintained sequential experiment lease.

Six question-and-note records are frozen before inference and inserted into the
warm memory scopes only after both cold runs finish. Every note is supported by
the public task contract. Notes contain no completed solution, hidden test values
or facts extracted from contestant answers. The full task contract remains
available to the no-memory runs. The queries and their reviewed notes remain in
the isolated test memory bank for inspection.

The warm dispatcher performs a real production Brain search and Jev decision.
Its exact memory IDs, content hash, execution-request receipt, query, profile and
provider telemetry are retained. The runner temporarily authorizes only that
synthetic project's Jev lookup and removes its addition afterward. Production
project authorizations and the confidence threshold are preserved. An API failure
holds the condition; a valid low-confidence response uses the production fallback
and is reported explicitly.

## Commands

Create a fresh parent with `orchestrator.py start`, then select its exact path:

```powershell
python benchmarks/jev_comparison/run.py --run .orchestration/EXACT_PARENT --phase prepare
python benchmarks/jev_comparison/run.py --run .orchestration/EXACT_PARENT --phase cold
python benchmarks/jev_comparison/run.py --run .orchestration/EXACT_PARENT --phase warm
```

The last two commands invoke real Claude and, in the warm phase, OpenRouter/Jev.
Existing account and provider authorization is required. `prepare` performs no
contestant inference. Input changes after the first request require a new
experiment; uncertain calls and partial seed writes are never silently retried.
The runner writes `results.json`, `results.md` and `results.html`. The Orchestrator
Experiment runs view shows each condition's roster, inputs, outputs and checks.

## What the numbers mean

End-to-end time covers guarded dispatch, memory lookup, provider execution,
cleanup, response parsing and local grading. Separate provider and lookup times
remain available. Local setup, memory insertion and controller work are outside
the timed runs; their model usage is unknown and excluded. Anthropic input tokens
include reported fresh input, cache creation and cache reads. OpenRouter-reported
Jev charges are separate from Claude cost estimates and unknown subscription
billing.

Two observations per arm support a descriptive pilot, not a reliable causal speed
claim. Fixed cold-first order can confound provider load and cache warming. A
perfect score in both arms shows no measured correctness gain. A Jev fallback
does not demonstrate useful reranking. Supplied memory is observed; claimed use
is self-report; helped/neutral/harmful feedback requires a separate review of the
accepted answer and its exact context.
