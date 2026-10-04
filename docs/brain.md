# Orchestrator memory

The **Skill packs** page connects project-scoped JEV recommendations, lead review,
complete worker context and accepted-answer usefulness feedback. See
[task skill packs](skill-packs.md) for the dashboard and OpenWhispr workflow.

Open **Open Brain Dashboard.cmd** from the application folder. It opens or reuses
one small Python server on this computer. It does not start a model, install a
graph server or register a new startup task. When Jev is enabled for a project in
`runtime/jev-config.json` (it is for `agent-orchestrator` and `openwhispr`), recall
ranking sends bounded excerpts to OpenRouter and is billed per call; that includes
startup, dispatch and `brain search` by default. Dashboard searches stay local
unless **Use Jev ranking (OpenRouter, billed)** is selected. The retrieval method,
including **Keywords only**, controls local candidate selection and does not itself
control Jev. Use `python orchestrator.py brain search QUERY --project ID --no-jev`
for a CLI search that stays local.

The approved first version uses SQLite FTS5, reviewed facts/preferences, compact
problem-action-outcome episodes, procedures, and dated relationships. Graphiti's
episode provenance and temporal-fact concepts informed the design. This is our
own implementation; Graphiti is not installed. Automatic search starts with
keywords and aliases. A complete lexical match avoids semantic work; relationship
questions can expand the graph. An explicitly configured embedding connector can
help weak lexical matches. There is no automatic semantic contradiction resolution.

## Adaptive retrieval

An optional [Jev connector](jev-openrouter.md) can score the already-retrieved
shortlist through OpenRouter for authorized projects. Task profiles tailor the
scoring to implementation, review, research or handoff work. See the
[memory workflow](jev-memory-workflow.md) for bot delivery, limits and planned
use cases. Source review and forgetting remain enforced by the Brain.

The search form's **Retrieval method** selector supports:

| Choice | Work performed |
| --- | --- |
| Automatic | Keywords first; relationship queries add one graph hop. Weak or absent lexical matches may use the configured semantic index. |
| Keywords only | FTS5, aliases, importance and age; no graph or embedding request. |
| Keywords + relationships | Keywords plus one graph hop, without embedding requests. |
| Semantic fallback | Keywords plus an explicit attempt to retrieve semantic matches; no graph expansion. |

The dashboard's **Use Jev ranking** checkbox is off by default. Selecting it can
make billed OpenRouter requests for any retrieval method, including **Keywords
only** and **Deep**. An explicitly configured semantic connector is a separate
provider path for **Semantic fallback**.

The CLI accepts `--strategy auto|keyword|graph|semantic`. `--hops 0|1|2`
overrides graph depth for `auto` or `graph`. The default is now adaptive rather
than unconditional graph expansion. Keyword match quality is a routing heuristic,
not a confidence percentage. Matching one filename does not count as an exact
answer when other meaningful query constraints are absent.

An empty active project returns immediately with no memory context or recall-trace
write. Worker operating guidance still applies. A populated project with no matches
still records a trace when storage permits. Graph traversal happens after any
provider request so semantic seeds can reach related memories and current relation
periods are checked. Network work does not hold the Brain lock. Sources and indexed
content are checked again before context is supplied. Each lookup inspects at most
128 distinct task sources across all stages, with fresh proof validation after
network work. Task-sized recall uses separate search, ranking and delivery budgets:

| Depth | Jev shortlist ceiling | Delivered memories | Context characters |
| --- | ---: | ---: | ---: |
| Compact | 16 | 6 | 8,000 |
| Balanced | 48 | 12 | 16,000 |
| Deep | 100 | 24 | 32,000 |

These are ceilings, not targets. Fewer matches or large excerpts produce fewer
delivered memories; unrelated project records never fill unused space. Local
keyword search checks up to 1,000 rows and retains up to 100 candidates for deep
recall. Jev only judges locally found candidates; it cannot discover unseen facts.
Wider shortlists use bounded batches, at most eight requests and three concurrent
requests per lookup. Byte limits can reduce the scored coverage, which is reported.
A dashboard search with Jev selected has a 40-second Jev deadline: batches that could not finish
before it are never started and keep their local order (the retrieval details say
how many). The page waits 70 seconds; if it stops waiting, it says that ranking
may still finish on the server and count toward usage, so wait before searching again.
Low-confidence scores keep their existing positions. Semantic search still needs
an explicitly configured index; Jev reranking is not an embedding index.

Workers automatically select compact for tiny/small tasks, balanced for medium,
and deep for large. Override with `--memory-depth compact|balanced|deep`.
The Brain search form has a **Recall depth** selector; the CLI uses `--depth`.
Startup defaults to balanced and accepts `--memory-depth`; its 64 KiB receipt
ceiling can further reduce delivered records, recorded as `startup_delivery`.
Direct library searches and CLI searches remain compact unless specified.

More reviewed memories increase the chance of finding an applicable fix or decision.
They do not guarantee faster work. Task size determines the context budget;
project size alone does not force more text into every bot. One recall request,
one newly captured review episode, and one supplied memory are different counters.
Actual benefit requires a reviewed usefulness rating or a controlled task comparison.

**How this memory was found** shows the route actually executed, lexical match
quality, memory count, context characters, provider request count, and timings for
keywords, graph expansion, semantic search, packing and saving the receipt. Lookup
time excludes receipt saving; total elapsed time includes it. Saved stage timings
are approximate because a receipt cannot include its own completed write time.
Worker-memory details retain this evidence per task. Old records say the method was
not recorded; missing token or charge information remains unknown. Supplied context
is evidence of delivery, not proof that a provider read or benefited from it.

Semantic retrieval is **off until explicitly configured and indexed**. See
[the optional semantic connector](brain-semantic.md) for project authorization,
indexing, provider limits, and deletion behavior. Neither startup nor search
installs a model, builds an index automatically, or picks a paid service.

For a small offline comparison from the source checkout, run
`python benchmarks/brain_retrieval.py --output comparison.json`. It uses temporary
synthetic memories and isolated searches, records each case/mode separately, and
makes no provider requests. It measures retrieval behavior and overhead, not the
speed, quality or cost of a whole bot task.

For the complete timing and delivery sequence, parallel-worker limits, and cross-project linking workflow, see [Brain timing and linked projects](brain-timing-and-links.md).

## Dashboard workflow

Select a project, then search its memories or open a card to inspect its source,
review and history. Add a memory with a source note; it enters **Pending review**.
Approve it after checking the evidence. Link related approved memories, replace
outdated facts with **Supersede**, or **Forget** unwanted memories. The graph is
interactive: select nodes, drag and zoom. Drag the selected node's green **+** handle to another memory to save a typed connection. **Connect memory** offers a keyboard alternative and a project dropdown for explicitly reviewed cross-project reuse. Selected foreign evidence becomes a source-bound reference in this project; Forget and source validity still apply. Both the overview and **Relationships**
view load up to **3,000 nodes** by default from a separate project graph feed.
The 2D map fills a loose, irregular cluster with memories across its center.
Toggle **3D view** for a globe: drag to spin continuously in either direction
on both axes, Shift-drag to pan, and use
**Reset rotation** to restore its orientation. Zoom, search, and opening memories
work in both views. Turning off 3D returns to the 2D cluster.
**Show up to** also offers 5,000 or 10,000. The caption gives the actual number
shown and eligible project total, and reports any truncation. The library's
200-record browsing window and worker recall limits do not limit this map.

The canvas groups connected components without a continuous force simulation.
Use **Find a memory in map** to locate a loaded node, then **Open memory** to
inspect its evidence. Search results page ten at a time; increase the node limit
to include older records outside the current map. Click a node or press Enter to
open it, use arrow keys to move selection, drag the background to pan, drag nodes
to rearrange, and use the wheel, zoom buttons or **Fit map**. Labels appear on
hover/selection and when zoomed far enough into a small visible group.

The graph feed contains compact titles, kinds and explicit connections for the
selected project/local user. It includes approved records within their validity
period and current connections between the selected nodes, up to 30,000 edges.
It does not revalidate canonical source proofs or call a model; source checks
still occur during recall. Full memory bodies load only when opening a node.
The review queue loads pending records separately.

The page polls the project's change feed every 10 seconds, starting from the
head it loaded, so only later writes count. Memories written elsewhere (`brain
propose`/`approve`, an accepted task's `--remember-file`, another dashboard
window) are applied quietly when nothing would be disturbed: no open dialog, no
active search, and not the Relationships view. Otherwise the Refresh button shows
how many changes are waiting. Recall activity follows a separate recall revision
(see below), not the memory change feed. A hidden tab does not poll.

The **Orchestrator** crumb at the top opens the Orchestrator viewer, starting
its server if it is not running. When the selected project is named after a run,
the viewer opens on that run. The viewer's **MEMORY** crumb returns here with the
run's project selected; `/#project=PROJECT_ID` works as a bookmark too.

Only approved, currently valid records with still-accepted canonical task sources
can be recalled. A future-dated record waits until its start time. Source content
changes or a revoked task review exclude the old memory from recall. Dashboard
record status reflects its memory review; source validation is repeated by search.

Plain task sources and review-bound sources for the same task are validated
independently from one canonical read per validation phase. A missing, malformed or
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
whether execution was initiated with that context, then appends at most 6, 12 or 24
memories within the selected character budget. This is a character bound, not an exact model
token count. The query and noncompact recall depth are part of the assignment contract. An identical repeated
assignment returns its saved result without retrieving again or calling a model.
Explicit fallback attempts use the same query and project but revalidate current
memory when each attempt starts; each attempt records its own context and hash.
The handoff plan records whether its memory context changed between attempts.
Antigravity and VS Code worker transports have byte ceilings that include the
operating guide and task. Delivery trims memory records to fit those ceilings,
records `delivery.retrieved_count` versus `delivered_count`, and preserves source
warnings. It never truncates the assigned task or guide to fit more memory.
Incomplete briefs do not trigger retrieval. A quota-held task marks any prepared
context as unsent. Copies retained in canonical task evidence survive later
forgetting in the brain, so the audit can show exactly what the worker received.
Review and authorization rules remain in the canonical task/coordinator records.
Memory content grants no permissions and does not transfer leadership.

Accepting a successful, project-scoped task now automatically stores a compact
episode containing the task identity and the review note. This runs for both the
normal CLI and `TaskStore.review` API, without a model call or raw-answer import.
New automatic episodes also include a bounded **performance snapshot** when
measurements exist: creation-to-finalization wall seconds, provider execution
seconds, task size, observed models, normalized input/output/cache tokens,
provider-reported cost, separately verified additional charges, and Brain/Jev
recall overhead. Unknown measurements remain `null`; a reported model price is
not an invoice, cache tokens are not double-counted, and a prepared memory
packet is not counted as supplied until execution was requested.

Find these with `brain search "performance duration retry cancellation" --project
PROJECT --depth balanced`, or enter a specific task plus **performance duration
usage** in the dashboard. The performance fields are tied to canonical source
proofs; changed measurements exclude the old memory until reviewed. Existing
receipts retain their original format and are not rewritten or resurrected after
Forget. Native multi-agent run timing still needs a reviewed run-level capture:
a short artifact-import task is not the duration of the entire build. Preserve
interruptions, failed attempts and unmeasured native usage as explicit limitations
when using historical observations for future planning.

Open a measured episode to see **Task time and usage** as readable duration,
token, cost and recall fields. Missing values say **Not recorded**. This display
preserves the stored evidence; malformed or older records remain readable as text.
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
record capacity. Search traces keep a plain SHA-256 hash of the JSON-encoded
query and memory IDs, not the raw query. Short or predictable queries may be
guessed from that hash. Canonical tasks and startup packets can retain raw queries
as separate evidence.

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
