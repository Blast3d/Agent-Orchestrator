# Solo versus mixed-provider audit pilot

New executions use [isolated child runs](../ISOLATED_EXPERIMENTS.md), one per
condition and repetition, with their own live **Experiment runs** dashboard
view. Use a fresh parent; historical combined pilots are immutable. Normal and
edge checks appear as separate sections inside each execution. `report.py`
remains a historical combined-run export; use the dashboard for new comparisons.

This compact, synthetic benchmark compares a solo frontier model with the same
implementation model plus independent auditors. It changes no production app and
does not clone a repository, download models, or save audio/media datasets.

| Workload | Seed | Solo control | Orchestrated condition |
|---|---|---|---|
| Small | Usage aggregation | OpenAI implementation, then its own audit/revision | OpenAI implementation, Claude audit, OpenAI revision |
| Medium | Dependency and budget scheduler | OpenAI implementation, then its own audit/revision | OpenAI implementation, parallel Claude/Grok audits, OpenAI revision |

There are two repetitions of each condition: eight scored attempts. Each has an
initial test, one audit/revision opportunity, and a final test. The solo model
receives the same public test feedback and may correct itself. Initial prompts,
contracts and source bytes match across conditions. Effort is low for small
OpenAI/Claude assignments and medium for their medium assignments; the configured
Grok supplied-text route uses low effort. OpenAI requests `gpt-6-astra`; Claude
requests Opus. Record provider-reported identity separately from requested labels.

The small team uses two provider roles, and the medium team three. This is a
repair-and-audit comparison; it does not measure a team splitting production work
across a large live repository. Different transport/system-prompt overheads remain
part of the measured deployment configuration. Total compute is not matched:
the team receives additional review calls. Interpret its quality, time, and
resource consumption together, not as proof that coordination alone caused a gain.

## Fixed evaluation

`fixtures/` contains the deliberately defective source modules. `contracts/`
contains their requirements. `grader.py` contains public examples plus local
holdout cases and a reference implementation. It checks exact behavior, invalid
inputs, mutation, dependency cycles, deterministic order, and seeded random DAGs.
The checks are correlated cases, not independent real-world tasks; sample size is
two workloads with two repetitions, not the number of assertions.

The runner freezes hashes before the first model call. Never edit those files
during a pilot. No hidden grades, reference implementations, or another trial's
answer enter model prompts. All calls disable project recall. Native calls use a
fresh ephemeral Codex invocation, a read-only sandbox and disabled execution,
delegation, browsing, app, plugin and memory features; the runner also verifies
that recorded items show no tool activity. Hosted audits use the maintained
guarded supplied-text dispatcher with no memory or automatic model fallback.

The candidate evaluator limits available imports and builtins and runs with a
deadline. It is a bounded check for these reviewed synthetic outputs, not a
general-purpose security sandbox for arbitrary hostile Python.

## Reuse

Run these without invoking any model:

```powershell
python benchmarks/solo_vs_team/grader.py --task small --self-test
python benchmarks/solo_vs_team/grader.py --task medium --self-test
python benchmarks/solo_vs_team/grader.py --task medium --candidate PATH_TO_COPY.py
```

For another authorized live pilot, create a new run in the maintained application
root with project `orchestration-benchmark` and `--no-memory`. Then:

```powershell
python benchmarks/solo_vs_team/run_pilot.py --run .orchestration/EXACT_RUN_ID
```

This starts real model calls using existing accounts and normal quota admission.
An optional `--only small-solo-r1` selects one exact attempt. Successful saved calls
are reused; held/running/uncertain calls require explicit reconciliation. Do not
erase a receipt to force a retry. The controller owns coordinator checkpoints and
reloads the operating guide before new dispatch. Final task review, native memory
capture, contribution audit and verified closeout remain lead responsibilities.

## Measurements

- Primary outcome: an attempt passes every final check. Also retain initial/final
  case counts and regressions; a team gets no credit just for producing reviews.
- Time: compare initial call + longest parallel audit + final call. This includes
  each call's quota admission and process startup, and excludes grading, outer
  checkpoints and human setup pauses. Also retain raw attempt wall time; flag any
  interrupted/resumed attempt as unsuitable for a raw wall-time comparison.
  Fixture development and this controller conversation are separate setup costs.
- Usage: record input, cached input, output and provider-reported cost separately.
  Claude input includes uncached, cache creation and cache read components;
  cached OpenAI input is already part of its total input and is not added twice.
  Reconcile xAI input against its reported total minus output; the observed CLI
  receipts count cache separately. Inconsistent totals stay unknown. Reasoning
  output already included in output tokens is not added again.
  Provider tokenizers differ, so cross-provider totals are accounting summaries.
- Allowance: snapshot official available meters before/after where readable.
  Shared-account activity can affect them. Never add percentages from different
  providers/windows, treat an unreadable meter as zero, or equate a reservation
  estimate with actual consumption.
- Dollars: a provider's API-equivalent cost estimate does not prove an additional
  subscription charge. Unknown cost stays unknown; no new billing route is used.
- Disk: count the compact benchmark folder, trial artifacts and linked canonical
  hosted-task artifacts. Keep only source, prompts, answers and measurement data.

Treat a clean sweep as a possible ceiling effect. Two repeated tasks can reveal
obvious overhead or defects; they cannot establish a general model ranking or
prove which approach wins across the user's larger projects.
