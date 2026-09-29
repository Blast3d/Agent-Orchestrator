# Jev through OpenRouter

Jev can rank the Brain's recalled memories and recommend which permitted worker
fits a task. The connector is off until you configure it. The key is read from
your local `.env` or process environment. Existing source review, permissions and
dispatch checks remain in force.

## Set up your account and key

If your key already lives in another application's .env, pass its path with
--env-file so there is no second key copy. Check jev status before changing
an existing installation: configure replaces the entire project and purpose
allowlist. The steps below describe a new installation.

See [memory profiles, bot delivery and the use-case map](jev-memory-workflow.md)
for how to use the connection.

1. Create an [OpenRouter account](https://openrouter.ai) and a dedicated
   [API key](https://openrouter.ai/settings/keys). Set a small key spending limit.
2. Copy [`.env.example`](../.env.example) to `.env` in the same folder as
   `orchestrator.py`. Put your key after the equals sign in **`.env` only**:

```dotenv
OPENROUTER_API_KEY=your-key-goes-here
```

Git ignores `.env`; the example must stay blank. The connector reads just this
named key, without executing shell syntax or exporting it to worker processes.
An existing process environment key takes precedence. As an alternative to a
file, enter the key through a hidden prompt in PowerShell:

```powershell
$env:OPENROUTER_API_KEY = [System.Net.NetworkCredential]::new('', (Read-Host 'OpenRouter API key' -AsSecureString)).Password
```

3. Enable only the synthetic demo, then test one request. This uses your
   OpenRouter credit; the demo contains no saved memories or project source.

```powershell
python orchestrator.py jev configure --project jev-demo --purpose route --daily-calls 10 --enable
python orchestrator.py jev smoke
```

An `ok` response proves a request completed; check the returned model, provider,
usage and cost. It does not establish memory relevance or routing quality.

4. When ready to send this project's queries and recalled memory excerpts through
   OpenRouter to TypeSafe, explicitly enable its two purposes:

```powershell
python orchestrator.py jev configure --project agent-orchestrator --purpose memory_rank --purpose route --daily-calls 100 --enable
python orchestrator.py jev status
python orchestrator.py jev recall "How does memory retrieval work?" --project agent-orchestrator
```

`configure` replaces the allowed project/purpose list. Repeat `--project` for each
additional project you authorize. Omit `--enable` to save disabled settings.
Repeat `--env-file` when reconfiguring if your key lives outside this folder.
Restart any already-running Brain/viewer services after this code update. The
`.env` key is read on each eligible lookup, so later key edits do not need a
restart. If you use the environment alternative, launch the services from that
PowerShell session. The connector does not register a startup task.

Turn it off at any time:

```powershell
python orchestrator.py jev disable
Remove-Item Env:OPENROUTER_API_KEY -ErrorAction SilentlyContinue
```

## Brain behavior

Keyword, relationship and any configured semantic lookup run first. Jev scores
a task-sized shortlist of up to 16, 48 or 100 reviewed memories before packing
at most 6, 12 or 24 respectively (see [recall budgets](brain.md#adaptive-retrieval)). It cannot
introduce a memory outside the locally retrieved shortlist. Scores meeting the 0.8 confidence
threshold reorder only the confident positions; uncertain baseline positions remain fixed.
Fewer than two confident scores preserve the baseline. Missing credentials, exhausted daily call slots,
network failures and malformed answers also preserve baseline retrieval.

Each enabled lookup sends the query plus candidate IDs, titles and bounded content
excerpts. Network calls run outside the Brain lock. Candidates are checked before
the call and again afterward for scope, validity, accepted sources and unchanged
content. Forgotten or changed memories cannot return through a stale score. These
validation phases inspect only the bounded shortlist. Each request stays within
16 KiB and at most sixteen candidates; wider recall makes at most eight batches
with three concurrent requests. Cache identity includes each batch's current
source fingerprints. Failed batches preserve their baseline positions. Conflict
checks compare a small set of pairs within each batch, not every possible pair.

The Brain's **How this memory was found** panel and recall receipts show whether
Jev was applied, the model, elapsed time and provider-reported charge when known.
Provider confidence is a model estimate; it is not proof a memory is correct.

## Orchestrator routing advice

Create a small JSON file containing only workers you currently permit, with
capability descriptions. For example, `workers-for-jev.json`:

```json
{
  "claude": "Review code and draft implementation explanations.",
  "grok": "Provide an independent technical review."
}
```

```powershell
python orchestrator.py jev route --project agent-orchestrator --task "Review this task's retry design" --workers-file workers-for-jev.json
```

The result includes `recommended_worker`, or null when deferred or unavailable.
This command does not launch a worker. The lead still checks current readiness,
content permissions and allowance through the existing guarded dispatcher.

## API and limits

The adapter uses `POST https://openrouter.ai/api/alpha/decisions` with
`typesafe/jev-1.13`. This is the Decisions API, not chat completions. It requires
no new SDK package. See the [official OpenRouter Jev guide](https://openrouter.ai/blog/insights/what-is-jev/)
and [current model pricing](https://openrouter.ai/typesafe/jev-1.13).
On September 25, 2026 the listed price was $0.042 per million input tokens, with
no output-token charge. Recheck pricing before budgeting.

Local settings are in `runtime/jev-config.json`; the UTC-day request counter is
`runtime/jev-usage.json`. Every attempted HTTP request consumes a slot, including
uncertain failures. Request bodies are capped at 16 KiB, responses at 64 KiB, and
the default timeout is 10 seconds. There are no automatic retries, redirects or
model/provider fallbacks. A daily call limit is **not a dollar cap**; use the
OpenRouter key limit for spending control. Missing usage remains unknown.

Offline tests use temporary SQLite memories and mocked HTTP. They validate
integration behavior, not live Jev quality, account access or performance gains.

## Production workflows

[The workflow guide](jev-workflows.md) covers the fourteen explicit evidence, curation and orchestration workflows, optional combined passage flags, and verified decision cache. Memory > Jev workflows exposes the same reviewed-source service. Configuration replaces the full allowlist; use the complete example in that guide to retain all enabled purposes.
