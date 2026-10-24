# Brain access for native agents

Hosted workers receive Brain recall through the dispatcher. Native Claude Code
and Codex sessions do not, so they only see memory a lead pastes into their
prompts. Two additions close that gap without weakening review:

- a **read-only search server** that native agents call themselves, and
- a **session inbox** that turns finished native sessions into candidates a
  reviewer accepts or rejects.

Neither one calls a model. Neither one can make a memory active without a
named reviewer.

## Read-only search server

`app/brain_mcp.py` is a dependency-free stdio MCP server, built like the Relay
server. It offers three tools:

| Tool | What it returns |
| --- | --- |
| `brain_projects` | The projects this server may read, with current reviewed memory counts |
| `brain_search` | Bounded recall for one project, with provenance, at compact, balanced or deep depth |
| `brain_get` | One current reviewed memory with its full text and current relationships |

The server never proposes, approves, relates, supersedes or forgets memory. It
reads only the projects named with `--project` when it starts. Pending,
expired, forgotten and other-project memories are refused. Searches record the
usual recall trace, a query hash and the returned IDs, so the dashboard's
Recent recall shows them.

Jev ranking stays off unless the server starts with `--allow-jev`. Those
requests are billed through OpenRouter, so turn it on deliberately.

Register it with Claude Code for every session on this account:

```powershell
claude mcp add --scope user brain -- python "C:\Users\jacob\Documents\Agent-Orchestrator\app\brain_mcp.py" --project openwhispr --project agent-orchestrator
```

For Codex, add it to `~/.codex/config.toml`:

```toml
[mcp_servers.brain]
command = "python"
args = ["C:\\Users\\jacob\\Documents\\Agent-Orchestrator\\app\\brain_mcp.py", "--project", "openwhispr", "--project", "agent-orchestrator"]
```

Name only projects whose memory that agent may read. Memory text reaches the
agent's own model provider when it reads a result.

## Session inbox

`app/brain_session_inbox.py` builds a rule-based summary of a finished native
session: the first and last requests, files changed, commands run, errors seen
and the final reply. It files the summary as a **candidate** in
`runtime/brain/session-inbox/`. Candidates never reach recall.

Sessions are skipped when they hold nothing durable:

| Skip reason | Meaning |
| --- | --- |
| `non_interactive` | A print-mode, SDK or `codex exec` run; orchestrator workers are reviewed as tasks instead |
| `no_requests`, `no_activity` | No request, or a single question with no tool use |
| `too_short` | Under 30 seconds with no file changes |
| `smoke_test` | Only a few commands such as `echo`, `git status` or `--version` |
| `setup_failure` | Every error was setup state, such as a missing command or a permission error |
| `unknown_project` | The folder maps to no Brain project |
| `rejected_before`, `already_reviewed` | A reviewer already decided on this session or an identical candidate |

The project comes from the workspace's `.orchestration/project.json`, or else
from the git root's folder name when the Brain already has that project.

Before anything is written, obvious secrets are redacted, including API keys,
tokens, private keys and `password=` assignments. Broad claims that a tool "is
broken" or "doesn't work" are removed from the reply, with a warning on the
candidate, because such claims harden into false rules once the tool is fixed.

### Capture

Claude Code captures through a `SessionEnd` hook in `~/.claude/settings.json`:

```json
"SessionEnd": [{"hooks": [{"type": "command", "timeout": 30,
  "command": "python C:/Users/jacob/Documents/Agent-Orchestrator/app/brain_session_inbox.py hook"}]}]
```

The hook reads the hook JSON on stdin, always exits 0, and logs each outcome to
`runtime/brain/session-inbox/recent-captures.json`. Sessions that crash or
close without a `SessionEnd` are not captured.

Codex has no session-end hook here, so sweep its idle rollouts instead. The
sweep is safe to repeat:

```powershell
python orchestrator.py brain inbox scan-codex --hours 24
```

### Review

```powershell
python orchestrator.py brain inbox list
python orchestrator.py brain inbox show CANDIDATE_ID
python orchestrator.py brain inbox accept CANDIDATE_ID --reviewer Jacob --note "Checked the summary against the diff and tests."
python orchestrator.py brain inbox reject CANDIDATE_ID --reviewer Jacob --reason "One-off chat, nothing durable."
```

Accepting proposes and approves an ordinary episode. Its user-note source names
the agent, session and reviewer. `--project` and `--title` adjust it first.
Rejecting deletes the candidate and remembers its session and fingerprint so it
is not offered again. The inbox holds at most 200 pending candidates.
