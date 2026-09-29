"""Content-free memory activity for the contribution map; credit stays separate."""
import math


def _number(value, integer=False):
    try:
        if type(value) not in (int, float) or not math.isfinite(value) or value < 0:
            return None
        if integer and type(value) is not int:
            return None
        return value
    except OverflowError:
        return None


def memory_activity(events):
    """Project explicit recall events only, deduplicated by their trace/task ID.

    Optional `jev` contains current-invocation metadata from a verified recall
    receipt. Never infer calls from a token count, parse prose, or add origin_usage
    from cached decisions. Absent telemetry remains unknown, including legacy
    events. No query, content, IDs, reasons, model names or source paths are emitted.
    """
    rows = {}
    for event in events:
        if event.get('kind') == 'memory_recall':
            # A repeated display/import of a trace is not another invocation.
            key = event['task_id']
            current = rows.get(key)
            if current is None or (not isinstance(current.get('jev'), dict)
                                   and isinstance(event.get('jev'), dict)):
                rows[key] = event
    if not rows:
        return None
    records = list(rows.values())
    samples = []
    for event in records:
        jev = event.get('jev') if isinstance(event.get('jev'), dict) else {}
        cache = jev.get('cache') if isinstance(jev.get('cache'), dict) else {}
        sample = {key: _number(jev.get(key), integer=key != 'elapsed_ms' and key != 'cost_usd')
                  for key in ('provider_calls', 'elapsed_ms', 'cost_usd', 'candidate_count',
                              'scored_candidate_count', 'returned_count')}
        for key in ('input_tokens', 'output_tokens'):
            # Explicit current Jev metadata wins, including null and zero.
            sample[key] = _number(jev.get(key) if key in jev else event.get(key), integer=True)
        sample['order_changed'] = jev.get('order_changed') if type(jev.get('order_changed')) is bool else None
        sample['cache_hit'] = (cache['status'] == 'hit') if cache.get('status') in ('hit', 'miss') else None
        sample['low_confidence'] = (jev['status'] == 'low_confidence') if jev.get('status') in (
            'ok', 'low_confidence', 'partial', 'error', 'disabled', 'blocked', 'skipped', 'unavailable') else None
        samples.append(sample)

    def metric(key):
        values = [row[key] for row in samples if row[key] is not None]
        value = _number(sum(values)) if values else None
        return {'value': value, 'recorded': len(values), 'total': len(samples)}

    return {'recalls': len(records), **{key: metric(key) for key in (
        'provider_calls', 'elapsed_ms', 'cost_usd', 'input_tokens', 'output_tokens',
        'candidate_count', 'scored_candidate_count', 'returned_count',
        'order_changed', 'cache_hit', 'low_confidence')}}
