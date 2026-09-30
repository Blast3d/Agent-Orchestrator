# OpenWhispr voice gateway (opt-in migration path)

The gateway is an independent Agent-Orchestrator service. OpenWhispr sends text
and receives typed events; this service owns the existing guarded `run` dispatcher,
quota checks, Brain recall, worker selection and task records. It listens only on
`127.0.0.1`, uses a per-start bearer token in the current Windows user's
`%LOCALAPPDATA%\Agent-Orchestrator\voice-gateway.json`, and does not auto-start.

Start it in an Agent-Orchestrator terminal:

```powershell
python orchestrator.py voice serve --worker codex
```

`--worker` selects the initial worker from the current dispatcher roster. This
is a deliberate first integration slice, not automatic multi-agent planning or
model routing. The worker can be changed at the next gateway start. The gateway
uses the maintained dispatcher rather than invoking a provider from OpenWhispr.
The dispatcher runs under the stable `openwhispr` Brain project, with a stable
voice assignment ID. It returns unreviewed worker results as user-facing answers;
these are not automatically accepted into durable Brain memory.

In OpenWhispr, say `command switch to orchestrator`, then dictate a request.
The hands-free inbox stores its question and final events durably until read or
skipped. If the worker returns a typed clarification question, the next ordinary
dictation to the selected Orchestrator target answers it. `command answer
question` repeats the pending question. Direct Codex, Claude Code, Cursor,
Hermes and Discord routes remain separate. A gateway failure never falls through
to the previous focused window or a pinned contact.
Events recovered after an OpenWhispr restart enter the inbox silently; the user
can choose to replay them. The gateway accepts at most two active voice requests.

## Contract

- `POST /v1/requests`: `{schema_version:1, request_id, text, project_id}`. The
  request ID is idempotent; the same ID with changed content returns 409.
- `GET /v1/requests/{id}`: current status.
- `GET /v1/requests/{id}/events?after=N`: ordered events. Types are `accepted`,
  `started`, `question`, `answered`, `final`, `failed`, and `outcome_unknown`.
- `POST /v1/requests/{id}/answers`: `{question_id,text}`. A repeat of the exact
  answer is idempotent.

Every route requires `Authorization: Bearer <token>` from the descriptor and
rejects browser `Origin` requests. The gateway journal is
`%LOCALAPPDATA%\Agent-Orchestrator\voice-jobs.sqlite`; canonical dispatcher
tasks remain in Agent-Orchestrator's task store. If the service stops during an
active job, its state becomes `outcome_unknown` on restart. Inspect the canonical
task and provider state before deliberately issuing a new request.

## Current boundary and migration

This release provides the explicit target, dispatch connection and one-question
continuation. It does not move the established direct routes. Their migration
requires supervised parity checks for target selection, question handling,
readback, interruption, and failure behavior. Move one direct route at a time
behind an opt-in adapter after those checks. Do not infer live microphone, Piper,
provider, or UI behavior from the synthetic tests.

Rollback is to stop `voice serve` and say `command switch to codex` (or another
working direct target). The direct routes and their existing response watchers
are untouched. Pending Orchestrator events remain in the local journal and
readback archive for inspection.
