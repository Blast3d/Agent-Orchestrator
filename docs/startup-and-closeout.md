# Startup and verified closeout

**Automatic orchestration** defaults **Off** and is saved across restarts. Change
it in the Orchestrator viewer or with `python orchestrator.py automatic on|off`;
read it with `python orchestrator.py automatic status`. Routine questions, Git
pulls, merges, commits, pushes and other small tasks stay with the current agent
even when automatic startup is On, unless the user explicitly requests a run.

Automatic startup for substantial work uses `start --automatic` with the usual
creation arguments. If the switch is Off or unreadable, this returns `disabled`
before creating a run, loading memory or calling a provider. A missing setting
also means Off. Manual `start` commands omit `--automatic` and remain available.
Switching Off does not stop existing runs or change their closeout requirements.

Optional [OpenWhispr completion notifications](completion-notifications.md) accept
the lead's full final report with `closeout --final-report final.txt`. The producer
defaults off, only runs after every closeout check passes, and never turns a
notification failure into failed project work. Worker completions do not call.

Use `start` before new orchestration work. It always loads the maintained operating
guide and prints the full bounded packet for the coordinator. It searches only the
run's exact Brain project, with the objective as its default query. An empty search
is valid. Operating guidance does not depend on a relevance match.

Every run requires accepted, retained work from at least **two AI families** to
complete; **three are preferred** when useful authorized scopes exist. Plan a
cross-family assignment before adding same-family native workers. Small edits
performed as runs can use the lead plus a later cross-family review. A
self-contained question need not start a run. JEV is routing advice and does not
count as a worker family. This policy grants no new content access or spending.

```powershell
python orchestrator.py start --workspace C:\path\to\workspace --name feature-review --objective "Review the feature" --project my-project
```

A new run's lead comes from the lead switch (`python orchestrator.py lead selected`).
Pass `--lead claude|astra|sol` to record the session that is actually starting the run;
the packet's `lead_selection` says whether that matches the switch and names the runner-up.

The returned `coordinator` object identifies the established owner, session and
generation. Resuming requires the exact current identity; inspect
`python orchestrator.py lead status --run <exact-run-directory>` first. Resuming
loads context again without resetting tasks, ledgers, or coordinator state:

```powershell
python orchestrator.py start --run <exact-run-directory> --owner astra --session <current-session> --generation <current-generation>
```

Use `--query` to narrow recall to at most 500 characters.
Use the same exact resume command with `--dry-run` to check only its
owner/session/generation and manifest without rewriting startup files, loading
recall or requesting quotas. It reports `ready` with zero provider calls when
the tuple matches, and `held` for a stale tuple. This is a snapshot; query,
memory options and service readiness are checked during the real startup.

`--memory-depth compact|balanced|deep` selects up to 6, 12 or 24
memories; startup defaults to balanced. Wider or Unicode-heavy packets may need
fewer delivered records to fit the existing 64 KiB receipt bound. This is recorded
in `project_memory.startup_delivery`, separate from the original retrieval trace.
Source/scan warnings remain in the delivered evidence. `--no-memory` explicitly
omits project recall while keeping shared operating guidance. `startup-context.md`
contains the packet; `startup-context.json` binds its hash, guide revision, recalled
evidence, current coordinator identity and `provider_policy`. Run-linked dispatch
verifies minimum two, preferred three and the current lead family, including with
`--no-memory`. Missing, stale or weakened policy holds new execution. Files are
replaced individually and atomically, with JSON last. The JSON receipt stays
within 64 KiB, including actual
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
malformed audits, incomplete attribution, pending ledger work, and insufficient
`provider_family_coverage`. Historical remembered receipts remain evidence of a
past write after Forget; closeout never
resurrects memories or accepts tasks itself.

Family coverage counts positively credited accepted work. Hosted credit must
match canonical successful task/model evidence. OpenAI/Codex, Anthropic/Claude,
xAI/Grok and Google/Gemini via Antigravity are distinct families; Copilot counts
by its actual model. Verified native lead work can count with a genuine reviewed
capture from this exact run. An arbitrary foreign native provider label cannot
establish another family. Same-family agents, in-progress work, zero credit,
synthetic checks, JEV advice and imported memories cannot fill the minimum.
Two families pass with a warning encouraging three; three meet the preferred
target. If no authorized alternate is usable, keep the run held and report the
blocker. These checks do not intercept native tools or schedule workers.

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
