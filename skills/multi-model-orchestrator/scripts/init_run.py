"""Compatibility entry point; maintained application code lives in Agent-Orchestrator."""
import json
from pathlib import Path
import sys
_candidate = Path(__file__).resolve().parents[3] / 'app'
if not (_candidate / 'paths.py').is_file():
    _registry = json.loads((Path.home() / '.codex/model-workers.json').read_text(encoding='utf-8'))
    _candidate = Path(_registry['application_root']) / 'app'
_target = _candidate / Path(__file__).name
sys.path.insert(0, str(_candidate))
__file__ = str(_target)
exec(compile(_target.read_bytes(), str(_target), 'exec'), globals())
