# Version 0.1.0 validation — September 12, 2026

This first source release packages the maintained Orchestrator application,
Brain dashboard, coordinator viewer, quota tools, agent skills and VS Code
bridge as a portable Windows x64 ZIP and a separate source ZIP.

Validation on the Windows development host:

- Python suite: 1,006 tests run, success, six skipped.
- VS Code bridge: seven tests passed.
- New packaging tests: four passed, covering preserved settings, target-specific
  bootstrap paths, excluded private files and rejected ZIP traversal.
- Extracted package: relative imports, bundled subprocess runtime, SQLite FTS5
  secure deletion and manifest integrity passed.
- Actual CMD launcher passed from a different directory containing spaces,
  with system Python removed from the test process's PATH.
- Two local Brain dashboard processes returned valid health responses and HTML
  on distinct loopback ports. Both test processes were stopped afterward.
- Existing custom settings survived bootstrap without modification.

These are local package and service checks on the same Windows host, not a
clean-machine VM test. Provider authentication, live inference, VS Code model
consent and visible UI interaction were not part of this package validation.

Source publication excludes private configuration, runtime state, task prompts
and results, Brain databases, account credentials, local archives and build
caches. The ZIP contains hashes and third-party license/source notices.

ASTRA/OpenAI implemented and integrated packaging and performed local validation.
A separate OpenAI worker reviewed and validated OpenWhispr. Claude Opus supplied
the first-run validation checklist, adapted into the packaging guide; Grok
reviewed the generic packaging design. Accepted Grok recommendations include
failing clearly when a packaged interpreter is missing, preserving malformed
existing settings, checking archive-relative paths and redistribution notices,
and verifying concurrent local service ports. Their assignments received only
generic specifications, with project memory and source disclosure disabled.
These supplied-text reviews are not independent source audits.
