"""Normalize the derived suite audit without changing captured trial evidence."""
import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'app'))
from contributions import write_report
from experiment_usage import normalize_usage


def read(path):
    return json.loads(path.read_text(encoding='utf-8-sig'))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--run', type=Path, required=True)
    parent = parser.parse_args().run.resolve(strict=True)
    data = read(parent / 'results.json')
    path = parent / 'review/overall-contributions-ledger.json'
    ledger = read(path)
    runs = [r['run_id'] for r in data['current']]
    if data.get('fresh_repeat'):
        runs.append(data['fresh_repeat']['failed_run_id'])
    records = {}
    for run in runs:
        for source in (parent.parent / run / 'calls').glob('*/record.json'):
            record = read(source)
            for key in ('call_id', 'job_id'):
                if record.get(key):
                    records[(run, record[key])] = (record, source)
    totals = {'input_tokens': 0, 'output_tokens': 0}
    count = 0
    for event in ledger['activity']:
        if event['kind'] != 'delegation':
            continue
        run = event['id'].split(':', 1)[0]
        match = records.get((run, event['task_id']))
        if not match:
            assert event['agent_id'] == 'setup-astra', event
            continue
        record, source = match
        assert record['status'] == 'succeeded'
        usage = normalize_usage(record)
        event['raw_provider_usage'] = record['usage']
        event['usage_source'] = str(source)
        event['usage_basis'] = 'Logical input, including provider cache creation/read exactly once; app.experiment_usage.normalize_usage.'
        for key in totals:
            assert usage[key] is not None, source
            event[key] = usage[key]
            totals[key] += usage[key]
        event['cached_input_tokens'] = usage['cached_input_tokens']
        count += 1
    expected_rows = data['current'] + data.get('interrupted_attempts', [])
    for key in totals:
        assert totals[key] == sum(row['usage']['totals'][key] for row in expected_rows), (key, totals)
    assert count == sum(row['model_calls'] for row in data['current']) + sum(row['completed_contestant_calls'] for row in data.get('interrupted_attempts', []))
    ledger['basis'] = ('Rollup of parent setup, six scored child ledgers, interrupted-attempt reconciliation and fresh-repeat coordination, counted once. '
        'Work points are coordinator estimates. Derived usage is normalized from original call receipts to logical input, including provider cache parts exactly once; captured raw child ledgers are unchanged.')
    path.write_text(json.dumps(ledger, indent=2) + '\n', encoding='utf-8')
    report = write_report(parent / 'review/overall-contributions', ledger)
    assert report['attribution_complete']
    receipt = dict(status='passed', completed_contestant_calls=count, normalized_totals=totals,
        matches_scored_plus_interrupted_usage=True, captured_evidence_unchanged=True)
    (parent / 'review/contribution-usage-reconciliation.json').write_text(json.dumps(receipt, indent=2) + '\n', encoding='utf-8')
    print(json.dumps(receipt))


if __name__ == '__main__':
    main()
