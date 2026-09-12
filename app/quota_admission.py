"""Advisory quota admission (stdlib only). Does not import usage_guard."""
from __future__ import annotations

import math
from datetime import datetime, timezone
from typing import Any


def _require_aware_utc(moment: datetime, label: str) -> datetime:
    if not isinstance(moment, datetime):
        raise ValueError(f'{label} must be a datetime')
    if moment.tzinfo is None or moment.utcoffset() is None:
        raise ValueError(f'{label} must be timezone-aware UTC')
    return moment.astimezone(timezone.utc)


def _instant(value: Any, label: str) -> datetime:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f'{label} is not a valid timestamp')
    text = value.strip()
    if text.endswith('Z'):
        text = text[:-1] + '+00:00'
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError as exc:
        raise ValueError(f'{label} is not a valid timestamp') from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError(f'{label} is not a valid timestamp')
    return parsed.astimezone(timezone.utc)


def _finite_number(value: Any, label: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f'{label} must be a finite number')
    number = float(value)
    if not math.isfinite(number):
        raise ValueError(f'{label} must be a finite number')
    return number


def _percent(value, label, *, positive=False):
    number = _finite_number(value, label)
    if not 0 <= number <= 100 or (positive and number == 0):
        raise ValueError(f'{label} must be within 0..100')
    return number


def _keys(value, label):
    if (not isinstance(value, list) or any(not isinstance(k, str) or not k for k in value)
            or len(set(value)) != len(value)):
        raise ValueError(f'{label} must contain unique pool IDs')
    return value


def _require_mapping(value: Any, label: str) -> dict:
    if not isinstance(value, dict):
        raise ValueError(f'{label} must be a mapping')
    return value


def _validate_policy(policy: dict) -> None:
    _require_mapping(policy, 'policy')
    pools = policy.get('worker_pools')
    estimates = policy.get('estimates_pct')
    if not isinstance(pools, dict) or not pools:
        raise ValueError('policy.worker_pools is required')
    if not isinstance(estimates, dict) or not estimates:
        raise ValueError('policy.estimates_pct is required')
    for worker, keys in pools.items():
        if not isinstance(worker, str) or not worker:
            raise ValueError('invalid worker name in policy')
        _keys(keys, f'policy.worker_pools[{worker}]')
        for key in keys:
            if not isinstance(key, str) or not key:
                raise ValueError(f'invalid pool id for worker {worker}')
    for size, estimate in estimates.items():
        if not isinstance(size, str) or not size:
            raise ValueError('invalid task size in policy')
        _percent(estimate, f'policy.estimates_pct[{size}]', positive=True)
    if 'floor_pct' in policy:
        _finite_number(policy['floor_pct'], 'policy.floor_pct')
    if 'warning_pct' in policy:
        _finite_number(policy['warning_pct'], 'policy.warning_pct')
    if 'worker_start_threshold_pct' in policy:
        _percent(policy['worker_start_threshold_pct'], 'policy.worker_start_threshold_pct')


def _provider_for(key: str) -> str:
    if key.startswith('agy:'):
        return 'antigravity'
    return key.split('-', 1)[0]


def _pool_keys(policy: dict, data: dict, worker: str) -> list[str]:
    configured = list(policy['worker_pools'][worker])
    declared = data.get('worker_pools', {})
    if not isinstance(declared, dict):
        raise ValueError('data.worker_pools must be a mapping')
    if worker in declared:
        keys = declared[worker]
        _keys(keys, f'data.worker_pools[{worker}]')
        return list(keys)
    return configured


def _reservation_hold(reservations: dict, key: str, observed: datetime | None) -> float:
    held = 0.0
    for res_id, reservation in reservations.items():
        rec = _require_mapping(reservation, f'reservations[{res_id}]')
        pools = rec.get('pools')
        _keys(pools, f'reservations[{res_id}].pools')
        estimate = _percent(rec.get('estimate_pct'), f'reservations[{res_id}].estimate_pct')
        finished = rec.get('finished_at')
        if finished is not None:
            finished_at = _instant(finished, f'reservations[{res_id}].finished_at')
            if observed is not None and finished_at < observed:
                continue
        if key not in pools:
            continue
        held += estimate
    return held


def _active_cooldown(cooldown: Any, label: str, now: datetime) -> bool:
    rec = _require_mapping(cooldown, label)
    until = _instant(rec.get('until'), f'{label}.until')
    return until > now


def _window_freshness(now: datetime, observed: datetime, max_age_seconds: float, refresh_failed: bool) -> str:
    if refresh_failed:
        return 'cached'
    if (now - observed).total_seconds() > max_age_seconds:
        return 'cached'
    return 'fresh'


def evaluate_advisory(policy, data, worker, size, current_time):
    """Decide whether advisory admission should start work without blocking on stale quota."""
    _validate_policy(policy)
    now = _require_aware_utc(current_time, 'current_time')
    payload = _require_mapping(data, 'data')
    if worker not in policy['worker_pools']:
        raise ValueError('Unknown worker')
    if size not in policy['estimates_pct']:
        raise ValueError('Unknown task size')

    windows = _require_mapping(payload.get('windows', {}), 'windows')
    reservations = _require_mapping(payload.get('reservations', {}), 'reservations')
    cooldowns = _require_mapping(payload.get('cooldowns', {}), 'cooldowns')
    refresh_errors = _require_mapping(payload.get('refresh_errors', {}), 'refresh_errors')

    keys = _pool_keys(policy, payload, worker)
    estimate = _finite_number(policy['estimates_pct'][size], f'policy.estimates_pct[{size}]')
    threshold = float(policy.get('worker_start_threshold_pct', 20))
    local_only = (not keys) and worker == 'local-chat'
    result = {
        'worker': worker,
        'size': size,
        'allowed': True,
        'status': 'local' if local_only else 'ready',
        'estimate_pct': 0 if local_only else estimate,
        'reasons': [],
        'windows': [],
        'warnings': [],
        'admission_mode': 'advisory',
        'threshold_pct': threshold,
        'reading_status': 'unknown' if not keys else 'fresh',
        'provider_refresh_errors': {},
        'cooldown_active_pools': {},
    }

    configured = set(policy['worker_pools'][worker])
    cooldown_targets = set() if local_only else set(keys) | configured
    # Dynamic account windows can be temporarily absent. Match their owner;
    # a rejection from another provider must never hold this worker or local chat.
    owner = 'antigravity' if worker in ('gemini', 'antigravity-claude') else worker.split('-')[0]
    if not local_only:
        cooldown_targets.update(k for k in cooldowns if _provider_for(k) == owner)
    _reservation_hold(reservations, None, None)  # Validate even when no window exists.
    for key, cooldown in cooldowns.items():
        if not isinstance(key, str) or not key:
            raise ValueError('invalid cooldown pool ID')
        active = _active_cooldown(cooldown, f'cooldowns[{key}]', now)
        if active and key in cooldown_targets:
            result['cooldown_active_pools'][key] = cooldown['until']

    for provider, failure in refresh_errors.items():
        rec = _require_mapping(failure, f'refresh_errors[{provider}]')
        _instant(rec.get('at'), f'refresh_errors[{provider}].at')
        if provider == owner or any(_provider_for(k) == provider for k in keys):
            result['provider_refresh_errors'][provider] = dict(rec)
    if not keys and not local_only:
        result['warnings'].append('No account reading is available; usage collection runs in the background')

    seen_windows = []
    statuses = []

    for key in keys:
        if not isinstance(key, str) or not key:
            raise ValueError('invalid pool id')
        window = windows.get(key)
        provider = _provider_for(key)
        failure = refresh_errors.get(provider)
        failure_at = None
        if failure is not None:
            failure_rec = _require_mapping(failure, f'refresh_errors[{provider}]')
            failure_at = _instant(failure_rec.get('at'), f'refresh_errors[{provider}].at')

        if key not in windows:
            result['warnings'].append(
                f'{key}: usage unknown; refresh or supply a current account reading'
            )
            statuses.append('unknown')
            cooldown = cooldowns.get(key)
            if key in result['cooldown_active_pools']:
                result['reasons'].append(f'{key}: provider rejected work; cooldown active')
            continue

        rec = _require_mapping(window, f'windows[{key}]')
        observed = _instant(rec.get('observed_at'), f'windows[{key}].observed_at')
        if observed > now:
            raise ValueError(f'windows[{key}].observed_at is in the future')
        remaining = _percent(rec.get('remaining_pct'), f'windows[{key}].remaining_pct')
        max_age = _finite_number(rec.get('max_age_seconds'), f'windows[{key}].max_age_seconds')
        if max_age < 0:
            raise ValueError(f'windows[{key}].max_age_seconds must be a finite number')
        source = rec.get('source')
        if not isinstance(source, str) or not source:
            raise ValueError(f'windows[{key}].source is required')

        reset_at = rec.get('reset_at')
        reset_passed = False
        if reset_at is not None:
            reset_moment = _instant(reset_at, f'windows[{key}].reset_at')
            reset_passed = reset_moment <= now

        refresh_failed = bool(failure_at and failure_at >= observed)
        freshness = _window_freshness(now, observed, max_age, refresh_failed)
        if refresh_failed:
            result['warnings'].append(f'{key}: latest quota refresh failed')
        if (now - observed).total_seconds() > max_age:
            result['warnings'].append(f'{key}: reading is stale')
        if reset_passed:
            result['warnings'].append(f'{key}: reset boundary passed; fetch a fresh reading')

        cooldown = cooldowns.get(key)
        if key in result['cooldown_active_pools']:
            result['reasons'].append(f'{key}: provider rejected work; cooldown active')

        held = _reservation_hold(reservations, key, observed)
        available = max(0.0, remaining - held)
        seen_windows.append({
            'id': key,
            'remaining_pct': round(remaining, 2),
            'reserved_pct': round(held, 2),
            'available_pct': round(available, 2),
            'reset_at': rec.get('reset_at'),
            'reset_display': rec.get('reset_display'),
            'observed_at': rec['observed_at'],
            'max_age_seconds': rec['max_age_seconds'],
            'source': rec['source'],
            'freshness': freshness,
            'reset_passed': reset_passed,
        })
        statuses.append('unknown' if reset_passed else freshness)
        if not reset_passed and available <= threshold:
            result['reasons'].append(
                f'{key}: available allowance is at or below the worker start threshold'
            )

    extra_cooldowns = [key for key in cooldown_targets if key not in keys]
    for key in extra_cooldowns:
        cooldown = cooldowns.get(key)
        if key in result['cooldown_active_pools']:
            result['reasons'].append(f'{key}: provider rejected work; cooldown active')

    result['windows'] = seen_windows
    if 'unknown' in statuses:
        result['reading_status'] = 'unknown'
    elif 'cached' in statuses:
        result['reading_status'] = 'cached'
    elif 'fresh' in statuses:
        result['reading_status'] = 'fresh'
    else:
        result['reading_status'] = 'unknown'

    if result['reasons']:
        result['allowed'] = False
        result['status'] = 'held'
    elif local_only:
        result['status'] = 'local'
        result['reading_status'] = 'unknown'
    else:
        result['status'] = 'ready'
        result['allowed'] = True
    return result
