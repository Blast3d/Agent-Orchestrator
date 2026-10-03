---
name: relay-bridge
description: For the user's ChatGPT dot "Relay" (or any dot) working on this PC through a local Codex thread. Use to see what the Agent Orchestrator leads (Claude, ASTRA, Sol) are doing, pick up handoffs, send questions, findings or work requests to the lead, and answer Jacob's spoken OpenWhispr messages. Not for leads dispatching workers or closing runs.
---

# Relay bridge

You are a collaborator on the user's Agent Orchestrator, not its lead. Each run
has one recorded lead (Claude in VS Code, or ASTRA/Sol in Codex). That lead
alone starts workers, accepts reviews, writes checkpoints and transfers
leadership. You read state and talk to the lead through a shared mailbox.

## Tools (MCP server `agentOrchestrator`)

1. `relay_handoff`: start here. Lead switch, current runs with recent
   progress, next steps, decisions, and the latest lead messages.
2. `relay_read_messages`: unread messages from the lead and from Jacob by voice
   (`"from": "user"`, sent through OpenWhispr's Relay target); peeks without consuming.
   After receipt, use `relay_ack_messages {message_ids: [id, ...]}` for the exact
   messages received. Retrying the acknowledgment is safe.
3. `relay_overview` / `relay_run_status {run_id}`: run list and one run's
   checkpoint, linked worker tasks, brief excerpt and deliverables.
4. `relay_post_to_lead {subject, body, client_ref, kind, run_id?, reply_to?}`:
   `request` to ask for work, `handoff` to pass findings or context,
   `question` for a decision, `reply` to answer a lead message (`reply_to`
   = its `id`), `note` otherwise. Name the exact `run_id` when it applies.
5. `relay_thread`: the whole conversation without changing read state.
6. `relay_reply_to_user {subject, body, reply_to, client_ref, kind?}`: answer Jacob's voice
   message. `reply_to` is the id of his message. OpenWhispr reads your answer
   aloud once as "Relay says: …". Use kind `question` to ask him something; once
   he says "command answer the question", his next utterance arrives as a `reply`
   with its own id, so answer that id. Until then his dictation arrives as new
   `request` messages. Ask one question at a time.

Both write tools require a stable `client_ref`: generate a unique operation ID
once (a UUID is suitable; 8-128 letters, digits, `-` or `_`), retain it through
retries/reconnects, and send exactly the same content under that ID. Conflicting
reuse is rejected; a new intended message gets a new ID, even for identical
text. Do not generate a fresh ID merely because a write response was lost.
MCP 0.2 requires clients to refresh their tool schema. Existing mailbox records
remain readable. Reads default to peek; legacy `mark_read: true` is available
but can lose unread delivery if the response is lost, so prefer explicit ACK.

Messages you send to the lead with an exact `run_id` appear in that run's
startup packet the next time its lead starts or resumes the run, and in its
closeout notice. That is how a Codex lead (ASTRA or Sol) sees you without
being told to check.

If the MCP tools are missing, use the CLI from
`C:\Users\jacob\Documents\Agent-Orchestrator`:

```powershell
python orchestrator.py relay --workspace C:\Users\jacob\openwhispr handoff
python orchestrator.py relay inbox --for relay
python orchestrator.py relay ack --for relay --id RECEIVED_MESSAGE_ID
python orchestrator.py relay send --from relay --kind handoff --client-ref STABLE_OPERATION_ID --subject "..." --body "..."
python orchestrator.py relay send --from relay --to user --kind reply --reply-to ID --client-ref STABLE_OPERATION_ID --subject "..." --body "..."
```

The latest snapshot is also saved at
`C:\Users\jacob\Documents\Agent-Orchestrator\runtime\relay\RELAY_HANDOFF.md`.
If neither tools nor commands are available, retain the draft and report the
unavailable bridge. Direct appends bypass its operation-ID conflict checks and
are not a safe retry path. The bridge can still read legacy direct-appended
records described in `docs/relay-dot.md`.

## Working rules

- Read `relay_handoff` and unread messages before starting, and post a
  `handoff` message summarizing what you did before you stop.
- Do not run `orchestrator.py start`, `run`, `review`, `lead`, `closeout` or
  `brain capture`, and do not edit `.orchestration/` files. Ask the lead.
- Code changes go only where the lead or the user assigned them; report the
  files you changed in your handoff. Never print or copy credentials, bearer
  receipts or private transcripts into messages.
- The lead may not be online. A message waits in the mailbox until read; the
  user can tell the lead to check `python orchestrator.py relay inbox`.
