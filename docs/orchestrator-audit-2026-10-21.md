# Orchestrator audit: Gemini participation, balanced workers and rule efficiency

Audit date: October 21, 2026. Run `bot-voice-and-team-balance-20261022T020611Z-abf8443e`, led by
Claude (Opus 5.5). Codex, Grok and Gemini each delivered three accepted tasks: Codex two
implementations and a review, Grok an implementation, an assessment and a review, and Gemini an
assessment and two reviews. Gemini also ran one synthetic boundary verification that earns no credit.
The same run fixed OpenWhispr readback for Claude's helpers; see that repository's handoff.

## Why Gemini stopped getting work

Gemini's last accepted task before this run was on October 9. From October 10 until this run,
task records show 14 successful Claude tasks, 12 Grok, 5 Codex and none for Gemini (two held).
Six causes combined:

| Cause | Evidence |
| --- | --- |
| The Antigravity transport capped a request at 12,000 bytes while every request carried the 5.8 KB coordinator guide, leaving about 6 KB for the task. | Four Gemini tasks on October 7 and 10 held with "The operating guide and task exceed this worker transport". |
| Closeout needed only two families, which Claude or Codex plus Grok always met. | `provider_policy.MINIMUM_FAMILIES = 2`; nothing named Gemini. |
| Gemini could never be a quota alternate. | `usage_guard.validate_policy`, `task_handoff.WORKERS`, `--fallback-worker` choices and `runtime/policy.json` all listed only Claude, Codex and Grok. |
| The agent had a fixed 90-second deadline, against 2/7.5/15/22.5 minutes for other workers. | `antigravity_boundary.command_for` passed `--print-timeout 90s`. |
| The rules discouraged balance. | The stored routing preference named only OpenAI, Claude and Grok; `provider-balance.md` said not to target equal shares. |
| The pinned model was a middle tier. | `agy models` lists `gemini-3.8-flash-high` above the pinned `-medium`. |

## What changed

| Change | Where | Verification |
| --- | --- | --- |
| Policy schema 2: every non-lead family must deliver accepted work, with accepted task counts within `max(1, ceil(0.15 x highest))`. Runs started earlier keep their recorded two-family policy, including on resume. | `provider_policy.py` (written by Codex), `orchestration_lifecycle.py`, `run_operations.py` | Policy, family-gate and lifecycle suites, including legacy-resume and unbalanced-hold cases. |
| `orchestrator.py balance --run RUN` and `run auto`, which picks the least-used eligible non-lead family, Gemini on ties. | `team_balance.py` (written by Codex), `dispatch_worker.py`, `orchestrator.py` | 19 balance tests; used live in this run. |
| Workers receive a 957-byte worker guide instead of the 6.5 KB coordinator guide; both hashes are recorded and rechecked before execution. | `app/assets/worker-guide.md`, `orchestration_context.py`, `dispatch_worker.py` | Dispatcher tests; every worker request in this run. |
| Gemini runs `gemini-3.8-flash-high`. Its proof lists both verified tiers after a denial regression of the new tier on the same pinned executable, hook and configuration; MCP and handler-timeout proof is inherited and labelled. | `antigravity_boundary.py`, `config/antigravity-boundary.json` | Probe job `63ebb491...`: seven tool attempts denied with the exact hook message, no marker, canary absent, hashes unchanged. |
| Gemini requests may be 28,000 bytes instead of 12,000, the quoted Windows command line is checked exactly in UTF-16 against a 32,000-unit cap, and the deadline follows task size with a 15-second margin. | `antigravity_boundary.py`, `memory_delivery.py` | Boundary tests; live reviews with 8 KB and 20 KB briefs on the high tier. |
| Each hosted worker has two automatic alternates and Gemini is always reachable: Claude and Codex try Grok then Gemini, Grok tries Gemini then Codex, Gemini tries Grok then Codex. | `usage_guard.py`, `task_handoff.py`, `runtime/policy.json`, `config/workers.json` | Handoff test of a quota-held primary handing off to Gemini. |
| The planner plans at least one assignment per non-lead family and prints a near-equal allocation. | `team_planner.py` | Planner tests; `team --type audit` prints gemini 3, grok 3, codex 2. |
| Finished dispatched workers are announced to OpenWhispr when a Claude Code lead runs the dispatcher (loopback only, no proxy, redirects refused, never affects the task). Opt out with `AGENT_ORCHESTRATOR_WORKER_READBACK=off`. | `worker_readback.py` (written by Grok) | 13 tests including a redirect that must reach nothing; live 404 receipts against the not-yet-restarted app. |
| Rules text rewritten for the new policy. | Operating guide, skill and references, `README.md`, `docs/startup-and-closeout.md`, generated global instructions | `install_global.py`, then `doctor`: 108 checks pass. |

## Do the worker tool limits help?

Grok's verdict, checked against the command lines and history, was to relax them for some tasks only.
**Keep them for ordinary work.** Supplied-text-only workers cannot read private files, follow prompt
injection from fetched pages or create new billable tool routes, and every request's input is
recorded. The cost is accuracy: workers can treat omitted code as missing, and the lead spends time
preparing excerpts. This run lowered that cost without new access: the worker guide saves about
5.5 KB per request compared with sending the coordinator guide, Gemini's room for the task grew from
about 6 KB to about 27 KB, and the worker guide states that unsupplied material is unknown.

Round 3's four reviews were built from exact diffs and numbered excerpts. They returned 18 findings,
and 15 were adopted after checking the source. One of the three rejections shows the remaining cost:
Gemini reported a dropped speaker role that the renderer actually forwards, in code outside its
excerpt. The other two rejections flagged an intentional existing default and proposed ranking
`run auto` by accepted tasks (see Limits).

A future read-only repository mode is plausible for code review, but only after per-provider canary
tests prove that files outside the allowed folder, private paths, writes, shell escapes, web fetches and
delegation are all denied. Web search stays off by default; when a task needs public sources, the lead
fetches them and supplies the text.

## Rule efficiency

- A resume with `start --no-memory` no longer needs `--query`; this run's two reloads took 139 ms
  and 141 ms with zero provider calls, against about 7 seconds and four relevance calls for a recall
  reload.
- A new run whose recall query would be invalid is refused before any folder is created. The earlier
  behavior left an orphan active run (`bot-voice-and-team-balance-20261022T020551Z-f1894721`), kept as
  evidence.
- Gemini's findings F1 to F5 (worker-only guide, cheap reload, query validation, Gemini fallbacks,
  size-based deadline) were all adopted.
- Independent assignments ran in parallel: three in round 1, two in round 2 and four in round 3.

## Defects found by cross-family review

- Codex: urllib keeps `Authorization` across redirects, so a misbehaving loopback listener could have
  forwarded the bridge token. Fixed and covered by a mutation-checked test.
- Grok (7 of 8 adopted): resumed legacy runs were silently upgraded to policy 2, and stale receipts
  fell back to it (fixed through `policy_for`); the planner floor, the balance scan's truncation flag
  and the guide's rounding were also corrected.
- Gemini (7 of 9 adopted across two reviews): spoken helper names could voice local paths and
  usernames; a late `SubagentHandback` could be lost; an empty transcript counted as settled; the
  shared helper voice could match Claude's voice. All fixed in OpenWhispr.

## Verification

- Agent-Orchestrator: full suite 2,015 tests, 0 failures, 10 skipped; `doctor` 108 of 108 checks.
- OpenWhispr: full suite 5,824 tests, 0 failures, 265 skipped, run from `C:\Users\jacob\openwhispr`.
  From a lowercase `c:\users\...` working directory, 35 component and hook files fail and one hangs
  because Node loads React twice; that lesson is in OpenWhispr's `docs/agent-lessons.md`.
- Live: the high-tier denial probe passed, the startup reload took 141 ms, and worker readback reached
  the running OpenWhispr, which answered 404 because it predates the new route.

## Limits

- No live closeout of a pre-change run, live `SubagentStop` firing or audible OpenWhispr playback was
  observed here; OpenWhispr needs a restart to load its new route and voice.
- The high tier inherits the MCP and handler-timeout checks rather than rerunning them.
- `run auto` ranks families by dispatched, non-rejected tasks, so a family whose reviews are pending is
  not over-assigned; closeout still balances accepted tasks.
- After a lead handoff, the required families follow the new lead once its startup reloads.
