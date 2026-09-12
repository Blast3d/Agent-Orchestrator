# Useful additions and what "free" means

Research snapshot: September 7, 2026. Recheck official pages and the account's
actual access before setup; quotas, offered models, and terms can change. The
user will acquire tools as needed. This list is a selection guide, not permission
to install software, download large weights, or subscribe to a service.

## Best additions to consider first

1. **Gemini through Antigravity CLI:** another cloud model family through Google's
   supported consumer CLI. Suitable for research, synthesis, and independent
   review. The individual plan offers baseline quotas; check account usage rather
   than assuming a fixed daily request count. Keep AI-credit overage disabled.
   Gemini CLI's former consumer Code Assist route stopped serving requests on
   June 18, 2026, so its old free-login recommendation no longer applies.
   Developer API access has separate quotas and data terms.
   [Plans](https://antigravity.google/docs/plans),
   [credit controls](https://antigravity.google/docs/cli/credits/),
   [consumer shutdown](https://developers.google.com/gemini-code-assist/docs/deprecations).

2. **Ollama, or LM Studio if a visual model manager is preferred:** local inference
   from downloaded models, with no per-token hosted bill for that local path.
   Model quality/speed depend on hardware, quantization, and the selected model;
   the runtime alone does not provide a strong coder or reviewer. Start with one
   suitable model after checking available RAM/VRAM. Both have local APIs.
   Do not confuse Ollama cloud routing or optional LM Studio features with local
   inference. Ollama's `OLLAMA_NO_CLOUD=1` is an explicit local-only control when
   configuring it for that purpose.
   [Ollama on Windows](https://docs.ollama.com/windows),
   [Ollama privacy/local controls](https://docs.ollama.com/faq),
   [LM Studio API](https://lmstudio.ai/docs/developer),
   [LM Studio offline use](https://lmstudio.ai/docs/app/offline).

3. **OpenCode:** an optional common agent interface for different providers and
   local models, useful if managing several separate coding CLIs becomes awkward.
   It supports programmatic runs and configurable agent models/permissions.
   The open-source software is free; the chosen inference backend may not be.
   Pair it with a verified free or local route. It remains a worker under Codex,
   not a replacement orchestrator. Use installed-version help: stable and beta
   documentation may differ.
   [Providers](https://opencode.ai/docs/providers/),
   [CLI](https://opencode.ai/docs/cli/),
   [agent configuration](https://opencode.ai/docs/agents/).

## Additional API and coding options

| Option | Useful role | Free boundary and official source |
| --- | --- | --- |
| Grok / xAI | Grok Build CLI for bounded coding/review; Bots for persistent app-driven tasks | Distinct from Groq. Free web Build availability does not establish free CLI or Bot entitlement. Verify account access; Bots may use paid Cursor access, eligible linked subscriptions, or a finite trial. [CLI](https://docs.x.ai/build/cli/reference), [Bot plans](https://cursor.com/help/grok-bot/plans) |
| Groq API | Fast short critiques, extraction, structured transformation using supported models | Free quotas vary by model/account; inspect token and request limits. [API compatibility](https://console.groq.com/docs/openai), [limits](https://console.groq.com/docs/rate-limits) |
| OpenRouter | Experiment with multiple model families through one API | Use explicit `:free` variants/free routing. Current FAQ lists 50 free-model requests/day without qualifying credit purchases; availability varies. Pin a specific model for reproducible comparisons. [FAQ](https://openrouter.ai/docs/faq) |
| GitHub Copilot CLI | Optional coding/review worker if the user wants another installed interface | Current Free plan includes CLI with limited AI credits and automatic model selection; verify individual eligibility. [Plans](https://docs.github.com/en/copilot/get-started/plans), [CLI](https://docs.github.com/en/copilot/concepts/agents/copilot-cli/about-copilot-cli) |
| Aider | Narrow code edits through configured cloud or local models | Free client software; inference separate. Scriptable message files and disabling automatic commits suit bounded work. [Models](https://aider.chat/docs/llms.html), [scripting](https://aider.chat/docs/scripting.html) |

NotebookLM already provides useful source and creative artifact roles. Keep it
in the roster where account quotas permit. Claude Code/Antigravity can use the
user's existing access, but do not label a subscription or an unknown account
entitlement as a free API. Cursor is not a dependency.

## Data and cost routing

Free cloud inference can have different data policies from paid service or local
execution. Gemini Developer API pricing marks some free-tier content as used for
product improvement. Check the chosen route before sending private source text:
[Gemini pricing](https://ai.google.dev/gemini-api/docs/pricing).

OpenRouter forwards prompts to selected providers, whose retention rules vary;
preserve configured privacy filters. Groq documents default handling and
feature/abuse exceptions. Do not assume an OpenAI-compatible API implies OpenAI
hosts the data or the same policies apply.
[OpenRouter data handling](https://openrouter.ai/docs/guides/privacy/data-collection),
[Groq data handling](https://console.groq.com/docs/your-data).

Add one useful capability at a time. Test the exact interface with a small,
nonsensitive task, record its actual model and cost category, then give it a real
bounded role. An agent framework or API gateway is valuable only if it simplifies
dispatch, isolation, or monitoring; it does not create model diversity by itself.
