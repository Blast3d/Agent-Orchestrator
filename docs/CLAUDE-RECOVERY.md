# Claude recovery and better work sharing

The original Claude coding attempt was stopped by our dispatcher after exactly
180 seconds. The dispatcher discarded its timeout output, so that attempt cannot
tell us exactly what the backend was doing. It does not establish that Claude
could not code or had exhausted its allowance.

On September 7/8, 2026, an instrumented replay of the same coding brief through
Claude Code 2.1.263, Sonnet 5, medium effort, began answering at 371.959 seconds
and finished at 472.458 seconds. It reported no retries, no stderr and a successful
terminal result. The old cutoff would have killed this successful replay before
its first answer. The completed replay is diagnostic evidence; its replacement
F09 implementation was not installed over the existing working feature.

Smaller assignments worked well in this session: Claude returned the actual
deadline module and tests in 15.6 seconds at low effort. Grok returned the actual
progress parser and tests in 61 seconds at low effort. An earlier Grok attempt
had also reached the old cutoff while its session reported reasoning. These
are observations from specific tasks, not promised response times.

## What changed

- Tasks have finite deadlines of 2, 5, 10 or 15 minutes according to size.
- Claude's progress is read while it runs. Available answer text and private
  local error output survive an interruption; thinking text is not saved in
  progress artifacts. Grok's buffered JSON route provides less live detail.
- A complete provider result and successful execution are required before an
  answer reaches review. Partial text does not count as delivered work.
- Unknown execution keeps its reservation for reconciliation. A missing Claude
  terminal event, including after a nonzero exit, does not authorize a handoff.
  An earlier quota retry cannot override a later authentication error. Confirmed
  terminal quota failures still use the guarded replacement-worker route.
- The global skill now gives ready external agents useful deliverables: code,
  tests, documentation, diagrams, presentation content and independent review.
  Codex sets interfaces, integrates and checks the result. Assignments name an
  owner, reviewer, allowance estimate, acceptance checks and suitable alternate.

Claude authored the deadline policy, reviewed Grok's parser, and drafted the
illustrated user guide. Grok authored the progress parser. Codex integrated the
files, implemented the process transport, corrected reviewed defects, and ran
validation. Native reviewers audited routing and the final execution boundaries.

## What the percentages mean

The previous panel gave Codex agents all accepted building, review and
presentation work. Their share was 73.4 of 95 reviewed work points, or 77.3%
when aggregated before rounding. Adding more voters alone did not share production
work. The historical ledger remains unchanged.

The new audit estimates each agent's share of accepted work from retained
deliverables and stated weights. It records calls and available usage separately.
It does not convert answer length, token counts or model-reported list-price
estimates into work percentages or subscription charges. More useful work can
be distributed across included allowances; duplicated prompts and reviews still
consume allowance, and equal percentages are not the goal.

The original Claude attempt and the first Grok timeout remain uncertain provider
executions. Their reservations have not been silently cleared. Distinct controlled
diagnostic tasks ran with fresh allowance admission and kept the earlier holds.

See [the illustrated guide](worker-handoffs.md) and the run's
`contribution-audit.md`, `contributions-ledger.json`, `project-map.html`, diagnostic
summaries and review decisions in
`.orchestration/claude-recovery-work-balance-20260908T013945Z-c98dbbed`.

Claude's supported streaming flags and terminal events are described in the
[official headless documentation](https://code.claude.com/docs/en/headless).
