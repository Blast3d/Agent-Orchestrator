# Scoped file tools for workers

By default a worker answers a supplied brief and uses no tools. **Scoped mode** lets a
worker read and edit a disposable copy of a repository instead: a detached git worktree
created for that one assignment. Workers get file tools only (read, list, find, search,
write and exact-text edit). Shell and command execution, the web, other tool servers,
sub-agents, messaging and scheduling stay denied. Nothing reaches the real repository
until the lead reviews the collected patch and applies it explicitly.

Supplied-text mode is unchanged and stays the default.

## Use it

```powershell
python orchestrator.py run claude --run RUN_DIR --prompt-file brief.md --output answer.json `
  --task "Fix the parser bug" --worktree-from C:\path\to\repo [--worktree-ref main]
python orchestrator.py worktree show JOB_ID
python orchestrator.py worktree apply JOB_ID --check
python orchestrator.py worktree apply JOB_ID [--target C:\path\to\repo]
```

The dispatcher creates the worktree under the job workspace only after quota admission.
When the worker finishes (or stops), it collects the patch into the task folder
(`runs/tasks/JOB_ID/scoped/worker.patch`, with `collect.json`) and removes the worktree.
`apply` stages the changes with `git apply --index` and never commits. Review the patch,
apply it, run the project's tests, then record the review as usual.

## Route status

A route runs scoped only while `config/scoped-proofs/<worker>.json` records a passing live
validation whose fingerprints (worker executable, Python, the scoped modules) still match.
Anything else holds the task before quota or provider activity.

| Worker | How the file tools are provided | Status |
|---|---|---|
| Claude | Built-in Read/Write/Edit/Glob/Grep under `--restricted`, `--safe-mode`, `--strict-mcp-config`, `--permission-prompts none`; cwd is the worktree | Verified |
| Grok | Our MCP server (`app/scoped_files_mcp.py`) as the only server, reached through Grok's `search_tool`/`use_tool`; built-ins removed | Verified |
| Gemini (agy) | AGY's own file tools behind a path-checking pre-tool hook (`app/antigravity_scoped_tools.py`); everything else denied | Verified |
| Codex | Codex 0.164 offers MCP tools only through its code-mode JavaScript host, which would run model-written code | Held |
| VS Code Copilot, local chat | Need a Python tool loop over the same tools | Not built |

Validate (or re-validate after a CLI update or a change to a scoped module) with
`python app/scoped_validation.py claude|grok|gemini`. Each run is one synthetic model call:
it builds a throwaway repository, asks the worker to attempt every boundary once and writes
the proof only when all nine checks pass (inside read and write work; outside reads and
writes, `.git`, protected configuration, shell, web and foreign tools are refused). Probes
are recorded under `runtime/scoped-validation/` and never credited.

## Path policy

All routes we control share `app/scoped_files.py`:

- Paths stay inside the worktree: no `..`, other drives, UNC or device paths, alternate data
  streams, 8.3 short names, trailing dots or spaces, links, junctions or reparse points.
- `.git` is unreadable and unwritable at any depth.
- Readable but write-protected at any depth: `.agents .claude .codex .grok .gemini .cursor
  .vscode .idea .github`, `.mcp.json .gitmodules .gitattributes AGENTS.md CLAUDE.md
  CLAUDE.local.md GEMINI.md .cursorrules .windsurfrules`.
- Bounded reads, writes, listings and searches; atomic UTF-8 writes; no delete or rename.

Claude's route relies on Claude Code's own `--restricted` confinement instead, so the
collected patch is checked again: `apply` refuses any patch that touches a protected path,
whose bytes changed after collection, or whose paths differ from the reviewed list.

## Git hardening

`app/worker_worktree.py` runs every git command with an empty hooks folder, fsmonitor,
maintenance and network transports disabled, and stripped `GIT_*` overrides. Commits with
symbolic links or submodules are refused. Collection reads attributes from the base commit
(`--attr-source`), so a worker-written `.gitattributes` cannot select a filter program, and
writes a raw-byte patch with fixed prefixes and no rename detection. Apply runs without
`--3way`, so a diverged target fails cleanly instead of running merge drivers.

## Known limits

- Path checks are not atomic against another local process changing the folder.
- Filters that the base commit's or the user's own attributes select still run on worker
  content during collection; they are the user's configured programs.
- Grok reads hooks from `~/.claude` as user-level hooks; they cannot be switched off without
  editing user settings, so their digest is pinned in the proof and any change holds the
  route. User-level skills still appear in Grok's prompt.
- Files hidden by `.gitignore` are not collected.
- A regular-expression search with a hostile pattern is bounded only by the task deadline.
