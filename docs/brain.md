# Orchestrator memory

Open **Open Brain Dashboard.cmd** from the application folder. It opens or reuses
one small Python server on this computer. It does not start a model, install a
graph server, call a provider, or register a new startup task.

The approved first version uses SQLite FTS5, reviewed facts/preferences, compact
problem-action-outcome episodes, procedures, and dated relationships. Graphiti's
episode provenance and temporal-fact concepts informed the design. This is our
own implementation; Graphiti is not installed. Search uses keywords, explicit
aliases, importance, age and up to two relationship hops. It does not provide
embedding similarity or automatic semantic contradiction resolution.

## Dashboard workflow

Select a project, then search its memories or open a card to inspect its source,
review and history. Add a memory with a source note; it enters **Pending review**.
Approve it after checking the evidence. Link related approved memories, replace
outdated facts with **Supersede**, or **Forget** unwanted memories. The graph is
interactive: select nodes, drag and zoom. The overview/library graph shows a
bounded recent window, not every stored memory; search finds older matching
records. The review queue loads pending records separately.

The page polls the project's change feed every 10 seconds, starting from the
head it loaded, so only later writes count. Memories written elsewhere (`brain
propose`/`approve`, an accepted task's `--remember-file`, another dashboard
window) are applied quietly when nothing would be disturbed: no open dialog, no
active search, and not the Relationships view. Otherwise the Refresh button shows
how many changes are waiting. Recall traces are not part of the feed; use Refresh
to see new recall activity. A hidden tab does not poll.

The **Orchestrator** crumb at the top opens the Orchestrator viewer, starting
its server if it is not running. When the selected project is named after a run,
the viewer opens on that run. The viewer's **MEMORY** crumb returns here with the
run's project selected; `/#project=PROJECT_ID` works as a bookmark too.

Only approved, currently valid records with still-accepted canonical task sources
can be recalled. A future-dated record waits until its start time. Source content
changes or a revoked task review exclude the old memory from recall. Dashboard
record status reflects its memory review; source validation is repeated by search.

Plain task sources and review-bound sources for the same task are validated
independently from one canonical read per lookup. A missing, malformed or
non-object canonical result excludes its dependent memories while other valid
matches remain available. Search, export and vault responses include
`source_validation` counts/reasons and warnings for these exclusions; zero
lexical matches and unverifiable source evidence remain distinguishable.

The dashboard is local single-user software, bound to `127.0.0.1`. Its API checks a
random server token; writes also require the exact origin. It serves no arbitrary
files, uses no CDN, and treats memory text as text. These controls do not isolate
it from other software running under your Windows account.

## Commands and worker integration

```powershell
python orchestrator.py brain status
python orchestrator.py brain search "quota handoff recovery" --project agent-orchestrator
python orchestrator.py brain propose --file candidate.json
python orchestrator.py brain approve MEMORY_ID --reviewer Astra --note "Checked against the source and current project decision."
python orchestrator.py brain vault --project agent-orchestrator
```

For a worker, explicitly add the same project and a relevant query to its existing
authorized command:

```powershell
python orchestrator.py run claude --prompt-file brief.md --output answer.json --task review --project my-project --assignment-id review-v1 --memory-query "timeout recovery"
```

The dispatcher records recalled IDs, the exact bounded context, its hash and
whether execution was initiated with that context, then appends at most six
memories within 8,000 characters. This is a character bound, not an exact model
token count. The query is part of the assignment contract. An identical repeated
assignment returns its saved result without retrieving again or calling a model.
Explicit fallback attempts use the same query and project but revalidate current
memory when each attempt starts; each attempt records its own context and hash.
The handoff plan records whether its memory context changed between attempts.
Incomplete briefs do not trigger retrieval. A quota-held task marks any prepared
context as unsent. Copies retained in canonical task evidence survive later
forgetting in the brain, so the audit can show exactly what the worker received.
Review and authorization rules remain in the canonical task/coordinator records.
Memory content grants no permissions and does not transfer leadership.

Accepting a successful, project-scoped task now automatically stores a compact
episode containing the task identity and the review note. This runs for both the
normal CLI and `TaskStore.review` API, without a model call or raw-answer import.
The note should state what was verified and the useful outcome. This episode is
review evidence; it does not claim to summarize the entire solution. Unscoped
tasks are explicitly skipped, and rejected or unreviewed answers do not become
active memory.

To save a richer curated outcome instead, supply a candidate:

```powershell
python orchestrator.py review JOB_ID --decision accepted --reviewer Astra --note "Verified the implementation against the acceptance checks." --remember-file episode.json
```

If the file names a project, it must match the task exactly; otherwise the task's
project is used. The command attaches the canonical
task ID automatically and proposes/approves the curated record. If memory storage
fails, the completed task review remains saved; the command reports the memory
error for separate follow-up. No extra model is called to summarize it.

Each accepted task has a bounded `runs/tasks/JOB_ID/memory-outcome.json` receipt.
It reports `remembered`, `skipped` or `error`, and preserves the exact memory ID.
Retry a failed write using `python orchestrator.py remember JOB_ID`; for a curated
retry, repeat `--remember-file episode.json`. This command never repeats inference
or task review. Repeated calls deduplicate; Forget is respected. Changed sources,
changed review notes and ambiguous interrupted writes require inspection rather
than silently creating another memory. Review and memory outcomes remain separate.

Example candidate (replace the example with a verified outcome):

```json
{
  "project_id": "my-project",
  "kind": "episode",
  "title": "Recovered a stopped worker",
  "content": "Inspect the canonical result before repeating an assignment.",
  "tags": ["recovery", "interruption"],
  "episode": {
    "problem": "Worker output export was interrupted.",
    "action": "Inspected the saved task and its finalization evidence.",
    "outcome": "Recovered the completed answer without repeating inference."
  }
}
```

For a standalone user-sourced candidate, include
`"source": {"type":"user", "note":"The actual user statement or source explanation"}`.
Task sources require a successful finalized result to propose, and an accepted
canonical review to approve. User-source review is a human/agent judgment; the
software cannot prove that a note accurately quotes the user.

## Storage, retention and forgetting

Defaults: **1 GiB for brain data, 2 GiB for managed application data**, with
optional writes held at 80% and 16 MiB retained for recovery. The active SQLite
page cap is 256 MiB, with up to 4 MiB of bounded cleanup headroom. Conservative
write reservations include possible WAL and temporary growth. Files, WAL,
temporary exports, backups and reservations under `runtime/`, `runs/` and
`.orchestration/` count toward admission. Existing canonical tasks are not
automatically deleted to make space.

Limits: 10,000 memory records, 500 pending candidates, 30,000 relationships,
200 search traces and 1,000 adapter changes. A memory's text is limited to 3,000
characters; episodes have three fields of up to 1,000 characters each. Exact
duplicates are collapsed only when source, tags, importance and validity also
match. Semantic duplicates and contradictions need review. Forgetting restores
record capacity. Search traces keep query hashes and IDs, not raw queries.

Search skips stale source hits before selecting its current candidates. It checks
up to 1,000 candidate records and 128 distinct task sources; `recall_incomplete`
and a context warning identify a scan that reaches those limits. Context packing
can omit records that cannot fit with their provenance; `context_omitted_ids`
makes that visible. Future-dated approved records expose `validity_state=scheduled`.
An expired relationship can receive a new validity period while retaining its
old period; repeated current links remain idempotent.

These are cooperative admission limits, **not an operating-system disk quota**.
They hold participating brain/dispatcher writes, and cannot cap unrelated
programs or legacy unreserved writers. Read-only recall still works when optional
trace writes are held. A quota reservation also cannot guarantee that a provider
will finish within its allowance.

**Forget** removes the memory's text, FTS entries, relationships, matching traces
and managed vault views. ID-only change events remain so a future adapter can
delete its copy. SQLite secure deletion and WAL truncation are exercised by the
tests. Another SQLite process holding an old transaction can delay physical
checkpoint cleanup. OS backups, canonical task evidence, worker prompts already
sent, and copies exported outside this managed brain are separate records.

`brain vault` generates a project/user-specific, dated Markdown snapshot. Managed
views are invalidated by edits and forgetting. Time can make an idle snapshot
stale; use `brain search` for current evidence. No raw chat-history import or
continuous model-based memory extraction runs in the background.

Storage reservations survive interrupted writers. They do not expire by time.
Inspect `runtime/storage-reservations.json` and the referenced canonical task;
confirm the writer stopped before releasing its exact reservation through
`StorageBudget.release(token)`. Provider quota reservations are separate and
need their existing reconciliation workflow. Preserve corrupt state for repair.

If a mutation commits but reservation release fails, its response keeps the saved
record and reports `write_status.cleanup_pending` plus the reservation ID.
Close a locked vault viewer and retry when forgetting reports that an export is
in use; the unsuccessful operation leaves the memory unchanged. Orphaned vault
temporary files are swept under the brain lock when the store reopens.

## Future Graphiti integration

`app/brain_interface.py` defines the backend contract. `brain export --project ID`
returns current records, relationships and a consistent `change_cursor`.
`brain changes --project ID --after CURSOR` returns scoped ID-only events; a
trimmed cursor reports `resync_required`. A future adapter must re-export on
resync, revalidate sources, propagate forgetting, enforce budgets and keep
project/user scopes. No consumer or Graphiti migration is enabled today.

This is an application adapter boundary, not a promise that Graphiti can use
SQLite as its native graph driver. Graphiti can be evaluated later if richer
semantic retrieval justifies the added service, embedding and extraction costs.

References: [Graphiti source](https://github.com/getzep/graphiti),
[SQLite FTS5](https://www.sqlite.org/fts5.html),
[SQLite page limits](https://www.sqlite.org/pragma.html#pragma_max_page_count).
The source audit is saved in the brain-options orchestration run.

## Verification

Run `python -m unittest discover -s tests` for the application regressions.
`python tests/benchmark_brain.py` creates an isolated 10,000-record synthetic
fixture and prints measured lookup/total latency and storage. It calls no model.
Performance results describe that fixture and this machine, not a hosted service
or every future database. Build-specific results live in the brain-build run.


## Project continuity and observable memory

Use one enduring project ID for related work and a separate unique ID for each
orchestration run. New runs read an optional workspace `.orchestration/project.json`
with `{"schema_version":1,"project_id":"agent-orchestrator"}`; `init_run.py --project ID`
is an explicit override. The configured ID is saved in the manifest and should
also be used for worker assignments and memory recall. Existing task sources are
not silently migrated. A selected historical viewer run stays pinned; choose the
current run when following new work.

The dashboard checks memory changes and a separate bounded recall revision every
ten seconds. A saved search can therefore update Recent recall without a memory
write. Healthy refreshes have no routine flashing button. Errors expose **Retry
now**; updates deferred while you read or edit expose **Apply pending updates**.
The latest lookup and saved trace rows use lookup time consistently; total search
response time also includes optional trace storage. An unrecorded measurement is
not shown as zero.

Search records demonstrate that memories matched, not that a model read them or
that they helped. The next measurement design links project, run, task, retrieved
memory, requested worker input and reviewed outcome. Unknown stages remain
unknown. Accepted native-agent checkpoints do not currently enter the automatic
canonical-task review memory path. See the quiet-refresh memory-measurement run's
review report for source-backed proposals and the difference between implemented
repairs and future measurement work.
# Reviewed bundles and observable worker use

Project-scoped supplied-text dispatch now retrieves memory using the task label
by default. `--memory-query` refines the query; `--no-memory` opts out. The chosen
policy is part of assignment identity. Native workers still need the lead to
recall and include relevant evidence in their prompts.

The Brain dashboard's **Worker memory use** panel shows a bounded sample of up to
50 recent canonical tasks: lookup/preparation, context included in a requested
execution, historical capture receipts, and usefulness feedback. It checks at
the existing quiet ten-second interval. The reader scans at most 2,000 indexes
under an 8 MiB read budget and caches for five seconds. Partial or unreadable
samples are not lifetime performance metrics.

Task indexes are capped at 64 KiB. Full brief-check section text remains in
canonical task results; indexes retain the structural check summary and task
identity, lifecycle, review and usage fields. Older oversized indexes can be
rebuilt using the existing [reviewed summary repair](task-progress-and-repair.md)
workflow without changing canonical answers or memory source hashes.

`brain use JOB_ID` prints a content-free context summary. For an accepted task
with verified, nonempty supplied context, use the dashboard's **Review usefulness**
or `brain feedback JOB_ID --rating helped|neutral|harmful --reviewer NAME --note TEXT`.
The rating is an explicit judgment bound to the answer, source review, project,
and requested memory context. It is not inferred from success or lookup counts.

An accepted task can use a curated `--remember-file` with a `memories` array and
optional `relations`. Each memory needs a unique short `key` plus normal title,
content, kind, and optional tags/importance. A relation has `from`, `to`,
`relation`, and a 10-500-character evidence `reason`. Endpoints may be keys or
existing memory IDs in the same project/local-user scope; at least one endpoint
must be a bundle key. Reasons appear in memory details while source proof remains
valid. A bundle permits 1-24 memories and at most 64 explicit relationships;
the normal remember-file CLI's 16 KiB input cap still applies.

For reviewed native work, the current owner can import a closeout without
pretending a provider executed it:

```text
python orchestrator.py brain capture --run RUN --file RUN/review/knowledge.json --owner astra --session SESSION --generation GENERATION --reviewer NAME --note "Verified the implementation and its focused tests."
```

This command accepts a file within the application root, at most 96 KiB. Add a
stable `capture_id`, the exact manifest `project_id`, and `evidence` containing
1-12 relative files within that run (each at most 2 MiB). The named reviewer
curates knowledge; no model or transcript extractor runs. An imported canonical
artifact retains evidence hashes and is excluded from provider timing metrics.
The node/edge write is atomic. Identical retries reuse its receipt, changed
evidence holds, and Forget cannot trigger replay. Interrupted uncertain writes
stay held for inspection rather than risking duplicate or resurrected knowledge.

Storage limits are unchanged. Sparse graphs should first be investigated as
capture/scoping problems. Use small, independently useful nodes and justified
connections; do not manufacture edges or fill the graph with raw chat.
