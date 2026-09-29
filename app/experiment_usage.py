'''Per-condition experiment usage accounting (standard library only).

This module is pure: it performs no file access, no network calls and no
provider API work.  It consumes raw call dictionaries that the caller has
ALREADY scoped to a single experiment condition; it never groups other trials
and never mutates the records it is given.

Unknown stays unknown.  A measurement that is missing, malformed or
unreconcilable is reported as ``None``, never as ``0``.  Strict totals
(``input_tokens``, ``output_tokens``, ``cached_input_tokens``) are ``None`` when
any call in the group lacks that measurement; ``known_*`` totals sum only the
observed calls and are reported separately.  Monetary charges are tracked apart
from tokens: only an explicitly verified ``record['billing']`` block can set
``additional_charge_usd``, and provider cost estimates never can.
'''

from __future__ import annotations

import math

__all__ = ['normalize_usage', 'summarize_usage', 'SUMMARY_FIELDS']

PROVIDER_OPENAI = 'OpenAI'
PROVIDER_ANTHROPIC = 'Anthropic'
PROVIDER_XAI = 'xAI'
UNKNOWN_LABEL = 'unknown'

_PROVIDER_ALIASES = {
    'openai': PROVIDER_OPENAI,
    'open_ai': PROVIDER_OPENAI,
    'anthropic': PROVIDER_ANTHROPIC,
    'xai': PROVIDER_XAI,
    'x.ai': PROVIDER_XAI,
    'x_ai': PROVIDER_XAI,
}

_INPUT_KEYS = ('input_tokens', 'inputTokens', 'prompt_eval_count')
_OUTPUT_KEYS = ('output_tokens', 'outputTokens', 'eval_count')
_CACHE_READ_KEYS = ('cached_input_tokens', 'cache_read_input_tokens', 'cachedInputTokens')
_CACHE_CREATE_KEYS = ('cache_creation_input_tokens', 'cacheCreationInputTokens')
_TOTAL_KEYS = ('total_tokens', 'totalTokens')
_ESTIMATE_KEYS = (
    'cost', 'cost_usd', 'costUsd', 'total_cost', 'total_cost_usd',
    'estimated_cost', 'estimated_cost_usd', 'estimated_charge_usd',
    'price_usd', 'charge_usd',
)

SUMMARY_FIELDS = (
    'calls',
    'succeeded_calls',
    'input_tokens',
    'output_tokens',
    'cached_input_tokens',
    'known_input_tokens',
    'known_output_tokens',
    'known_total_tokens',
    'unknown_usage_calls',
    'additional_charge_usd',
)

_TOKEN_FIELDS = (
    ('input_tokens', 'known_input_tokens', 'strict_input', 'missing_input'),
    ('output_tokens', 'known_output_tokens', 'strict_output', 'missing_output'),
    ('cached_input_tokens', 'known_cached_input_tokens', 'strict_cached', 'missing_cached'),
)


# --------------------------------------------------------------------------
# value validation
# --------------------------------------------------------------------------

def _is_number(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _token(value):
    '''Return a nonnegative integer token count, or None if not observable.

    Booleans, negatives, fractional values, NaN/Infinity and non-numbers are
    all rejected.  Integral floats (e.g. ``12.0``) are accepted as ``12``.
    '''
    if not _is_number(value):
        return None
    if isinstance(value, float):
        if not math.isfinite(value) or value != int(value):
            return None
        value = int(value)
    if value < 0:
        return None
    return int(value)


def _first_token(usage, keys):
    '''First valid token count among ``keys``; invalid aliases are skipped.'''
    for key in keys:
        if key in usage:
            token = _token(usage[key])
            if token is not None:
                return token
    return None


def _canonical_provider(value):
    '''Canonical provider name for normalization rules, else None.'''
    if not isinstance(value, str):
        return None
    return _PROVIDER_ALIASES.get(value.strip().lower())


def _provider_label(value):
    '''Grouping label for a provider field; unrecognized strings are kept.'''
    if not isinstance(value, str) or not value.strip():
        return UNKNOWN_LABEL
    return _PROVIDER_ALIASES.get(value.strip().lower(), value.strip())


def _worker_label(value):
    if not isinstance(value, str) or not value.strip():
        return UNKNOWN_LABEL
    return value.strip()


def _verified_charge(record):
    '''Charge in USD only from an explicitly verified billing block.'''
    billing = record.get('billing')
    if not isinstance(billing, dict):
        return None
    if billing.get('verified') is not True:
        return None
    currency = billing.get('currency')
    if not isinstance(currency, str) or currency.strip().upper() != 'USD':
        return None
    amount = billing.get('additional_charge_usd')
    if not _is_number(amount):
        return None
    try:
        amount = float(amount)
    except (OverflowError, ValueError):
        return None
    if not math.isfinite(amount) or amount < 0:
        return None
    return amount


# --------------------------------------------------------------------------
# normalization
# --------------------------------------------------------------------------

def _xai_input(raw_input, output, cache_create, cache_read, reported_total, usage):
    '''Reconcile xAI input tokens, which may or may not include cache parts.

    Returns ``(input_tokens, unreconciled)``.  With no reported total the raw
    input is retained as-is and no extra cache tokens are assumed.
    '''
    if not any(key in usage for key in _TOTAL_KEYS):
        return raw_input, False
    if reported_total is None or output is None or raw_input is None:
        return None, True
    difference = reported_total - output
    if difference == raw_input:
        return raw_input, False
    if cache_create is not None and cache_read is not None:
        if difference == raw_input + cache_create + cache_read:
            return difference, False
    return None, True


def _normalize_with_flags(record):
    result = {
        'input_tokens': None,
        'output_tokens': None,
        'cached_input_tokens': None,
        'known_total_tokens': None,
        'additional_charge_usd': None,
    }
    flags = {'malformed_usage': False, 'xai_unreconciled': False, 'estimate_ignored': False}
    if not isinstance(record, dict):
        flags['malformed_usage'] = True
        return result, flags

    usage = record.get('usage')
    if usage is None:
        usage = {}
    elif not isinstance(usage, dict):
        flags['malformed_usage'] = True
        usage = {}

    if any(key in usage for key in _ESTIMATE_KEYS) or any(key in record for key in _ESTIMATE_KEYS):
        flags['estimate_ignored'] = True

    provider = _canonical_provider(record.get('provider'))
    raw_input = _first_token(usage, _INPUT_KEYS)
    output = _first_token(usage, _OUTPUT_KEYS)
    cache_read = _first_token(usage, _CACHE_READ_KEYS)
    cache_create = _first_token(usage, _CACHE_CREATE_KEYS)

    if provider == PROVIDER_ANTHROPIC:
        # Anthropic reports uncached input separately from cache parts; the
        # sum is trustworthy only when all three components are valid.
        if raw_input is not None and cache_create is not None and cache_read is not None:
            total_input = raw_input + cache_create + cache_read
        else:
            total_input = None
    elif provider == PROVIDER_XAI:
        total_input, unreconciled = _xai_input(
            raw_input, output, cache_create, cache_read,
            _first_token(usage, _TOTAL_KEYS), usage,
        )
        flags['xai_unreconciled'] = unreconciled
    else:
        # OpenAI (and unrecognized providers): cached input is already a
        # subset of the reported input and is never added to it.
        total_input = raw_input

    result['input_tokens'] = total_input
    result['output_tokens'] = output
    result['cached_input_tokens'] = cache_read
    if total_input is None and output is None:
        result['known_total_tokens'] = None
    else:
        result['known_total_tokens'] = (total_input or 0) + (output or 0)
    result['additional_charge_usd'] = _verified_charge(record)
    return result, flags


def normalize_usage(record):
    '''Normalize one already condition-scoped raw call record.

    Never raises for malformed input and never mutates ``record``.  Returns
    ``{input_tokens, output_tokens, cached_input_tokens, known_total_tokens,
    additional_charge_usd}`` where unknown measurements are ``None``.
    ``known_total_tokens`` sums only the observed input/output values.
    '''
    result, _flags = _normalize_with_flags(record)
    return result


# --------------------------------------------------------------------------
# summarization
# --------------------------------------------------------------------------

def _new_acc():
    return {
        'calls': 0,
        'succeeded_calls': 0,
        'known_input_tokens': 0,
        'known_output_tokens': 0,
        'known_cached_input_tokens': 0,
        'unknown_usage_calls': 0,
        'strict_input': True,
        'strict_output': True,
        'strict_cached': True,
        'missing_input': 0,
        'missing_output': 0,
        'missing_cached': 0,
        'strict_charge': True,
        'missing_charge': 0,
        'charge_total': 0.0,
    }


def _accumulate(acc, norm, succeeded):
    acc['calls'] += 1
    if succeeded:
        acc['succeeded_calls'] += 1
    for field, known_key, strict_key, missing_key in _TOKEN_FIELDS:
        value = norm[field]
        if value is None:
            acc[strict_key] = False
            acc[missing_key] += 1
        else:
            acc[known_key] += value
    if norm['input_tokens'] is None or norm['output_tokens'] is None:
        acc['unknown_usage_calls'] += 1
    charge = norm['additional_charge_usd']
    if charge is None:
        acc['strict_charge'] = False
        acc['missing_charge'] += 1
    else:
        acc['charge_total'] += charge


def _finalize(acc):
    known_input = acc['known_input_tokens']
    known_output = acc['known_output_tokens']
    charge = None
    if acc['calls'] > 0 and acc['strict_charge'] and math.isfinite(acc['charge_total']):
        charge = round(acc['charge_total'], 6)
    return {
        'calls': acc['calls'],
        'succeeded_calls': acc['succeeded_calls'],
        'input_tokens': known_input if acc['strict_input'] else None,
        'output_tokens': known_output if acc['strict_output'] else None,
        'cached_input_tokens': (
            acc['known_cached_input_tokens'] if acc['strict_cached'] else None
        ),
        'known_input_tokens': known_input,
        'known_output_tokens': known_output,
        'known_total_tokens': known_input + known_output,
        'unknown_usage_calls': acc['unknown_usage_calls'],
        'additional_charge_usd': charge,
    }


def _as_items(records):
    if records is None:
        return [], False
    if isinstance(records, dict):
        return [records], False
    if isinstance(records, (list, tuple)):
        return list(records), False
    try:
        return list(records), False
    except TypeError:
        return [], True


def summarize_usage(records):
    '''Summarize raw calls for ONE experiment condition.

    Returns ``{totals, by_provider, by_worker, notes}``.  Workers are grouped
    by ``(worker_id, provider)`` so the same worker id on two providers never
    merges.  Input records are read only and never mutated.
    '''
    items, not_iterable = _as_items(records)

    totals = _new_acc()
    providers = {}
    workers = {}
    skipped = 0
    malformed_usage = 0
    xai_unreconciled = 0
    estimates_ignored = 0
    missing_provider = 0
    missing_worker = 0

    for raw in items:
        if not isinstance(raw, dict):
            skipped += 1
            continue
        norm, flags = _normalize_with_flags(raw)
        if flags['malformed_usage']:
            malformed_usage += 1
        if flags['xai_unreconciled']:
            xai_unreconciled += 1
        if flags['estimate_ignored']:
            estimates_ignored += 1
        provider = _provider_label(raw.get('provider'))
        worker = _worker_label(raw.get('worker_id'))
        if provider == UNKNOWN_LABEL:
            missing_provider += 1
        if worker == UNKNOWN_LABEL:
            missing_worker += 1
        succeeded = raw.get('status') == 'succeeded'
        if provider not in providers:
            providers[provider] = _new_acc()
        key = (worker, provider)
        if key not in workers:
            workers[key] = _new_acc()
        for acc in (totals, providers[provider], workers[key]):
            _accumulate(acc, norm, succeeded)

    notes = [
        'strict token totals are None when any call lacks that measurement; '
        'known_* totals sum observed calls only',
        'additional_charge_usd is reported only from verified billing blocks '
        'and is tracked separately from token counts',
    ]
    if not_iterable:
        notes.append('records was not iterable; treated as an empty condition')
    if skipped:
        notes.append('%d record(s) ignored: not a dictionary' % skipped)
    if malformed_usage:
        notes.append(
            '%d record(s) had a malformed usage payload; tokens left unknown'
            % malformed_usage
        )
    if xai_unreconciled:
        notes.append(
            '%d xAI call(s) reported a total inconsistent with its parts; '
            'input tokens left unknown' % xai_unreconciled
        )
    if estimates_ignored:
        notes.append(
            '%d provider cost estimate(s) ignored; estimates never set '
            'additional_charge_usd' % estimates_ignored
        )
    if missing_provider:
        notes.append(
            '%d record(s) lacked a provider and were grouped under unknown'
            % missing_provider
        )
    if missing_worker:
        notes.append(
            '%d record(s) lacked a worker_id and were grouped under unknown'
            % missing_worker
        )
    if totals['missing_input']:
        notes.append(
            'totals.input_tokens unknown: %d call(s) without observed input tokens'
            % totals['missing_input']
        )
    if totals['missing_output']:
        notes.append(
            'totals.output_tokens unknown: %d call(s) without observed output tokens'
            % totals['missing_output']
        )
    if totals['missing_cached']:
        notes.append(
            'totals.cached_input_tokens unknown: %d call(s) without reported cache reads'
            % totals['missing_cached']
        )
    if totals['calls'] == 0:
        notes.append('no calls in this condition; charges remain unknown')
    elif totals['missing_charge']:
        notes.append(
            'totals.additional_charge_usd unknown: %d call(s) without verified billing'
            % totals['missing_charge']
        )

    by_provider = []
    for provider in sorted(providers):
        entry = {'provider': provider}
        entry.update(_finalize(providers[provider]))
        by_provider.append(entry)

    by_worker = []
    for worker, provider in sorted(workers):
        entry = {'worker_id': worker, 'provider': provider}
        entry.update(_finalize(workers[(worker, provider)]))
        by_worker.append(entry)

    return {
        'totals': _finalize(totals),
        'by_provider': by_provider,
        'by_worker': by_worker,
        'notes': notes,
    }
