"""Snapshot actual native-session usage without mixing it with scored calls."""
import argparse
from datetime import datetime, timezone
import json
from pathlib import Path


def snapshot(session_id):
    base = Path.home() / '.codex/sessions/2026/09/26'
    paths = list(base.glob('*' + session_id + '.jsonl'))
    if len(paths) != 1:
        return {'session_id': session_id, 'status': 'unknown', 'usage': None}
    meta, contexts, latest = None, [], None
    for line in paths[0].read_text(encoding='utf-8').splitlines():
        try:
            row = json.loads(line)
        except ValueError:
            continue
        if row['type'] == 'session_meta':
            meta = row['payload']
        elif row['type'] == 'turn_context':
            contexts.append({key: row['payload'].get(key) for key in ('model', 'effort', 'turn_id')})
        elif row['type'] == 'event_msg' and row['payload'].get('type') == 'token_count' and row['payload'].get('info'):
            latest = row
    usage = latest['payload']['info']['total_token_usage'] if latest else None
    return {'session_id': session_id, 'source': str(paths[0]), 'status': 'measured_snapshot' if usage else 'unknown',
        'session_started_at': (meta or {}).get('timestamp'), 'through_at': latest.get('timestamp') if latest else None,
        'contexts': contexts, 'usage': usage, 'actual_subscription_charge_usd': None,
        'coverage': 'Cumulative native-session counters through the last observed token event; includes setup and reporting to this checkpoint, excludes later messages. Cached input is already part of input tokens.'}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--run', required=True, type=Path)
    args = parser.parse_args()
    parent = args.run.resolve(strict=True)
    lead = json.loads((parent / 'review/lead-model-receipt.json').read_text())
    worker = json.loads((parent / 'review/astra-harness-audit.json').read_text())['worker']
    data = {'generated_at': datetime.now(timezone.utc).isoformat(), 'excluded_from_scored_contestant_usage': True,
        'lead': snapshot(lead['session_id']), 'astra': snapshot(worker['session']),
        'historical_controller_usage': None, 'historical_comparison_limit': 'Historical reports did not measure controller usage; do not infer old setup cost as zero.'}
    (parent / 'review/controller-usage.json').write_text(json.dumps(data, indent=2) + '\n')
    print(json.dumps({key: data[key]['usage'] for key in ('lead', 'astra')}))


if __name__ == '__main__':
    main()
