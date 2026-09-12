# Dispatcher audit changes

The supplied-text dispatcher now distinguishes a worker answering from Codex
accepting that answer. Successful execution produces `status: awaiting_review`,
`execution_status: succeeded`, and `review_status: pending`. A named reviewer must
check the answer against the task requirements and record a substantive note before
accepting or rejecting it. A nonempty answer is never automatically accepted.

## Task evidence and outputs

Each dispatch has a unique job ID and writes:

- `runs/tasks/<job_id>/record.json`: lifecycle, prompt SHA-256 and length, timestamps,
  worker, requested model/effort, actual model metadata, reservation and export status.
- `runs/tasks/<job_id>/result.json`: canonical answer, provider response, usage,
  lifecycle and review evidence.
- `workspaces/tasks/<job_id>/`: temporary provider task material where needed.

The requested output path must have an existing parent and is exclusively created
before a quota refresh or reservation. Concurrent dispatches cannot both claim the
same output. A placeholder identifies the owning job while work runs. Replacement
of the claimed file is detected before writing the answer. Canonical output is
independent of the export path: an export failure cannot discard the answer or skip
reservation cleanup. JSON state updates use flushed temporary files and atomic
replacement. They cannot guarantee recovery from every disk or power failure.

The requested export is a snapshot. Review changes the canonical result and task
record, not previously exported copies. Review is blocked while dispatch cleanup
is still running, preventing a late dispatcher write from erasing a review.

## Provider boundaries

- Claude uses the native executable directly, with safe mode, no tools, no Chrome,
  strict MCP configuration, no permission prompts accepted automatically, and
  remote-control startup disabled. Default model/effort are `sonnet`/`medium`;
  `--claude-model` and `--claude-effort` support explicit bounded changes. Actual
  returned `modelUsage`, `usage`, and the provider response remain available for
  review, so requested model aliases are not mistaken for verified model identity.
- Worker child environments remove Anthropic API/auth/base-URL variables and
  Bedrock/Vertex/Foundry routing switches, as well as Google and xAI API keys.
  Updater activity is disabled in the child. The parent environment is unchanged.
  Do not add Claude `--bare`: the installed CLI documents API-key authentication
  for that mode.
- Grok retains its direct official executable, no tools/subagents/web search,
  no updater, and account access route.
- Automatic Gemini/Antigravity dispatch is held before any provider activity.
  Antigravity plan mode does not enforce a tool-free boundary. Its manual interface
  and quota monitoring can still be used; automatic dispatch needs an independently
  verified isolation mechanism before being enabled.
- Local chat still calls the loopback Ollama profile. No cloud SDK or fallback was
  added to that path.

## Failure and recovery

Ordinary setup, provider-exit, malformed-result and export errors finish the quota
reservation. Provider rate-limit exits also set the guard cooldown. Finished
reservations remain accounted for until the guard has an appropriately fresh
official usage snapshot. Successful answers survive optional dashboard/notification
failures; cleanup errors are recorded separately.

Timeouts and interruptions are different: the dispatcher kills and reaps its direct
CLI child where possible and records its PID and termination evidence. A stopped
CLI does not prove remote inference stopped. Such tasks are `recovery_required`,
and their reservation stays active until the orchestrator confirms execution ended
and explicitly reconciles the reservation. Local Ollama request timeouts are also
uncertain because generation may continue in the server. A hard process crash may
leave a `running` record and active reservation; neither expires automatically.

The quota estimate is admission planning, not a provider-enforced spend limit.
Turn and wall-time bounds do not guarantee a maximum token or subscription cost.
There is no retry, provider switch, or paid fallback after failure.

## Commands

```powershell
python app/dispatch_worker.py claude --prompt-file brief.txt --output answer.json --task "Review supplied dispatcher code" --size small
python app/dispatch_worker.py review JOB_ID --decision accepted --reviewer Codex --note "Reproduced each reported defect and checked the source evidence."
```

`accepted` and `rejected` both require successful execution, completed cleanup, and
an unreviewed answer. A review does not finish an uncertain reservation. The review
note is evidence written by the reviewer; the application cannot establish that a
reviewer's factual claims are true.

## Validation

`python -m unittest discover -s tests -p test_dispatch_worker.py -v` covers 24
regressions without live model calls: exclusive output ownership and replacement,
invalid/export paths, canonical result survival, ordinary versus uncertain failure
cleanup, quota rejection, disabled automatic Antigravity, response versus acceptance,
actual model metadata, child environment isolation, native Claude flags, and direct
process termination. Root orchestration separately owns the live provider check.
