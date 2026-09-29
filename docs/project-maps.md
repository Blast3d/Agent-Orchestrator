# See who helped

Open **Open Project Maps.cmd** in the application folder.

Pick a project to see it in the middle, with the agents that helped around it. Each agent shows its estimated share of the finished work. Choose an agent to see what it helped finish. Choose an area of work to see just that part of the project.

Use **Compare projects** to see how the teams differed. Each project has its own bar. A blank or patterned section means that part has not been assigned yet; it does not get divided among the other agents.

These pictures come from the same reviewed contribution reports as the written audit. Shares are estimates of work kept in the result. They are not scores for how good an agent is. An agent that tried several times does not automatically get a bigger share.

Memory activity is shown separately. An agent with recorded recall activity has a **memory recall** badge, even when its finished-work credit is 0%. Choose that agent to see **Memory usage**: recorded recalls, provider calls, cache hits, elapsed time, input/output tokens, reported cost, candidate and returned counts, and whether the ranking changed. These measurements cover the whole selected run and remain visible when filtering work categories. They do not establish that the memory improved the answer.

**Worker assignments** counts dispatched worker tasks; memory recall does not increase that counter. A cache hit can represent a real recall with zero new provider calls. Missing measurements show **Not recorded**; incomplete totals are labeled as known values with coverage. An unchanged low-confidence result is visible activity, even when no separate finished output is credited.

To calculate a share, give each finished piece of work a size, credit the agents whose work was kept, and divide each agent's total by the whole project's total. If an agent is credited with 3 of the project's 10 work points, its share is 30%. Shared pieces are split between their contributors. The sizes and credits are reviewed estimates, not measurements of effort.

New combined reports appear when Codex finishes the contribution audit. The page works locally without an internet connection or a chart-service account. A project whose files are temporarily unavailable keeps its last saved view with a clear note.

Earlier projects need a reviewed contribution report before their shares can be shown. The application does not invent old team history. The first views cover the orchestration work already recorded here; future projects will build that history.

## For maintaining the app

`python orchestrator.py visuals --open` refreshes and opens the page. `python orchestrator.py visuals --add-report PATH` includes a saved contribution report from another project. Combined audits produced through the `contributions` command register automatically. No model call is needed to refresh these pictures.

The visual page is `runtime/project-map.html`. `runtime/project-library.json` keeps the local report locations and the last valid display data. The interface receives names, shares, short descriptions of finished work, and allowlisted numeric memory-usage summaries. It does not receive memory text, queries, source evidence, account details or backend paths. Refreshing the map makes no provider calls.

The maintained page template is `app/assets/project-map.html`. The adapter is `app/project_visuals.py`. It recomputes shares from the report evidence, preserves missing data and safely embeds the display data. The written report remains the place to inspect the weighting and supporting evidence.

To retain recall evidence in a reviewed contribution ledger, add an activity with `kind: memory_recall`, its agent ID, and the exact trace ID as `task_id`. The optional `jev` object is the current invocation's structured metadata from the verified Brain recall receipt, including `provider_calls`, `elapsed_ms`, `input_tokens`, `output_tokens`, `cost_usd`, candidate/returned counts, `order_changed`, `status` and `cache.status`. Top-level input/output token counters remain compatible with existing ledgers. Repeated records of the same trace are counted once per agent. Cached `origin_usage` is never added to current usage. No free-text evidence is parsed to invent measurements. The map summarizes only recorded events, not every recall ever made within the shared Brain project.
