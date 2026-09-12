# Portable Windows release

Download `Agent-Orchestrator-0.1.0-windows-x64.zip` and `SHA256SUMS.txt` from
the repository release. Extract the entire ZIP to a writable folder, then
double-click **Setup.cmd**. Python 3.13.15, the terminal-reader dependencies,
dashboards, command launchers and the VS Code extension are included.
The package targets Windows x64; it does not require a system Python install.

Open **Open Brain Dashboard.cmd**, **Open Orchestrator Viewer.cmd**, or
**Open Dashboard.cmd**. From a terminal, use `Orchestrator.cmd --help`.
The services bind to loopback and select available ports. Setup creates only
missing local configuration and folders; existing settings are preserved byte
for byte, including malformed files that need deliberate repair.

Provider applications and account logins remain separate prerequisites. Install
and authenticate your existing Codex, Claude Code or Grok CLI before assigning
provider work. The supported Claude route currently expects the npm installation
under the current user's AppData folder; Grok uses the current user's `.grok/bin`.
Quota-reader compatibility is pinned in the application and must be revalidated
when provider CLIs change. The release does not establish provider readiness.

For Copilot, install `extensions/agent-orchestrator-bots-0.1.0.vsix` using
**Extensions: Install from VSIX** in VS Code, then enable the Orchestrator bot,
select a model and grant the editor's model access. See [VS Code bots](vscode-bots.md).

Global agent discovery is optional and changes your user-level skills and registry.
After choosing this installation as the maintained home, run:

```bat
"Run Python.cmd" scripts\install_global.py
```

The general `doctor` command checks those global integrations too. `Setup.cmd`
and `scripts/check_package.py` check portable components without installing global
skills, starting models or opening a browser. A clean portable package can pass
its own checks while general `doctor` still reports missing provider integrations.

Keep your existing installation and its `runtime/`, `runs/`, `.orchestration/`
and `config/` folders when upgrading. Stop its local services before replacing
application files. Extract a new release alongside it first and validate the new
copy. Fresh ZIPs contain no saved prompts, Brain database, credentials or machine
settings. Existing settings contain installation paths; moving an already
configured installation requires reviewing those paths and rerunning optional
global registration. Do not run both copies against one writable database.
Historical panel reports and recorded project results remain in the old local
installation; the **Open Panel Results** shortcut requires those separate records.

## Build from source

Use Windows x64, Python 3.13 and pip to reproduce the portable build:

```powershell
python scripts/package_app.py --output dist/release
```

The build downloads the official embeddable runtime, verifies its pinned SHA-256,
and installs the versions in `requirements.txt` from binary wheels into the
bundle. The runtime uses relative isolated search paths. Dependency metadata and
licenses remain in the ZIP; see [third-party notices](../THIRD-PARTY-NOTICES.md).
Python's [embeddable-package documentation](https://docs.python.org/3/using/windows.html#the-embeddable-package)
describes this distribution model. The runtime and digest come from the
[Python 3.13.15 release](https://www.python.org/downloads/release/python-31315/).

The source ZIP and Git repository contain maintained app code, scripts,
launchers, skills, extension source, tests and documentation. An explicit source
selection excludes local state, private configuration, archives and build caches.
The Windows ZIP adds the runtime, dependencies, VSIX and file hash manifest.
Existing output archives are never overwritten; choose a fresh output directory.

Use `python scripts/smoke_package.py PATH_TO_ZIP "dist/smoke with spaces"` to
extract into a new folder, run the bundled checks and actual CMD launcher with
system Python off PATH, probe two temporary local dashboards and stop both.
See the [version 0.1.0 validation record](release-0.1.0.md) for observed results.

For source-only packaging use `--source-only`. To run a source checkout, install
`requirements.txt` with your Python environment, then run **Setup.cmd**.

## Release verification

1. Inspect the selected source files and scan both ZIPs for private settings,
   credentials, local reports and unexpected files before upload.
2. Verify archive checksums, then extract the Windows ZIP to a different path
   containing spaces. Leave system Python off the test process's PATH.
3. Run **Setup.cmd** through its actual launcher. Confirm dependency imports,
   subprocess CLI startup, SQLite FTS5 secure-delete support and all manifest hashes.
4. Confirm `sys.executable` and dependency `__file__` values resolve inside that
   extracted package; the bundled interpreter must also launch subprocesses.
5. Preseed custom settings, rerun bootstrap and compare the bytes unchanged.
6. Start the loopback dashboard with browser opening disabled, probe its health
   and page, then stop only that test process. Check that a separate installation
   can select its own available port and instance identity.
7. Record source tests, extension tests, archive hashes and actual smoke results.
   Record any skipped tests or provider checks as limits, not successes.

These checks establish packaged startup and local service behavior. They do not
prove provider authentication, live inference, VS Code consent, or visible user
interaction. Those checks use the user's own accounts on the target computer.
