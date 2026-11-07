"""Persist participation preferences without making model or provider calls."""

import argparse
import copy
import json
import math
import sys
from datetime import datetime, timedelta
from pathlib import Path

from paths import ROOT
from task_store import timestamp, write_json
from usage_guard import file_lock

FAMILIES = ('OpenAI', 'Anthropic', 'xAI', 'Google')
FAMILY_WORKERS = dict(zip(FAMILIES, ('codex', 'claude', 'grok', 'gemini')))
TIERS = ('best', 'balanced', 'economy')
PRESETS = {
    name: dict(zip(FAMILIES, values))
    for name, values in (
        ('balanced', (25, 25, 25, 25)),
        ('code', (30, 40, 15, 15)),
        ('visual', (40, 20, 15, 25)),
        ('research', (25, 20, 20, 35)),
    )
}
SOURCES = ('default', 'startup', 'cli', 'viewer', 'visualizer')
MIN_SHARE = 5
MAX_SHARE = 85
MIN_TOLERANCE = 5
MAX_TOLERANCE = 25
DEFAULT_TOLERANCE = 10

_MIX_KEYS = {
    'schema_version', 'shares', 'tolerance', 'tiers',
    'preset', 'updated_at', 'source',
}
_RUN_KEYS = {'schema_version', 'run_id', 'current', 'history'}
_ALIASES = {
    alias.lower(): family
    for family in FAMILIES
    for alias in (family, FAMILY_WORKERS[family])
}


def family_name(text):
    if isinstance(text, str):
        result = _ALIASES.get(text.strip().lower())
        if result is not None:
            return result
    raise ValueError('Unknown family; choose OpenAI, Anthropic, xAI or Google')


def _keys(value, expected, label):
    if not isinstance(value, dict) or set(value) != set(expected):
        raise ValueError(f'{label} must contain exactly: {", ".join(sorted(expected))}')


def _integer(value, lower, upper, label):
    if type(value) is not int or not lower <= value <= upper:
        raise ValueError(f'{label} must be an integer from {lower} to {upper}')
    return value


def _choice(value, choices, label):
    if not isinstance(value, str) or value not in choices:
        raise ValueError(f'Invalid {label}')
    return value


def _shares(value):
    _keys(value, FAMILIES, 'Shares')
    result = {
        family: _integer(value[family], MIN_SHARE, MAX_SHARE, family + ' share')
        for family in FAMILIES
    }
    if sum(result.values()) != 100:
        raise ValueError('Shares must sum to 100')
    return result


def default_mix():
    return {
        'schema_version': 1,
        'shares': dict(PRESETS['balanced']),
        'tolerance': DEFAULT_TOLERANCE,
        'tiers': dict.fromkeys(FAMILIES, 'best'),
        'preset': 'balanced',
        'updated_at': timestamp(),
        'source': 'default',
    }


def validate_mix(value):
    """Validate persisted data and return an independent, ordered copy."""
    _keys(value, _MIX_KEYS, 'Mix')
    _integer(value['schema_version'], 1, 1, 'Schema version')
    shares = _shares(value['shares'])
    tolerance = _integer(
        value['tolerance'], MIN_TOLERANCE, MAX_TOLERANCE, 'Tolerance'
    )
    _keys(value['tiers'], FAMILIES, 'Tiers')
    tiers = {
        family: _choice(value['tiers'][family], TIERS, 'tier')
        for family in FAMILIES
    }
    preset = value['preset']
    if preset is not None:
        _choice(preset, PRESETS, 'preset')
        if shares != PRESETS[preset]:
            raise ValueError('Preset does not match shares')
    source = _choice(value['source'], SOURCES, 'source')
    updated_at = value['updated_at']
    if not isinstance(updated_at, str):
        raise ValueError('updated_at must be an ISO-8601 UTC timestamp')
    try:
        parsed = datetime.fromisoformat(updated_at.replace('Z', '+00:00'))
        if parsed.utcoffset() != timedelta(0):
            raise ValueError('Timestamp is not UTC')
    except (ValueError, OverflowError) as exc:
        raise ValueError('updated_at must be an ISO-8601 UTC timestamp') from exc
    return {
        'schema_version': 1,
        'shares': shares,
        'tolerance': tolerance,
        'tiers': tiers,
        'preset': preset,
        'updated_at': updated_at,
        'source': source,
    }


def rebalance(shares, family, value):
    """Move one slider; split the remainder using exact integer arithmetic."""
    shares = _shares(shares)
    family = family_name(family)
    if type(value) not in (int, float):
        raise ValueError('Slider value must be a finite number')
    if isinstance(value, float) and not math.isfinite(value):
        raise ValueError('Slider value must be a finite number')
    # Clamp before rounding to avoid overflow for very large finite inputs.
    value = max(MIN_SHARE, min(MAX_SHARE, value))
    value = math.floor(value + 0.5)
    others = [item for item in FAMILIES if item != family]
    extra = 100 - value - 3 * MIN_SHARE
    weights = [shares[item] - MIN_SHARE for item in others]
    if not any(weights):
        weights = [1, 1, 1]
    total = sum(weights)
    splits = [extra * weight // total for weight in weights]
    remainders = [extra * weight % total for weight in weights]
    ranking = sorted(range(3), key=lambda index: (-remainders[index], index))
    for index in ranking[:extra - sum(splits)]:
        splits[index] += 1
    result = {family: value}
    result.update(
        (item, MIN_SHARE + split) for item, split in zip(others, splits)
    )
    return {item: result[item] for item in FAMILIES}


def apply_updates(mix, *, preset=None, shares=None, family=None, value=None,
                  tolerance=None, tiers=None, source='cli'):
    result = validate_mix(mix)
    slider = family is not None or value is not None
    if slider and (family is None or value is None):
        raise ValueError('Both family and value are required')
    if sum((preset is not None, shares is not None, slider)) > 1:
        raise ValueError('Choose only one of preset, shares or family/value')
    if preset is not None:
        _choice(preset, PRESETS, 'preset')
        result['shares'] = dict(PRESETS[preset])
        result['preset'] = preset
    elif shares is not None or slider:
        result['shares'] = (
            _shares(shares) if shares is not None
            else rebalance(result['shares'], family, value)
        )
        result['preset'] = next(
            (name for name, candidate in PRESETS.items()
             if result['shares'] == candidate),
            None,
        )
    if tolerance is not None:
        result['tolerance'] = tolerance
    if tiers is not None:
        if not isinstance(tiers, dict):
            raise ValueError('Tier updates must be an object')
        seen = set()
        for name, tier in tiers.items():
            canonical = family_name(name)
            if canonical in seen:
                raise ValueError('Duplicate tier family')
            seen.add(canonical)
            result['tiers'][canonical] = tier
    result['source'] = source
    result['updated_at'] = timestamp()
    return validate_mix(result)


def global_path(root=None):
    return Path(ROOT if root is None else root) / 'runtime' / 'team-mix.json'


def run_path(run_dir):
    return Path(run_dir) / 'team-mix.json'


def _lock_path(path):
    return path.with_name(path.name + '.lock')


def _read_json(path):
    try:
        return json.loads(path.read_text(encoding='utf-8-sig'))
    except FileNotFoundError:
        raise
    except (OSError, UnicodeError, ValueError) as exc:
        raise ValueError(f'Could not read {path.name}') from exc


def read_global(root=None):
    try:
        value = _read_json(global_path(root))
    except FileNotFoundError:
        return default_mix()
    return validate_mix(value)


def save_global(mix, root=None):
    result = validate_mix(mix)
    path = global_path(root)
    with file_lock(_lock_path(path)):
        write_json(path, result)
    return result


def update_global(root=None, **updates):
    """Read, change and write the default for new runs under one lock."""
    path = global_path(root)
    with file_lock(_lock_path(path)):
        mix = apply_updates(read_global(root), **updates)
        write_json(path, mix)
    return mix


def _validate_run(value, run_dir):
    _keys(value, _RUN_KEYS, 'Run mix')
    _integer(value['schema_version'], 1, 1, 'Schema version')
    if value['run_id'] != Path(run_dir).name:
        raise ValueError('Run mix run_id does not match directory')
    history = value['history']
    if not isinstance(history, list) or not 1 <= len(history) <= 200:
        raise ValueError('Run history must contain 1 to 200 mixes')
    history = [validate_mix(item) for item in history]
    if history[0]['source'] != 'startup':
        raise ValueError('First history entry must have source startup')
    current = validate_mix(value['current'])
    if current != history[-1]:
        raise ValueError('Current mix must equal the last history entry')
    return {
        'schema_version': 1,
        'run_id': Path(run_dir).name,
        'current': current,
        'history': history,
    }


def read_run_mix(run_dir):
    try:
        value = _read_json(run_path(run_dir))
    except FileNotFoundError as exc:
        raise ValueError('Run mix has not been initialized') from exc
    return _validate_run(value, run_dir)


def init_run_mix(run_dir, mix):
    path = run_path(run_dir)
    with file_lock(_lock_path(path)):
        try:
            existing = _read_json(path)
        except FileNotFoundError:
            existing = None
        else:
            return _validate_run(existing, run_dir)
        startup = apply_updates(mix, source='startup')
        result = {
            'schema_version': 1,
            'run_id': Path(run_dir).name,
            'current': startup,
            'history': [copy.deepcopy(startup)],
        }
        write_json(path, result)
    return result


def retune_run(run_dir, **updates):
    path = run_path(run_dir)
    with file_lock(_lock_path(path)):
        try:
            metadata = _read_json(Path(run_dir) / 'run.json')
        except FileNotFoundError as exc:
            raise ValueError('Run metadata is missing') from exc
        if not isinstance(metadata, dict) or not isinstance(metadata.get('status'), str):
            raise ValueError('Run metadata must contain a status')
        if metadata['status'] == 'completed':
            raise ValueError('Completed runs cannot be retuned')
        result = read_run_mix(run_dir)
        current = apply_updates(result['current'], **updates)
        history = result['history'] + [copy.deepcopy(current)]
        if len(history) > 200:
            history = [history[0]] + history[-199:]
        result.update(current=current, history=history)
        write_json(path, result)
    return result


def lead_family(run_dir):
    try:
        coordinator = _read_json(Path(run_dir) / 'coordinator.json')
    except (FileNotFoundError, ValueError):
        return None
    if not isinstance(coordinator, dict):
        return None
    owner = coordinator.get('owner')
    if not isinstance(owner, str):
        return None
    return {'astra': 'OpenAI', 'sol': 'OpenAI', 'fable': 'Anthropic'}.get(owner)


def _tier_models():
    """Display labels for each family's tiers; never fails the description."""
    try:
        from model_tiers import labels
        return labels()
    except Exception:  # noqa: BLE001 - labels are optional decoration
        return None


def _description(mix, run_dir=None, history_count=0):
    return {
        'scope': 'global' if run_dir is None else 'run',
        'run_id': None if run_dir is None else Path(run_dir).name,
        'mix': validate_mix(mix),
        'lead_family': None if run_dir is None else lead_family(run_dir),
        'history_count': history_count,
        'families': list(FAMILIES),
        'presets': copy.deepcopy(PRESETS),
        'tiers': list(TIERS),
        'tier_models': _tier_models(),
        'limits': {
            'min_share': MIN_SHARE, 'max_share': MAX_SHARE,
            'min_tolerance': MIN_TOLERANCE, 'max_tolerance': MAX_TOLERANCE,
        },
        'model_calls': 0,
    }


def describe(run_dir=None, root=None):
    if run_dir is None:
        return _description(read_global(root))
    state = read_run_mix(run_dir)
    return _description(state['current'], run_dir, len(state['history']))


MAX_LISTED_RUNS = 50
_RECENT_TASKS = 400


def _object_or_none(path, limit=256 * 1024):
    try:
        if path.is_symlink() or not path.is_file() or path.stat().st_size > limit:
            return None
        value = json.loads(path.read_text(encoding='utf-8-sig'))
        return value if isinstance(value, dict) else None
    except (OSError, UnicodeError, ValueError):
        return None


def _candidate_runs(root):
    """Run folders in this installation plus those recent task outputs point into."""
    root = Path(ROOT if root is None else root)
    seen = {}
    local = root / '.orchestration'
    if local.is_dir():
        for path in local.iterdir():
            seen.setdefault(path.name, path)
    tasks = root / 'runs' / 'tasks'
    try:
        records = sorted((path for path in tasks.glob('*/record.json') if path.is_file()),
                         key=lambda path: path.stat().st_mtime_ns, reverse=True)[:_RECENT_TASKS]
    except OSError:
        records = []
    for path in records:
        record = _object_or_none(path, 512 * 1024)
        run_id, output = (record or {}).get('run_id'), (record or {}).get('requested_output')
        if not isinstance(run_id, str) or not isinstance(output, str) or run_id in seen:
            continue
        parts = Path(output).parts
        for index, part in enumerate(parts[:-1]):
            if part == '.orchestration' and parts[index + 1] == run_id:
                seen[run_id] = Path(*parts[:index + 2])
                break
    return seen.values()


def list_runs(root=None):
    """Retunable and legacy runs, open ones first; reads saved files only."""
    rows = []
    for path in _candidate_runs(root):
        manifest = _object_or_none(path / 'run.json')
        coordinator = _object_or_none(path / 'coordinator.json')
        if manifest is None or coordinator is None or path.is_symlink():
            continue
        policy = (_object_or_none(path / 'startup-context.json', 64 * 1024) or {}).get('provider_policy')
        schema = policy.get('schema_version') if isinstance(policy, dict) else None
        status = manifest.get('status') if isinstance(manifest.get('status'), str) else 'unknown'
        objective = manifest.get('objective') if isinstance(manifest.get('objective'), str) else ''
        rows.append({
            'run_id': path.name, 'path': str(path.resolve()),
            'title': (objective[:77] + '...') if len(objective) > 80 else (objective or path.name),
            'status': status, 'lead_family': lead_family(path), 'policy_schema': schema,
            'retunable': schema == 3 and status != 'completed' and run_path(path).is_file(),
            'updated_at': coordinator.get('updated_at') or manifest.get('created_utc'),
        })
    rows.sort(key=lambda row: str(row['updated_at'] or ''), reverse=True)
    rows.sort(key=lambda row: (row['status'] == 'completed', not row['retunable']))
    return {'runs': rows[:MAX_LISTED_RUNS], 'truncated': len(rows) > MAX_LISTED_RUNS, 'model_calls': 0}


def _parse_shares(text):
    abbreviations = dict(zip(('o', 'a', 'x', 'g'), FAMILIES))
    result = {}
    for assignment in text.split(','):
        name, separator, value = assignment.partition('=')
        if not separator:
            raise ValueError('Shares must use FAMILY=INTEGER assignments')
        canonical = abbreviations.get(name.strip().lower())
        if canonical is None:
            canonical = family_name(name)
        if canonical in result:
            raise ValueError('Duplicate share family')
        try:
            result[canonical] = int(value)
        except ValueError as exc:
            raise ValueError('Shares must be integers') from exc
    return _shares(result)


def _parse_tiers(groups):
    result = {}
    for group in groups or []:
        for assignment in group:
            name, separator, tier = assignment.partition('=')
            if not separator:
                raise ValueError('Tiers must use FAMILY=TIER assignments')
            canonical = family_name(name)
            if canonical in result:
                raise ValueError('Duplicate tier family')
            result[canonical] = _choice(tier.strip(), TIERS, 'tier')
    return result


class _Parser(argparse.ArgumentParser):
    def error(self, message):
        raise ValueError(message)


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    json_output = '--json' in argv
    parser = _Parser(prog='orchestrator.py mix')
    subcommands = parser.add_subparsers(dest='command', required=True)
    show = subcommands.add_parser('show')
    show.add_argument('--run', type=Path)
    show.add_argument('--json', action='store_true')
    presets = subcommands.add_parser('presets')
    presets.add_argument('--json', action='store_true')
    runs = subcommands.add_parser('runs')
    runs.add_argument('--json', action='store_true')
    setting = subcommands.add_parser('set')
    setting.add_argument('--run', type=Path)
    setting.add_argument('--json', action='store_true')
    selection = setting.add_mutually_exclusive_group()
    selection.add_argument('--preset', choices=tuple(PRESETS))
    selection.add_argument('--shares')
    selection.add_argument('--family')
    setting.add_argument('--value', type=float)
    setting.add_argument('--tolerance', type=int)
    setting.add_argument('--tier', nargs='+', action='append')
    setting.add_argument('--source', choices=('cli', 'viewer', 'visualizer'), default='cli')
    try:
        args = parser.parse_args(argv)
        if args.command == 'presets':
            result = copy.deepcopy(PRESETS)
        elif args.command == 'runs':
            result = list_runs()
        elif args.command == 'show':
            result = describe(args.run)
        else:
            updates = {
                'preset': args.preset,
                'shares': None if args.shares is None else _parse_shares(args.shares),
                'family': args.family,
                'value': args.value,
                'tolerance': args.tolerance,
                'tiers': _parse_tiers(args.tier),
                'source': args.source,
            }
            if args.run is not None:
                state = retune_run(args.run, **updates)
                result = _description(state['current'], args.run, len(state['history']))
            else:
                result = _description(update_global(**updates))
        print(json.dumps(result, indent=None if json_output else 2))
        return 0
    except (ValueError, OSError, TimeoutError) as exc:
        if json_output:
            print(json.dumps({'error': str(exc)}))
        else:
            print(f'Error: {exc}', file=sys.stderr)
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
