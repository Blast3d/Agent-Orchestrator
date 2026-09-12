# Required contribution audit at task completion

The user wants an audit after every orchestrated user task, with estimated percentages for each participating agent. Include lead Codex coordination, implementation, integration and review when they produced accepted work. Agent instance, tool and provider/model are separate identities.

Maintain `contributions-ledger.json` alongside the run brief: meaningful work items, category, accepted/rejected/pending status, relative weight, agent allocations and evidence. New application runs create a template. Choose weights proportional to scope and review them against what actually shipped. Label percentages **estimated share of accepted work**, never measured effort. Do not calculate contribution from tokens, elapsed time, number of prompts or lines changed alone.

Before final delivery generate the combined audit:

```text
python ~/Documents/Agent-Orchestrator/orchestrator.py contributions --ledger RUN/contributions-ledger.json --output-dir RUN --require-complete
```

Read the application's `docs/contribution-audits.md` for the schema and task attachment commands. Finalized guarded tasks automatically save activity audits, refreshed after review. These cannot infer shared authorship: fill reviewed allocations before delivering accepted work. Native workers, MCP jobs and lead work need ledger entries too; the CLI cannot observe every session action.

Show a concise final table with agent, role and estimated overall contribution; add category splits when useful, such as coding versus research. Link the saved report. Keep recorded usage separate: delegation attempts and known input/output counters, with coverage stated when incomplete. Missing telemetry remains unknown. Changing account balances include other app activity and do not establish task usage.

Rejected/failed drafts count in recorded usage but not accepted output. Accepted review findings can count under review with evidence. Do not count a deliverable twice. Named contribution shares plus any explicitly unattributed work sum to 100%; never silently inflate partial attribution. If no output was accepted, report that no accepted-work percentage exists and show activity instead.

Mark the run's audit complete only after reviewing evidence and generating its report. These percentages are coordinator estimates with a stated basis, not quality scores or proof of provenance. Do not assign credit to unused providers or label native Codex workers as Claude, Grok or Gemini.

## Show the result visually

The user prefers a plain-language visual view: project in the center, agents branching around it, shares and a way to compare projects. The application `contributions` command refreshes and registers each combined report with Project Maps. Use `python ~/Documents/Agent-Orchestrator/orchestrator.py visuals --open` to show it when useful, and link `runtime/project-map.html` in final delivery. Add an existing external project report with `visuals --add-report PATH`; never invent historical attribution just to populate the view.

Keep technical details in the written audit. Use short human descriptions of finished work and a readable `display_name` in the matching run manifest when the original task title is overly technical. Preserve missing shares visually; do not normalize known shares to fill the whole picture. Keep agent identity distinct from provider identity. All graph lines mean participation in that project, not communication or dependence between agents.
