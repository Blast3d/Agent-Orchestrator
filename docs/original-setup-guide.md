x  # Your model orchestrator

Codex leads the project, assigns bounded jobs to specialist models, reviews their
output, and delivers the final result. The global `multi-model-orchestrator` skill
is installed with automatic discovery enabled. Ask Codex to use the orchestrator
for a project; you do not need to run each model yourself.

## Open these

- [Usage Dashboard](Usage%20Dashboard.cmd): remaining allowance, held workers, and active reservations.
- [Local Chat](Local%20Chat.cmd): small local Qwen chat worker.
- [Gemini Chat](Gemini%20Chat.cmd): Gemini through Antigravity, with AI-credit overage disabled.
- [Grok Chat](Grok%20Chat.cmd): your signed-in SuperGrok CLI.
- [Unload Local Models](Unload%20Local%20Models.cmd): release Ollama's loaded model from GPU memory.
- [Start Usage Monitor](Start%20Usage%20Monitor.cmd) and [Stop Usage Monitor](Stop%20Usage%20Monitor.cmd): control this sign-in session's monitor.

These chat shortcuts are for your direct use. Codex uses the guarded dispatch
helper for orchestrated supplied-text jobs. Direct manual chat remains under your
control and can consume quota independently of reserved orchestration work.

## Who does what

| Worker | Role and verified access |
|---|---|
| Codex | Lead, architecture, implementation, integration, final checks |
| Gemini via Antigravity | Research, alternate reasoning and creative direction; headless response verified |
| Grok / xAI | Alternative coding review and drafting; real extraction and planted-bug checks passed |
| Claude / Anthropic | Narrative, code and independent review; account setup complete; session and weekly quota checks verified |
| NotebookLM | Source-grounded mind maps, presentation drafts and audio overviews; check the appropriate feature allowance |
| Local Qwen 4B Instruct | Basic chat, short summaries and prompted JSON extraction; two small capability checks passed |

Claude is an Anthropic model. Grok is from xAI and is distinct from Groq.
Different apps may run the same underlying model, so the roster records actual
model metadata when available. Codex remains the coordinator.

## Usage protection

The monitor refreshes official quota readers every five minutes and generates the
dashboard locally. It is configured to start at Windows sign-in. Silent Windows
notifications warn when a worker becomes low or its allowance cannot be verified.
Windows notification settings can hide a notification; the dashboard retains status.

- Warn at **20% available**.
- Preserve a **10% safety buffer**.
- Reserve another **1%, 3%, 8% or 15%** for tiny, small, medium or large tasks.
- Hold assignments when any applicable limit is insufficient, stale, unknown,
  or temporarily rejected by the provider.
- Count parallel tasks and models sharing the same account pool together.
- Refresh before and after cloud dispatch. Hold a completed reservation until a
  newer quota reading arrives, preserving completed output even if monitoring fails.

Percent estimates are initial planning allowances, not guarantees of task cost.
Codex should split long work into checkpoints and revise estimates from observed
usage. The guard cannot stop you using a provider in another application.

Automatic quota sources: Codex's official account quota protocol, Antigravity's
quota-free JSON command, and Grok's official terminal usage report. Claude's official terminal reader now verifies both session and weekly quotas. NotebookLM has separate feature counters and no verified consumer quota
API; Codex must obtain a fresh reading for the requested feature before dispatch.
Missing readings never mean unlimited usage.

The policy, current readings, reservations, and dashboard are under
`%USERPROFILE%\.codex\orchestrator`. `policy.json` holds thresholds. The Windows
sign-in entry is named `CodexModelUsageMonitor`. Stopping the monitor pauses it
until it is started again or the next sign-in.

## Local models and computer responsiveness

Your verified machine has an RTX 5090 with about 32 GB VRAM, a Ryzen 9 9950X3D2,
and 64 GB installed RAM. New Ollama weights are stored on the almost-empty X: NVMe:

- Model repository: `X:\AI-Models\Ollama`
- Ollama 0.33.3 runtime: `X:\AI-Tools\Ollama\v0.33.3`
- Chat alias: `codex-chat-light`
- Pinned base: `qwen3:4b-instruct-2507-q4_K_M`

The tested model used about 3 GB of GPU memory with 4K context. During the final
small benchmark, whole-GPU use peaked around 21.2 GiB out of 31.8 GiB, leaving
about 10.7 GiB free. Normal cold loading in that run took about 1.5 seconds;
first backend initialization was substantially slower. Results are task-specific.

Only one Ollama model and one request run at a time, using four CPU threads. It
unloads after two idle minutes. The eight-GiB scheduler allowance is not a hard
GPU memory reservation. Heavy simultaneous inference can still compete for GPU
compute; live dictation latency and coding responsiveness were not measured.

OpenWhispr's existing Gemma dictation cleanup uses a separate llama.cpp service.
Its settings and existing model files were left in place. The unload shortcut
affects Ollama, not that service. Ollama does not automatically start at sign-in;
the quota monitor does not load local models.

## Google and Grok setup notes

Google's old Gemini CLI consumer Code Assist route stopped serving requests on
June 18, 2026. Gemini works here through `agy` instead. The old Gemini login
shortcuts now open that supported route. [Google deprecation notice](https://developers.google.com/gemini-code-assist/docs/deprecations).

The official xAI-signed Grok Build 1.0.13 executable is installed under
`%USERPROFILE%\.grok\bin\grok.exe`, using your SuperGrok account. A live job
returned `grok-4.6-build` in model usage metadata. Extraction and all four planted
coding issues were correct; its creative outline needed a one-word trim. Treat
creative output as a draft for review.

Grok Bot is a separate persistent cloud teammate product. Its supported public
external job-control interface was not established. Grok-to-Cursor account linking
is permanent; no link, Bot trial, or on-demand purchase was performed here.
See [Grok research](grok-research.md) for supported routes and current plan details.

Saved validation: `local-model-validation.json`, `antigravity-gemini-validation.json`,
`grok-validation.json`, `guarded-grok-smoke.json`, `orchestrator-install-validation.json`,
and the quota adapter test/evidence files. These distinguish live checks from
synthetic tests and unverified app behavior.
