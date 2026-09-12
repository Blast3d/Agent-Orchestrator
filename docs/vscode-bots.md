# VS Code chat bots

The existing VS Code integrations can participate in automatic orchestration:

| Integration | Task route | Shared allowance |
| --- | --- | --- |
| Codex | The coordinator's native Codex workers | Existing Codex account pool |
| Claude Code | `python orchestrator.py run claude ...` | Existing Claude account pools |
| Copilot | `python orchestrator.py run vscode-copilot ...` | Copilot account, shared with VS Code chat |

These are interfaces to the existing accounts. Opening another chat does not
create another subscription allowance or establish a different model family.
Codex and Claude retain their current worker routes. The Copilot worker uses the
supported VS Code Language Model API through the locally installed **Agent
Orchestrator Bots** extension. It creates a fresh supplied-text request; it does
not send into, resume or copy an existing conversation.

## Enable Copilot assignments

The status bar's **Usage monitor** button (or **Agent Orchestrator: Usage Monitor
On/Off** in the Command Palette) controls background allowance checks independently
of Copilot assignments. It reads the current status when clicked and offers On or
Off. Opening VS Code never enables the monitor; turn it off when finished coding.
After installing extension 0.1.1, an existing VS Code window may need a normal
reload to show the button. The application folder comes from the saved Codex
worker registry, or **Agent Orchestrator: Application Root** in user settings.

In one trusted VS Code window, run **Agent Orchestrator: Enable VS Code Bots**
from the Command Palette and select the model for tasks. If VS Code requests
permission for the extension to use Copilot, allow access to enable assignments.
The selection is saved for that workspace. VS Code must remain open.

Use **Agent Orchestrator: Choose Bot Model** to change the selection and
**Agent Orchestrator: Disable VS Code Bots** to stop accepting new tasks.
Enable one window at a time. If multiple windows are enabled, the dispatcher
holds the task until only one target remains; it does not guess the destination.

```powershell
python orchestrator.py vscode-bots
python orchestrator.py run vscode-copilot --prompt-file brief.txt --output answer.json --task review --size small --project my-project --assignment-id review-v1 --require-brief-check
```

Existing outputs are preserved. Assignment IDs, memory scope, task deadlines,
saved progress, accepted/rejected review and contribution records use the normal
dispatcher. Successful answers enter **awaiting_review**. They are not accepted
automatically. A held bridge or changed model does not start another request.
After any failed or uncertain execution, inspect its saved record before retrying.

## Behavior and limits

The bridge provides no model tools. It sends only the supplied brief, including
scoped reviewed memory when requested by the normal dispatcher. It does not
collect workspace files, clipboard content, existing chat history or editor
selection. No code executes from a returned answer.

The authenticated endpoint binds only to loopback, refuses browser-origin
requests, permits one active task, limits the brief to 32 KB and the answer to
512 KB, and cancels when the client disconnects or its deadline expires. The
dispatcher applies its own output limits too. Partial or interrupted responses
retain quota reservations for reconciliation. The bridge never invokes tools
returned by a model. These are API capability limits, not OS sandboxing.

Copilot model access and initial consent are controlled by VS Code. Selecting
a model does not prove that the first request has been authorized. The API does
not report account allowance or actual token usage; those stay **unknown**.
All Copilot bot requests share `copilot-account` in the quota guard. This host's
advisory admission permits unknown allowance, while confirmed holds and pending
reservations retain their normal meaning. There is no new API-key route,
billing change, or automatic alternate for Copilot. Model selection uses the
existing Copilot model list and can affect that account's consumption.

## Local maintenance

Source lives in `extensions/vscode-bots/`. Package with
`python scripts/package_vscode_bots.py NEW_OUTPUT.vsix`, then install the local
VSIX with VS Code. No marketplace publishing or dependency download is needed.
`app/vscode_bots.py` supplies discovery and status; `app/vscode_worker.py` forwards
one task into the existing bounded stream executor. Bridge receipts live in
the user's `.agent-orchestrator/vscode-bots` folder and contain a local bearer;
do not print or share them. Closed-window receipts are ignored.

Validation commands:

```powershell
node --test tests/test_vscode_bridge.cjs
python -m unittest discover -s tests -p test_vscode_bots.py
```

Those checks use synthetic models and fixtures. A real Copilot response must be
validated separately after selecting a model and granting any VS Code consent.

References: [VS Code Language Model API](https://code.visualstudio.com/api/extension-guides/ai/language-model)
and [session behavior](https://code.visualstudio.com/docs/agents/concepts/sessions).
