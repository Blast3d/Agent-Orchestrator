# Verified project lessons

## Keep ancillary Claude usage tips out of quota freshness checks

When Claude Code `/usage` shows complete account bars but the collector times
out, inspect which rendered line triggered the parser's stale-data check.
Claude Code 2.1.288 added a local analytics tip saying cached sessions cost
more; scanning the whole screen for `cached` rejected that tip as if the account
bars were stale. `app/quota_tui.py` now ignores only the exact tip after the
analytics heading. Other cached, loading and error text still fails closed.
On 2026-11-07, 45 focused checks passed and a fresh zero-token `/usage` refresh
returned both account windows. Revalidate each new CLI version against a live
loading-to-loaded transition before adding it to the verified version set.

## Keep advisory quota preference separate from admission

When an advisory worker stays held below 20%, trace both admission and alternate
routing. `evaluate_advisory` formerly converted the preference into a blocking
reason, while Codex lacked automatic alternates. Positive allowance now remains
ready with a warning; a frozen plan defers before reservation only while another
authorized worker remains. Its positive terminal worker can continue. Preserve
known zero before its exact reset, exhausted pending reservations, provider
cooldowns and uncertain assignment reuse; a collector timeout cannot release a
confirmed exhausted period. Strict admission and the 5% lead boundary are separate.
On 2026-10-03, 373 focused checks passed; a real Codex task at 15% continued to
Grok, and repeating it reused the same job without inference. The live viewer
reported Codex ready at 14%. Evidence: `.orchestration/quota-advisory-continuity-20261004T031734Z-935f2ed8/`.
A reviewed rejected preflight still needs to reuse its exact assignment and reach
the accepted saved alternate; share the no-execution proof between task review
and assignment validation. Next time, test admission, frozen routing and repeat identity together, and update
the effective runtime policy as well as registry and installed guidance.

## Drain a modestly oversized POST before returning 413 on Windows

When a loopback client sends just over the dashboard's 64 KiB request limit,
closing the connection with unread body bytes can make Windows report
`WinError 10053` instead of delivering the 413 response. `app/brain_dashboard.py`
now drains at most 8 KiB beyond the accepted limit, with a short deadline,
before replying; it still rejects the body without parsing it. On 2026-09-28,
the exact oversized Jev POST test passed three isolated runs and the 1,678-test
suite passed after the change. Next time, check what the client receives from
the HTTP boundary, not only the server's intended status.

## Count quota reservations in the same window as the held percentage

A worker can retain unresolved reservations from earlier quota periods without
those estimates reducing current availability. A global unfinished-task count
would make a held-bot sentence attribute today's reserved percentage to old work.
`Guard.status()` now counts unfinished reservations in the limiting measured
window and applies the same `window_period_start` boundary used by admission.
On 2026-09-28, `tests/test_usage_guard.py` checked a shared pool and an old
reservation, while `tests/test_usage_readiness.py` checked the wording. Next
time, derive human-facing counts from the same pool and period as the displayed
percentage.

## A Brain seed import must not look like a lost worker assignment

When the first hosted call in a freshly seeded project reports a missing assignment
index, inspect the canonical task types before attempting a retry. Reviewed native
memory imports carry `assignment_project_id` for scope but have no worker assignment
identity. `_existing_assignment` previously treated that project field alone as
evidence of a deleted index and held an otherwise new call.

On 2026-09-26, `app/assignment_receipts.py` was changed to exclude only a matching,
accepted canonical `native-review`/`memory-curation` import with zero provider calls,
no reservation and no assignment identity. Missing, inconsistent or real provider
records still hold dispatch. The 29 focused `tests/test_assignment_receipts.py`
checks passed, including valid seed imports and preservation of the deleted-real-index
guard. The benchmark's temporary-root tests also exercise real capture followed by
the first hosted assignment without inference. Those checks alone are not a live
provider result; retain the scored suite's receipts for that evidence.

## Keep Grok quota-only sessions outside the checkout

When `python orchestrator.py refresh --provider grok` times out, inspect the
official CLI's trust prompt before treating the cached allowance as current.
Grok resolves trust to the enclosing Git root, even from the nested
`runtime/workspaces/grok` directory. `app/quota_tui.py` now creates a disposable
empty temp workspace by default, while preserving explicit workspace overrides;
the quota session still disables tools, web search, and subagents and requests
only `/usage`. On 2026-09-26, `python orchestrator.py refresh --provider grok`
then succeeded in 2.8 seconds. The official panel showed zero model calls and
the normalized reader accepted the fresh result. Next time, keep quota reads in
an empty directory outside the repository; do not approve a trust prompt for
the source checkout merely to refresh allowance data.

### Jev returns valid decisions with extended billing details

On November 17, 2026, a synthetic OpenRouter decision included `usage.is_byok` and `usage.cost_details`. The previous allowlist rejected the entire answer. The connector now validates the billing fields and still checks the pinned model, provider, distribution and token counts. For a recurrence, inspect a synthetic response shape and replay it offline. The regression is in `tests/test_jev_openrouter.py`.
