"""Worker model tiers. Resolution never starts a provider."""
from copy import deepcopy
import json
from pathlib import Path

from paths import CONFIG

TIERS = ('best', 'balanced', 'economy')
FAMILY_WORKERS = {
    'OpenAI': 'codex',
    'Anthropic': 'claude',
    'xAI': 'grok',
    'Google': 'gemini',
}
DEFAULT_LADDERS = {
    'codex': {
        'best': {'model': 'astra', 'effort': 'medium'},
        'balanced': {'model': 'sol', 'effort': 'medium'},
        'economy': {'model': 'sol', 'effort': 'low'},
    },
    'claude': {
        'best': {'model': 'opus', 'effort': 'medium'},
        'balanced': {'model': 'sonnet', 'effort': 'medium'},
        'economy': {'model': 'haiku', 'effort': 'low'},
    },
    'grok': {
        'best': {'model': 'grok-4.6', 'effort': 'medium'},
        'balanced': {'model': 'grok-4.6', 'effort': 'low'},
        'economy': {'model': 'grok-4.6', 'effort': 'low'},
    },
    'gemini': {
        tier: {'model': 'gemini-3.8-flash-high', 'effort': None}
        for tier in TIERS
    },
}


class Ladders(dict):
    """Dictionary-compatible ladders with diagnostics and route validation."""

    def __init__(self, values, reasons, validation):
        super().__init__(values)
        self.reasons = tuple(reasons)
        self.validation = validation


def _configuration(config):
    if config is not None:
        if not isinstance(config, dict):
            raise ValueError('Worker configuration must be an object')
        return config
    path = Path(CONFIG) / 'workers.json'
    try:
        if path.stat().st_size > 512 * 1024:
            raise ValueError('Worker configuration exceeds its size limit')
        value = json.loads(path.read_text(encoding='utf-8-sig'))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ValueError('Worker configuration could not be read') from exc
    if not isinstance(value, dict):
        raise ValueError('Worker configuration must be an object')
    return value


def _gemini_models():
    """Models listed by the saved Antigravity boundary proof, plus the route's fixed model."""
    import antigravity_boundary as boundary

    try:
        proof = json.loads(Path(boundary.VALIDATION).read_text(encoding='utf-8-sig'))
        models = boundary.verified_models(proof)
    except (OSError, UnicodeError, ValueError, TypeError) as exc:
        raise ValueError('Saved Gemini model proof is unavailable or invalid') from exc
    if not isinstance(models, (list, tuple, set, frozenset)):
        raise ValueError('Gemini verified models must be a collection')
    if any(not isinstance(model, str) for model in models):
        raise ValueError('Gemini verified models must contain strings')
    return set(models), boundary.MODEL


def validate_selection(worker, model, effort, config=None):
    """Return (verified, reason); never replace a rejected model silently."""
    try:
        config = _configuration(config)
        if worker not in DEFAULT_LADDERS:
            raise ValueError('Worker has no model tier ladder')
        if not isinstance(model, str) or not model.strip():
            raise ValueError('Model must be a nonempty string')
        if worker == 'gemini':
            if effort is not None:
                raise ValueError('Gemini does not accept a reasoning effort')
        elif effort not in ('low', 'medium', 'high'):
            raise ValueError('Reasoning effort must be low, medium or high')

        if worker == 'codex':
            from codex_worker import select_codex_model
            select_codex_model(model)
        elif worker == 'claude':
            from claude_models import require_model_allowed
            # Always consult the current route policy, even with injected config.
            require_model_allowed(model)
        elif worker == 'grok':
            workers = config.get('workers', [])
            if (not isinstance(workers, list)
                    or any(not isinstance(item, dict) for item in workers)):
                raise ValueError('Worker settings must be a list of objects')
            matches = [item for item in workers if item.get('id') == 'grok']
            if len(matches) > 1:
                raise ValueError('Duplicate Grok worker records')
            allowed = (
                matches[0].get('verified_models', ['grok-4.6'])
                if matches else ['grok-4.6']
            )
            if (not isinstance(allowed, list)
                    or any(not isinstance(item, str) for item in allowed)):
                raise ValueError('Grok verified_models must be a list of strings')
            if model not in allowed:
                raise ValueError('Grok model has not been verified: ' + model)
        else:
            allowed, command_model = _gemini_models()
            if model not in allowed:
                raise ValueError('Gemini model has not been verified: ' + model)
            if model != command_model:
                raise ValueError(
                    'Gemini transport cannot select the requested model: ' + model
                )
    except (ValueError, OSError, ImportError) as exc:
        return False, str(exc)
    return True, None


def _merge_overrides(values, overrides):
    if not isinstance(overrides, dict):
        raise ValueError('model_tiers must be an object')
    for worker, tiers in overrides.items():
        if worker not in values or not isinstance(tiers, dict):
            raise ValueError('Invalid worker in model_tiers')
        for tier, selection in tiers.items():
            if tier not in TIERS or not isinstance(selection, dict):
                raise ValueError('Invalid tier in model_tiers')
            if set(selection) - {'model', 'effort'}:
                raise ValueError('Unknown model_tiers selection field')
            merged = {**values[worker][tier], **selection}
            if not isinstance(merged['model'], str) or not merged['model'].strip():
                raise ValueError('Tier model must be a nonempty string')
            effort = merged['effort']
            if worker == 'gemini':
                if effort is not None:
                    raise ValueError('Gemini tier effort must be null')
            elif effort not in ('low', 'medium', 'high'):
                raise ValueError('Invalid tier reasoning effort')
            values[worker][tier] = merged


def ladders(config=None):
    """Merge partial overrides, recording malformed-policy fallback reasons.

    Structurally invalid overrides fall back as a unit. Structurally valid but
    unverified selections remain visible and fail route validation; replacing
    those with defaults would hide a rejected model request.
    """
    reasons = []
    config_error = None
    try:
        config = _configuration(config)
    except ValueError as exc:
        config_error = str(exc)
        reasons.append(config_error)
        config = {}

    values = deepcopy(DEFAULT_LADDERS)
    if 'model_tiers' not in config:
        reasons.append('model_tiers is missing; using default ladders')
    else:
        try:
            _merge_overrides(values, config['model_tiers'])
        except ValueError as exc:
            values = deepcopy(DEFAULT_LADDERS)
            reasons.append('Invalid model_tiers; using defaults: ' + str(exc))

    validation = {}
    for worker, tiers in values.items():
        validation[worker] = {}
        for tier, selection in tiers.items():
            validation[worker][tier] = (
                (False, config_error) if config_error else
                validate_selection(worker, **selection, config=config)
            )
    return Ladders(values, reasons, validation)


def _mix_tier(mix, family):
    if not isinstance(mix, dict):
        raise ValueError('Mix must be an object')
    tiers = mix.get('tiers', {})
    if not isinstance(tiers, dict):
        raise ValueError('Mix tiers must be an object')
    value = tiers.get(family)
    if value is not None and value not in TIERS:
        raise ValueError('Invalid mix tier for ' + family)
    return value


def resolve(worker, tier=None, *, run_dir=None, config=None,
            read_run=None, read_global=None):
    """Resolve explicit > run > global > best, retaining fallback diagnostics."""
    if worker not in DEFAULT_LADDERS:
        raise ValueError('Worker has no model tier ladder')
    if tier is not None and tier not in TIERS:
        raise ValueError('Choose best, balanced or economy')

    table = ladders(config)
    reasons = list(table.reasons)
    family = next(key for key, value in FAMILY_WORKERS.items() if value == worker)
    selected, source = tier, 'explicit'

    if selected is None and run_dir is not None:
        if read_run is None:
            from team_mix import read_run_mix
            read_run = read_run_mix
        try:
            run = read_run(run_dir)
            if not isinstance(run, dict) or 'current' not in run:
                raise ValueError('Run mix has no current object')
            selected = _mix_tier(run['current'], family)
            if selected is not None:
                source = 'run'
        except ValueError as exc:
            reasons.append('Run tier unavailable: ' + str(exc))

    if selected is None:
        if read_global is None:
            from team_mix import read_global as global_reader
            read_global = global_reader
        try:
            selected = _mix_tier(read_global(), family)
            if selected is not None:
                source = 'global'
        except ValueError as exc:
            reasons.append('Global tier unavailable: ' + str(exc))

    if selected is None:
        selected, source = 'best', 'default'

    verified, reason = table.validation[worker][selected]
    if reason:
        reasons.append(reason)
    return {
        'tier': selected,
        **table[worker][selected],
        'source': source,
        'verified': verified,
        'reason': '; '.join(reasons) or None,
    }


def resolve_request(worker, tier=None, *, model=None, effort=None, run_dir=None,
                    config=None, read_run=None, read_global=None):
    """Dispatcher adapter for legacy explicit model and effort flags."""
    if tier is None and model is not None:
        selection = {
            'tier': None,
            'model': model,
            'effort': effort if effort is not None else 'medium',
            'source': 'explicit',
            'verified': False,
            'reason': None,
        }
        verified, reason = validate_selection(
            worker, selection['model'], selection['effort'], config
        )
        selection.update(verified=verified, reason=reason)
        return selection

    selection = resolve(
        worker, tier, run_dir=run_dir, config=config,
        read_run=read_run, read_global=read_global,
    )
    if effort is not None:
        selection['effort'] = effort
        verified, reason = validate_selection(
            worker, selection['model'], effort, config
        )
        # An effort override cannot repair a failed policy/configuration read.
        selection['verified'] = selection['verified'] and verified
        messages = [selection['reason'], reason, 'Explicit effort override']
        selection['reason'] = '; '.join(item for item in messages if item)
    return selection


def labels(config=None):
    table = ladders(config)
    names = {
        'astra': 'GPT-6-Astra',
        'sol': 'GPT-6-Sol',
        'opus': 'Claude Opus',
        'sonnet': 'Claude Sonnet',
        'haiku': 'Claude Haiku',
        'grok-4.6': 'Grok 4.6',
        'gemini-3.8-flash-high': 'Gemini 3.8 Flash High',
    }
    result = {}
    for family, worker in FAMILY_WORKERS.items():
        result[family] = {}
        for tier, selection in table[worker].items():
            label = names.get(selection['model'], selection['model'])
            if selection['effort'] is not None:
                label += ' · ' + selection['effort']
            if not table.validation[worker][tier][0]:
                label += ' (unverified)'
            result[family][tier] = label
    return result


def main(argv=None):
    """Print the effective ladders; reads configuration only, never starts a model."""
    import argparse
    parser = argparse.ArgumentParser(description=main.__doc__)
    parser.add_argument('--json', action='store_true')
    args = parser.parse_args(argv)
    table = ladders()
    report = {'ladders': {worker: {tier: dict(selection, verified=table.validation[worker][tier][0],
                                              reason=table.validation[worker][tier][1])
                                   for tier, selection in tiers.items()}
                          for worker, tiers in table.items()},
              'labels': labels(), 'reasons': list(table.reasons), 'model_calls': 0}
    if args.json:
        print(json.dumps(report, indent=2))
    else:
        for family, worker in FAMILY_WORKERS.items():
            row = ', '.join(f'{tier}: {label}' for tier, label in report['labels'][family].items())
            print(f'{family:<9} ({worker}) {row}')
        for reason in report['reasons']:
            print('Note:', reason)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
