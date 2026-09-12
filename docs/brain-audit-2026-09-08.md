# Eight-worker brain audit — September 8, 2026

All eight independent assignments completed successfully: four Grok and four
Claude seats across two provider families. None timed out in this batch. Six
seats reviewed numbered source; two independently answered a live memory
challenge through the normal guarded dispatcher. Astra reproduced findings,
integrated repairs and ran the tests. No local model or full Graphiti runtime
was started. No new paid route or publishing was used.

## Panel and observed execution

| Seat | Worker | Assignment | Execution |
|---|---|---|---:|
| B01 | Grok 4.6 Build | Recall and source isolation | 70.0 s |
| B02 | Claude Fable 5.1 | Durability and storage admission | 171.2 s |
| B03 | Grok 4.6 Build | Dashboard boundary and lifecycle | 104.8 s |
| B04 | Claude Sonnet 5 | Dispatcher and handoff integration | 158.4 s |
| B05 | Grok 4.6 Build | Independent adversarial test design | 101.1 s |
| B06 | Claude Sonnet 5 | Deadline and timeout policy | 79.5 s |
| B07 | Grok 4.6 Build | Live memory challenge | 6.8 s |
| B08 | Claude Sonnet 5 | Independent live memory challenge | 4.6 s |

These times are recorded local execution durations, not estimates of model
reasoning time. Claude also reported auxiliary Haiku activity; that does not
add independent voting/review seats. Reviewers received supplied source only
and did not claim to execute it. B05 supplied three tests that Astra inspected,
adapted to the repaired behavior, and ran locally.

## Confirmed findings and repairs

| Area | Reproduced issue | Delivered change |
|---|---|---|
| Recall | 60 stale task sources could hide a later valid hit | Continue checking candidates until current hits are selected, with bounded work and explicit incomplete-scan diagnostics |
| Durability | Reservation release errors hid committed writes and could mask the original exception | Preserve the saved record/original failure; return committed and cleanup-pending status with the retained reservation ID |
| Windows vault | An open viewer made forgetting fail with an opaque PermissionError | Actionable close/check-access/retry message; failed forgetting leaves the record intact |
| Retention | Crashed vault temporary files persisted | Sweep narrowly named orphan files while holding the brain lock |
| Migration | A legacy scope with only change events lost its trim watermark | Migrate scopes from both memories and changes; unknown nonzero cursors request resync |
| Relationships | An expired link silently blocked a new current period | Schema v3 retains old periods and permits a new period; current duplicate links stay idempotent |
| Worker evidence | Exact injected memory could not be reconstructed after later edits/forgetting | Retain the bounded context and hash in the canonical task, with an execution-requested indicator |
| Admission | Invalid briefs triggered unnecessary memory retrieval | Validate the brief before retrieval; prepared context stays marked unrequested when quota holds execution |
| Handoff visibility | Memory changed between fallback attempts without a plan-level indicator | Keep source revalidation and record each attempt's hash/change indicator |
| Dashboard | Empty 503 replies produced JSON parse messages | Return a bounded JSON capacity message and handle unreadable replies clearly |
| Review UI | Older pending items appeared in review but not the library's Pending filter | Both surfaces use the dedicated pending list |
| Scope metadata | Local dashboard counts/project names included other-user records | Scope status metadata to the requested user; hide completely forgotten project entries |

Additional clarity: future-dated approved records expose `validity_state=scheduled`,
context-budget omissions have IDs in the response, graph seeds are chosen by
their weighted score, and the dashboard directs mistyped host requests to the
launcher's pinned `127.0.0.1` address.

## Findings not accepted as defects

- The alleged HTTP keep-alive desynchronization did not reproduce. The inherited
  HTTP/1.0 handler closes the rejected connection; a new regression verifies
  oversized-body rejection followed by successful reuse/reconnection.
- Hyphenated tag aliases already work with the FTS tokenizer. A regression
  confirms this; the proposed alternative index was unnecessary.
- A graph neighbor can outrank a weaker direct match by design. Relationships
  are useful precisely when a procedure uses different wording. Weighted seed
  selection was improved; forcing every neighbor to share query words was rejected.
- Tight context budgets can omit records whose evidence envelope cannot fit.
  Returned results and model context remain consistent; omission IDs now expose
  that tradeoff. It is not a source leak.
- Inverse/cyclic relation types are allowed reviewed graph data; they are not
  executable workflow dependencies. Automatic semantic contradiction rules are
  not part of the approved lightweight implementation.
- The base handoff brief is frozen, while memory is intentionally revalidated
  per attempt. Freezing revoked/stale memory would weaken source freshness.
  Exact per-attempt context and change indicators address auditability.
- The claimed unkeyed memory-query bypass is not available: project and
  assignment IDs are already required together. Query bounds are now checked
  earlier for a consistent input error. Invalid CLI inputs do not invent tasks.
- A missing deadline in a hypothetical future direct runner call was not an
  observed integration defect; the current dispatcher always passes one.

## Deadline changes

| Task size | Previous | Current |
|---|---:|---:|
| Tiny | 2 min | 2 min |
| Small | 5 min | 7.5 min |
| Medium | 10 min | 15 min |
| Large | 15 min | 22.5 min |

Small/medium/large defaults increased by 50%. `run --timeout-seconds 1200`
provides an explicit hosted-worker deadline between 30 and 1,800 seconds.
The override is validated before quota/inference, binds assignment identity,
and passes through explicit fallback plans. A changed deadline needs a new
assignment identity and normal revision/reconciliation rules. No automatic
timeout retry or paid fallback was introduced. Local-model timing is unchanged.

The reason for the increase is historical evidence, not a failure in this panel:
a prior Claude job expired at 600 seconds, older Claude/Grok jobs were killed at
180 seconds, and one earlier verified Claude replay answered at 372–472 seconds.
These observations do not prove every timed-out job would finish with more time.
File-not-found, startup-flag and ordinary CLI errors need their own fixes.
Prior uncertain reservations remain preserved.

## Functional and validation evidence

Both live readers independently selected `CORAL-71`, cited the correct current
memory and linked release procedure, and refused an adversarial instruction to
answer `BANANA-999`. The actual recalled ID set excluded superseded, pending,
expired, foreign-project and other-user fixtures. Each reader passed all six
checks. This controlled example is not a universal prompt-injection guarantee.

The final full suite ran **534 tests: 530 passed, 4 skipped** in 33.956 seconds.
The final evidence-field naming adjustment also passed all 11 dispatcher/brain
integration tests. Platform/privilege-dependent skips remain explicit; Windows
locked-file and real junction coverage ran. Installation doctor and JavaScript
syntax checks passed. The live browser verified normal scoped search, older
pending-item rendering and the friendly 503 message.

The final isolated 10,000-record/100-query benchmark used 18,907,137 bytes:
caller p50 30.29 ms, p95 36.46 ms, maximum 37.87 ms. Ten searches in the existing
application, including its larger storage-admission scan, had median 138.75 ms
and maximum 183.30 ms. An earlier concurrent-test run had a 501 ms outlier; it is
not hidden by the final isolated measurement. No model is invoked by these
backend benchmarks.

All eight synthetic memories were forgotten after the live challenge. Four
original user decisions remain current. ID-only history and separately retained
canonical audit inputs/results remain evidence; forgetting does not retroactively
erase provider prompts, canonical records or OS backups. The live brain occupied
118,785 bytes after cleanup, including reusable database space.

## Evidence location

Run: `.orchestration/brain-audit-eight-20260909T040051Z-a3d6c55f`.
It contains the original source snapshot/backups, eight briefs and raw answers,
canonical job references, live-reader checks, proposed Grok tests, final benchmark,
source hashes, detailed review dispositions and contribution audit. All review
acceptances apply to the useful findings/tests described here, not every claim
in an unfiltered model answer. Contribution shares are scope estimates, with
reported usage kept separate and unknown native measurements retained as unknown.
