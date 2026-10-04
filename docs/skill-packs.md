# Task skill packs

Open **Open Brain Dashboard.cmd**, then choose **Skill packs**. The JEV page also
links to it. Select the project and enter a concrete task.

1. Choose **Manual selection** or **Ask JEV to recommend**. JEV can make up to
   two billed calls: choose a pack, then a skill from its bounded shortlist.
   Low confidence or an unavailable provider leaves manual selection available.
2. Read the relevant skill instructions, select up to three skills, and enter
   the lead's reviewer name and review note. Choose **Review and load**.
3. Copy the complete context to a native coding agent, or deliberately execute
   the selected supplied-text worker. The dashboard workers can return answers
   or proposed patches; these routes have no tools and cannot edit files.
4. Read the actual answer and accept it with a substantive review note.
5. Record **helped**, **neutral**, or **harmful** with evidence. Brain keeps this
   explicit judgment alongside the accepted outcome and the exact supplied
   context. A judgment is not a measured improvement or proof of obedience.

Opening the catalog, loading instructions, and viewing plans are local. Worker
execution uses the selected provider's existing route. Its scoped Brain recall
can also make billed JEV ranking calls under the existing project settings.
The page shows recommendation telemetry separately from worker activity.

## Packs and discovery

`config/skill-packs.json` is the maintained adapter to the existing local skill
library. It names individual files and project allowlists; it does not scan your
home folder or install skills into your profiles. Core, engineering and the
project's domain pack are presented first. Optional MCP, documents, design,
research and video packs are available where explicitly allowed.

Claude's parked Vibe collection stays under
`C:\Users\jacob\agent-skill-library\dormant\vibe-skills\bundled\skills`.
Only explicitly referenced dormant skills appear in this catalog. Seeing their
names here does not add them to Codex or Claude's automatic discovery folders.
The manifest's roots use `~` and named project/library locations; update the
specific root if a project moves. The saved Civil 3D checkout is currently absent,
so that domain pack reports a warning instead of guessing another location.

The catalog and an agent's discovery list are different counts. The fresh
standalone Codex CLI scan on 2026-10-03 found 32 enabled entries at home, 33 in
Orchestrator and 37 in OpenWhispr, with no parked Vibe entries. These numbers do
not include the desktop application's separately exposed plugin metadata.

## OpenWhispr

Start the local gateway from the Orchestrator folder when needed:

```powershell
python orchestrator.py voice serve --worker codex
```

It stays off at sign-in. Starting it does not enable the microphone or change
your selected OpenWhispr target. In OpenWhispr, say
`command switch to orchestrator` when you want that destination.

After loading a plan scoped to `openwhispr`, the Skill packs page offers
**Copy OpenWhispr request**. Send the copied text to the Orchestrator target:

```text
use skill plan <32-character plan id>: <the exact reviewed task>
```

The prefix is an explicit selection for that request. The gateway verifies the
project, task and current file hashes, creates an isolated hosted run and passes
the complete context through the guarded dispatcher. It does not claim another
lead's run. Malformed, unreviewed, mismatched or stale selections stop before a
provider call. Ordinary requests retain their existing behavior.

Voice results stay unreviewed until the lead reviews their canonical job in the
task inbox or with `review`. For these jobs, use `skills use` and `skills feedback`
below to record a source-bound judgment. The dashboard's answer acceptance
controls apply to executions started from that page.

The gateway/client transport and recovery are tested. A real spoken command,
microphone capture and Piper playback still need a live OpenWhispr session.

## Command line and native coding agents

Run these from the canonical Orchestrator folder:

```powershell
python orchestrator.py skills catalog --project openwhispr
python orchestrator.py skills recommend --project openwhispr --task "Investigate a failing test"
```

Add `--jev` only when requesting JEV's recommendation. The returned plan includes
its ID and manifest hash. Review with the current values and the catalog's skill ID:

```powershell
python orchestrator.py skills review --project openwhispr --plan PLAN_ID --manifest MANIFEST_SHA --skill systematic-debugging --reviewer "Lead" --note "Read the instructions and checked their fit for this exact task."
python orchestrator.py skills context --project openwhispr --plan PLAN_ID
```

`context` returns a JSON packet containing the full instructions and their hashes.
Native Codex or Claude agents must actually receive and read its `text`, plus the
run's operating guide and authorized Brain recall. A saved packet alone is not
delivery. Follow [startup and closeout](startup-and-closeout.md) for a substantial
native task. For a guarded worker on your own prepared run, add
`--run EXACT_RUN --skill-plan PLAN_ID --no-auto-fallback` to the usual `run` command.

After reviewing an answer:

```powershell
python orchestrator.py skills use --project openwhispr --job JOB_ID
python orchestrator.py skills feedback --project openwhispr --job JOB_ID --rating helped --source SOURCE_SHA --context CONTEXT_SHA --reviewer "Lead" --note "Describe the observed contribution of the supplied procedure."
```

Use the source and context hashes returned by `skills use`. Feedback is accepted
only for a finalized accepted answer with matching actual requested context.

## Freshness and recovery

Plans bind the manifest and every selected file hash. Changing instructions
requires a fresh selection and review. Complete context is limited to three
skills and 24 KiB; oversized selections fail clearly without cutting instructions.
Large coordinator skills remain available through normal native discovery.

Repeating the same dashboard execution never starts another worker. After a
restart, an interrupted execution becomes **uncertain**. Inspect the saved run
and canonical task before starting replacement work. A feedback write interrupted
after it begins is also held for inspection, preventing duplicate Brain records.
Closing the dashboard or gateway preserves the saved plans, tasks and memory.
