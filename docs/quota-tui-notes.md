# Grok quota adapter

`python quota_tui.py grok` launches the official CLI in a hidden Windows ConPTY,
runs the built-in `/usage` command, then closes only that launched process.
It never sends a model prompt, extracts tokens, or calls a private billing API.
The account's privacy and billing settings are not changed. Use `--output FILE`
to save normalized JSON; no raw terminal transcript is saved.

The tested version is Grok Build 1.0.13. An unknown upgraded CLI version fails
closed until its output and freshness semantics are checked. Isolated dependencies
are in `.quota-deps`: pywinpty 3.0.5, pyte 0.8.2, wcwidth 0.8.3. To restore them:

```powershell
python -m pip install --target .quota-deps --index-url https://pypi.org/simple pywinpty==3.0.5 pyte==0.8.2 wcwidth==0.8.3
```

## Correctness and freshness

The CLI's displayed weekly percentage is **used**, and its minimal summary
floors this value to an integer. The adapter uses `max(0, 99 - displayed_used)`
as a conservative lower bound on percentage remaining. For example, displayed
0% used becomes at least 99% remaining; 98% used becomes at least 1% remaining.
The returned `max_age_seconds` is 600; the coordinator must reject stale readings
and subtract reservations for other jobs sharing this account's pool.

The reset display omits year and time zone. It is preserved as `reset_display`,
with `reset_at: null`; a reset must be refreshed rather than assumed.

The fullscreen modal is deliberately rejected. It can paint a cached balance
before a fresh request finishes, even if that request later fails. In supported
`--minimal` mode, `/usage` requests a non-silent billing fetch, and the usage
summary is appended after the successful billing result. Each adapter call starts
a fresh process and requires the explicit no-model-calls session marker plus the
complete fetched weekly/reset summary. Nothing from a prior invocation is reused.

Official source inspected on 2026-09-07:

- [Slash-command dispatch](https://github.com/xai-org/grok-build/blob/main/crates/codegen/xai-grok-pager/src/slash/commands/usage.rs)
- [Minimal-mode usage fetch](https://github.com/xai-org/grok-build/blob/main/crates/codegen/xai-grok-pager/src/app/dispatch/status.rs)
- [Successful result emits summary](https://github.com/xai-org/grok-build/blob/main/crates/codegen/xai-grok-pager/src/app/dispatch/billing.rs)
- [Used percentage and floor rounding](https://github.com/xai-org/grok-build/blob/main/crates/codegen/xai-grok-pager/src/views/credit_bar.rs)
- [Fullscreen cached-balance rendering](https://github.com/xai-org/grok-build/blob/main/crates/codegen/xai-grok-pager/src/views/usage_modal.rs)

Validation: `python -m unittest test_quota_tui -v` passes three test methods,
including boundary usage values, partial summaries, unknown versions, duplicates,
and cache/error/loading displays. `grok-quota-validation.json` contains the live
normalized observation. Live reading completed in about 2.5 seconds with no model
calls. A ten-minute reading is an observation, not a guarantee that other Grok
products or jobs cannot consume the shared pool after it was observed.

## Claude status

Claude 2.1.263 was live-verified after the user completed interactive onboarding.
The official screen-reader /usage panel shows the current five-hour session and
account-wide weekly limit. The collector requires a fresh Refreshing-to-loaded
transition and explicit zero model tokens in the new session. It excludes local
session analytics, promotional percentages, and usage-credit balances.

The monitor-added CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC=1 flag caused the usage
fetch to fail in this installed version. Removing it from the reader child restored
normal quota fetching. No global privacy setting was changed. The child passes
remoteControlAtStartup=false through its own --settings argument to avoid creating
remote sessions for recurring quota checks. Auto-update remains disabled for this
reader; unknown upgraded versions stay held until revalidated.

claude-quota-validation.json records the successful read, including conservative
remaining bounds, reset displays, and zero-inference evidence. Both Grok and Claude
use --provider <name>; unavailable readings exit 2.
