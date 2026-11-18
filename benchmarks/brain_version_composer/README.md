# Old/new Brain with native composer leads

This is a six-trial diagnostic: Astra, Sol 6.1 and Terra 6.1 each coordinate the
same synthetic repair task with the old and new Brain startup context. It never
launches the lead from another model, impersonates a composer switch, or changes
the application's global lead setting.

Jacob confirmed Astra means `gpt-6-astra`. The other leads must be
`gpt-6.1-sol` and `gpt-6.1-terra`; all use High reasoning. Every trial has the same
two workers: Sol 6.1 repairs aggregation and Terra 6.1 repairs reporting. The lead
owns allocation, assignment, review and integration. Helpers that built this
test are preparation contributors, never scored contestants.

The baseline is the parent of `0261045`, immediately before the October 24 Brain
upgrade. The candidate is the committed HEAD at preparation. Full commit IDs,
source exports, grader, fixtures, corpus, model settings and their hashes are
frozen in a new ignored runtime directory. Production databases/configuration and
earlier experiments are untouched. A synthetic long history record deliberately
exercises the new summary-first packing; this is not a representative workload.

## Prepare and verify

```powershell
python -m unittest discover -s benchmarks/brain_version_composer -p test_suite.py -v
python -I -B benchmarks/memory_control/grader.py --self-test
python benchmarks/brain_version_composer/suite.py prepare --out runtime/benchmarks/UNIQUE_SUITE --parent .orchestration/EXACT_PREPARATION_RUN
python benchmarks/brain_version_composer/suite.py status --suite runtime/benchmarks/UNIQUE_SUITE
python benchmarks/brain_version_composer/suite.py report --suite runtime/benchmarks/UNIQUE_SUITE
```

Preparation runs the actual exported old and new Brain code in separate Python
processes against identical deterministic reviewed seeds. Socket and subprocess
calls are blocked inside the recall worker. Keyword retrieval, Jev off, no
embeddings, no trace writes. Both 3,000 and 8,000 character budgets are measured;
the native trials receive the frozen 3,000-character result. The ample-budget
control shows whether the supplied facts converge when space is sufficient.

## Composer sequence

Open the exact `START.txt` printed by `status`, select the requested model and
High reasoning, and paste it into a **fresh, non-forked composer conversation**.
Changing the model inside a trial is refused. Reusing a conversation for the
other Brain arm is refused. The preparation conversation is refused as a trial.
This prevents this setup agent from doing Sol or Terra's scored lead work.

| Order | Composer lead | Brain |
| --- | --- | --- |
| 1 | GPT-6-Astra, High | Old |
| 2 | GPT-6-Astra, High, fresh conversation | New |
| 3 | GPT-6.1-Sol, High, fresh conversation | New |
| 4 | GPT-6.1-Sol, High, fresh conversation | Old |
| 5 | GPT-6.1-Terra, High, fresh conversation | Old |
| 6 | GPT-6.1-Terra, High, fresh conversation | New |

The trial's generated BRIEF specifies file ownership, prompt capture, fixed
worker identities, two public feedback opportunities, and final grading.
Workers must be newly spawned native sessions bound to that trial's lead.
Only the three implementation modules may change. The hidden evaluator is run
once at finish, after outputs are frozen; its 280 cases are not available for
revision. `result.json` preserves actual session/model evidence and output hashes.
Interrupted trials stay active/held; do not delete receipts to retry them.

## What the results mean

Compare each lead's old/new pair on final correct cases, elapsed wall time,
available native token counters, public feedback rounds, delivered seed coverage,
context size and Brain lookup time. Usage that was not reported stays unknown;
subscription charges are not inferred. Wall time includes human and tool waits.
Initial broken fixture quality is established by the grader self-test, not by a
contestant. No score exists for unstarted conditions.

This first suite tests bounded startup recall and native team coordination.
It does **not** measure inbox capture, whole-application workflows, native MCP
discovery, JEV ranking, or general model superiority. The Brain MCP's working
connection is verified separately against `docs/brain-native-access.md`.
Production Brain access during a synthetic trial would contaminate that trial.
File/tool scope is a cooperative instruction boundary, not an OS sandbox;
transcripts and recorded prompts still require review before accepting results.

The normal Orchestrator lifecycle currently lists only Astra, Sol and Claude and
expects cross-family coverage. This user-requested same-family experimental
roster is tracked separately; no policy is weakened, Terra is not mislabeled as
Sol, and no general closeout success is claimed. The preparation run remains held
until its own lifecycle evidence is resolved. One pair per lead and partial order
balancing support descriptive comparisons only. A larger independently prepared
replication is needed for statistical conclusions.
