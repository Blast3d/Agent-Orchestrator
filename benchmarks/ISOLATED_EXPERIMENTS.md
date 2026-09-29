# Isolated experiment conditions

New experiments create a comparison parent and one ordinary orchestration run
per condition and repetition. A condition is an execution such as solo without
memory or a three-provider team with seeded memory. Normal and edge cases are
result sections inside that execution; they are not extra provider runs.

Start a **fresh** parent using the maintained startup command, then supply its
exact returned run path to one of these runners:

```powershell
python orchestrator.py start --workspace . --name memory-comparison --objective "Controlled isolated memory comparison" --project memory-comparison --no-memory
python benchmarks/memory_control/run_experiment.py --run ".orchestration/<exact-new-parent-id>" --phase all
```

The second command invokes real models and consumes their allowances. It needs
the usual user authorization. To run the earlier implementation-plus-auditors
design, create another fresh parent and use:

```powershell
python benchmarks/solo_vs_team/run_pilot.py --run ".orchestration/<exact-new-parent-id>"
```

`--only small-solo-r1` selects one exact condition in that second runner. All
conditions are registered in advance so untouched conditions remain visibly
planned. Never pass the historical combined pilots to these revised runners;
they reject old call folders and frozen records without rewriting them.

The parent `experiment.json` links child run IDs. Each child's `condition.json`
shows its exact roster, role, requested model, memory mode, participant count,
and stage. Solo means one OpenAI contestant doing implementation and revision,
with zero delegated helper agents. A three-provider implementation team has
three declared contestants; the earlier audit design labels its reviewers as
auditors. Several turns from one contestant do not mean several bots.

Controller work and fixture/setup agents are outside scored contestant usage.
Their usage remains explicitly unknown unless independently measured, never
zero. Each child owns `calls/`, its workspace and outputs, its summary, and a
unique Brain project. Call records and raw stdout/stderr are retained locally;
the dashboard can show completed and failed calls before the condition ends.

For the memory comparison, both cold children must complete before any seed
insertion. Each warm child receives the same frozen, prewritten partial facts
in its own Brain scope. Memory IDs are intentionally different between warm
children; fact text is identical. Prior contestants' repaired code is never a
seed. Native memory and agent delegation are disabled at CLI level; warm Brain
retrieval is explicitly included in the supplied prompt.

Conditions run sequentially, including across runner processes. Team members
may work concurrently inside one condition. The durable
`.orchestration/experiment-active.json` lease and its OS lock prevent another
experiment from overlapping an active or uncertain condition. A crash, timeout,
or failed call blocks further conditions and retains evidence and reservations.
The runner never clears a lease because its PID died or a deadline elapsed.
An operator must reconcile the exact saved calls and provider state; uncertain
work cannot be silently retried. Start a fresh experiment for a deliberate
repeat. The experimental lease does not claim to stop unrelated manual model
calls elsewhere on the machine.

Run the isolation regression tests without any provider inference:

```powershell
python -m unittest discover -s benchmarks -p test_experiment_runs.py -v
```

These tests use temporary workspaces, real startup/Brain persistence, and
scripted fixture responses. Their synthetic token values check separation;
they are not benchmark measurements or live-provider evidence.
