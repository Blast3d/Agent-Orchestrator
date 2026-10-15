# Orchestrator audit: Brain memory and model switching

Audit date: October 14, 2026. This is an architecture and correctness audit of the
current Windows checkout, with targeted implementation and repository-wide local
regression testing. It is not a formal penetration test or a live multi-provider
qualification run.

Automatic orchestration was Off. One Codex agent performed this work directly;
no orchestration run, worker dispatch, new provider account, embedding request or
JEV inference was started. The source checkout already contained substantial
uncommitted work. Its initial source snapshot, hashes, Git status and patch were
retained under `output/audit-2026-10-14/` before edits. Existing changes were
preserved. This report records the audit phase; the user subsequently requested
publication of both these fixes and the earlier source changes.

## Findings and implemented changes

| Priority | Finding and reproduction | Change | Verification |
| --- | --- | --- | --- |
| Medium | Semantic candidates replaced existing keyword rows and assigned `1.5 + cosine`. This discarded lexical agreement and the complete-match boost. A weaker vector hit could beat an exact configuration path. | Fuse current keyword/vector ranks using reciprocal rank fusion with constant 60; retain complete-query priority and expose rank provenance. Keyword-only fallback remains unchanged. | `tests/test_brain_hybrid_fusion.py`: agreement, exact path, score-scale independence, invalidated sources, trace projection and fallback. |
| Medium | Recall cut the list to the requested result count before packing. An oversized episode could produce an empty result even when a smaller validated candidate fit. | Apply the delivered-result cap during packing, scanning only the existing bounded shortlist. Preserve source fields, character limits and omitted IDs. | A 950-character/one-result fixture now skips the oversized episode and returns the usable fact. |
| Medium | Resuming a frozen handoff resolved default models again before checking the saved contract. A changed Claude default or Codex lead selection turned the original assignment into a contract conflict. | Read the saved contract under the workflow lock. Reuse its selected models when the caller omits overrides; still validate current pause policy, contract hash, brief, assignment and supported model identity. | `tests/test_handoff_resume_models.py`: Claude/Codex preference changes reuse the original job; explicit changes, paused models and tampering hold; uncertain work is never repeated or reassigned. |
| Low | The global worker discovery mirror differed from maintained configuration. | Back up the previous mirror and synchronize only `.codex/model-workers.json` from `config/workers.json`. | Installation doctor: all 108 checks pass. |
| Low | Architecture documentation described mandatory fresh quota, a fixed Codex lead and pending historical integration checks. | Document configured/advisory admission, recorded leadership, separate local services and this dated validation record. | Source comparison against current admission, lifecycle and service code. |

The hybrid improvement applies when the existing authorized semantic connector
returns usable candidates. It does not enable that connector. The packing and
handoff-resume fixes also apply without embeddings. No Brain schema migration or
memory-content rewrite was needed.

The current installation has no `runtime/brain/semantic-config.json`, so hybrid
retrieval is implemented and tested but is not active in the live configuration.
Enabling embeddings remains a separate endpoint, project-disclosure and cost
choice; no such choice was inferred from this audit.

## Research and design decisions

Microsoft documents reciprocal rank fusion as a way to combine ranked lists whose
raw scoring scales differ. Brain now uses that technique locally, without adopting
Azure or adding a dependency. Its constant is a starting policy, not a claim of
optimal tuning for this corpus. [Microsoft: hybrid search ranking](https://learn.microsoft.com/en-us/azure/search/hybrid-search-ranking).

Anthropic recommends selective context retrieval, lightweight references and
structured notes for longer work. This supports preserving Brain's compact,
source-backed records and bounded context rather than importing full transcripts.
The packing change improves use of the existing budget without stripping
provenance. [Anthropic: effective context engineering](https://www.anthropic.com/engineering/effective-context-engineering-for-ai-agents).

LangGraph distinguishes thread checkpoints from durable cross-thread memory.
Orchestrator already has this useful separation: coordinator/task records own
execution state, while Brain owns reviewed knowledge. The implementation keeps
that boundary and repairs saved-plan continuity rather than introducing a second
execution framework. [LangGraph: persistence](https://docs.langchain.com/oss/python/langgraph/persistence).

LiteLLM documents ordered provider fallback chains and notes that another provider
cannot decrypt the original provider's encrypted reasoning state. The useful
pattern here is an explicit ordered route plus portable task/checkpoint evidence.
Orchestrator already carries supplied briefs and reviewed records, so it can keep
that vendor-neutral continuation contract. This audit repairs reuse of its frozen
model settings. It does not adopt generic retry-on-error behavior: uncertain CLI
execution needs reconciliation before another provider can safely start.
[LiteLLM: provider failover](https://docs.litellm.ai/docs/proxy/reliability).

Anthropic's long-running-agent guidance emphasizes explicit progress artifacts,
incremental changes and verification before declaring completion. Frozen task
receipts, exact-run checkpoints and acceptance evidence remain the continuation
contract here. [Anthropic: effective harnesses](https://www.anthropic.com/engineering/effective-harnesses-for-long-running-agents).

## Coverage and architecture assessment

| Area | Source and verification coverage | Assessment |
| --- | --- | --- |
| Startup and provider policy | Automatic switch, startup context, family coverage, lifecycle and their regression tests | Off remains Off; startup context and accepted contribution requirements remain separate from model selection. |
| Brain persistence | Store schema, reviewed sources, validity/supersession/forgetting, automatic capture and tests | Keep SQLite/FTS5. A read-only integrity check returned `ok`; this audit found no evidence requiring a new database service. |
| Retrieval and context delivery | Keyword/semantic/graph composition, JEV shortlist, packing, byte budgets, source revalidation and tests | Implemented rank fusion and packing fixes. Retained exact scope, bounded source checks and post-provider revalidation. |
| Dispatch and fallback | Brief checks, canonical results, assignment receipts, quota admission, cleanup, uncertain execution and fallback tests | Retained bounded, authorized worker chains. Transport success still awaits explicit review. No automatic retry of uncertain work. |
| Leadership switching | Lead selection, owner/session/generation checks, prepare/claim/transfer, launch-intent and quota tests | New-run preferences remain distinct from existing-run ownership. ASTRA and Sol continue sharing a Codex quota pool. |
| UI and local services | Brain/viewer/voice/Relay/VS Code behavior covered by repository Python and Node regression suites | Automated checks only; no claim of interactive browser, microphone, voice playback or user-workspace validation. |
| Packaging and discovery | Package/install tests and offline installation doctor | Repaired discovery drift. No release package or clean-machine installation was produced. |

Manual source inspection focused on the control path and memory/model boundaries
above. The full regression suite exercises the broader repository; passing it is
not proof that every line or every provider integration is defect-free.

## Validation evidence

Evidence lives in `output/audit-2026-10-14/` and is excluded from source publication.

- Before changes: 1,927 Python tests run, zero failures, seven skipped.
- Targeted surrounding regression set: 167 tests passed.
- Final standard-entry-point result: 1,940 Python tests run, zero failures, seven
  skipped because this Windows account cannot create the required file symlinks.
  Runtime: 192.707 seconds. See `final-cli-tests.json` and `final-cli-tests.log`.
- VS Code bridge and usage-monitor Node tests: 12 passed.
- Installation doctor: 108 checks passed after mirror repair.
- All seven changed/added Python source, test and benchmark files compiled;
  `git diff --check` passed.
- Existing offline retrieval benchmark: all 16 cases passed, zero provider calls.
- Real loopback `/api/search` smoke check with a temporary synthetic Brain:
  HTTP 200, expected fused first result and fusion metadata visible; zero provider
  calls. This is API evidence, not interactive browser or live-provider evidence.
- New controlled hybrid/packing benchmark: baseline 1/4, revised 4/4 expected
  first-result checks passed. Both preserved character bounds and made zero
  provider calls. These deliberately chosen regression fixtures do not measure
  real embedding quality, production answer quality, latency improvement or cost
  savings.

The first final-suite harness ran from stdin and failed one Windows multiprocessing
test because its spawned child could not import `<stdin>`. That failed attempt is
retained in `final-tests.log`; it was corrected by invoking the normal
`python -m unittest` entry point, without changing the application or that test.

To reproduce the controlled comparison:

```powershell
python benchmarks/brain_hybrid.py --output output/brain-hybrid.json
python benchmarks/brain_retrieval.py --output output/brain-retrieval.json
python -m unittest discover -s tests -p "test_*.py"
node --test tests/test_vscode_bridge.cjs tests/test_vscode_usage_monitor.cjs
python orchestrator.py doctor
```

## Remaining limits and follow-up decisions

1. Hosted model switching was tested with synthetic provider outcomes and real
   local receipts, not new live provider calls. Current authentication, allowance,
   provider-version compatibility and returned model identity need bounded live
   qualification when such a run is requested.
2. A frozen selector such as `opus` is still a vendor alias. Its provider-side
   resolution can change. Use an explicit supported version when reproducibility
   is required and retain returned model metadata; do not invent version mappings.
3. User-authored Brain memories prove reviewed provenance, not perpetual factual
   freshness. Existing validity dates, supersession and source review should be
   used for changing facts. Blanket TTL deletion would discard durable procedures
   and preferences, so it was not introduced.
4. Uncertain provider execution remains an operator reconciliation case. A local
   timeout, stopped process or changed model preference cannot prove the remote
   work stopped or authorize a replacement.
5. No automatic summaries, new vector service, local model, additional hosted
   route or background model scheduler was introduced. A representative retrieval
   evaluation should precede a storage/embedding-provider migration.
6. The saved Brain dashboard descriptor did not identify a live server at the
   final health probe. Its next launch and new CLI invocations load the changed
   source. Other long-running callers may retain imported code until restarted;
   this audit did not interrupt services or claim a live server reload.

## Review and recovery

The audit's source changes are limited to `brain_fusion.py`, `brain_recall.py`,
`brain_retrieval_metadata.py`, `task_handoff.py`, their two new regression files,
the new `brain_hybrid.py` benchmark and this audit/documentation update. The exact
audit-only diff and changed-file manifest are saved alongside validation evidence.

The pre-edit snapshot contains the user's initial dirty checkout, not merely Git
HEAD. If reversing these changes, compare against that snapshot and remove only
this audit's changes; a blanket Git reset would destroy unrelated work. The
previous discovery mirror is retained as `global-model-workers.before.json`.
