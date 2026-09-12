# Usage monitoring and dispatch protection

Official-source research checked 2026-09-07. No credentials were extracted, undocumented endpoints called, or account requests made by this research agent. The parent agent separately verified the Antigravity command described below. Only this research note was edited for this task.

## Supported monitoring surfaces

| Provider | Supported source | Coverage and limitation |
|---|---|---|
| Antigravity CLI | `agy -p /usage --output-format json`; standalone `/quota` and `/credits` reports also supported | Fresh backend quota report without a model turn. Use returned group/bucket identifiers and windows. |
| Claude Code subscription | Interactive `/usage`; documented status-line JSON; SDK rate-limit events | Status-line fields appear after a response and may be absent. No independent documented consumer quota-polling endpoint established. |
| Grok Build / SuperGrok | Interactive `/usage`; grok.com Settings > Usage | No documented no-inference headless quota command established. Per-job token/cost output is not allowance remaining. |
| NotebookLM / Gemini Notebook | Product UI and published feature-specific limits | No official consumer quota-status API or CLI established. Keep feature counters and reset certainty separate. |

## Antigravity: suitable for automatic preflight

Official engine changelog **1.1.11, August 7, 2026** introduced standalone print-mode `/usage`, `/quota`, and `/credits` reports as TSV or structured JSON. These reports do not start an agent turn, consume model quota, or leave a conversation. Version 1.1.12 fixed stale status-line quota reporting. Installed version reported by the parent is 1.1.27. [Engine changelog](https://antigravity.google/changelog?tab=engine).

```powershell
agy -p /usage --output-format json
agy -p /credits --output-format json
```

**Parent's live verification:** `/usage` returned `num_turns: 0` and `command.data.groups[].buckets[]`, with bucket fields `id`, `window`, `remaining_fraction`, and `reset_time`. This schema was observed on this installation; the public command page does not define that exact JSON schema. Validate types, preserve unknown fields, and reject malformed/missing fractions instead of defaulting to full allowance. Interpret `remaining_fraction` as a fraction, not percentage used. [Quota command](https://antigravity.google/docs/cli/commands/usage).

Run this as its own command, not as a user message inside a streaming agent conversation. Normal agent-result `usage.input_tokens`, `output_tokens`, `thinking_tokens`, `cache_read_tokens`, and `total_tokens` measure consumption, not account headroom. Failed headless runs report nonzero exit and `status`/`error`; preserve the actual error. No stable exhaustive quota-error-code schema was found. The changelog documents honoring server retry delays. [Headless behavior](https://antigravity.google/docs/cli/headless).

Gemini Flash and Pro share quota, weighted by model pricing; non-Gemini models have separate fixed limits. Use backend group/bucket membership rather than inventing independent budgets for each model. Free plans refresh weekly; Pro has five-hour refreshes subject to weekly limits; Ultra also has five-hour and weekly limits. Prefer returned reset timestamps over calculated schedules. [Shared pools](https://antigravity.google/blog/changes-to-antigravity-plans), [plans](https://antigravity.google/docs/plans).

AI credits are a distinct overage resource. `useG1Credits: false` restricts automatic credit fallback; do not count a credit balance as free model quota or enable overages to rescue a job. [Credit controls](https://antigravity.google/docs/cli/credits).

## Claude Code: preserve account-wide and multiple-window limits

The official status-line input includes `rate_limits.five_hour.used_percentage`, `rate_limits.seven_day.used_percentage`, and each window's `resets_at` in Unix epoch seconds. Percentages are **used**, so remaining is `100 - used_percentage`. Each window can be missing independently. Subscriber fields are documented for Pro/Max after the first API response. Feature-detect the installed version; do not assume an absent window is unused. The status-line command itself consumes no API tokens. A refresh timer reruns the local display script; it is not a documented guarantee of an independently refreshed account quota. [Status-line JSON](https://code.claude.com/docs/en/statusline).

SDK `rate_limit_event` carries `rate_limit_info.status` values `allowed`, `allowed_warning`, or `rejected`, with optional `resetsAt` and `utilization`. Preserve explicit rejected/warning states. The reference excerpt does not specify the utilization unit or bucket identity sufficiently for safely converting it to a percentage; prefer explicit status-line percentages and verify the installed SDK contract. Events arise during a session, so they cannot guarantee initial preflight freshness. [SDK reference](https://code.claude.com/docs/en/agent-sdk/typescript).

Interactive `/usage` shows limits/reset times. Documented session/weekly/Opus-limit messages are quota exhaustion; temporary server throttling is distinct. API-key HTTP 429 is not proof that the subscription's weekly allowance is exhausted. Low prepaid credit is another distinct condition. [Commands](https://code.claude.com/docs/en/commands), [error reference](https://code.claude.com/docs/en/errors).

**Current billing correction:** the proposed separate monthly Agent SDK credit was paused on June 15. The canonical support banner says SDK and `claude -p` still use subscription limits, and the announced monthly credit is unavailable. Older indexed snippets still incorrectly describe the proposed change. Subscription usage is shared across Claude and Claude Code. Verify authentication mode because an API-key route has different billing and limits. [Paused change](https://support.claude.com/en/articles/15036540-use-the-claude-agent-sdk-with-your-claude-plan), [shared subscription usage](https://support.claude.com/en/articles/11145838-use-claude-code-with-your-pro-or-max-plan).

## Grok: no verified headless remaining-quota report

Official Grok Build documents `/usage` as an interactive TUI command. The public CLI reference and official repository headless guide do not establish `grok -p /usage` as a local, quota-free command. Do not send it as a prompt and trust a model-generated allowance report. [TUI commands](https://docs.x.ai/build/modes-and-commands), [CLI reference](https://docs.x.ai/build/cli/reference).

Grok's documented status-line JSON contains context usage and session cost, but no subscription-quota percentage or reset field. Its field list is explicitly exhaustive. [Status-line schema](https://docs.x.ai/build/features/status-line).

Headless output can supply `usage`, `modelUsage`, `total_cost_usd`, and completeness flags. These record job consumption. OAuth cost is often omitted; missing means unknown rather than free. `usage_is_incomplete` or `cost_is_partial` means the total cannot be treated as complete. Errors use a nonzero exit and an error object with `type`/`message`; no official exhaustive subscription-limit error enumeration was found. [Official headless guide](https://github.com/xai-org/grok-build/blob/main/crates/codegen/xai-grok-pager/docs/user-guide/14-headless-mode.md).

SuperGrok has a shared weekly product pool. Settings > Usage shows percentage used, product breakdown, reset schedule, and extra-credit balance. Free Chat/Voice limits have separate schedules. The parent's signed-in session now reports SuperGrok; this note does not infer a purchase or permanent entitlement. Use the actual account reading. Keep optional top-ups separate from included quota. [Usage FAQ](https://docs.x.ai/grok/faq).

For a separately authorized **xAI API team**, the official Management API has prepaid balance and spending-limit resources; these are not a consumer SuperGrok quota workaround. `GET /v1/billing/teams/{team_id}/prepaid/balance` reports `total.val` using USD-cent representation, and spending limits have separate fields. The sample uses signed balance values; implement the documented accounting semantics before interpreting them. [Billing API](https://docs.x.ai/developers/rest-api-reference/management/billing). Direct inference API HTTP 429 indicates model rate limiting; distinguish RPS/TPM throttle from subscription exhaustion. [API rate limits](https://docs.x.ai/developers/rate-limits).

## NotebookLM: track feature budgets, not one model percentage

The official NotebookLM help URL currently redirects to Gemini Notebook help. Limits are feature-specific: chats, audio, video, reports, mind maps, deep research, and others. The free table lists 50 chats/day and 3 audio overviews/day; slide-deck/infographic limits are qualitative rather than numeric. Daily quotas reset after 24 hours and monthly quotas after 30 days; the documentation does not establish a calendar-midnight anchor. Automatically generated initial artifacts do not count toward limits. Shared notebooks do not merge collaborators' limits. [Current official limits](https://support.google.com/notebooklm/answer/16213268?hl=en).

No official consumer quota-status endpoint or machine-readable remaining-count schema was established. A third-party NotebookLM MCP wrapper is not itself an official quota API. Record UI-observed remaining counts/reset messages with timestamps, and distinguish locally counted work from authoritative account totals. External app/browser usage can make a local ledger incomplete.

## Implications for the guard (implementation recommendations)

- Keep `known`, `stale`, `unknown`, `blocked`, and `throttled` distinct. Unknown is not 100% available.
- Identify each budget by provider, account scope, shared pool, and window. A job must fit every applicable window; reservations must be shared across models using the same pool.
- Require explicitly labeled remaining versus used percentages. Context-window fullness, request token counts, monetary credits, and subscription allowance are different metrics.
- At or near 2% remaining, reject new substantial jobs; warn earlier. No provider promises that a percentage guarantees completion. Compare conservative job estimates plus reserved work and a finishing margin; split large tasks into checkpoints.
- Refresh before dispatch when a supported no-inference route exists. Otherwise require a sufficiently recent UI observation or route to another worker. Do not spend a probe model turn to ask how much quota is left.
- Keep hard rejection until the reported reset or a fresh successful quota observation. Reaching a predicted reset time only makes a stale reading due for refresh; it does not prove full allowance has returned.
- Honor actual retry information for temporary throttles, cap retries, and persist partial work. Never silently enable overages or rotate accounts to evade limits.
