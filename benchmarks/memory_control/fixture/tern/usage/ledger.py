"""Reconcile usage snapshots into provider totals."""


def aggregate_attempts(events):
    latest = {}
    for event in events:
        latest[event['id']] = event
    totals = {}
    for event in latest.values():
        if event['status'] == 'cancelled':
            continue
        provider = event['provider'].lower()
        row = totals.setdefault(provider, {'provider': provider, 'attempts': 0,
            'failures': 0, 'tokens': 0, 'unknown_attempts': 0})
        row['attempts'] += 1
        row['failures'] += event['status'] == 'error'
        row['tokens'] += event['tokens'] or 0
        row['unknown_attempts'] += not event['tokens']
    return list(totals.values())
