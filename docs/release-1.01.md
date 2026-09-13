# Version 1.01

This portable update adds the Memory Brain button beside the Usage monitor,
automatic orchestration startup guidance, scoped Brain recall, and verified run
closeout. The shared guide is included in worker requests even when project
recall is disabled. Native workers receive authorized context from their lead.
Optional global registration installs the Codex and Claude startup instructions.

The Windows x64 ZIP includes Python 3.13.15, pinned dependencies, application
launchers, and the VS Code bot extension 0.1.1 with the Usage monitor control.
The archive filename now follows the included extension's own version.

Source validation: 1,052 Python tests ran successfully, with six skipped. The
checks cover startup identity and guide hashes, exact Windows request bytes,
memory source validation, and refusal to credit failed or unresolved tasks.
The installed development copy passed all 105 offline installation checks;
the Memory Brain control and contribution maps were checked in the browser.

The portable archives are validated after extraction to a separate folder with
spaces, using their bundled interpreter with system Python removed from PATH.
Release assets include SHA-256 checksums and a per-file package manifest.
Package checks cover dependency imports, subprocess startup, loopback service
health, isolated runtime paths, and preservation of existing user settings.

These checks run on the existing Windows host. They do not establish provider
authentication, inference completion, VS Code model consent, or clean-machine
compatibility. An extra live Grok implementation audit was interrupted; it remains
unresolved in local task evidence and its quota reservation is retained. Local
automated checks and the accepted design reviews are recorded separately.

Extract the new ZIP alongside an existing installation first. Preserve its
configuration, Brain database, task history and run folders during upgrades;
stop its local services before replacing application files. Runtime state,
personal configuration and credentials are excluded from the release. See
[packaging and setup](packaging.md) for installation and upgrade instructions.
