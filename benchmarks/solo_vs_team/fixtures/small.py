"""Synthetic usage report with intentionally seeded defects. No I/O."""


def summarize_usage(events):
    totals = {}
    for event in events:
        if event['status'] != 'completed':
            continue
        provider = event['provider']
        if provider not in totals:
            totals[provider] = {'provider': provider, 'attempts': 0,
                                'input_tokens': 0, 'output_tokens': 0,
                                'complete_measurements': 0}
        row = totals[provider]
        row['attempts'] += 1
        row['input_tokens'] += event.get('input_tokens') or 0
        row['output_tokens'] += event.get('output_tokens') or 0
        row['complete_measurements'] += 1
    return list(totals.values())
