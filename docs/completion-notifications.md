# Completion calls and questions through OpenWhispr

The Orchestrator can send a lead's complete final report or an explicit question
to the local OpenWhispr notification service. OpenWhispr owns the approved Discord
contact, Call/Busy/Off setting, greeting, availability answer, Piper routing, and
message delivery. The Orchestrator neither holds Discord credentials nor calls
Discord directly. The dedicated settings screen and inbound Discord reply
interface are deferred.

This producer is **off by default**. It requires an OpenWhispr build supporting
`/v1/completion-notifications`, configured with the approved recipient and an
active Away profile. OpenWhispr's `command busy` / `command message only` changes
future deliveries to messages without changing the Orchestrator setting.

For project completion calls, configure OpenWhispr's authenticated local endpoint
`PUT /v1/completion-notifications/settings` with this setting:

```json
{"mode":"call","trigger":"project","contactAlias":"<approved-recipient-alias>"}
```

`trigger: "project"` keeps automatic final-turn responses in the inbox without
calling or speaking them. Explicit verified Orchestrator project reports trigger
the call; questions still use messages. OpenWhispr's alternative `trigger: "turn"`
supports ordinary lead-turn notifications. Use project mode for this workflow to
avoid two different notification sources reporting the same result.

## Enable and inspect

```powershell
python orchestrator.py notifications enable
python orchestrator.py notifications status
python orchestrator.py notifications disable
```

Enable persists across Orchestrator launches and applies to future explicit
notifications. It does not scan old runs, replay old reports, launch providers,
or start a background service. Disabling prevents new admissions; an already
admitted OpenWhispr delivery is controlled by OpenWhispr's mode.

Local settings and attempt metadata live outside the checkout at
`%LOCALAPPDATA%\Agent-Orchestrator\completion-notifications.sqlite`. The producer
stores IDs, content hashes, types and admission outcomes, without full report
text, contact IDs or tokens. OpenWhispr separately persists the full delivery
content in its private profile. Preserve both histories across upgrades.

## Finish a project

Write the lead's complete final answer to a UTF-8 file, including important
limitations, then use the maintained closeout command with the current identity:

```powershell
python orchestrator.py closeout --run <exact-run-directory> --owner astra --session <current-session> --generation <current-generation> --final-report final.txt
```

All existing closeout checks must pass, including current lead ownership,
accepted work, memory receipts, contribution audit and map. The completed run is
saved before attempting notification. A held closeout does not call or message.
Notification failures never withdraw an otherwise valid completed result.
The command's returned `completion_notification` is separate from the canonical
closeout evidence; inspect it and `notifications status` for delivery admission.

The equivalent explicit command re-runs the same closeout checks; it does not
trust an old `completed` flag:

```powershell
python orchestrator.py notifications notify result --run <exact-run-directory> --owner astra --session <current-session> --generation <current-generation> --text-file final.txt
```

There is no notification when `--final-report` is absent. Raw worker completions,
partial responses, tool output and helper reviews do not invoke this hook.
One stable ID, `orchestrator:<run-id>:final`, represents the run's final report.
Repeated commands with identical content cannot place another call or message.
Changing text, project title or type under that ID is rejected.

## Ask a question

Use a deliberate stable question ID within the exact current unfinished run:

```powershell
python orchestrator.py notifications notify question --run <exact-run-directory> --owner astra --session <current-session> --generation <current-generation> --question-id drawing-choice-1 --text-file question.txt
```

OpenWhispr sends questions by Discord message even in Call mode. A question ID
cannot be reused for different content. A genuinely new question needs a new ID.
Questions require current lead ownership and a planning/in-progress run. Discord
replies do not automatically resume or answer a run; that interface is separate.

## Delivery boundaries

- The discovery file is `~/.openwhispr/cli-bridge.json`, or the explicit
  `OPENWHISPR_CLI_BRIDGE_FILE` override. Only the documented version/port/token
  fields are accepted. The destination is always literal `127.0.0.1`, ports
  8200–8219. Proxy environment settings and redirects cannot reroute the token.
- The producer commits an attempt before making one authenticated local POST.
  `accepted` means OpenWhispr durably admitted it, **not that Discord delivered
  it or the user heard it**. Inspect OpenWhispr for delivery status.
- Timeouts, interrupted processes, invalid receipts, inactive OpenWhispr and
  uncertain outcomes stay held. Status and re-enabling never retry them. No
  automatic retry or reset command is provided; inspect both histories before
  any deliberate recovery. Do not erase history or invent a new identity to
  retry a possibly delivered report.
- The ledger stops at 1,000 identities instead of pruning deduplication history.
  Input is bounded to 100,000 UTF-16 code units, matching OpenWhispr. Full UTF-8
  text, Markdown, line endings and links are preserved; nothing is summarized or
  silently truncated by this producer.
- Keep OpenWhispr's trigger set to `project` for this workflow. Distinct IDs across
  unrelated turn and project events cannot deduplicate each other; project mode
  prevents automatic final-turn calls alongside the explicit project report.

Automated checks validate local producer, admission, recovery and closeout
behavior. Actual Discord calling, phone audibility and availability recognition
need a live test in the selected OpenWhispr build.

## Rollback

Run `python orchestrator.py notifications disable` and omit `--final-report`.
Keep the private attempt ledger. No worker or provider adapter changes are
required, and ordinary closeout remains available independently of OpenWhispr.
