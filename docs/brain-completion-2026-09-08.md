# Orchestrator memory: completion status (2026-09-08)

The approved lightweight memory task is complete. This page records the final
live status, what was delivered, how it was validated, and the real limitations.
It closes run `brain-audit-eight-20260909T040051Z-a3d6c55f`, coordinated by
Astra through the eight-seat audit and by Fable after the authorized
five-percent handoff (generation 7 claim, session `ad7834d9-d127-4220-8744-f4a1d4b8f05b`).

## Live status at completion

- Dashboard serving at `127.0.0.1:50815` (HTTP 200, Memory · Orchestrator page,
  verified live by the lead and independently by the outgoing Astra worker's
  read-only check including a Playwright browser render).
- Brain holds 5 active `agent-orchestrator` memories (the four original user
  decisions plus the newly curated worker-continuation preference) and
  2 curated audit-outcome episodes scoped to the audit run's project. No pending
  records. Synthetic audit fixtures are forgotten and their scope recalls empty.
- Storage: ~119 KB brain data against the 1 GiB brain / 2 GiB managed admission
  bounds; storage status `ready`, cooperative admission enforcing.
- Live recall on the current 4-decision set measured caller p50 ≈ 139 ms across
  10 queries (`review/final-live-performance.json` in the run).

## Delivered

- **Dashboard** — local single-user server bound to 127.0.0.1 with token-checked
  API: project selection, search, cards with source/review/history, pending
  review queue, approve/supersede/forget, interactive bounded graph.
- **CLI** — `orchestrator.py brain` with `status`, `propose`, `approve`, `list`,
  `get`, `search`, `relate`, `supersede`, `forget`, `export`, `vault`, `changes`,
  `dashboard`.
- **Worker recall** — `--memory-query` on the keyed `run` command: bounded
  project-scoped context (max 6 memories / 8,000 characters), recorded context
  IDs and hash, revalidation per fallback attempt, no model calls for retrieval.
- **Review-to-memory** — `review JOB_ID --remember-file` attaches canonical task
  provenance; task-sourced memories require a finalized accepted task in the
  exact same project.
- **Dated relationships** — temporal validity on memories and relationships,
  supersession, scheduled/expired validity states, bounded multi-hop recall.
- **Storage bounds** — cooperative admission (1 GiB brain / 2 GiB managed,
  80% optional-write hold, 16 MiB recovery reserve, 10,000 records), durable
  reservations, secure deletion on forget.
- **Future adapter** — `brain_interface.py` contract with `export`/`changes`
  cursors and `resync_required`, keeping a later Graphiti evaluation possible
  without installing it now.
- **Hosted-task deadlines** — 2/7.5/15/22.5-minute tiers plus an explicit
  bounded 30..1800 s override frozen into the assignment identity (audit seat
  B06 outcome).

## Validation performed

- Final application suite: 541 tests, 537 passed, 4 skipped (includes the
  worker-continuation follow-up run `worker-after-handoff-20260909T043252Z-3ee86da6`).
- Eight-seat audit (four Grok, four Claude): all seats completed, every seat's
  review recorded with accepted-subset dispositions in the run's
  `review/dispositions.json`; confirmed regressions fixed and re-tested.
- Two independent live readers passed all six scoped-recall checks against the
  synthetic answer key, including refusing an injected adversarial instruction
  (controlled challenge, not a universal guarantee).
- 10,000-record synthetic benchmark: lookup p50 8.7 ms / p95 14.8 ms, caller
  wall p50 30.3 ms, ~18.9 MB SQLite file, zero model calls, scope-leak and
  context-limit checks passed (`review/final-benchmark.json`).
- Installation doctor, JS syntax and live browser checks passed.
- Final read-only verification by the outgoing Astra worker after the handoff:
  live dashboard HTTP, exact 4-ID recall, foreign-project/other-user/forgotten
  scope exclusion, telemetry suppression and browser render — all passed
  (`review/astra-worker-result.json`).

## Contributions

Recorded in the run's `contribution-audit.md` / `contributions-ledger.json`:
Astra led the build, integration and the eight-seat audit and remained a bounded
read-only verification worker after the handoff; four Grok and four Claude audit
seats contributed accepted review subsets (details in `review/dispositions.json`);
Fable claimed leadership at 5% per the user's standing instruction, curated the
final three reviewed memories, verified live state and delivered this closeout.

## Real limitations

- Keyword/alias FTS5 search with importance, age and up to two relationship
  hops — no embeddings, no automatic semantic-contradiction resolution.
- Storage limits are cooperative admission, not an OS quota; they cannot cap
  unrelated programs or legacy unreserved writers.
- The dashboard is local single-user software; its token and origin checks do
  not isolate it from other software under the same Windows account.
- The adversarial-instruction result reflects one controlled synthetic
  challenge; it is not a general prompt-injection guarantee.
- Benchmark numbers describe the synthetic fixture on this machine, not a
  hosted service.
- No raw chat-history import and no background model-based extraction; curation
  is deliberate and reviewed.

## References

- [docs/brain.md](brain.md) — usage, lifecycle, storage and adapter reference.
- [docs/brain-audit-2026-09-08.md](brain-audit-2026-09-08.md) — the eight-seat audit report.
- Run ledger: `.orchestration/brain-audit-eight-20260909T040051Z-a3d6c55f/`
  (`review/dispositions.json`, `review/validation.md`, `review/final-benchmark.json`,
  `review/live-workers.json`, `review/astra-worker-result.json`).
- Follow-up run: `.orchestration/worker-after-handoff-20260909T043252Z-3ee86da6/review/validation.md`.
