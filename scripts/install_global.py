"""Install this application's global skill and discovery registry; no model calls."""
import json
from pathlib import Path
import shutil

ROOT = Path(__file__).resolve().parents[1]

STARTUP_BEGIN = '<!-- agent-orchestrator-startup -->'
STARTUP_END = '<!-- /agent-orchestrator-startup -->'


def install_startup_hook(path, root=ROOT):
    """Keep personal instructions intact while installing a discoverable trigger."""
    path = Path(path)
    original = path.read_text(encoding='utf-8') if path.exists() else ''
    block = f'''{STARTUP_BEGIN}
## Automatic orchestration startup

Routine questions, git pull, merge, commit, push and other small tasks stay with
the current agent. Do not start orchestration for these unless the user explicitly
requests it. Apply the normal Git scope, publication and verification checks.

Before automatically starting substantial coding, research or multi-agent project
work, read `python "{root / 'orchestrator.py'}" automatic status`.
The persistent Automatic orchestration switch defaults Off. If `enabled` is false,
unreadable or unavailable, continue directly with the current agent; do not start
a run, dispatch workers or perform orchestration bookkeeping. Manual orchestration
requested by the user remains available regardless of this switch.
Turning Off does not cancel existing runs or change their closeout requirements.
If `enabled` is true and the work is substantial, use the installed
`multi-model-orchestrator` skill and `python "{root / 'orchestrator.py'}" start --automatic`
with the workspace, objective and short run name. If startup returns `disabled`,
continue directly. For explicit manual runs, use `start` without `--automatic`.

Every orchestration run, including a manually requested small edit run,
needs accepted retained work from at least two AI
families; prefer three when useful authorized scopes exist. Plan a cross-family
assignment before adding same-family native workers. A small edit run can use the
lead plus a later cross-family reviewer. JEV is advisory and does not count.

Before new worker assignments, run `python "{root / 'orchestrator.py'}" start`
with the workspace, objective and a short run name, or resume the exact existing
run with its current owner/session/generation. Read the returned operating guide
and scoped Brain recall. Follow `{root / 'docs' / 'startup-and-closeout.md'}`.
After a checkpoint or handoff changes the generation, reload that run's startup
context. Never choose an unrelated latest run automatically.

The user's lead-orchestrator switch picks Claude (Opus 5.5), ASTRA (Codex
GPT-6-Astra) or Sol (Codex GPT-6-Sol) to lead new runs and the runner-up at the
5% handoff; see it with `python "{root / 'orchestrator.py'}" lead selected`. When
you start a run yourself, pass `--lead` with your own identity (claude, astra
or sol). If the switch names a different lead, say so once and continue the
work the user asked you to do.

Give every native worker the operating guide and relevant authorized recall in
its actual assignment. The guarded dispatcher supplies operating guidance itself,
even with project recall disabled. Keep each provider's content permissions and
task scope; loading memory does not authorize broader access or extra spending.

Before final orchestration delivery, review tasks, verify capture outcomes,
complete the contribution ledger/map, and run `orchestrator.py closeout`.
Startup records the provider policy and run-linked dispatch verifies it even with
project recall disabled. The provider-family coverage check holds closeout until
positive accepted work proves two actual families. Copilot counts by actual model,
not brand; same-family agents and arbitrary foreign native labels cannot fill the
minimum. Native lead credit requires a reviewed capture from the exact run.
If no authorized alternate is usable, report the blocker and keep the run held.
Do not waive the threshold, invent credit, broaden content access or add spending.
Native tools remain outside the dispatcher; these checks are not a scheduler.
Report any incomplete evidence explicitly. A saved packet is not proof a model
read it. The user should not have to remind the lead to load this workflow.
{STARTUP_END}'''
    if STARTUP_BEGIN in original or STARTUP_END in original:
        if original.count(STARTUP_BEGIN) != 1 or original.count(STARTUP_END) != 1:
            raise ValueError('Startup instruction markers are ambiguous; preserve personal instructions')
        begin, end = original.index(STARTUP_BEGIN), original.index(STARTUP_END) + len(STARTUP_END)
        if end <= begin:
            raise ValueError('Startup instruction markers are out of order')
        updated = original[:begin] + block + original[end:]
    else:
        updated = original + ('\n\n' if original else '') + block + '\n'
    path.parent.mkdir(parents=True, exist_ok=True)
    if original != updated:
        path.write_text(updated, encoding='utf-8')


def install_orchestrator_skill_mirrors(root=ROOT, home=None):
    """All discoverable providers receive the same maintained guide, verbatim."""
    home = Path(home) if home is not None else Path.home()
    source = Path(root) / 'skills/multi-model-orchestrator'
    destinations = [home / profile / 'skills/multi-model-orchestrator'
                    for profile in ('.codex', '.claude', '.agents')]
    for destination in destinations:
        shutil.copytree(source, destination, dirs_exist_ok=True,
                        ignore=shutil.ignore_patterns('__pycache__'))
    return destinations


def install():
    install_orchestrator_skill_mirrors()
    claude_skills = Path.home() / '.claude/skills'
    shutil.copytree(ROOT / 'skills/orchestrator-takeover', claude_skills / 'orchestrator-takeover',
                    dirs_exist_ok=True, ignore=shutil.ignore_patterns('__pycache__'))
    # A connected ChatGPT dot (Relay) reaches this PC through local Codex threads.
    shutil.copytree(ROOT / 'skills/relay-bridge', Path.home() / '.codex/skills/relay-bridge', dirs_exist_ok=True)
    registry = json.loads((ROOT / 'config/workers.json').read_text(encoding='utf-8'))
    (Path.home() / '.codex/model-workers.json').write_text(json.dumps(registry, indent=2) + '\n', encoding='utf-8')
    install_startup_hook(Path.home() / '.codex/AGENTS.md')
    install_startup_hook(Path.home() / '.claude/CLAUDE.md')
    return {'global_skill_installed': True, 'agents_skill_installed': True, 'claude_takeover_installed': True,
            'registry_updated': True, 'codex_startup_hook': True, 'claude_startup_hook': True}

if __name__ == '__main__':
    print(json.dumps(install()))
