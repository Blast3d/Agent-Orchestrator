# Task: repair a dependency-aware token-budget scheduler

Change the supplied Python module so `allocate_jobs(jobs, budgets, completed=())`
meets this contract. Preserve the function name/signature. Standard library only.
No I/O, network, subprocesses, external project imports, or import-time execution.
Return the complete replacement module, not a diff. Do not change this contract.

## Validation

All invalid inputs raise ValueError. Validate the whole input before scheduling.
Never mutate jobs, nested dependency lists, budgets, or completed, even on failure.
Additional dict fields are allowed. Reject booleans wherever integers are required.

- A canonical identifier is a nonempty string equal to its stripped form; case is
  significant. This applies to job IDs, provider names, and dependency IDs.
- `budgets` must be a dict of canonical provider names to nonnegative integers.
  Empty budgets is valid when no jobs require providers. Preserve unused providers.
- `jobs` must be a list of dicts with required fields `id`, `provider`,
  `estimated_tokens`, `priority`, `depends_on`. IDs must be unique. Each provider
  must exist in budgets. Costs are nonnegative integers; priorities are integers
  (negative priorities are valid). `depends_on` is a list of canonical identifiers;
  duplicate dependencies are allowed and equivalent to a single dependency.
- `completed` must be a list or tuple of canonical identifiers. Duplicates are
  allowed. Completed IDs may refer to external jobs not present in jobs.
- A dependency must name a supplied job or a completed ID. Self-dependencies are
  always invalid, including on completed jobs. Every job is validated, including
  jobs whose IDs are already completed.
- Reject cycles among NOT-already-completed jobs, even if they are unaffordable.
  Dependencies on already-completed IDs are satisfied and do not form cycle edges.

## Scheduling

Start with an independent copy of budgets and the supplied completed IDs.
Repeatedly select exactly one unscheduled, not-already-completed job whose
dependencies are all completed/selected AND whose cost fits its provider's
remaining budget. Among ALL such eligible jobs choose highest priority, then
lexicographically smallest ID. Deduct its cost and immediately mark it completed.
Recompute eligibility after each selection. An unaffordable high-priority job must
not prevent another affordable job from running. Zero-cost jobs are valid.
Stop when no eligible affordable job remains. Already-completed jobs consume
no budget and appear in neither scheduled nor blocked.

Return exactly:
`{'scheduled': [IDs in selection order], 'remaining': {provider: unused budget},
  'blocked': {unscheduled ID: reason}}`.

Blocked reasons are computed AFTER scheduling: `budget` if all dependencies are
satisfied, otherwise `dependency`. Order blocked IDs lexicographically. The result
must be deterministic for any input job order.

Public examples (J abbreviates a job with the five required fields):

- J(b,p,2,priority=2,depends=[a]), J(a,p,1,priority=1,depends=[]), budgets={p:3}
  => scheduled=[a,b], remaining={p:0}, blocked={}.
- J(x,p,5,priority=9,depends=[]), J(y,p,1,priority=0,depends=[]), budgets={p:1}
  => scheduled=[y], remaining={p:0}, blocked={x:'budget'}.
- J(a,p,0,priority=0,depends=[b]), J(b,p,0,priority=0,depends=[a]) is invalid,
  but completed=[a] makes it valid and schedules b with no budget consumption.
