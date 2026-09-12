# Worker readiness diagnosis - September 8, 2026

Claude, Grok Build and local Ollama returned correct answers in new guarded tests. The earlier problems had several separate causes; they were not a general loss of access to every model.

The immediately preceding KM workstation skills package used native Codex helpers by coordinator choice. Claude, Grok and Antigravity were not attempted on that packaging task. Their absence from its contribution report is not evidence of a failed call.

## Current result

| Worker | Verified now | Remaining limitation |
| --- | --- | --- |
| Claude Code | Account quota read; exact synthetic answer passed in 2.34 seconds on low effort. Main response model: claude-sonnet-5. | This does not prove a large coding assignment will finish. Two earlier uncertain jobs retain reservations. |
| Grok Build CLI | Account quota read; exact synthetic answer passed in 4.85 seconds. Returned model: grok-4.6-build. | Separate from Grok Bot. One earlier uncertain job retains its reservation. |
| Local Ollama | Restarted existing service; exact synthetic answer passed in about 3 seconds, mostly model loading. | Small pinned Qwen model is suitable for basic extraction/chat. Earlier code-audit suggestions were rejected for quality. |
| Google Antigravity | Official quota-only usage reading succeeded. | General automatic dispatch remains held pending tool-permission validation. Supervised panel work succeeded previously. |
| Grok Bot desktop | Prior handoff evidence inspected; no new Bot task sent today. | Previous launcher exited without a usable window. Current separate Bot allowance is unknown; automatic external job control is not verified. |
| NotebookLM | Existing guard/configuration inspected; no new inference sent today. | Feature-specific allowance is unknown. No automatic verified quota reader is configured. |

At the monitor's verified refresh around 22:48 UTC, Claude's conservative weekly reading was 92% remaining and Grok Build's was 97%. After retained reservations, the guard allowed about 76% and 94% respectively for planning. Antigravity Gemini showed 82.26%. These are dated observations, not permanent balances. The latest machine-readable snapshot is in the evidence link below.

## What actually failed

1. **The monitor had stopped.** Its saved PID was no longer running. The last log entry reported an invalid-JSON parse error, consistent with the earlier damaged usage file. Recovering that file had not restarted the monitor. The original cause of the file corruption remains unknown. Stale readings caused the guard to hold new cloud work, as designed.
2. **Some account reads were intermittent.** The first Claude and Grok quota reads failed this session; a diagnostic retry and subsequent monitor refreshes succeeded. The initial UI failure was not captured precisely, so this is not a confirmed login or quota-exhaustion problem. The old guard hid recognized collector causes behind one generic message.
3. **Claude's later coding attempt timed out while thinking.** OpenWhispr service job `4fe475847ca044a98d1b06da06d4cb1c` reached its 600-second deadline with 805 streamed events and no answer text. Events were still arriving near the deadline. Local execution ended, but provider completion remained unknown. A separate earlier replay had completed in about 472 seconds, showing why the original 180-second cutoff was insufficient for that replay; simply increasing the deadline does not guarantee completion of every task.
4. **Grok Bot's desktop launcher did not produce a usable window.** The prior OpenWhispr B1 handoff explicitly recorded that no provider request was sent and no provider allowance was consumed. The work was reassigned. The working Grok Build CLI does not repair or validate this separate desktop route.
5. **Antigravity has a deliberate dispatch restriction.** Its allowance can be read, but plan mode alone does not disable tools. The general adapter remains held until that boundary is verified. This is different from a failed account or the retired local Gemini consumer-CLI route.
6. **Some starts failed before inference; some answers failed review.** Earlier malformed briefs and a missing output directory prevented jobs from starting. Later local-model source suggestions were rejected because they made unsupported claims or proposed existing features. These require different remedies from a broken connection.

Claude and Grok both have accepted prior audit and building artifacts. The historical task records therefore do not support saying that either provider never worked.

## Changes made in this session

- Restarted the hidden quota monitor, then verified a completed fresh cycle for Codex, Antigravity, Grok Build and Claude. The monitor was restarted again after the code change to load it.
- Restarted Ollama on the existing SSD-backed profile and tested the installed local model. No model download or dictation-service reconfiguration was needed. The dispatcher can also start this worker itself, so a stopped service alone was not a permanent dispatch failure.
- Updated `app/usage_guard.py` to preserve recognized quota-reader causes, including timeout, setup/trust, unverified freshness and missing executable. Arbitrary terminal output stays suppressed. Failed reads still hold new work.
- Passed 33 usage-guard tests and 6 quota-reader tests. New regression cases cover actionable timeout reporting, suppression of private/arbitrary output, and rejection of success-shaped output from a failed reader process.
- Passed the installation doctor after global synchronization; this verifies installation consistency, separately from the live response checks.
- Updated and installed the global orchestration skill's usage-protection and provider-routing references with these findings.
- Accepted all three new diagnostic responses after checking the exact expected JSON, and completed their individual contribution audits.

No old uncertain reservation was released. No billable fallback, automatic Antigravity dispatch or new Bot account link was enabled. Restarting the monitor is a recovery action; automatic crash recovery has not been added.

## Operating rules from this diagnosis

Report whether a worker was not selected, held before execution, failed during execution, returned an unacceptable answer, or completed accepted work. Check both quota eligibility and route readiness: the current `status` command's ready label describes the quota check, not the full adapter. Use small deliverables and appropriate reasoning effort for coding, validate each returned artifact, and preserve partial output and uncertainty before a handoff. Keep Grok Build and Grok Bot identities and allowances separate.

The remaining integration work is a supervised Grok Bot window/allowance check and validation of Antigravity's automatic tool boundary. A resilient monitor restart/watchdog and a unified route-versus-quota readiness display would improve reliability further; neither is represented here as implemented.

## Evidence and contribution breakdown

- [Current evidence snapshot](../.orchestration/worker-diagnosis-20260908/readiness-evidence.json)
- [Contribution report](../.orchestration/worker-diagnosis-20260908/contribution-audit.md)
- [Contribution data and formula](../.orchestration/worker-diagnosis-20260908/contributions-ledger.json)
- [Earlier Claude recovery investigation](CLAUDE-RECOVERY.md)

This diagnosis credits an estimated 97% of accepted work to Codex and 1% each to Claude, Grok Build and local Ollama. The three workers performed small diagnostic responses; Codex performed the investigation, repairs, test preparation and review. Those percentages are declared review estimates, not measured time, token cost, coding shares or the earlier panel's contribution totals. Raw private terminal captures stay in the local run folder and are not included in this report.
