# Next implementation work

Completed in the thirteen-agent panel: the searchable task inbox, duplicate
assignment protection and structured brief checks. Ordered handoffs at confirmed
quota limits were added at the user's request. Vote totals, contribution shares
and 206 passing checks are recorded in the [panel report](../.orchestration/democratic-panel-20260907T233109Z-6dbc6824/panel-results.html).

1. **Enforced Antigravity text-worker permissions.** Keep the supported `agy` route, configure a scoped policy without changing normal interactive permissions, and prove attempted file, shell, web, MCP and delegation access is denied. Re-enable automatic supplied-text dispatch only after those checks pass.
2. **Task recovery controls.** Add a task view with cancel/reconcile actions, provider job IDs and explicit crash recovery. A timeout must retain enough evidence to prevent duplicate work and premature quota release.
3. **NotebookLM feature allowance workflow.** Provide a clearly dated manual reading form first; add automatic counters only if a supported interface is established. Separate chat, slides, audio and other limited features.
4. **Calibrated task sizing.** Compare estimated reservations to observed usage, including other account activity; split large projects into resumable checkpoints. Do not present percentage estimates as token guarantees.
5. **Packaging and upgrade checks.** Add an installer that detects Windows/Python/provider versions, configures paths on a new machine and revalidates terminal quota readers after updates. Account login remains per recipient. Current vendor dependencies are pinned; a clean-machine install is not yet tested.
6. **Task quality evaluation.** Build small, role-specific acceptance fixtures: code-review defects with known answers, structured extraction schemas, presentation factuality and audio playback checks. Keep model identity and demonstrated capabilities distinct from brand reputation.

The quota dashboard and read-only task inbox exist. Future recovery controls
should preserve the visible distinction between recorded state and verified
provider completion.
