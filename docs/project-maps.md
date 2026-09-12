# See who helped

Open **Open Project Maps.cmd** in the application folder.

Pick a project to see it in the middle, with the agents that helped around it. Each agent shows its estimated share of the finished work. Choose an agent to see what it helped finish. Choose an area of work to see just that part of the project.

Use **Compare projects** to see how the teams differed. Each project has its own bar. A blank or patterned section means that part has not been assigned yet; it does not get divided among the other agents.

These pictures come from the same reviewed contribution reports as the written audit. Shares are estimates of work kept in the result. They are not scores for how good an agent is. An agent that tried several times does not automatically get a bigger share.

To calculate a share, give each finished piece of work a size, credit the agents whose work was kept, and divide each agent's total by the whole project's total. If an agent is credited with 3 of the project's 10 work points, its share is 30%. Shared pieces are split between their contributors. The sizes and credits are reviewed estimates, not measurements of effort.

New combined reports appear when Codex finishes the contribution audit. The page works locally without an internet connection or a chart-service account. A project whose files are temporarily unavailable keeps its last saved view with a clear note.

Earlier projects need a reviewed contribution report before their shares can be shown. The application does not invent old team history. The first views cover the orchestration work already recorded here; future projects will build that history.

## For maintaining the app

`python orchestrator.py visuals --open` refreshes and opens the page. `python orchestrator.py visuals --add-report PATH` includes a saved contribution report from another project. Combined audits produced through the `contributions` command register automatically. No model call is needed to refresh these pictures.

The visual page is `runtime/project-map.html`. `runtime/project-library.json` keeps the local report locations and the last valid display data. The interface receives names, shares and short descriptions of finished work; it does not receive model token counters, source file evidence, account details or backend paths.

The maintained page template is `app/assets/project-map.html`. The adapter is `app/project_visuals.py`. It recomputes shares from the report evidence, preserves missing data and safely embeds the display data. The written report remains the place to inspect the weighting and supporting evidence.
