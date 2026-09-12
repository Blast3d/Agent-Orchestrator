"""Install this application's global skill and discovery registry; no model calls."""
import json
from pathlib import Path
import shutil

ROOT = Path(__file__).resolve().parents[1]

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
    return {'global_skill_installed': True, 'claude_takeover_installed': True, 'registry_updated': True}

if __name__ == '__main__':
    print(json.dumps(install()))
