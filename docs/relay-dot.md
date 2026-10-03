# Relay: a ChatGPT dot working with the Orchestrator

Relay is the user's OpenAI dot (an always-on ChatGPT agent, created 2026-10-02
UTC). It runs on OpenAI's cloud computer, so it cannot read this PC's files or
call local commands directly. When the user grants it **Your computer** access,
it can create and control **local Codex threads** on this PC while the desktop
app is open, and those threads load the user's Codex MCP servers and skills.
There is no public API for sending messages to a dot. Anything that has to reach
Relay waits on this PC until Relay checks in.

The bridge treats Relay as a **collaborator**: it reads run state and exchanges
messages with the lead and with the user. It does not lead, take worker
assignments from the dispatcher, accept reviews, checkpoint or claim leadership.

The mailbox has three parties and Relay is the hub: `lead` ↔ `relay` and
`user` ↔ `relay`. The lead and the user never write to each other here.
`user` is Jacob speaking through OpenWhispr's opt-in Relay target.

```mermaid
flowchart LR
    R[Relay dot<br/>OpenAI cloud] -- "Your computer" access --> T[Local Codex thread<br/>on JakesPC]
    T -- MCP stdio --> M[relay_mcp.py]
    M --> B[(runtime/relay<br/>mailbox + RELAY_HANDOFF.md)]
    M -. read only .-> O[.orchestration runs<br/>runs/tasks records]
    L[Lead: Claude in VS Code<br/>or ASTRA / Sol in Codex] -- orchestrator.py relay --> B
    U[Jacob by voice<br/>OpenWhispr Relay target] -- relay send --from user --> B
```

## One-time setup

1. In ChatGPT, open Relay's profile, find **Your computer** under
   **Computers**, and choose **Allow access**. Keep the desktop app open and
   signed in while Relay works here.
2. The MCP server is registered in `~/.codex/config.toml` as
   `agentOrchestrator` (check with `codex mcp get agentOrchestrator`).
   It bridges this folder's runs and `C:\Users\jacob\openwhispr\.orchestration`.
3. The `relay-bridge` Codex skill (`~/.codex/skills/relay-bridge`) tells
   Relay's local threads how to use it. `scripts/install_global.py` reinstalls it.
4. Tell Relay something like: *"On my computer, open a Codex thread in
   Agent-Orchestrator, use the relay-bridge skill, and read the handoff."*

## Tools Relay gets

| Tool | Does |
| --- | --- |
| `relay_handoff` | Refreshes and returns `runtime/relay/RELAY_HANDOFF.md` |
| `relay_overview` | Lead switch and current/recent runs (no experiments) |
| `relay_run_status` | One run's checkpoint tail, linked tasks, brief excerpt, deliverables |
| `relay_read_messages` | Peeks at unread messages to Relay from the lead or Jacob (`"from": "user"`); does not consume them |
| `relay_ack_messages` | Marks exact `message_ids` read after the client receives them; safe to retry |
| `relay_thread` | Every direction, without changing read state |
| `relay_post_to_lead` | Relay→lead `note`, `question`, `request`, `handoff` or `reply` |
| `relay_reply_to_user` | Relay→Jacob `reply`, `note` or `question`; `reply_to` is required |

All tools are local and make no model calls. Run IDs must be exact folder names.
Messages are capped at 16 KB and the mailbox at 5 MB.

### Delivery and retry contract (MCP 0.2)

Both `relay_post_to_lead` and `relay_reply_to_user` require `client_ref`, an
operation ID of 8-128 letters, digits, `-` or `_`. Generate it once before the
write (a UUID works), retain it across a lost response or reconnect, and retry
with the same subject, body, destination, kind, run and reply target. Reusing a
sender/reference pair with different content is rejected. A deliberate new
message needs a new reference even when its text is identical. Old mailbox
records and existing valid references remain compatible; content alone is
never an idempotency key. Clients must refresh the MCP tool list after upgrade.

`relay_read_messages` now defaults to `mark_read: false`. After receiving the
result, call `relay_ack_messages {message_ids: ["the-message-id"]}` for just the
messages received. A lost read response leaves them unread; a lost acknowledgment
response can be retried. An unknown or other-recipient ID rejects the entire
acknowledgment. Explicit `mark_read: true` remains as a legacy compatibility
option but consumes messages before transport receipt and should not be used
for reliable delivery. Mailbox history is retained regardless of read state.

The CLI also peeks by default. `relay inbox --mark-read` preserves the old
behavior. Existing CLI writes without `--client-ref` still work, but are not
retry-safe; new callers should always preserve a stable reference.

**If a thread lacks the tools.** Relay's first local Codex child (2026-10-02) did not
expose user MCP servers. It read `RELAY_HANDOFF.md` and appended one JSON line to
`runtime/relay/mailbox.jsonl` under the `mailbox.lock` byte lock. The bridge accepts
such lines when they carry an integer `seq` (last seq + 1), `"from": "relay"`,
`"to": "lead"` or `"to": "user"`, a `kind`, `subject` and `body` (plus optional
`run_id`, `reply_to`, `id`, `at`). OpenWhispr only reads lines with an `id` of 6–64
letters, digits, `-` or `_`. Malformed lines are skipped, not fatal. When the thread can run commands,
`python orchestrator.py relay send --from relay ...` does the same with validation.

## For the lead (Claude, ASTRA or Sol)

```powershell
python orchestrator.py relay --workspace C:\Users\jacob\openwhispr handoff    # refresh the snapshot Relay reads
python orchestrator.py relay inbox --for lead                                 # unread Relay messages
python orchestrator.py relay ack --for lead --id RECEIVED_MESSAGE_ID          # after receipt
python orchestrator.py relay send --as claude --kind reply --reply-to ID --subject "..." --body "..."
python orchestrator.py relay send --as astra --kind handoff --run RUN_ID --subject "..." --body-file note.md
python orchestrator.py relay thread --limit 20
```

`orchestrator.py start` lists unread Relay messages whose `run_id` is that exact
run in a "Relay messages for this run" packet section (plus a count of unread
messages that name no run). `closeout` reports the same as a `relay_inbox` notice
with `blocks_completion: false`; it never holds completion. Listing does not mark
anything read. Quiet runs get byte-identical packets.

Read them with `relay inbox --for lead --run RUN_ID` before acting. Treat a Relay `request` like a
user-relayed idea: the lead decides, and normal content authorization,
allowance checks and review still apply. Mention accepted Relay contributions
in the contribution ledger as native work by "Relay (OpenAI dot, GPT-6 Astra)".

## Voice target (OpenWhispr)

`command relay` or `command switch to relay` selects Relay as the OpenWhispr
auto-send target ([relayTarget.js](../../../openwhispr/src/helpers/relayTarget.js)).
Each later utterance becomes a `user`→`relay` `request` written through
`relay send --from user --body-stdin --client-ref <request id>`. A repeated
`client_ref` with identical content returns the original message; conflicting
reuse is rejected. OpenWhispr polls `mailbox.jsonl` every 10 seconds while requests wait.
Answer references retain the legacy request/question pair when it fits and use
a stable SHA-256 reference for longer identities; persisted uncertain references
are reconciled unchanged, never blindly resent.

Relay answers Jacob with `"from": "relay", "to": "user"` and `reply_to` set to his
message id (from `relay_read_messages` or the handoff). `kind: "question"` keeps
the request open and is read as "Relay asks: … To reply, say command answer the
question." Only after Jacob says that does his next utterance go out as a `reply`
to the question's id, so answer that new id. Any other kind is the final answer,
read aloud once as "Relay says: …". Messages to `user` without a matching
`reply_to` are not read aloud. Replies already in the mailbox when OpenWhispr
starts are saved for Replay rather than spoken. Nothing wakes Relay; Jacob hears
"Relay last wrote N minutes ago" from `command where am I`.

Question arming and mailbox progress updates roll back in memory when their
state save fails. OpenWhispr still archives each source event before recording
mailbox consumption. The archive recognizes existing source IDs before history
is loaded and shows one canonical copy of legacy duplicated records. Explicit
read/skip retains a small hash-only event receipt, preventing a delayed mailbox
retry from resurrecting an acknowledged event. These receipts have no automatic
expiry; removing them while old pending requests remain can allow replay.
Durable source lookup currently scans archive records with bounded transient
text; a very large archive may warrant a recoverable on-disk index later.

Both readers number messages by file order: a line whose `seq` does not exceed
the previous line's takes the next number. Hold `mailbox.lock` when appending
directly anyway.

## What Relay can and cannot see

Relay sees run manifests, coordinator checkpoints, task summaries (worker,
status, review state), the first 4 KB of each brief, deliverable file names,
and the mailbox. The bridge does not return Claude or Codex transcripts, bearer
receipts, provider credentials or Brain records. A connected Relay thread
still has the file access Codex grants it, so keep secrets out of the
workspaces it uses.

## Not included

- A public ChatGPT connector or tunnel. It would expose this PC to the
  internet, and it needs separate approval.
- Push notifications to Relay. Relay finds out about new messages when it
  next checks in, or when the user tells it to look.

Tests: `python -m unittest tests.test_relay_bridge tests.test_relay_lifecycle`
(synthetic runs, a real stdio round trip, packet and closeout listing, no provider
calls) and, in OpenWhispr, `node --test test/helpers/relayTarget.test.js`.
