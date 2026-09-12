# Who leads, and how workers are chosen

The recorded lead (ASTRA by default, or Fable after an explicit claimed handoff)
is in charge of orchestration: scope, decomposition, assignments, source
selection, integration, disagreement resolution, testing, and final delivery.
Workers advise or produce assigned artifacts. Their recommendations do not
authorize changing the user's goal, spending, publishing, or adding more workers.
The current lead may delegate a bounded implementation and still owns its acceptance.
References to Codex below apply to that recorded lead. Workers never self-promote.

These defaults reflect the successful presentation workflow and practical task
fit; they are not universal benchmark rankings or claims about an unseen model.
Use the actual model's capabilities, available context/tools, observed output
quality, cost policy, and current user preference to select the worker.

Apply [the user's provider-balance preference](provider-balance.md) before filling
these roles. Distinct job titles do not make an all-OpenAI or all-Anthropic team
diverse. The lead's provider is included when reporting the actual team mix.

| Task | Starting assignment | What determines success |
| --- | --- | --- |
| Overall plan, architecture decisions, integration | Codex lead | Coherent scope, compatible pieces, working final output |
| Coding and debugging | A ready Claude or Grok coding worker owns a bounded module/patch and tests; Codex integrates and checks | Correct behavior, evidence from relevant tests, reviewable changes |
| Documentation and annotations | Claude or Grok authors the actual README section, examples, comments or walkthrough | Accurate explanations checked against the final implementation |
| Story, writing, speaker script | Claude as the initial narrative worker; other creative model when demonstrated useful | Clear audience fit, coherent story, faithful claims, little editorial rework |
| Visual concepts and alternate design | Available model with strong visual/design performance; Antigravity supplied useful direction in the example | Useful composition, visual hierarchy, renderable specifications; inspect actual render |
| Source synthesis and project mind map | NotebookLM when approved sources and its formats fit | Grounded structure, source traceability, accurate labels |
| Broad research and alternative reasoning | Gemini through Antigravity CLI or another capable research worker with source access | Current primary sources, explicit uncertainty, evidence-backed alternatives |
| Independent code/content review | A capable model distinct from the maker where available | Specific reproducible defects or claim/source mismatches, not generic praise |
| High-volume extraction or short drafts | Suitable local model or a verified free-quota API model | Structured output reliability and acceptable quality on a small sample |
| Exact-script speech | Piper or another suitable available TTS tool | Complete recorded audio aligned to the reviewed script |
| Conversational audio interpretation | NotebookLM when requested | Reviewable transcript; unsupported additions corrected or labeled |

This user wants existing included allowances shared across useful production work.
Before a substantial build, name an external maker for at least one meaningful
deliverable and an external reviewer when a suitable approved route is ready.
Name the file/module, acceptance checks, allowance estimate and alternate up front.
For parallel modules, distribute ownership across capable available providers;
several seats on the same provider still share that provider's allowance. Small
coding modules usually need a smaller brief and modest reasoning effort; use
more reasoning for a demonstrated difficult part, not automatically for every file.

Give creative workers something that survives into delivery: a renderable visual,
diagram, slide section, narration script or finished copy. Antigravity and Atlas
can own bounded supplied-text visual specifications, annotations or reviews through
their currently verified supervised routes. NotebookLM can synthesize approved
sources or produce requested media when the feature allowance is known. Local Qwen
can extract labels, draft short captions, classify items or summarize small inputs;
keep complex coding and final correctness decisions with stronger workers.

Codex owns scope, interfaces, source permissions, integration, acceptance and the
final audit. Native Codex workers remain appropriate for work requiring local
access unavailable to other routes, targeted independent checks, or concrete
recovery work. Record the reason if Codex takes over an external maker's artifact.
Do not rewrite a sound external draft merely to give Codex authorship. Conversely,
reject an unsound draft and credit only its retained useful contribution.

Do not target equal percentages or add model calls to improve the chart. Track
accepted work, provider allowance and elapsed time separately. Aggregate original
work points before rounding provider totals. More external ownership can spread
included-plan usage, but repeated briefs, duplicated implementations and long
reviews can consume more total inference; choose the smallest useful deliverables.

If a tool can switch models, select a suitable model explicitly when supported
and authorized; record what actually ran. A creative task may need a vision/image
model, text narrative skill, or just deterministic diagram code. A tool's branding
does not settle that choice. Antigravity's selected model may overlap with Claude
or Gemini, and OpenCode can front many models.

For an unfamiliar model, start with one bounded representative task. Evaluate
factual errors, needed revisions, usable output, elapsed time, and observed cost
in the project ledger. Prefer evidence from the user's work over broad rankings.
If the worker repeatedly misses the contract, revise the assignment or switch
to an already authorized alternative. Do not keep a weak result just because
that model was assigned a role.

Honor explicit user rules such as "Claude writes, Codex reviews" or "local only"
ahead of these defaults. Role substitutions should be visible when they change
cost, privacy, capabilities, or an explicitly requested contributor. Do not
silently route a free-only job to a paid fallback.

Keep task-specific role performance notes with the project. Update these global
defaults only when the user requests a persistent change. Acquiring a tool does
not automatically make it the preferred worker for a category.
