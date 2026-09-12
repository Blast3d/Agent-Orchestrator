# Codex quota adapter

Run `python quota_codex.py --timeout 30`. It emits one JSON object on stdout.
Exit 0 means a fresh official response was parsed. Exit 1 means unknown allowance;
the dispatch guard must not assign new work based on that failed observation.

The installed Codex CLI 0.153.0 generated the local schemas in `codex-protocol`.
`v2/GetAccountRateLimitsResponse.json` documents the supported account response;
`ClientRequest.json` defines `account/rateLimits/read` with null parameters.
The adapter starts a separate stdio app-server in a new empty temporary directory,
initializes it, reads rate limits, and terminates it. It never starts a thread,
requests inference, signs in, redeems reset credits, or requests account changes.
The official CLI manages its existing authentication; the adapter does not read
credential files. Raw server stdout and stderr are never saved or displayed.

Process-local config overrides clear MCP servers and disable apps, plugins,
remote plugins, remote control, and analytics. A separate `config/read` validation
confirmed these effective settings without emitting any configuration values
other than counts and Boolean flags. These options are defined in the
[official Codex config schema](https://developers.openai.com/codex/config-schema.json).
The user's persistent configuration is unchanged.

Every meter from `rateLimitsByLimitId` is retained; the legacy `rateLimits` field
is only used when the multi-meter map is absent or empty. Remaining allowance is
100 minus `usedPercent`, with exhausted/over-limit usage producing zero. Individual
spend-control percentages are additional windows; an explicit backend denial adds
a zero-allowance block. Credits never supplement included allowance. Unknown
bucket/window schemas, missing percentages, malformed data, and connection failures
fail closed. Newly introduced fields need review after CLI/protocol changes.

Additional meter IDs are hashed in display output so no opaque backend identity
is echoed. Until their exact model applicability is established, the guard should
conservatively require sufficient allowance in every returned window. Reset times
are official Unix timestamps converted to UTC, not inferred schedules. Observation
freshness is ten minutes; a predicted reset never automatically restores allowance.

`codex-quota-validation.json` records a live read and isolation verification.
`python -m unittest test_quota_codex.py` checks low and exhausted allowances,
multiple meters, explicit denials, unknown schema, malformed percentages and
timestamps, redacted errors, unsupported server requests, and deadlines.
Network access from the managed sandbox failed closed; the authorized external
network read succeeded. This adapter monitors allowance; it cannot reserve capacity
against another client or guarantee a long task will finish.
