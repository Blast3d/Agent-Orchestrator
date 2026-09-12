# Contribution audits after every orchestrated task

Every finalized guarded worker task saves `contribution-audit.json` and `contribution-audit.md` beside its result. Review refreshes those files. The dashboard links to the audit. Codex also produces a combined audit when an orchestrated user task finishes, including its own integration and review work.

Reports distinguish **estimated share of accepted work** from **recorded usage**. Contribution is calculated from meaningful work items, their relative weights, accepted status, agent allocations and evidence. Categories can include coding, research, design, narration, testing and review. Usage reports delegation attempts, reported input/output token counters, observed model metadata and measurement coverage. Failed or rejected drafts remain visible as usage.

The formula is `sum(accepted item weight × agent allocation) / total accepted weight`. Category shares use only accepted items in that category. Named shares plus unattributed work sum to 100%, rounded to one decimal. No accepted work means no contribution percentage. Unallocated accepted work remains explicitly unattributed instead of inflating known shares.

Weights and allocations are coordinator estimates, not measured effort. Codex verifies them against what was produced and retained. An accepted defect finding can count as review; an unused draft cannot count as accepted implementation. Do not count the same output twice. Multiple native Codex agents share a provider identity; they are not separate model providers.

Missing token counters remain unknown. Top-level input/output counters are used once; cached-token details and auxiliary model breakdowns remain in the original result rather than being added again. A share over known counters is not an account quota percentage, billing amount, effort or quality score. A delegation attempt does not prove a billable inference call occurred.

## Team ledger and audit

New `init_run.py` runs contain `contributions-ledger.json`. This illustrative 90/10 coding allocation is an example, not a claim about a past job:

```json
{
  "schema_version":1,"scope_id":"example","title":"Example coding task",
  "basis":"Estimated accepted allocation, reviewed against the patch and corrections.",
  "contributors":[
    {"id":"claude","name":"Claude","provider":"Anthropic","model":"unknown"},
    {"id":"codex","name":"Codex","provider":"OpenAI","model":"unknown"}
  ],
  "work_items":[{
    "id":"code","label":"Accepted implementation","category":"coding","status":"accepted","weight":1,
    "allocations":[
      {"agent_id":"claude","percent":90,"evidence":"Accepted initial implementation"},
      {"agent_id":"codex","percent":10,"evidence":"Verified integration corrections"}
    ]
  }],
  "activity":[]
}
```

Activity entries have unique `id`, `agent_id`, `kind`, `status`, `task_id`, nullable `input_tokens`/`output_tokens`, and `actual_models` as a string list. Only `kind: delegation` counts toward delegation share. Polls are not new assignments. Record coordination and review separately where known, keeping unreported tokens null.

```powershell
python orchestrator.py contributions --ledger PROJECT_RUN/contributions-ledger.json --output-dir PROJECT_RUN --require-complete
```

`--require-complete` holds final delivery if accepted work is unattributed or the accepted-work denominator is absent. It still saves the honest incomplete report. For a wholly failed task, report activity without demanding an accepted-work percentage.

## Individual task attribution

Use the saved job ID as `scope_id`. Automatic identities use `worker:claude`, `worker:grok`, etc., and `reviewer:Codex` for a named reviewer. Additional contributors need evidence. The observed worker identity and usage replace supplied estimates of those fields.

```powershell
python orchestrator.py review JOB_ID --decision accepted --reviewer Codex --note "Verified the accepted patch and integration corrections." --contributions-file attribution.json
python orchestrator.py contributions --task JOB_ID --ledger attribution.json --require-complete
```

The second command can fill in attribution after acceptance. Without a ledger, `contributions --task JOB_ID` refreshes its automatic activity audit. Accepted work without allocations is marked `needs_attribution`. The global skill requires Codex to finish attribution before final orchestration delivery.

Reports use no model calls. The software validates arithmetic and record structure; honest weighting, evidence and authorship decisions remain the coordinator's responsibility.

## Who did what, and how much (summary for dashboards)

`app/contribution_summary.py` folds the saved evidence into one view without a
model call: per run, each agent's accepted share, delegations and reported
tokens plus the dispatched tasks linked to that run (by manifest job id, audit
activity or assignment scope); across runs, per-worker totals from canonical
task records. Prompts, responses and evidence text never enter the output, and
unknown token counts stay unknown. The coordinator's own conversation is not
metered, so ASTRA and Fable always show unknown tokens for their own work.

```powershell
python app\contribution_summary.py --markdown                 # every run + per-worker totals
python app\contribution_summary.py --run RUN_NAME --markdown  # one run's agents and tasks
python app\contribution_summary.py --run RUN_NAME             # JSON for a dashboard panel
```

The same JSON is intended for a "Who did what" panel in the Orchestrator viewer
(selected run) and the memory dashboard (when the project is a run); the panel
lands once the current dashboard work settles.
