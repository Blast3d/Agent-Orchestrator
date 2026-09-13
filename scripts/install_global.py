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

For substantial coding, research, or multi-agent project work, use the installed
`multi-model-orchestrator` skill without waiting for the user to request it.
Small self-contained questions and routine edits can stay with the current lead.

Before new worker assignments, run `python "{root / 'orchestrator.py'}" start`
with the workspace, objective and a short run name, or resume the exact existing
run with its current owner/session/generation. Read the returned operating guide
and scoped Brain recall. Follow `{root / 'docs' / 'startup-and-closeout.md'}`.
After a checkpoint or handoff changes the generation, reload that run's startup
context. Never choose an unrelated latest run automatically.

Give every native worker the operating guide and relevant authorized recall in
its actual assignment. The guarded dispatcher supplies operating guidance itself,
even with project recall disabled. Keep each provider's content permissions and
task scope; loading memory does not authorize broader access or extra spending.

Before final orchestration delivery, review tasks, verify capture outcomes,
complete the contribution ledger/map, and run `orchestrator.py closeout`.
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


def install():
    source = ROOT / 'skills/multi-model-orchestrator'
    destination = Path.home() / '.codex/skills/multi-model-orchestrator'
    shutil.copytree(source, destination, dirs_exist_ok=True, ignore=shutil.ignore_patterns('__pycache__'))
    claude_skills = Path.home() / '.claude/skills'
    shutil.copytree(source, claude_skills / 'multi-model-orchestrator', dirs_exist_ok=True,
                    ignore=shutil.ignore_patterns('__pycache__'))
    shutil.copytree(ROOT / 'skills/orchestrator-takeover', claude_skills / 'orchestrator-takeover',
                    dirs_exist_ok=True, ignore=shutil.ignore_patterns('__pycache__'))
    registry = json.loads((ROOT / 'config/workers.json').read_text(encoding='utf-8'))
    (Path.home() / '.codex/model-workers.json').write_text(json.dumps(registry, indent=2) + '\n', encoding='utf-8')
    install_startup_hook(Path.home() / '.codex/AGENTS.md')
    install_startup_hook(Path.home() / '.claude/CLAUDE.md')
    return {'global_skill_installed': True, 'claude_takeover_installed': True,
            'registry_updated': True, 'codex_startup_hook': True, 'claude_startup_hook': True}

if __name__ == '__main__':
    print(json.dumps(install()))
