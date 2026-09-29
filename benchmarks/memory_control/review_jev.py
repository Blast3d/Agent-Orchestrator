"""Independent post-suite evidence checks; no provider requests or answer changes."""
import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import subprocess
import sys

from compare_jev import ROOT, build, write, read


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--run', required=True, type=Path)
    args = parser.parse_args()
    parent = args.run.resolve(strict=True)
    data = build(parent)
    assert data['status'] == 'completed' and not data['pending']
    original_status = read(parent / 'review/suite-execution.json')['status']
    assert original_status == 'completed' or (original_status == 'held' and (data.get('fresh_repeat') or {}).get('status') == 'completed')
    restoration = read(parent / 'review/jev-config-restoration.json')
    assert all(restoration[k] for k in ('exact_original_restored', 'temporary_projects_removed', 'original_projects_preserved', 'purposes_unchanged', 'confidence_unchanged'))
    frozen = read(parent / 'review/experiment-lock.json')
    for name, digest in frozen['sha256'].items():
        assert hashlib.sha256((Path(__file__).parent / name).read_bytes()).hexdigest() == digest
    production = read(parent / 'review/production-source-snapshot.json')
    for name, digest in production['sha256'].items():
        assert hashlib.sha256((ROOT / name).read_bytes()).hexdigest() == digest
    conditions = {}
    latest_cold_end = max(row['ended_at'] for row in data['current'] if not row['memory'])
    latest_trial_end = max(row['ended_at'] for row in data['current'])
    seen_projects, seen_calls, source_seed_hashes = set(), set(), set()
    for row in data['current']:
        condition_id = row.get('source_condition_id', row['id'])
        child = parent.parent / row['run_id']
        metadata = read(child / 'condition.json')
        assert metadata['status'] == 'completed'
        assert metadata['project_id'] not in seen_projects
        seen_projects.add(metadata['project_id'])
        raw = read(child / 'trials' / condition_id / 'summary.json')
        # Re-run the same frozen grader over the retained final answer, not a modified candidate.
        checked = subprocess.run([sys.executable, '-I', '-B', str(Path(__file__).parent / 'grader.py'),
            '--workspace', str(child / 'trials' / condition_id / 'final'), '--role', 'all'],
            cwd=ROOT, capture_output=True, timeout=30)
        grade = json.loads(checked.stdout)
        assert grade == raw['final_grade'], ('Final grade no longer reproduces', row['id'])
        write(child / 'review/independent-final-regrade.json', grade)
        expected = set()
        if row['memory']:
            seed = read(child / 'review/seed-insertion.json')
            assert seed['inserted_at'] > latest_cold_end and seed['inserted_at'] < row['started_at']
            expected = set(seed['memory_outcome']['memory_ids'])
            assert len(expected) == 4
            source_seed_hashes.add(metadata['seed_source_sha256'])
            assert row['calls_with_memory'] == row['model_calls']
        for key in raw['calls']:
            assert (child.name, key) not in seen_calls
            seen_calls.add((child.name, key))
            call = read(child / 'calls' / key / 'record.json')
            assert call['status'] == 'succeeded' and set(call.get('memory_ids', [])) == expected
            assert call.get('helper_count') == 0
            if row['memory']:
                assert call['memory_sha256'] == hashlib.sha256(call['memory_context'].encode()).hexdigest()
                assert call.get('memory_trace_id')
            else:
                assert not call.get('memory_context')
            if call['provider'] == 'OpenAI':
                assert call.get('tool_events') == [] and call['requested_model'] == 'gpt-6-astra' and call['requested_effort'] == 'medium'
            else:
                canonical = read(ROOT / 'runs/tasks' / call['job_id'] / 'result.json')
                assert canonical['execution_status'] == 'succeeded'
                if canonical.get('review'):
                    assert canonical['review'].get('reviewed_at', canonical['review'].get('at', latest_trial_end)) >= latest_trial_end
                if row['memory']:
                    assert canonical['memory_context']['execution_requested'] is True
                else:
                    assert not (canonical.get('memory_context') or {}).get('ids')
        if '-jev-' in row['id']:
            assert row['jev']['lookups'] == row['model_calls']
            assert row['jev']['provider_calls'] is not None and row['jev']['provider_calls'] > 0
        else:
            assert row['jev']['lookups'] == 0
        conditions[row['id']] = {'status': 'passed', 'run_id': child.name, 'grade_reproduced': True,
            'final_passed': row['final_passed'], 'total': row['total'], 'complete_submission': row['complete_submission'],
            'protocol_errors_observed': row['protocol_errors'], 'exact_memory_set_verified': True,
            'no_native_tool_events': True, 'jev': row['jev'],
            'memory_usefulness_review': 'neutral: source delivery is verified; one fixed-order observation does not isolate a benefit attributable to individual notes or JEV.'}
    assert len(source_seed_hashes) == 1
    if data.get('fresh_repeat'):
        assert read(parent.parent / data['fresh_repeat']['repeat_parent_run_id'] / 'review/jev-config-restoration.json')['exact_original_restored']
    review = {'status': 'passed', 'reviewer': 'GPT-6 Sol xhigh', 'at': datetime.now(timezone.utc).isoformat(),
        'provider_calls': 0, 'all_trials_finished_before_reviews': True, 'all_trial_end': latest_trial_end,
        'cold_before_any_seed': True, 'same_four_seed_texts': True, 'isolated_project_count': len(seen_projects),
        'frozen_inputs_preserved': True, 'production_source_preserved': True, 'jev_config_restored': restoration,
        'conditions': conditions, 'grader_self_test_limit': 'Unchanged original self-test has group-metadata parity failure; substantive reference280/public30 parity and retained output regrade verified separately.'}
    write(parent / 'review/manual-review.json', review)
    print(json.dumps({'status': review['status'], 'conditions': len(conditions), 'regraded_checks': sum(r['total'] for r in conditions.values())}))


if __name__ == '__main__':
    main()
