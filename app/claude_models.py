"""Fresh, model-specific Claude defaults and reversible user-requested pauses."""
import json
import re

from paths import ROOT

_MODEL = re.compile(r'[A-Za-z0-9][A-Za-z0-9._:-]*(?:\[[A-Za-z0-9._-]+\])?')
_FAMILY = re.compile(r'[a-z][a-z0-9-]*')


def _configuration():
    path = ROOT / 'config/workers.json'
    try:
        if path.stat().st_size > 512 * 1024:
            raise ValueError('Claude model policy exceeds its size limit')
        config = json.loads(path.read_text(encoding='utf-8-sig'))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ValueError('Claude model policy could not be read; no Claude model may start') from exc
    if not isinstance(config, dict) or not isinstance(config.get('policy', {}), dict):
        raise ValueError('Claude model policy must contain an object')
    paused = config.get('policy', {}).get('paused_claude_model_families', [])
    if (not isinstance(paused, list)
            or any(not isinstance(family, str) or not _FAMILY.fullmatch(family) for family in paused)):
        raise ValueError('Paused Claude model families must be a list of lowercase family names')
    return config


def _allowed(model, config):
    if not isinstance(model, str) or not _MODEL.fullmatch(model.strip()):
        raise ValueError('Choose a valid Claude model name before starting work')
    model = model.strip()
    base = model.lower().split('[', 1)[0]
    paused = config.get('policy', {}).get('paused_claude_model_families', [])
    if base == 'best' and 'fable' in paused:
        raise ValueError('Claude Fable is paused by the user; the best alias can select Fable, so choose an explicit allowed model')
    for family in paused:
        if any(base == prefix or base.startswith(prefix + '-') for prefix in (family, 'claude-' + family)):
            raise ValueError('Claude ' + family.title() + ' is paused by the user until further notice')
    return model


def require_model_allowed(model):
    """Check an explicit or previously recorded model against the current pause."""
    return _allowed(model, _configuration())


def select_model(role='worker', requested=None):
    """Resolve one concrete model before freezing a task or coordinator receipt."""
    if role not in ('worker', 'coordinator'):
        raise ValueError('Select the worker or coordinator Claude model role')
    config = _configuration()
    if requested is None:
        if role == 'coordinator':
            handoff = config.get('coordinator_handoff', {})
            if not isinstance(handoff, dict):
                raise ValueError('Claude coordinator model settings must be an object')
            requested = handoff.get('backup_model', 'claude-fable-5')
        else:
            workers = config.get('workers', [])
            if not isinstance(workers, list) or any(not isinstance(worker, dict) for worker in workers):
                raise ValueError('Claude worker model settings must be a list of worker objects')
            matches = [worker for worker in workers if worker.get('id') == 'claude']
            if len(matches) > 1:
                raise ValueError('Claude worker model settings contain duplicate worker records')
            requested = matches[0].get('requested_model', 'sonnet') if matches else 'sonnet'
    return _allowed(requested, config)
