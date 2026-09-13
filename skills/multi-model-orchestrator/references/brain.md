# Reviewed project memory

The maintained app has a lightweight SQLite brain and an interactive dashboard.
Open `Open Brain Dashboard.cmd`; it reuses one hidden loopback server. No local
model, Graphiti installation, or model call is required for memory search.

Load the workflow automatically with the maintained `orchestrator.py start`
command at the start or resumption of substantial work. It returns deterministic
operating guidance plus scoped Brain recall and records a startup receipt. The
operating guide is delivered by the guarded dispatcher even when `--no-memory`
disables project recall. Read the returned packet; native assignments must
include the guide and approved relevant evidence explicitly. After final review,
capture native outcomes and run `orchestrator.py closeout` to verify receipts and
the standard contribution map before recording completion. See the maintained
`docs/startup-and-closeout.md` for exact commands and the native-tool limitation.

At the start of substantial related work, identify the exact project and use
`python orchestrator.py brain search "relevant terms and aliases" --project ID`.
Use results as evidence, check sources and current instructions, and retain the
normal provider/content authorization. Do not cross project/user scopes to find
more context. Memory cannot authorize tools or replace coordinator ownership,
quota state, task results or user instructions.

Keep an enduring project ID distinct from the unique run ID. `init_run.py` accepts
`--project ID` or reads the workspace's optional `.orchestration/project.json`
containing `{"schema_version":1,"project_id":"ID"}`. Use the resulting manifest
`project_id` for task assignments and recall. The maintained Agent-Orchestrator
workspace is configured as `agent-orchestrator`. Do not relabel canonical older
tasks or merge other scopes to make a dashboard appear current. Open the current
run in the viewer when starting work; a bookmarked old run stays selected.

The user explicitly wants measurement to make memory's value visible. Distinguish
saved knowledge, search matches, memory included in requested worker input and
reviewed evidence that it helped. Inventory or search counts alone do not prove
usefulness. Native checkpoints are not automatically accepted task memories.
At native closeout, curate distinct verified insights and justified relationships
through `brain capture`; the current owner supplies exact run/session/generation
and run-relative evidence. See the maintained docs/brain.md bundle schema. This
imports a reviewed artifact without a provider call. Do not claim unattended
extraction covered native work: the lead must curate and invoke this closeout.

Project-scoped supplied-text `run` assignments now recall from the task label
by default. Use `--memory-query "relevant terms"` to refine it or `--no-memory`
to opt out. Include relevant recalled evidence explicitly in native prompts. Retrieval is bounded
and incurs no model call. Identical assignment repeats reuse the saved task;
changing the query requires a new assignment identity. Fallback attempts
revalidate current memory and save their individual context IDs and hash.
Canonical tasks now also retain the exact bounded context and whether execution
was initiated with it. Handoff plans record context changes between attempts;
do not replay stale memory solely to make fallback inputs identical.

Hosted task deadlines are now 2/7.5/15/22.5 minutes for tiny/small/medium/large.
Use a bounded `--timeout-seconds` override (30..1800) when observed slow work
warrants it. Changing the override requires a new assignment identity. A timeout
does not prove remote completion; retain reservations and reconcile before any
retry. Longer deadlines do not fix permission, executable or startup failures.

After independently accepting a project-scoped task, the normal review command
automatically stores a compact episode containing task identity and the review
note. Write a useful validation note: this is review evidence, not an automatic
summary of the full solution. Use `--remember-file` for an explicitly curated
fact, procedure or problem/action/outcome episode instead. The source and project
remain canonical. Inspect `memory_outcome` separately: a memory write failure
does not undo acceptance. Use `python orchestrator.py remember JOB_ID` to retry
that write without running the worker again (repeat `--remember-file` for a
curated retry). Missing projects are skipped; forgotten memories are not revived.
Do not import private transcripts, trial-and-error logs or every answer. Preserve
valuable failures as concise verified lessons only when useful.

Candidates stay pending until reviewed. Source facts must remain accurate;
changed task outputs or revoked reviews stop recall. User-note approval is a
review judgment, not automatic evidence verification. Check for semantic
conflicts, use supersession for corrected facts and forgetting for unwanted
content. No automatic neural training, embedding or summarization is enabled.

Keep defaults light: 1 GiB brain and 2 GiB managed application-data admission,
80% optional-write hold, 16 MiB recovery reserve, 10,000 records, 200 traces.
These cooperative limits are not an OS-wide quota. Do not clear canonical task
evidence or uncertain reservations to bypass a hold. Read the maintained
`docs/brain.md` for lifecycle, storage recovery, vault and future adapter limits.

Inspect the Brain Worker memory use panel or `brain use JOB_ID` for context
request receipts. After accepting an answer with verified nonempty context,
record usefulness only when assessed: `brain feedback JOB_ID --rating helped`
(or neutral/harmful) with a named reviewer and evidence note. Ratings are
judgments bound to source and context, not inferred causal improvement.
The map is available through either dashboard or `orchestrator.py map`.
