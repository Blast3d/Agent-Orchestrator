# Explore the implemented system

Open **Open System Map.cmd**, choose **System map** in the Brain dashboard, or
choose **Explore system map** in the Orchestrator viewer. The command is:

```text
python orchestrator.py map
```

Use `python orchestrator.py map --no-open` to regenerate without opening a tab.
The standalone `runtime/system-map.html` works offline and can be copied as one
file. It has no external scripts, fonts, model calls, or analytics. This is a
maintained implementation snapshot, not a live process or quota monitor.

Click branches to expand them, select components for their evidence and related
connections, search by name, or choose a guided workflow. The four workflows
cover speech/readback, Away access, creating a team, and learning from work.
Arrow labels distinguish audio transport, task execution, review, and retrieval.
The mobile view provides the same hierarchy and details in a list.

The maintained content is `app/assets/system-map-data.json`; the interaction shell
is `app/assets/system-map-template.html`. Keep only implemented components in
this map. Include operating prerequisites in component details and maintain
unfinished work separately. Regenerate after changing source data.

## Current implementation boundaries

- Native worker memory: the lead still searches the Brain and includes relevant
  results in native worker prompts explicitly. Automatic scoped recall is wired
  into the supplied-text dispatcher. Native reviewed closeouts now have a
  dedicated capture command; there is no automatic native-session extraction.
- Semantic memory: reviewed bundles supply concise nodes and relationship
  reasons. Automatic semantic deduplication, extraction, embeddings, and Graphiti
  are not installed. Exact retry fencing and explicit review are implemented.
- Usefulness: task receipts show context requested with execution; reviewers can
  judge its effect. There is no causal memory-on versus memory-off benchmark yet.
- Voice handoff: Orchestrator ownership and OpenWhispr's selected voice target
  remain separate. A lead transfer does not automatically retarget dictation or
  readback. Verify the chosen target after a handoff.
- Away readiness: the phone audio route is configured separately from desktop
  access. The last audit found RustDesk inactive and its configured container
  server unavailable; current phone reachability and audio routing were not
  live-tested. Starting or repairing those services is a separate task.
- Team capacity: this native session permits one lead plus three concurrent
  workers. Larger assignment sets use waves. Planner caps are advice, and a
  controlled timing/quality comparison is still needed to establish best team
  sizes for task types.

The September 10 implementation closes the first practical memory gaps with
reviewed atomic capture, automatic scoped supplied-text recall, visible request
receipts, and review-bound usefulness ratings. No record-limit increase was
needed: the existing project held six memories against a 10,000-record limit.
