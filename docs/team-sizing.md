# Choosing a useful team

Start with the smallest team that has distinct work to do. This application now
provides read-only advice through `python orchestrator.py team`; it does not
start models, refresh quota, reserve allowances or change coordinator ownership.
The counts below are starting points for this installation, not measured optimal
counts. Existing task records do not contain a controlled team-size comparison.

| Task | Independent production scopes | Later review assignments | Total roles including one lead | Initial simultaneous worker cap |
| --- | ---: | ---: | ---: | ---: |
| Simple command or small edit | 0; lead does it | 0 | 1 | 0 |
| Debugging | 1 diagnosis/fix | 1 | 3 | 1 |
| Implementation | 2 disjoint areas | 1 | 4 | 2 |
| Research or comparison | 4 separate questions | 1 | 6 | 4 |
| Broad audit | 6 separate audit areas | 2 | 9 | 4, in waves |
| Independent batch work | Up to 8 batches | 1 | Up to 10 | 4, in waves |

The lead coordinates, integrates and can work on its own bounded scope. Reviewers
are worker assignments and count toward the same simultaneous cap; their phase
follows completed production. These are assignment roles, not a requirement to
keep that many model sessions alive. Preserve independent review when reusing a
session. A shared file, dependency or single unanswered question is not several
independent production scopes.

The default planning cap is **4 simultaneous workers, excluding the lead**. This
is conservative advice for mixed hosted routes, not a global scheduler limit or
an assertion that four slots are currently available. Apply the actual transport
limit too: a native tool allowing four total agents leaves three worker slots
when the lead occupies one. For that case pass `--cap 3`. Increase to 6–10 only
when independent scopes, actual route capacity, quota and measured benefit exist.

```powershell
python orchestrator.py team --type implementation --independent-workstreams 2 --cap 2
python orchestrator.py team --type audit --independent-workstreams 6 --cap 4
python orchestrator.py team --type research --independent-workstreams 4 --cap 3 --size small --json
python orchestrator.py team --type simple --history-limit 0
```

`--independent-workstreams` defaults to **1** because independence should be
established, not assumed. `--provider` can be repeated to restrict which hosted
allowance pools the advice considers. Local models are excluded. `--cap` accepts
1–10; `--history-limit` accepts 0–1000 and defaults to the 300 most recently
modified canonical task indexes. The full JSON reports first-wave workers,
planned waves, total roles, quota state, and provider timing sample counts.

## Allowance and admission

Planning reads the saved policy and quota snapshot and evaluates it without
writing a reservation. It simulates competing assignments in a discarded copy,
so workers sharing an allowance pool cannot each spend the same remaining quota.
Active and not-yet-reconciled estimates still reduce available capacity. Low
readings at or below the policy warning threshold hold new planning slots.

**Unknown** means absent, stale, failed or malformed quota evidence; it does not
mean a measured zero allowance. **Held** means the available evidence permits no
new planning slots. Either can produce a zero-slot recommendation. **Ready** is
quota eligibility only: the existing dispatcher must still collect fresh
evidence, reserve actual work, validate its route, and honor the authorized model
and billing scope. No paid fallback or local-model startup is part of this tool.

## What the timing records show

The September 9, 2026 local audit read 221 canonical task indexes. Among
successfully started tasks, Claude had 59 observations with median preparation
4.40 seconds, execution 14.22 seconds and postprocessing 4.46 seconds. Grok had
56 observations with medians 2.55, 31.98 and 2.62 seconds respectively. Different
prompts, task sizes and dates make this an operational baseline, not a provider
speed ranking. The command recomputes these figures from current saved indexes.

Preparation runs from task creation to execution start and includes quota
collection and setup. Execution runs from start to end. Postprocessing runs from
end to finalization and can include another quota refresh. Queue wait is
**unknown**: creation-to-start alone cannot distinguish an actual queue from
preparation. Historical strict-mode tasks synchronously collected provider quota
before execution and again during cleanup. This host now uses advisory admission:
it reads saved usage and queues hidden, coalesced collection without waiting for
the provider panel. Inspect `quota_before`, `background_quota_refresh` and measured
phase timings before adding workers. A collection timeout is not bot failure.

Imported completed artifacts do not establish model execution time. The 34
Codex artifact entries in that snapshot had no execution start, so their timing
remains unknown. Reconciled tasks can receive much later finalization timestamps;
the planner excludes those postprocessing and total values instead of counting
manual recovery as model latency. Failed, held and uncertain tasks remain in
status counts but are excluded from successful-task latency distributions.

Only fixed provider/status labels and numeric timings are reported. The reader
uses `record.json` indexes, not responses or transcripts, and drops task titles,
prompts, answers, memory context and arbitrary provider labels before aggregation.
History reading is bounded to 512 KiB per index, 32 MiB per request and 5,000
directory entries; skipped or truncated data is reported. No history copy is
stored. Deadline defaults remain tiny 120, small 450, medium 900 and large 1,350
seconds. More time avoids premature termination; it does not reduce latency.

## Establishing the best count for this workload

For each recurring task class, keep the model/effort, input scope, acceptance
checks and memory snapshot constant. Compare the same representative cases at
one, two and four concurrent workers; use the same task boundaries wherever they
can be parallelized. Run enough repeated cases to separate provider jitter from
the effect of team size, and alternate the order. Do not duplicate real writes:
use isolated test copies or supplied-text work with distinct assignment IDs.

Record total user-visible completion time, preparation, execution, cleanup,
review delay, failures, accepted quality and actual reported usage. Keep missing
usage or phase measurements null. Choose the smallest count that meets quality
and latency requirements; increase beyond four only after a repeatable gain.
These experiments require real approved task work and have not been claimed as
completed by the advisory command.

The small-team starting point follows [OpenAI's practical guide to building
agents](https://cdn.openai.com/business-guides-and-resources/a-practical-guide-to-building-agents.pdf),
which recommends exhausting a single agent's capability before introducing more
coordination. [Anthropic's research-system engineering
report](https://www.anthropic.com/engineering/multi-agent-research-system) describes
benefits from independent research branches and explicit scope boundaries, while
noting high token use and poorer fit for tightly coupled coding tasks. Those
systems provide design evidence, not a universal optimum for this application's
providers or task mix.
