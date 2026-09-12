# Orchestrator application audit — September 7, 2026

## Later panel implementation update

The thirteen-seat panel completed 26 proposals, common discussion and 13 valid
ballots. Its top three choices are now implemented: a task inbox, remembered
assignment identities and a brief checker. The user's subsequent quota-handoff
request is implemented with frozen plans, linked tasks and suitability checks.
The current suite passes **206 checks**, and installation/global discovery checks
pass. Two handoff defects found in independent review were fixed and rechecked.

See [the visual project report](../.orchestration/democratic-panel-20260907T233109Z-6dbc6824/panel-results.html)
and its linked review evidence. Grok Build, Claude, local Qwen, supervised
Antigravity, Grok Bot Atlas and native Codex agents actually participated. The
Claude coding attempt timed out without a saved answer; native Codex completed
that module and the unresolved Claude reservation remains held. General automatic
Antigravity and unknown NotebookLM feature allowances remain held. Grok Bot needs
a fresh manual allowance reading. The following sections retain the earlier
audit's historical results, including its original test counts.

**Result:** the prototype is now organized under `Documents/Agent-Orchestrator`, with a single maintained implementation, global skill forwarding, durable task records and explicit review decisions. Concrete quota and dispatcher defects were corrected. This remains a local Windows application without a complete installer, task scheduler or task-management GUI.

Codex led integration. Two native Codex workers independently audited quota behavior and implemented dispatcher reliability. An actual Anthropic Claude worker reviewed supplied source code through the authenticated, quota-guarded CLI. This was not a native Codex subagent relabeled as Claude.

## Verified findings and disposition

| Priority | Finding | Resolution and evidence |
|---|---|---|
| High | A late complete quota snapshot could restore a window removed by a newer snapshot and reopen admission. | Fixed using provider snapshot ordering and omission protection. Offline reproduction and regression checks in `quota-audit.md`. |
| High | Duplicate pool IDs could overwrite a low balance with a larger balance. | Duplicate identities are rejected before state changes. |
| High | Negative or non-finite editable estimates could bypass the reserve calculation. | Policy shape, finite percentages, estimates and pool identities are validated before admission. |
| High | A requested output write could fail after inference and prevent reservation cleanup. | The output is claimed before quota/inference; canonical results and metadata are saved separately. Export failures and ordinary cleanup failures are recorded. |
| High | A nonempty answer was marked completed without checking its correctness. | Successful execution now becomes `awaiting_review`; accepted/rejected decisions require a separate finalized review record. Review cannot race unfinished cleanup. |
| High | Antigravity plan mode did not enforce the promised no-tools boundary. | Automatic supplied-text Gemini dispatch is held before quota reservation or inference. Manual Antigravity and its quota reader remain available. A verified scoped permissions adapter is still required. |
| Medium | Timeouts could lose execution evidence or imply a safe release while remote work was uncertain. | Native worker processes are tracked and locally stopped/reaped where possible; uncertain timeout/interruption records keep their reservations pending reconciliation. Abrupt parent crashes still need better recovery controls. |
| Medium | Dashboard expiry used 600 seconds regardless of the reading's configured lifetime. | It now uses actual expiry and reset boundaries. The status label says QUOTA READY to distinguish allowance from capability. |
| Medium | Source, installation copies, setup helpers and live state had unclear ownership. | Maintained code is under `app/`; state under `runtime/`; tasks under `runs/tasks/`; old commands redirect. Global skill scripts forward to the maintained code. Backup and historical setup evidence are under `archive/`. |
| Medium | Monitor restart could wait for several unnecessary provider reads and announce startup before the child initialized. | Stop is checked between provider reads; launch is serialized, startup acknowledged and a delayed stop reported explicitly. Existing Windows sign-in entry now targets the new app. |
| Medium | Moving Claude's quota workspace triggered its folder-trust check. | Carried trust to only the new isolated workspace, with no added tool allowances; a fresh quota read then passed. The reader still refuses uncompleted trust/auth dialogs. |

Google's official documentation confirms that plan mode adds planning instructions, while headless mode still permits workspace file operations. No-tools isolation cannot be inferred from `--mode plan`. Sources checked for this audit: [execution modes](https://antigravity.google/docs/cli/modes/), [headless permissions](https://antigravity.google/docs/cli/headless/), and [permission rules](https://www.antigravity.google/docs/cli/permissions/).

## Claude contribution and independent review

The initial broad audit timed out after the old dispatcher's 180-second limit and returned no usable report. That failure is retained. A smaller, bounded native Claude review succeeded, reporting `claude-sonnet-5` and auxiliary `claude-haiku-4-5-20251001` usage metadata. Reported list-price accounting is not proof of an extra subscription charge.

Claude identified the output-write cleanup failure, response-versus-acceptance confusion and Antigravity tool-boundary gap. Codex verified those findings against the source. Claude also alleged a local Ollama paid-cloud fallback because API keys remained in the parent environment. That claim was rejected: the inspected route uses a pinned local model through loopback Ollama, configured with cloud access disabled, and contains no Anthropic/Google fallback call. No global environment mutation was made on that suggestion.

Two live tasks then exercised the new dispatcher and review path. The first answer got three example decisions right but used an incorrect interpretation of the quota floor; Codex rejected it and clarified the units. The second got all four numeric decisions and the fixed floor right, but its proposed replacement still failed for an unknown value such as `[None, 80]`; Codex rejected it as a complete implementation. Both results are preserved with the review reason. No generated replacement was executed or merged into the guard.

This is an observed limit of the worker, and evidence that transport success must remain distinct from accepted work. The corrected application logic was implemented and validated locally against the regression suite. The application review command records a reviewer decision; it does not itself prove semantic correctness.

## Validation performed

- **70 offline behavior checks passed:** 28 quota guard, 12 Codex quota parser/protocol, 6 Grok/Claude quota parser, and 24 dispatcher/task-review checks.
- Four quota reproductions now demonstrate the corrected behavior: old snapshot held, duplicate IDs rejected, actual dashboard expiry honored and malformed estimates rejected.
- The offline application doctor passed all installation, dependency, policy, registry and global-skill consistency checks.
- The official skill validator accepted the maintained global skill.
- Live quota refresh succeeded for Codex, Antigravity, Grok and Claude from the new application layout. These account reads make no model calls.
- The global skill forwarding path returned live application state, and the startup helper acknowledged the new monitor process.
- Real Claude review jobs used the protected dispatcher. Results, model metadata, reservations and explicit rejection decisions were saved; ordinary completion left no active task reservations.

The final validation record is `docs/validation.json`. Provider balances are time-dependent; use the dashboard for fresh readings. Windows toast delivery was accepted in earlier setup, but visible notification delivery was not reconfirmed in this audit. No new local-model load or live dictation latency test was performed.

## Remaining work

1. Enforce and live-test scoped Antigravity tool denial before re-enabling automatic text jobs.
2. Add current NotebookLM feature allowances; those jobs remain held while usage is unknown.
3. Add crash/session reconciliation and cancellation controls, including provider-side job completion evidence. An abrupt process/PC failure may leave a reservation active until reviewed; there is no silent timeout release.
4. Calibrate percentage reservations and split long jobs into checkpoints. The guard is admission control, not a guaranteed provider-side spend limit; manual use in other apps can consume quota.
5. Add clean-machine installation, provider-version upgrade tests and configuration-driven adapter onboarding. The current registry describes tested routes; adding a row alone does not implement a new worker.
6. Expand role-specific acceptance fixtures. Claude's observed mistakes and Grok's prior creative constraint miss show why brand-based routing alone is insufficient.

See [the roadmap](ROADMAP.md) for implementation order and [the project guide](../README.md) for file locations and commands.
