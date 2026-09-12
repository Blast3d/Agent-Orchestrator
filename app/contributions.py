"""Evidence-based contribution summaries; never infer effort from calls or tokens.

Accepted-work weights and allocations are review estimates supplied in the ledger.
Activity is measured separately and may include rejected/failed delegations.
"""
from copy import deepcopy
from decimal import Decimal, InvalidOperation, ROUND_FLOOR
from html import escape
import json
import math
import os
from pathlib import Path
import re
import uuid


WORK_STATUSES = ('accepted', 'rejected', 'failed', 'pending')


def _text(value, field):
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f'{field} must be a nonempty string')
    return value


def _list(value, field):
    if not isinstance(value, list):
        raise ValueError(f'{field} must be a list')
    return value


def _number(value, field, *, positive=False, maximum=None):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f'{field} must be a finite number')
    try:
        number = Decimal(str(value))
        finite = number.is_finite() and math.isfinite(float(number))
    except (InvalidOperation, OverflowError, ValueError):
        finite = False
    if not finite or number < 0 or (positive and number == 0):
        raise ValueError(f'{field} must be finite and {"positive" if positive else "nonnegative"}')
    if maximum is not None and number > maximum:
        raise ValueError(f'{field} exceeds {maximum}')
    return number


def _rows(value, field):
    seen = set()
    for row in _list(value, field):
        if not isinstance(row, dict):
            raise ValueError(f'{field} entries must be objects')
        identifier = _text(row.get('id'), f'{field}.id')
        if identifier in seen:
            raise ValueError(f'Duplicate {field} id: {identifier}')
        seen.add(identifier)
        yield row


def _percentages(amounts):
    """Allocate exactly 1,000 tenths of a percent, with stable tie breaking."""
    total = sum(amounts, Decimal(0))
    if total == 0:
        return [None] * len(amounts)
    exact = [value * 1000 / total for value in amounts]
    units = [int(value.to_integral_value(rounding=ROUND_FLOOR)) for value in exact]
    order = sorted(range(len(amounts)), key=lambda index: (-(exact[index] - units[index]), index))
    for index in order[:1000 - sum(units)]:
        units[index] += 1
    return [value / 10 for value in units]


def _coverage(events):
    count = len(events)
    inputs = sum(event['input_tokens'] is not None for event in events)
    outputs = sum(event['output_tokens'] is not None for event in events)
    any_known = sum(event['input_tokens'] is not None or event['output_tokens'] is not None
                    for event in events)
    complete = sum(event['input_tokens'] is not None and event['output_tokens'] is not None
                   for event in events)
    return {'events': count, 'events_with_any_tokens': any_known,
            'events_with_complete_tokens': complete, 'input_events': inputs, 'output_events': outputs,
            'status': 'unavailable' if not any_known else ('complete' if complete == count else 'partial')}


def _token_total(events, key):
    known = [event[key] for event in events if event[key] is not None]
    return sum(known) if known else None


def build_report(ledger: dict) -> dict:
    """Validate an attribution ledger and return deterministic, JSON-safe metrics.

    Missing token measurements remain null. Shares of known tokens do not imply
    total-provider usage, billing, productive contribution, or model quality.
    """
    if not isinstance(ledger, dict) or type(ledger.get('schema_version')) is not int or ledger['schema_version'] != 1:
        raise ValueError('Expected contribution ledger schema_version 1')
    for key in ('scope_id', 'title', 'basis'):
        _text(ledger.get(key), key)
    contributors = []
    for row in _rows(ledger.get('contributors'), 'contributors'):
        contributors.append({key: _text(row.get(key), f'contributors.{key}')
                             for key in ('id', 'name', 'provider', 'model')})
    identities = {row['id'] for row in contributors}
    items = []
    for row in _rows(ledger.get('work_items'), 'work_items'):
        _text(row.get('label'), 'work_items.label')
        _text(row.get('category'), 'work_items.category')
        if row.get('status') not in WORK_STATUSES:
            raise ValueError('Invalid work item status')
        weight = _number(row.get('weight'), 'work_items.weight', positive=True)
        allocations, seen = [], set()
        for allocation in _list(row.get('allocations'), 'work_items.allocations'):
            if not isinstance(allocation, dict):
                raise ValueError('Allocations must be objects')
            agent = _text(allocation.get('agent_id'), 'allocations.agent_id')
            if agent not in identities:
                raise ValueError(f'Unknown allocation agent: {agent}')
            if agent in seen:
                raise ValueError(f'Duplicate allocation agent: {agent}')
            seen.add(agent)
            percent = _number(allocation.get('percent'), 'allocations.percent', maximum=100)
            _text(allocation.get('evidence'), 'allocations.evidence')
            allocations.append((agent, percent))
        total = sum((percent for _, percent in allocations), Decimal(0))
        if allocations and abs(total - 100) > Decimal('0.000001'):
            raise ValueError('Work item allocations must sum to 100 percent')
        items.append((row, weight, [(agent, percent / total) for agent, percent in allocations]))
    events = []
    for row in _rows(ledger.get('activity'), 'activity'):
        agent = _text(row.get('agent_id'), 'activity.agent_id')
        if agent not in identities:
            raise ValueError(f'Unknown activity agent: {agent}')
        for key in ('kind', 'status', 'task_id'):
            _text(row.get(key), f'activity.{key}')
        for key in ('input_tokens', 'output_tokens'):
            value = row.get(key)
            if value is not None and (type(value) is not int or value < 0):
                raise ValueError(f'activity.{key} must be null or a nonnegative integer')
        models = _list(row.get('actual_models', []), 'activity.actual_models')
        for model in models:
            _text(model, 'activity.actual_models entry')
        events.append(dict(row, input_tokens=row.get('input_tokens'), output_tokens=row.get('output_tokens'),
                           actual_models=list(dict.fromkeys(models))))

    def accepted_shares(selected):
        weights = {identifier: Decimal(0) for identifier in identities}
        accepted_weight = unattributed = Decimal(0)
        for row, weight, allocations in selected:
            if row['status'] != 'accepted':
                continue
            accepted_weight += weight
            if not allocations:
                unattributed += weight
            for agent, fraction in allocations:
                weights[agent] += weight * fraction
        if not math.isfinite(float(accepted_weight)):
            raise ValueError('Total accepted weight exceeds the supported numeric range')
        shares = _percentages([weights[row['id']] for row in contributors] + [unattributed])
        return accepted_weight, unattributed, shares

    accepted_weight, unattributed, shares = accepted_shares(items)
    agent_events = {row['id']: [event for event in events if event['agent_id'] == row['id']]
                    for row in contributors}
    delegations = [sum(event['kind'] == 'delegation' for event in agent_events[row['id']])
                   for row in contributors]
    delegation_shares = _percentages([Decimal(value) for value in delegations])
    token_counts = []
    for row in contributors:
        records = agent_events[row['id']]
        inputs, outputs = _token_total(records, 'input_tokens'), _token_total(records, 'output_tokens')
        token_counts.append((inputs, outputs, (inputs or 0) + (outputs or 0)))
    token_shares = _percentages([Decimal(count[2]) for count in token_counts])
    by_agent = []
    for index, contributor in enumerate(contributors):
        records = agent_events[contributor['id']]
        coverage = _coverage(records)
        by_agent.append(dict(contributor, accepted_work_pct=shares[index], delegations=delegations[index],
            delegation_pct=delegation_shares[index], input_tokens=token_counts[index][0],
            output_tokens=token_counts[index][1], known_tokens=token_counts[index][2] if coverage['events_with_any_tokens'] else None,
            known_token_share_pct=token_shares[index] if coverage['events_with_any_tokens'] else None,
            token_coverage=coverage, actual_models=sorted({model for event in records for model in event['actual_models']})))
    by_category = []
    for category in sorted({row['category'] for row, _, _ in items}):
        weight, missing, category_shares = accepted_shares([item for item in items if item[0]['category'] == category])
        by_category.append({'category': category, 'accepted_weight': float(weight),
            'unattributed_pct': category_shares[-1], 'attribution_complete': bool(weight) and not missing,
            'by_agent': [dict(row, accepted_work_pct=category_shares[index]) for index, row in enumerate(contributors)]})
    input_tokens, output_tokens = _token_total(events, 'input_tokens'), _token_total(events, 'output_tokens')
    caveats = [
        'Accepted-work percentages are review estimates based on the declared weights and evidence; they are not measured effort, elapsed time, lines of code, or token usage.',
        'Only accepted work enters contribution shares. Failed, rejected, and pending work can still appear in observed delegation and token usage.',
        'Known-token shares use only reported input and output tokens. Missing measurements stay unknown; auxiliary model names do not add tokens again.',
        'Agent identity and provider/model identity are separate. Multiple agents using the same model are not independent model providers.',
    ]
    if not accepted_weight:
        caveats.append('No accepted work exists yet; contribution percentages are pending, not zero.')
    if unattributed:
        caveats.append('Accepted work without allocations remains explicitly unattributed in the denominator.')
    if _coverage(events)['status'] != 'complete':
        caveats.append('Token coverage is incomplete or unavailable; known-token shares are not shares of all actual usage.')
    return {'schema_version': 1, 'scope_id': ledger['scope_id'], 'title': ledger['title'], 'basis': ledger['basis'],
        'status': ('needs_attribution' if unattributed else 'complete') if accepted_weight else 'pending',
        'accepted_weight': float(accepted_weight),
        'unattributed_pct': shares[-1], 'attribution_complete': bool(accepted_weight) and not unattributed,
        'by_agent': by_agent, 'by_category': by_category,
        'usage': {'delegations': sum(delegations), 'input_tokens': input_tokens, 'output_tokens': output_tokens,
                  'known_tokens': sum(count[2] for count in token_counts) if input_tokens is not None or output_tokens is not None else None,
                  'token_coverage': _coverage(events), 'known_token_share_basis': 'Reported input plus output tokens only; missing values excluded.'},
        'work_status_counts': {status: sum(row['status'] == status for row, _, _ in items) for status in WORK_STATUSES},
        'method': 'Weighted accepted work, with percentages rounded to one decimal by largest remainder; observed usage is reported separately.',
        'caveats': caveats, 'work_items': deepcopy(ledger['work_items']), 'activity': deepcopy(events)}


def _cell(value):
    return escape(str(value)).replace('|', '\\|').replace('\r', ' ').replace('\n', ' ')


def _pct(value):
    return 'Pending / unknown' if value is None else f'{value:.1f}%'


def _markdown(report):
    lines = [f'# Contribution audit: {_cell(report["title"])}', '',
        f'Scope: `{_cell(report["scope_id"])}`. Audit status: {report["status"]}.', '',
        f'Attribution basis: {_cell(report["basis"])}', '',
        '**Estimated share of accepted work**', '',
        '| Agent | Provider / recorded model | Accepted work |', '| --- | --- | ---: |']
    for row in report['by_agent']:
        lines.append(f'| {_cell(row["name"])} | {_cell(row["provider"])} / {_cell(row["model"])} | {_pct(row["accepted_work_pct"])} |')
    lines += [f'| Unattributed accepted work | — | {_pct(report["unattributed_pct"])} |', '',
        '**Observed activity and reported tokens**', '',
        '| Agent | Delegations | Delegation share | Input tokens | Output tokens | Known-token share | Token coverage |',
        '| --- | ---: | ---: | ---: | ---: | ---: | --- |']
    for row in report['by_agent']:
        coverage = row['token_coverage']
        inputs = 'Unknown' if row['input_tokens'] is None else str(row['input_tokens'])
        outputs = 'Unknown' if row['output_tokens'] is None else str(row['output_tokens'])
        lines.append(f'| {_cell(row["name"])} | {row["delegations"]} | {_pct(row["delegation_pct"])} | {inputs} | {outputs} | {_pct(row["known_token_share_pct"])} | {coverage["status"]}; {coverage["events_with_complete_tokens"]}/{coverage["events"]} complete events |')
    if report['by_category']:
        lines += ['', '**Accepted work by category**', '', '| Category | Accepted weight | Agent shares | Unattributed |',
                  '| --- | ---: | --- | ---: |']
        for category in report['by_category']:
            shares = '; '.join(f'{_cell(row["name"])}: {_pct(row["accepted_work_pct"])}' for row in category['by_agent'])
            lines.append(f'| {_cell(category["category"])} | {category["accepted_weight"]:g} | {shares} | {_pct(category["unattributed_pct"])} |')
    lines += ['', '**Evidence and review decisions**', '']
    if not report['work_items']:
        lines.append('No reviewed work items recorded.')
    for item in report['work_items']:
        lines.append(f'- {_cell(item["label"])} ({item["status"]}; {_cell(item["category"])}; weight {item["weight"]}): ' +
                     ('; '.join(f'{_cell(allocation["agent_id"])} {allocation["percent"]}% — {_cell(allocation["evidence"])}'
                                for allocation in item['allocations']) or 'No allocation recorded.'))
    lines += ['', '**Method and limits**', '', report['method'], '']
    lines += ['- ' + text for text in report['caveats']]
    return '\n'.join(lines) + '\n'


def _atomic_text(path, value):
    temporary = path.with_name(path.name + '.' + uuid.uuid4().hex + '.tmp')
    try:
        with temporary.open('x', encoding='utf-8', newline='\n') as handle:
            handle.write(value)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def write_report(directory: Path, ledger: dict, stem='contribution-audit') -> dict:
    """Write independent atomic JSON and Markdown snapshots, returning the report.

    JSON is canonical. A failure replacing Markdown raises so callers can retry;
    the two files are individually atomic, not a cross-file transaction.
    """
    if not isinstance(stem, str) or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_-]*', stem):
        raise ValueError('Report stem must be a simple filename without a path or extension')
    report = build_report(ledger)
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    serialized = json.dumps(report, indent=2, ensure_ascii=False, allow_nan=False) + '\n'
    markdown = _markdown(report)
    _atomic_text(directory / f'{stem}.json', serialized)
    _atomic_text(directory / f'{stem}.md', markdown)
    return report
