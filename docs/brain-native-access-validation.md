# Native Brain access review, October 24, 2026 UTC

The native access, session inbox, recall packing and optional Jev choice changes
were reviewed against the working implementation. Claude Code's existing Brain
registration connected successfully. Codex's Brain server and Claude Code's
SessionEnd capture hook were configured locally while preserving unrelated
settings and speech hooks. Those machine-specific files are outside this repo.

A fresh stdio subprocess exposed only `brain_projects`, `brain_search` and
`brain_get`. Real scoped recall returned reviewed memories, an unlisted project
was refused, and no Jev request was made by that probe. An isolated SessionEnd
subprocess produced a pending candidate without activating a memory. Native
clients still need a new session to load changed MCP configuration.

Review found that `brain_get` could retain a relationship after the peer memory
expired. A regression reproduced the defect before the fix. Related memories
now receive the same scope, current-state and source checks as the requested
memory.

## Compact ranking comparison

Both sides used compact depth, the same frozen labels and queries, existing
authorized projects and purposes, cache settings, and the 100-request UTC daily
limit. Labels were selected by Codex after reading the full reviewed memories;
they are not independent human judgments. All 70 drafts were reviewed: 67 were
labeled and three lacked a specific answer. A reproducible hash selection took
24 labeled cases per project, before provider results were available.

| Measure | Rubric | Choice |
| --- | ---: | ---: |
| First answer relevant | 35 / 48 | 36 / 48 |
| Relevant answer in first three | 40 / 48 | 41 / 48 |
| Relevant answer returned | 47 / 48 | 46 / 48 |
| Median lookup | 2,396 ms | 2,532 ms |
| Ranking applied | 22 / 48 | 15 / 48 |
| Provider attempts | 48 | 33 |
| Provider-reported cost, USD | 0.010665 | 0.007954 |

Choice had 15 `sensitive_input` holds before transmission; those cases retained
local order. Rubric had 26 low-confidence fallbacks and choice had 18. The single
first-answer gain, lower returned-answer coverage and unequal provider exposure
do not establish a clear quality improvement. **Rubric remains the default.**
No Jev reconfiguration was performed, so existing flags and limits were retained.

The query files, labels, selection hash, raw results and local setup receipts
remain under ignored `output/brain-review/`; private memory contents are not part
of this source change. The comparison is a bounded current snapshot, not proof
of general accuracy, user benefit or repeatability. No live user audio or client
restart was part of the native access probe.
