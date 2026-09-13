# Startup and verified closeout

Use `start` before new orchestration work. It always loads the maintained operating
guide and prints the full bounded packet for the coordinator. It searches only the
run's exact Brain project, with the objective as its default query. An empty search
is valid. Operating guidance does not depend on a relevance match.

```powershell
python orchestrator.py start --workspace C:\path\to\workspace --name feature-review --objective "Review the feature" --project my-project
```

The returned `coordinator` object identifies the established owner, session and
generation. Resuming requires the exact current identity; inspect
`python orchestrator.py lead status --run <exact-run-directory>` first. Resuming
loads context again without resetting tasks, ledgers, or coordinator state:

```powershell
python orchestrator.py start --run <exact-run-directory> --owner astra --session <current-session> --generation <current-generation>
```

Use `--query` to narrow recall to at most 500 characters. `--no-memory` explicitly
omits project recall while keeping shared operating guidance. `startup-context.md`
contains the packet; `startup-context.json` binds its hash, guide revision, recalled
evidence and current coordinator identity. Files are replaced individually and
atomically, with JSON last. The JSON receipt stays within 64 KiB, including actual
serialized escaping and platform newlines; the packet hash covers the Markdown's
exact UTF-8 bytes. After a checkpoint or handoff changes the generation,
run `start` again before new dispatch.

A startup receipt means **prepared for the lead**. It does not prove any model read,
understood or obeyed it. Native collaboration prompts bypass the supplied-text
dispatcher: the coordinator must include this guidance and the appropriate bounded
recall explicitly in each native prompt. A file on disk is not proof of native
delivery. The lifecycle commands do not spawn workers or check provider quotas.

After reviewing task results, record acceptance with the maintained review command;
it creates a separate automatic memory receipt. Native work also needs an explicit
reviewed `brain capture` bundle with immutable evidence inside this exact run:

```powershell
python orchestrator.py brain capture --run <run-id> --file <reviewed-bundle.json> --owner astra --session <current-session> --generation <current-generation> --reviewer ASTRA --note "Verified the reviewed knowledge against the final evidence."
```

Capture accepts a run ID; startup and closeout accept the exact run directory.
Check the nested `memory_outcome.status`, rather than the process exit alone.
Keep later status updates separate from files already hashed by capture. New
startup runs default to `native_work: true`; resumed runs acquire that default when
the field was absent. Explicit `false` is appropriate only for entirely hosted
retained work. Native assignments or retained contributors outside verified hosted
worker activity require a native capture regardless of that flag.

Complete the contribution ledger with real reviewed allocations and generate the
canonical `contribution-audit.json` using the maintained contribution command.
For every accepted hosted task, use its exact canonical job ID as an accepted
`work_items[].id`. Allocate positive credit to its observed worker and include
exactly one `activity` delegation with that `task_id`. Its provider, execution
status, token values, and `actual_models` must match canonical task evidence. A
contributor may aggregate one worker identity across jobs; its `model` is the
comma-and-space-separated union of those jobs' observed models, or `unknown` if
none were recorded. Imported native capture tasks are never worker delegations.
Then run:

```powershell
python orchestrator.py closeout --run <exact-run-directory> --owner astra --session <current-session> --generation <current-generation>
```

Closeout first verifies current startup ownership, guide hash and actual packet
bytes. It discovers tasks through exact `run_id`, manifest job IDs, or requested
output paths inside the run. A task belonging only to the same project does not
qualify. It holds completion for missing/unfinished canonical results, unreviewed
work, bad capture identities or source hashes, missing native capture, empty or
malformed audits, incomplete attribution, and pending ledger work. Historical
remembered receipts remain evidence of a past write after Forget; closeout never
resurrects memories or accepts tasks itself.

The current ledger must reproduce the complete canonical audit. Only then does
`ProjectLibrary` regenerate the map; closeout verifies the generated run entry
against that audit. Ownership and source evidence are checked again before final
mutation. `closeout.json` gives per-check reasons; only a fully passing result marks
`run.json` completed. A held check preserves existing task and memory evidence.
If revalidation fails after a previous completion, the manifest returns to
`in_progress` and preserves the earlier completion timestamp as historical evidence.

Discovery inspects at most 2,000 task entries and 16 MiB of bounded indexes, and
reads each linked canonical result within the maintained 8 MiB limit. Exceeding a
bound or encountering an unverifiable index holds completion rather than claiming
the scan was exhaustive. The coordinator lock and task review/memory locks protect
supported writers; arbitrary external file edits do not participate in those locks.
