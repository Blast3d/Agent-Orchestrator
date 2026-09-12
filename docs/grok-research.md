# Grok orchestration research

Verified from official public sources on 2026-09-07. Research only: no installer or model was executed, no account was accessed, and no global skill was changed. The user confirmed a free Grok account; do not assume a subscription or enable paid access.

## Recommended route

Use **Grok Build CLI** as an optional local worker under the existing orchestrator. It supports browser authentication, cached-auth headless execution, JSON output, ACP, custom subagents, and reusable skills. Start with a bounded task using public or synthetic input. Confirm the account's entitlement and actual result before marking Grok available.

```powershell
grok login
grok models
grok --no-auto-update -p "Your bounded task" --output-format json --max-turns 2 --no-subagents --no-memory
grok agent stdio
```

These are documented interfaces, not commands executed during this research. Use the verified full executable path if another product already supplies an `agent` command. Do not enable `--always-approve` for an initial capability test. Cached account login and an `XAI_API_KEY` are distinct authentication routes; a model-specific configured key can take precedence over an account token.

Sources: [headless and ACP](https://docs.x.ai/build/cli/headless-scripting), [CLI controls](https://docs.x.ai/build/cli/reference), [authentication precedence](https://docs.x.ai/build/enterprise).

## Official Windows installer and verification

The Windows tab in the [official overview](https://docs.x.ai/build/overview) was verified by inspecting its HTML. It specifies `irm https://x.ai/cli/install.ps1 | iex`.

- Installer source: [https://x.ai/cli/install.ps1](https://x.ai/cli/install.ps1). Retrieved and inspected as text only; saved locally as [grok-install-reviewed.ps1.txt](./grok-install-reviewed.ps1.txt). SHA-256 of this saved installer source: `3a4ee2b1d744252c00827abbdeb2589f6b3dae80e73d88e0a81e08dc0ee747e7`. This records the reviewed script, not the executable's identity or signature.
- Default binary directory: `%USERPROFILE%\.grok\bin`; contains both `grok.exe` and `agent.exe`.
- Script also uses `.grok\downloads`, generates PowerShell completions by executing the downloaded binary, updates `.grok\config.toml`, and adds the binary directory to User PATH. `GROK_BIN_DIR` overrides the binary directory; `GROK_VERSION` or `-Version` pins a release.
- **The inspected script performs no SHA-256 or Authenticode validation before installation/execution.** A locally computed SHA-256 would record the downloaded bytes, but requires a trusted expected value to establish publisher integrity.
- Primary distribution is `https://x.ai/cli`; its documented fallback in the script is `https://storage.googleapis.com/grok-build-public-artifacts/cli`.

Public metadata verified without downloading the executable:

| Item | Observed value |
|---|---|
| [Stable version pointer](https://x.ai/cli/stable) | `1.0.13` |
| [Native Windows x64 artifact](https://x.ai/cli/grok-1.0.13-windows-x86_64.exe) | HTTP 200 |
| Content length | 140,810,568 bytes |
| Last modified | 2026-08-28 22:45:02 UTC |
| Storage CRC32C metadata | `V2kpDQ==` |
| Storage MD5 metadata | `b6a/OMwaMCuonIAuSCvn4A==` |

The two plausible sibling SHA-256 URLs, ending in `windows-x86_64.exe.sha256` and `windows-x86_64.sha256`, returned HTTP 404. This establishes only that those paths are absent; it does not prove no checksum exists elsewhere. Storage MD5/CRC32C metadata can help check transfer consistency and is not equivalent to signature verification. Executable Authenticode status remains unverified because the executable was not downloaded.

## Account and cost caveats

- The [Grok Build CLI launch](https://x.ai/news/grok-build-cli) specifies SuperGrok or X Premium+ access. The newer [every-plan announcement](https://x.ai/news/grok-build-for-everyone) explicitly concerns **web and mobile Build**. Free CLI access was not established by this research; successful login alone would not establish inference entitlement.
- The [API quickstart](https://docs.x.ai/developers/quickstart) instructs users to load credits and create an API key. No current guaranteed free API starter credit was found. Do not treat historical free-credit promotions as current.
- The current [consumer FAQ](https://docs.x.ai/grok/faq) lists API within paid weekly usage reporting. It does not explain which direct API credentials/requests qualify. Verify the actual account/console entitlement rather than assuming either free API access or that subscriptions can never cover API activity. Extra usage credits and optional automatic top-ups can create additional spending.

## Grok Bot versus CLI subagents

**Grok Bot** is a separate persistent teammate product. Its Bots have individual roles and conversations but share the user's cloud computer, files, and signed-in sessions. Cursor controls model selection; there is no model picker. Bots can coordinate through conversations and shared files. No documented public Bot-control REST or MCP endpoint was found; do not represent app-driven handoffs as a verified headless integration. Sources: [Bots](https://docs.x.ai/grok-bot/bots), [settings](https://docs.x.ai/grok-bot/settings-and-notifications).

The [canonical Bot billing page](https://cursor.com/help/grok-bot/plans), linked by xAI's administration documentation, includes every paid individual Cursor plan (including Pro), Cursor Teams, or linked individual SuperGrok/Plus/Heavy/X Premium+. This is broader than the shorter plan list on the getting-started page. There is also a finite trial usage credit with a seven-day window. Bot usage is metered on Cursor, and enabled on-demand spending can continue after included usage. A Grok/X-to-Cursor account link is permanent and cannot be moved or unlinked. A regular free Grok login does not establish Bot entitlement.

**Grok Build CLI subagents** are independent child sessions. Custom definitions use `.grok/agents/` or `~/.grok/agents/`; personas are behavior overlays rather than separate models. Built-in `explore` and `plan` agents have no shell or edit tools. Global `~/.agents/skills/` and Claude-compatible skills are discovered automatically. Sources: [subagents](https://docs.x.ai/build/features/subagents), [skill discovery](https://docs.x.ai/build/features/skills-plugins-marketplaces).

## API and MCP capabilities

The xAI API supports structured outputs, custom function calls executed by our application, and server-side search and other tools. The beta `grok-4.20-multi-agent` research model supports four or sixteen internal agents; tokens and tool calls across the team are billed. It provides a Grok research team, while our orchestrator still owns cross-provider delegation. Sources: [function calling](https://docs.x.ai/developers/tools/function-calling), [structured outputs](https://docs.x.ai/developers/model-capabilities/text/structured-outputs), [multi-agent API](https://docs.x.ai/developers/model-capabilities/text/multi-agent).

The official `https://docs.x.ai/api/mcp` endpoint provides **documentation search**, not Grok inference. Remote MCP support lets Grok call external tools; it does not expose Grok itself as an inference MCP server. Sources: [Docs MCP](https://docs.x.ai/developers/docs-mcp), [remote MCP](https://docs.x.ai/developers/tools/remote-mcp).

## Initial capability test

Use a small public or synthetic product brief. Request a five-slide outline and a separate audit identifying unsupported claims, with no browsing, file changes, messaging, publishing, or nested delegation. Evaluate factual fidelity, useful structure, adherence to the output contract, and actual completion. Record the executable version, returned model identifier when available, latency, and usage. Inspect output and produced artifacts; login success or process exit alone is insufficient proof.
