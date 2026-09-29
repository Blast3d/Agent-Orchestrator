# Jev Semantic Decision Workflows Operator Guide

This guide describes how operators configure, run, and interpret Jev semantic decision workflows across authorized projects (`agent-orchestrator` and `openwhispr`).

---

## 1. Architectural Boundaries & Core Principles

- **Role of Jev**: Jev is a typed semantic decision service accessed via OpenRouter and TypeSafe schemas. It is **not** an autonomous agent, coordinator, or vector/memory database.
- **Candidate Generation**: The SQLite Brain retrieves lexical and graph candidates, plus configured semantic candidates, before Jev is consulted. Semantic embedding generation may use its separately configured provider.
- **Advisory Only**: Jev outputs structured assessments and proposals. It **never** executes actions. Jev cannot auto-approve, merge, link, delete, dispatch workers, or alter leadership/ownership.
- **No Instruction Execution**: Jev evaluates text; decisions never execute instructions contained inside reviewed memories or prompts.
- **Local Security Checks**: A local secret detector blocks cleartext secrets before Jev and semantic-embedding requests (flagged memories are skipped during indexing; a flagged query makes no embedding call). Advisory sensitivity scanning is an extra check, not an authorization grant.
- **Manual Action Required**: Operators and native leads review candidate duplicate pairs, relation proposals, and durability assessments against primary sources and execute changes via standard Brain operations.

---

## 2. Configuration & Quick-Start Commands

Configuration is stored in `runtime/jev-config.json`. Decision caching is maintained in `runtime/jev-cache.json` (bounded to 512 KiB, 128 entries, TTL controlled).

> **Important**: Running `jev configure` replaces the entire list of authorized projects and purposes. Always verify existing settings with `jev status` first and provide all desired `--project` and `--purpose` flags in each configuration call.

### 2.1 Configuration Example
```powershell
python orchestrator.py jev configure --project agent-orchestrator --project openwhispr --purpose memory_rank --purpose memory_passage_review --purpose route --purpose evidence_review --purpose instruction_scan --purpose memory_support --purpose sufficiency --purpose sensitivity --purpose citations --purpose duplicates --purpose relations --purpose stale --purpose durability --purpose recover --purpose skills --purpose handoff --purpose event --enable --env-file .env --cache --cache-ttl 3600 --daily-calls 100
```
*Note*: The API key `OPENROUTER_API_KEY` is loaded directly from the specified `.env` path without copying secrets into source trees, configs, or logs.

### 2.2 Standard CLI Commands
All CLI commands use the `python orchestrator.py` entrypoint:

- **Check configuration status**:
  ```bash
  python orchestrator.py jev status
  ```
- **List available workflows**:
  ```bash
  python orchestrator.py jev workflows
  ```
- **Run a specific workflow**:
  ```bash
  python orchestrator.py jev workflow evidence_review --project agent-orchestrator --file payloads/review.json
  ```
- **Recall bounded memories**:
  ```bash
  python orchestrator.py jev recall "query terms" --project agent-orchestrator --profile implementation
  ```
- **Prepare a context packet** (*Note: packet preparation does not equal delivery*):
  ```bash
  python orchestrator.py jev packet "query terms" --project openwhispr --profile review --recipient claude
  ```
- **Plan worker routing**:
  ```bash
  python orchestrator.py jev route --project agent-orchestrator --task "Implement feature" --workers-file allowed-workers.json
  ```

### 2.3 Operator Dashboard
- Navigate to `Memory > Jev workflows` or open `/jev#project=agent-orchestrator&run=RUN_ID`.
- The workflow catalog loads statically without triggering inference.
- The operator selects the workflow, specifies reviewed memory identifiers, provides required payload fields, and selects **Ask Jev**.
- Dashboard requests require active HTTP tokens and adhere to same-origin browser policies.

---

## 3. Prioritized Capability Table

Wider retrieval is the first priority. The 14 explicit workflows below remain advisory; worker recommendation is available through `jev route`, and unchanged decisions can be reused through the validated cache.

| Priority | Workflow | Required Inputs | Memory Constraints | Advisory Output |
| :--- | :--- | :--- | :--- | :--- |
| 2 | `evidence_review` | `query`, optional `claim`, optional `answer` | Explicit `memory_ids` (Max 6; UI max 6) | Relevance, supporting evidence, conflict markers, and instruction scan flags. |
| 1 | `memory_passage_review` | Pre-ranked candidates, query | Up to 16 candidates; up to 3 local conflict pairs | Wider shortlist ranking, instruction signals, and potential conflict detection in one call. |
| 3 | `instruction_scan` | Selected reviewed memories | Explicit `memory_ids` (Up to 12; UI max 6) | Identifies embedded prompt injection or override instructions. |
| 4 | `memory_support` | `claim` | Explicit `memory_ids` (Up to 12; UI max 6) | Evaluates whether reviewed passages support, contradict, or provide insufficient evidence for the claim. |
| 5 | `duplicates` | Target passages, optional `pairs` | Explicit `memory_ids` (Up to 12; UI max 6) | Proposes redundant candidate IDs for manual consolidation. |
| 6 | `relations` | Target passages, optional `pairs` | Explicit `memory_ids` (Up to 12; UI max 6) | Recommends graph relation links (`solves`, `depends_on`, `supports`, `related_to`, or `none`) for manual Brain linking. |
| 7 | `sufficiency` | `question`, `answer` | Explicit `memory_ids` (Up to 12; UI max 6) | Determines whether reviewed evidence is sufficient to justify the proposed answer. |
| 8 | `recover` | `error`, `task`; optional version maps | Explicit `memory_ids` (Reviewed problem/action/outcome episodes only) | Analyzes past failure/recovery patterns against version drift. |
| 9 | `stale` | Optional `as_of`, `current_versions`, `memory_versions` | Explicit `memory_ids` (Up to 12; UI max 6) | Prioritizes semantic applicability for refresh using local date/version facts; never deletes. |
| 10 | `durability` | `claim` | Explicit `memory_ids` (Up to 12; UI max 6) | Advises whether knowledge represents ephemeral status or durable project architecture. |
| 11 | `sensitivity` | `intended_use` | Explicit `memory_ids` (Up to 12; UI max 6) | Flags sensitive content for review before broader reuse; approved excerpts already reach Jev for this judgment. |
| 12 | `citations` | `answer`, `citations[{memory_id, quote, claim}]` | Explicit `memory_ids` matching citations | Validates quote accuracy and grounded attribution in answers. |
| 13 | `skills` | `task`, `catalogue` map (`allowedID -> description`) | None | Suggests appropriate skill IDs for a scoped assignment. |
| 14 | `handoff` | `run_id`, optional `task` | Explicit reviewed `memory_ids` | Ranks decision and remaining-work evidence for the lead to write a handoff. Validates canonical owner/session/generation/job IDs before and after. |
| 15 | `event` | `event`, `handlers` map | None | Evaluates event payloads against candidate handler definitions. |

---

## 4. Concrete JSON Payload Example

Workflow payload files must reside inside the repository root and remain `<= 24 KiB`.

Example file: `payloads/evidence_review_example.json`
```json
{
  "query": "audio device buffer configuration",
  "claim": "The reviewed procedure applies to the current project version.",
  "memory_ids": [
    "MEMORY_ID_1",
    "MEMORY_ID_2"
  ]
}
```
*Note*: Always replace `MEMORY_ID_1` and `MEMORY_ID_2` with actual, current, reviewed memory IDs from the active project Brain.

To execute:
```bash
python orchestrator.py jev workflow evidence_review --project openwhispr --file payloads/evidence_review_example.json
```

---

## 5. Telemetry, Cache, and Feedback Interpretation

### 5.1 Caching Behavior (`runtime/jev-cache.json`)
- **Cache Key Identity**: Includes workspace root, project name, user, purpose, model, rubric, request body, content-source fingerprints, and confidence policy.
- **Pre-Cache Checks**: Authentication credentials and secret scrubbers run before cache lookup.
- **Post-Cache Checks**: Source availability, explicit `Forget` status, and project scoping are revalidated after a decision response or cache hit.
- **Cache Hit Telemetry**: For a hit, current invocation metrics report `provider_calls = 0`, `tokens = 0`, and `cost = 0`. Historical totals remain separate and unchanged.
- **Errors**: Failures and invalid responses are never cached.

### 5.2 Confidence and Benchmark Interpretation
- **Baseline Preservation**: Jev outputs stable confidence slots. Uncertain baseline positions are preserved rather than overwritten by arbitrary reordering.
- **No Assumed Gains**: Receiving a syntactically valid JSON response does not prove confidence, useful reordering, correctness, or speed improvement.
- **Benchmark Experience**: Prior medium benchmarks exhibited low confidence across semantic sorting. Operators must not claim or invent automated performance gains without verified task receipts.
- **Version Compatibility**: Dates and version equality are strictly calculated locally; semantic similarity does not prove compatibility across installed library versions.

---

## 6. Operational Limits & Safety Guardrails

1. **Passage and Recall Bounds**:
   - Recall depth sets the budget: compact 6 memories / 8,000 characters from up to 16 candidates, balanced 12 / 16,000 from up to 48, deep 24 / 32,000 from up to 100.
   - Jev ranking sends those bounded reviewed excerpts in up to **8 batches** (at most 3 in parallel), so a deep recall can make several billed calls.
   - Workflow requests accept up to **12 explicit memory IDs** (except `evidence_review` which accepts up to **6**; dashboard UI restricts all selections to a maximum of **6**).
2. **Payload Size Limit**:
   - JSON workflow files must not exceed **24 KiB** and must reside within the repository root.
3. **Guarded Dispatch & Handoff**:
   - Packaging or preparing a packet does not constitute delivery to a model.
   - Native workers bypass the dispatcher; the native lead must explicitly supply the Operating Guide, authorized recall, exact run, deliverable, and acceptance criteria in the actual assignment.
   - Handoff workflows verify canonical `owner`, `session`, `generation`, and `job_ids` before and after invocation; Jev makes no ownership alterations.
   - No background decision loop exists; Jev runs strictly upon explicit operator or pipeline requests.
   - Quota checks that return stale readings remain purely advisory; preserve reservations for uncertain execution.

---

For accepted worker tasks, use Memory > Recall activity to mark recalled evidence **helped**, **neutral**, or **harmful**, with a concrete note. Workflow receipts show individual judgments, confidence/probability, source IDs and cache status. Source changes or forgetting withhold stale proposals. If a conflicting candidate cannot fit the final packet, the retained claim carries an omitted-evidence warning; request more evidence before resolving it.

## 7. Primary Reference Documentation

- [Classifying RAG Passages (TypeSafe Cookbooks)](https://docs.typesafe.ai/cookbooks/classifying_rag_passages)
- [Confidence Estimation Guidance (TypeSafe)](https://docs.typesafe.ai/confidence)
- [Citation Check Patterns (TypeSafe Cookbooks)](https://docs.typesafe.ai/cookbooks/citation_check)
