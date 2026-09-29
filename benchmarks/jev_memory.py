"""Small labeled synthetic ranking diagnostic; live mode makes at most six calls."""
import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'app'))
from brain_jev import ranking_request
import jev_openrouter


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--live', action='store_true', help='Use configured OpenRouter credits with synthetic inputs only.')
    parser.add_argument('--project', default='agent-orchestrator')
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    cases = json.loads(Path(__file__).with_name('jev_memory_cases.json').read_text(encoding='utf-8'))
    if len(cases) != 6:
        raise ValueError('This diagnostic is limited to its six reviewed synthetic cases.')
    config = jev_openrouter.load_config(ROOT)
    rows = []
    for case in cases:
        state, questions = ranking_request(case['query'], case['candidates'], case['profile'])
        jev_openrouter._request(state, questions)  # Validate the actual request shape offline.
        row = {'case': case['id'], 'profile': case['profile'], 'expected_best': case['expected_best']}
        if args.live:
            decision = jev_openrouter.evaluate(ROOT, args.project, 'memory_rank', state, questions)
            row['decision'] = decision
            row['correct_top'] = None
            row['applied'] = False
            if decision['status'] == 'ok':
                scores = [decision['answers']['memory_' + str(i)] for i in range(len(case['candidates']))]
                order = sorted(range(len(scores)), key=lambda i: -scores[i]['score'])
                row['scored_order'] = [case['candidates'][i]['id'] for i in order]
                row['correct_top'] = row['scored_order'][0] == case['expected_best']
                row['applied'] = all(score['confidence'] >= config.get('min_confidence', .8) for score in scores)
                actual_first = row['scored_order'][0] if row['applied'] else case['candidates'][0]['id']
                row['policy_correct_top'] = actual_first == case['expected_best']
        else:
            row['status'] = 'request_validated_offline'
        rows.append(row)
    report = {'created_at': datetime.now(timezone.utc).isoformat(), 'live': args.live,
              'synthetic_only': True, 'cases': rows,
              'limitations': 'Six deliberately constructed cases, not a representative accuracy benchmark or end-to-end bot benefit test.'}
    if args.live:
        decisions = [row['decision'] for row in rows]
        costs = [d['cost_usd'] for d in decisions]
        report['summary'] = {'requests': sum(d['provider_calls'] for d in decisions),
            'valid_responses': sum(d['status'] == 'ok' for d in decisions),
            'correct_scored_top': sum(row['correct_top'] is True for row in rows),
            'applied': sum(row['applied'] for row in rows),
            'correct_top_after_fallback': sum(row.get('policy_correct_top') is True for row in rows),
            'reported_cost_usd': sum(costs) if all(c is not None for c in costs) else None}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2, allow_nan=False), encoding='utf-8')
    print(json.dumps({'output': str(args.output), 'live': args.live, 'summary': report.get('summary'), 'cases': len(rows)}))


if __name__ == '__main__':
    main()
