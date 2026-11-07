# Team mix: participation sliders and model tiers

New runs (provider policy schema 3) follow a team mix: one slider per model
family, the lead included, that together always total 100%. Each slider is the
family's target share of the run's accepted contribution weight, the same
percentages the contribution map shows. A tolerance (default ±10 points) sets how
far a family may land from its target before closeout holds.

| Family | Worker | Example "code" preset | Example "visual" preset |
| --- | --- | ---: | ---: |
| OpenAI | Codex (ASTRA, Sol) | 30% | 40% |
| Anthropic | Claude | 40% | 20% |
| xAI | Grok | 15% | 15% |
| Google | Gemini | 15% | 25% |

Presets: `balanced` 25/25/25/25, `code` 30/40/15/15, `visual` 40/20/15/25 and
`research` 25/20/20/35 (OpenAI/Anthropic/xAI/Google). Moving one slider spreads
the difference over the other three in proportion to their current shares above
the 5% floor, so the total stays 100. Each share stays between 5% and 85%.

## Where to change it

- **Orchestrator viewer**: the Team mix panel under "Lead for new runs". Edit the
  default for new runs, or pick a run in the run picker and retune that run.
- **OpenWhispr voice visualizer**: Dashboards → Team mix. The same controls, plus
  a scope list of open runs (runs started before team mixes are shown but
  disabled).
- **CLI**:

```powershell
python orchestrator.py mix show                       # default for new runs
python orchestrator.py mix set --preset code
python orchestrator.py mix set --family claude --value 40     # rebalances the rest
python orchestrator.py mix set --shares OpenAI=30,Anthropic=40,xAI=15,Google=15 --tolerance 15
python orchestrator.py mix set --tier Anthropic=economy --tier xAI=balanced
python orchestrator.py mix show --run RUN_DIR         # one run
python orchestrator.py mix set --run RUN_DIR --preset visual
python orchestrator.py mix runs --json                # runs the visualizer can retune
```

The default lives in `runtime/team-mix.json`. Starting a run copies it to
`RUN_DIR/team-mix.json`; later changes to the default never touch that run.
Retuning a run appends to its history (startup entry kept, at most 200 entries)
with the source (`cli`, `viewer` or `visualizer`). Completed runs refuse
retunes. Only the user retunes a mix; a lead never changes it to pass closeout.

## What closeout checks

For schema 3, `provider_family_coverage` holds until:

1. every non-lead family has at least one accepted hosted task, as before; and
2. every family's accepted share is within the tolerance of its target. The
   lead's share counts native lead work proved by a reviewed run capture. Weight
   that cannot be tied to a family (for example a foreign native label) stays in
   the denominator as `unverified`.

The check reports `participation.targets`, `actual`, `deltas`, `within` and
`unverified_pct`, and a warning per family telling the lead to delegate more or
to assign more accepted work. Closeout also refuses to finish if the mix is
retuned while it runs. `orchestrator.py balance --run RUN` and `run auto` pick
the eligible family furthest below its target by task count; `orchestrator.py
team` splits planned assignments by the mix.

Runs started earlier keep their recorded policy: schema 2 runs keep near-equal
task counts and have no team mix; schema 1 runs keep the two-family minimum.

## Model tiers

Each family has a Best / Balanced / Economy ladder (`orchestrator.py tiers`):

| Worker | Best | Balanced | Economy |
| --- | --- | --- | --- |
| codex | GPT-6-Astra, medium | GPT-6-Sol, medium | GPT-6-Sol, low |
| claude | Opus, medium | Sonnet, medium | Haiku, low |
| grok | grok-4.6, medium | grok-4.6, low | grok-4.6, low |
| gemini | gemini-3.8-flash-high | same | same |

The mix's per-family tier sets the default; `run ... --tier economy` overrides it
for one assignment, and explicit `--claude-model`/`--codex-model` and effort
flags still work. Precedence: `--tier`, explicit model flag, run mix, default
mix, Best. A tier whose model is not verified for its route (a paused Claude
family, a Grok model outside the worker's `verified_models`, a Gemini model
outside the Antigravity boundary proof) holds before any reservation. Local
overrides go in `config/workers.json` under `model_tiers`, for example
`{"grok": {"best": {"model": "grok-4.7", "effort": "medium"}}}` once grok-4.7 is
verified and listed in the Grok worker's `verified_models`.

Changing the Grok default from low to medium effort makes "best" the smartest
setting; choose `balanced` for the previous behavior. The Codex default is now
GPT-6-Astra for Best regardless of the Codex lead switch.
