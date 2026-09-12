# Quota admission audit

Audit date: 2026-09-07. Reviewer: Codex native audit worker, reporting to the lead orchestrator. Claude separately reviewed task dispatch. This review covered the canonical application's quota guard, Codex adapter, Grok/Claude TUI adapter, monitor cancellation and focused tests.

The original 34 focused tests passed, but four additional offline probes reproduced defects. All four are now fixed. The quota test suite now passes **46 tests**: 28 guard tests, 12 Codex adapter tests and 6 TUI parser tests. No real model requests, credential reads or live quota-state changes were performed by this reviewer.

## Findings and resolution

| Priority | Finding | Evidence before the fix | Resolution |
| --- | --- | --- | --- |
| High | An older concurrent complete snapshot could reopen admission after a newer snapshot omitted a required pool. | A newer Claude snapshot omitted the weekly window and correctly held work. An older full response then reinserted the omitted 80% window and allowed work. Per-window timestamp comparison could not protect a window already deleted. | `Guard.observe` records a provider snapshot timestamp, ignores older/equal complete snapshots, and prevents older individual/manual readings from resurrecting omitted pools. A newer complete reading can restore the pool. Newer manual observations are preserved when an older complete response arrives. |
| Medium | Duplicate window IDs were silently resolved by the last value. | One snapshot contained the same Gemini pool at 2% and 100%. The second value won and a large task was admitted. | Snapshot validation rejects duplicate identities before any state mutation. The last valid low reading remains effective. This also protects Antigravity, whose JSON parser previously had no duplicate check. |
| Medium | Invalid editable policy numbers could bypass the safety buffer. | A large-task estimate of -15 admitted work with only 2% remaining and created a negative reservation. NaN values also evade ordinary numeric comparisons. | Policy validation runs on load and evaluation. Estimates must be finite positive numbers within 100%; thresholds must be finite percentages with warning at least the floor; worker/pool mappings must be well formed. Invalid policy data creates no reservation. |
| Medium | The dashboard freshness timer disagreed with admission. | A manual reading valid for 30 seconds was displayed as current for 600 seconds. Earlier reset boundaries and hidden admission flags were not included in the timer. | Every row expires at its earliest actual per-window freshness deadline or reset boundary, including hidden provider-block flags. The column now says **Quota status** and a healthy cloud row says **QUOTA READY**. |
| Medium | Monitor stop requests could wait through every sequential provider read. | The original restart helper waited 75 seconds, while the monitor could enter four sequential reads with outer timeouts totaling 235 seconds before checking its stop file. | `refresh` now accepts an optional cancellation callback and stops before the next provider. The monitor supplies its stop-file check and skips dashboard/alerts after cancellation. Direct user/dispatcher refreshes are independent of the monitor stop file. Startup/restart reporting belongs to the lead's separate lifecycle changes. |

Primary changed files: [usage_guard.py](../app/usage_guard.py), [test_usage_guard.py](../tests/test_usage_guard.py). Runtime paths now come from the canonical application's [paths.py](../app/paths.py); the old Local-AI-Setup and global-skill runtime paths are no longer hardcoded in the guard or these tests.

## Reproducible verification

From the application folder:

```powershell
python docs\reproduce_quota_audit.py
python -m unittest discover -s tests -p test_usage_guard.py -v
```

[The reproduction script](reproduce_quota_audit.py) creates temporary state directories and imports only the canonical app. It performs no provider launches or network calls. Its post-fix results are:

```json
{
  "older_complete_snapshot": {
    "held_after_newer_partial_snapshot": true,
    "incorrectly_reopened_by_older_snapshot": false
  },
  "duplicate_window_ids": {"ambiguous_duplicate_rejected": true},
  "dashboard_expiry": {
    "configured_validity_seconds": 30,
    "dashboard_validity_seconds": 30
  },
  "invalid_policy": {"invalid_policy_rejected": true}
}
```

The added regression tests cover reordered complete responses, omitted-pool tombstones, manual-reading ordering, duplicate rejection without state mutation, malformed persisted and in-memory policy, exact dashboard deadlines, monitor cancellation, and direct refresh despite a stale stop file. Existing tests still cover shared-pool parallel reservations, low/unknown/stale quotas, completed reservations held until a later snapshot, cooldowns, new pool inheritance, Codex protocol/schema handling and TUI parsing.

## What remains a product limitation

- **Quota ready is not task capability.** A successful quota check proves only that the currently known allowance permits the estimated task. Claude's actual task result and the lead's acceptance check must establish task correctness.
- **The percentages are planning estimates.** A 10% reserve and estimated task size cannot guarantee completion against provider-side accounting lag, usage outside this dispatcher, variable task cost or a service outage. There is no fabricated unlimited allowance or automatic paid fallback in the audited guard.
- **Unknown windows remain held.** When a known pool disappears, the guard retains its required membership. Restoring an authoritative complete reading reopens it; a genuine provider/account schema change needs explicit membership review rather than silently forgetting a limit.
- **TUI readers are version sensitive.** The offline fixtures verify parsing and fail-closed behavior. Live account freshness and actual notification delivery must be checked separately after provider CLI updates; this audit made no new live claims.
- **A running provider read is not forcibly interrupted by monitor stop.** Cancellation prevents the next provider read. Shutdown can still wait for the current adapter's configured timeout, at most 65 seconds at the guard subprocess boundary.

No unresolved high-priority defect was found in the bounded Codex or TUI parser review. That is a scope-limited finding, not an assurance about unexamined provider behavior or the rest of the application.
