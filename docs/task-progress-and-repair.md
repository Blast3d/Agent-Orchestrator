# Task progress, safety limits and summary repair

The current update passed 325 automated tests, with two platform link tests skipped, plus offline installation and browser checks. No live task summary was repaired during validation.

The Task Inbox is an offline snapshot saved to disk. Opening it does not assign work to a model or contact a provider. Saved task titles, answers and draft excerpts may contain private project text; check what is visible before recording or sharing the page.

## Daily workflow

1. **Open your snapshot.** Double-click **Open Task Inbox.cmd**, or run `python orchestrator.py inbox --open`. Reopen it whenever you want a refreshed view. The page does not update itself as a worker runs.
2. **Review progress and handoffs.** Tasks show recorded sizes, deadlines, earlier workers and saved draft excerpts when that evidence exists. Older tasks with missing details remain unknown. A handoff marked unverified does not establish that the earlier task belongs to this chain. Long drafts may appear shortened; every partial preview remains incomplete, even if a separate final answer is available. Final answers and acceptance notes stay separate.
3. **Interpret worker status safely.** Saved progress describes recorded milestones, not proof that a worker is currently running or has stopped. Check when the snapshot and progress were saved. Claude can provide progress while answering; buffered workers such as Grok may provide less detail before completion. Missing progress alone does not prove a task failed.
4. **Read attention notices.** The inbox flags stale summaries, unreadable evidence and failed contribution audits. The canonical saved result remains authoritative. The inbox does not retry, accept, cancel or repair tasks.
5. **Ask Codex for follow-up.** When a task needs attention or its summary needs rebuilding, ask Codex to investigate and perform the necessary steps. You do not need to copy technical hashes or flags yourself.

Workers also have limits on how much output the app holds. Shortening a draft preview can allow a complete final answer to arrive normally. If a hard output limit is exceeded, the app attempts to stop the worker process on your computer and marks the task for attention. That interruption cannot establish remote completion: uncertain work keeps its allowance reservation and does not automatically retry or move to another model.

## Optional manual summary repair

A summary is the small task index used by task lists. Repairing it does not repair or rewrite the saved answer. Codex can handle this maintenance, or you can use the following commands from the application folder after checking the task.

New summaries must fit the memory panel's existing 64 KiB index limit. Full
brief-check section text stays in the canonical result; the index retains the
check outcome and diagnostics. Initial writes, final saves and repair use the
same projection. Inspection can read older indexes up to 1 MiB so an oversized
legacy summary can be rebuilt within the current limit. Identity, review and
usage evidence are preserved; an unexpected oversized projection is refused.

First inspect the task:

```powershell
python orchestrator.py summary inspect JOB_ID
```

Replace `JOB_ID` with the saved task identifier. Inspection does not modify task evidence. If a repair is needed, use the exact `expected_result_sha256` value from that inspection in place of `HASH`:

```powershell
python orchestrator.py summary repair JOB_ID --expected-result-sha256 HASH --reviewer Codex --note "Checked the finalized canonical result and confirmed that only its summary is stale."
```

Use your own reviewer name and a note describing what you actually checked. A changed answer requires a fresh inspection. A matching summary is an explicit no-op. Reopen the inbox after a successful repair to refresh the page.

Repair only rebuilds a missing or stale summary for a coherent, finalized task, under the task review lock. It preserves the canonical result bytes and records the attempt before replacing the summary, then records verified success. If success evidence could not be saved, a later inspection still flags the incomplete attempt; a matching summary alone does not clear it.

Malformed or conflicting identities, changed source files, redirected paths and unresolved repair history require inspection. This command cannot determine remote completion, change acceptance, fix contribution calculations, release protected allowance or restart work.

Drafted by Antigravity using Gemini 3.8 Flash Medium; checked and edited against the implementation by Codex.
