# Public contracts

## Shared rules

Every invalid input described below raises `ValueError`; the message is unspecified.
Do not mutate any input, including invalid inputs or ignored extra fields. Required
fields must exist; unknown fields are ignored. Lists must be lists and records must
be dictionaries. An integer means `type(value) is int`, so booleans are invalid.
Ordering uses Python's ascending Unicode string order, never locale rules.

A normalized ID is a nonblank string after `strip().casefold()`. Aggregation and
allocation normalize IDs themselves. The report accepts only canonical IDs:
strings already equal to their nonblank `strip().casefold()` value. Internal
whitespace, Unicode, and backslashes are legal in IDs. No function reads external
or mutable global state, performs I/O, or changes behavior across calls. Same-module
helpers and literal constants are allowed.

## aggregate_attempts(events)

`events` is a list. Each event requires `id`, `revision`, `provider`, `status`, and
`tokens`. IDs and providers normalize as above; `revision` is an integer >= 0;
`status` is exactly `ok`, `error`, or `cancelled`; `tokens` is `None` or an integer
>= 0. Validate every event, including cancelled and superseded revisions.

An ID identifies one logical attempt. Keep its highest revision, independent of
input order. Repeated records for the same normalized ID and revision are allowed
only when their normalized provider, status, and tokens agree; conflicting
duplicates raise `ValueError` even if a newer revision exists. Extra fields do
not participate in equality. A newer revision may change provider or status.

Ignore attempts whose winning revision is cancelled. Group the remaining winners
by normalized provider. Return a list sorted by provider, one record per nonempty
group with exactly these keys: `provider`, `attempts`, `failures`, `tokens`, and
`unknown_attempts`. `attempts` counts winners; `failures` counts `error` winners;
`tokens` sums measured winners and is `None` if every winner has unknown tokens;
`unknown_attempts` counts winners with `tokens is None`. Zero is a measurement.
An empty or entirely cancelled ledger returns `[]`.

## allocate_slots(requests, slots)

`requests` is a list of records requiring `id`, `weight`, and `limit`. Normalize
IDs and reject duplicates after normalization. `weight` is an integer > 0;
`limit` is an integer >= 0. `slots` is an integer >= 0. Validate everything even
when slots is zero. Include zero-limit requests in the output.

Allocate using capped weighted largest remainders, with exact integer arithmetic:

1. Start with all allocations zero, remaining capacity R = slots, and active
   requests having positive limits.
2. While R > 0 and active requests remain, let W be their total weight. If any
   active request has `limit * W <= R * weight`, give every such request its full
   limit simultaneously, remove those requests, subtract their limits from R,
   and repeat. Limits here are original limits: no active request has yet received
   any slots.
3. When no request meets that cap condition, assign every active request
   `R * weight // W` slots. Give the leftover slots one apiece in descending order
   of `R * weight % W`, breaking ties by ascending normalized ID. Then R is zero.

Return exactly `{"allocations": [{"id": canonical_id, "slots": count}, ...],
"remaining": R}`. Rows are sorted by ID and contain every request. Unused capacity
is possible only when every request has reached its limit. Input order never
breaks a tie. Large integers must not lose precision through floating point.

## render_report(usage, plan)

`usage` is a list of aggregate-shaped records requiring canonical `provider`,
`attempts`, `failures`, `tokens`, `unknown_attempts`. Providers must be unique.
`attempts` is an integer > 0; `failures` and `unknown_attempts` are integers from
0 through attempts. Tokens are `None` or an integer >= 0, and must be `None`
exactly when `unknown_attempts == attempts`.

`plan` is a dictionary requiring `allocations` (a list) and `remaining` (integer
>= 0). Each allocation requires canonical `id` and integer `slots` >= 0. IDs
must be unique. Validate both inputs completely. Extra fields are ignored.

Return a string ending in a newline with this header, actual tab separators:
`provider\tattempts\tfailures\ttokens\tslots`.
Follow with one row per provider in the sorted union of usage providers and
allocation IDs. Missing usage contributes 0 attempts and failures; missing
allocation contributes 0 slots. Token display is `-` for no attempts, `?` when
all attempts are unmeasured, decimal known sum followed by `+` when some attempts
are unmeasured, and plain decimal otherwise. Thus known zero can display `0` or
`0+` and is never `?`.

Escape a provider's backslash, tab, CR, and LF as the literal sequences `\\`,
`\t`, `\r`, `\n` respectively (backslashes first). Sort before escaping. Rows
have no other padding. Append a `TOTAL` row containing summed attempts, failures,
known tokens with the same measurement rules over all attempts, and assigned
slots. Finish with the two-cell line `UNASSIGNED\t<plan remaining>`. Even empty
inputs produce header, `TOTAL\t0\t0\t-\t0`, and `UNASSIGNED\t0`.
