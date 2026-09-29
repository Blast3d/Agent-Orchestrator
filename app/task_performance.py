"""Version-one measured task performance for source-bound recall; no I/O."""
from datetime import datetime
import json
import math

from experiment_usage import normalize_usage


def number(value):
    try:
        return value if type(value) in (int, float) and value >= 0 and math.isfinite(value) else None
    except OverflowError:
        return None


def seconds(start, end):
    try:
        a, b = (datetime.fromisoformat(value.replace('Z', '+00:00')) for value in (start, end))
        if a.tzinfo is None or b.tzinfo is None:
            return None
        value = (b - a).total_seconds()
        return round(value, 3) if value >= 0 else None
    except (AttributeError, TypeError, ValueError, OverflowError):
        return None


def snapshot(result):
    """Freeze v1 field semantics; billing, model tokens and recall stay separate."""
    result = result if isinstance(result,dict) else {}
    worker = result.get('worker')
    worker = worker if isinstance(worker,str) else None
    provider = {'claude':'anthropic','grok':'xai','gemini':'google','codex':'openai'}.get(worker)
    usage = normalize_usage(dict(provider=provider, usage=result.get('usage'), billing=result.get('billing')))
    phases = result.get('phase_durations_ms')
    phases = phases if isinstance(phases, dict) else {}
    execution = number(phases.get('provider_execution'))
    model_usage = result.get('modelUsage')
    model_usage = model_usage if isinstance(model_usage, dict) and len(model_usage) <= 16 else {}
    models = sorted(key[:80] for key in model_usage if isinstance(key, str))[:6]
    costs = [number(value.get('costUSD')) if isinstance(value, dict) else None for value in model_usage.values()]
    reported_cost = sum(costs) if costs and all(value is not None for value in costs) else None
    payload = result.get('provider_result')
    if reported_cost is None and isinstance(payload, dict):
        reported_cost = number(payload.get('total_cost_usd'))
    reported_cost = number(reported_cost)
    context = result.get('memory_context')
    context = context if isinstance(context, dict) else {}
    retrieval = context.get('retrieval')
    retrieval = retrieval if isinstance(retrieval, dict) else {}
    jev = retrieval.get('jev')
    jev = jev if isinstance(jev, dict) else {}
    ids = context.get('ids')
    supplied_count = len(ids) if isinstance(ids, list) and len(ids) <= 200 else None
    recalled_count = supplied_count
    delivery = context.get('delivery')
    if (isinstance(delivery, dict) and supplied_count is not None
            and delivery.get('reason') == 'worker transport byte limit'
            and type(delivery.get('retrieved_count')) is int
            and supplied_count <= delivery['retrieved_count'] <= 24
            and delivery.get('delivered_count') == supplied_count):
        recalled_count = delivery['retrieved_count']
    result = {'schema_version':1, 'worker':worker[:60] if isinstance(worker,str) else None,
              'task_size':result.get('size') if result.get('size') in ('tiny','small','medium','large') else None,
              'models':models, 'wall_seconds':seconds(result.get('created_at'),result.get('finalized_at')),
              'execution_seconds':round(execution / 1000,3) if execution is not None else seconds(result.get('started_at'),result.get('ended_at')),
              'input_tokens':usage['input_tokens'], 'output_tokens':usage['output_tokens'],
              'cached_input_tokens':usage['cached_input_tokens'],
              'provider_reported_cost_usd':reported_cost,
              'verified_additional_charge_usd':usage['additional_charge_usd'],
              'recall_ms':number(context.get('elapsed_ms')),
              'memories_recalled':recalled_count,
              'memories_supplied':supplied_count
                  if context.get('execution_requested') is True else (0 if context.get('execution_requested') is False else None),
              'jev_calls':number(jev.get('provider_calls')), 'jev_cost_usd':number(jev.get('cost_usd'))}
    return result


def measured(value):
    return any(value.get(key) is not None for key in ('wall_seconds','execution_seconds',
        'input_tokens','output_tokens','provider_reported_cost_usd','recall_ms','jev_calls'))


def describe(value):
    # Compact structured text remains FTS-searchable and retains exact field names.
    return ('Performance timing duration usage (null = unknown; wall time includes waits; '
            'reported cost is not an invoice):\n' + json.dumps(value, ensure_ascii=True, separators=(',',':')))
