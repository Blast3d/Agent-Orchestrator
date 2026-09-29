"""Run one authorized matched suite with reversible, exact JEV project scope."""
import argparse
import hashlib
import json
from pathlib import Path
import sys

from run_experiment import Experiment, preflight, ROOT
from comparison_protocol import JEV_TRIALS, child_project
from transport import write, read, now
from storage_budget import StorageBudget
from usage_guard import file_lock, Guard
from jev_openrouter import _path, load_config


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--run', required=True, type=Path)
    args = parser.parse_args()
    parent = args.run.resolve(strict=True)
    review = parent / 'review'
    assert (review / 'astra-harness-review.md').exists(), 'Harness review required before live inference'
    assert read(review / 'pre-inference-validation.json')['status'] == 'passed'
    assert not any((parent / p).exists() for p in ('experiment.json', 'review/suite-execution.json')), 'Use a fresh exact parent; never retry a partially executed suite'
    config_path = _path(ROOT, 'jev-config.json')
    projects = [child_project(parent.name, name) for name in JEV_TRIALS if '-jev-' in name]
    original = read(config_path)
    assert not any(p in original.get('authorized_projects', []) for p in projects), 'New exact projects expected'
    write(review / 'jev-config-before.json', original)
    write(review / 'jev-usage-before.json', read(_path(ROOT, 'jev-usage.json')))
    receipt = {'status': 'starting', 'started_at': now(), 'authorized_temporary_projects': projects,
               'parent_run_id': parent.name, 'provider_calls_before_trial': 0}
    write(review / 'suite-execution.json', receipt)
    try:
        with StorageBudget(ROOT).allocation(32768, kind='task'):
            with file_lock(_path(ROOT, 'jev-config.lock')):
                current = read(config_path)
                assert current == original, 'Configuration changed while preparing suite'
                current['authorized_projects'] = sorted(set(current['authorized_projects']) | set(projects))
                write(config_path, current)
        write(review / 'jev-config-during.json', load_config(ROOT))
        ready = preflight(parent, True)
        write(review / 'live-preflight.json', ready)
        assert ready['status'] == 'ready', ready['errors']
        experiment = Experiment(parent, True)
        experiment.initialize()
        manifest = read(parent / 'run.json')
        manifest.update(display_name='GPT-6 Sol xhigh: historical Brain versus current JEV',
            actual_lead_model='gpt-6-sol', actual_lead_effort='xhigh',
            lead_model_receipt='review/lead-model-receipt.json')
        manifest['authorization']['provider_content_scopes'].append({'providers':['JEV via existing OpenRouter connector'],
            'content':'Only the exact synthetic JEV child scopes, frozen four notes and benchmark query; temporary project authorization restored after trials.'})
        write(parent / 'run.json', manifest)
        # Freeze current production memory and provider plumbing beside the task controls.
        paths = ['app/brain_store.py', 'app/brain_recall.py', 'app/brain_jev.py', 'app/jev_openrouter.py',
                 'app/jev_passage.py', 'app/jev_cache.py', 'app/dispatch_worker.py', 'app/assignment_receipts.py',
                 'app/assets/orchestration-context.md']
        hashes = {p: hashlib.sha256((ROOT / p).read_bytes()).hexdigest() for p in paths}
        write(review / 'production-source-snapshot.json', {'at': now(), 'sha256': hashes})
        for name in JEV_TRIALS[:2]:
            write(review / ('quota-refresh-before-' + name + '.json'), Guard().refresh('codex'))
            print(json.dumps({'phase': 'starting', 'condition': name}), flush=True)
            experiment.trial(name)
        experiment.seed()
        for name in JEV_TRIALS[2:]:
            write(review / ('quota-refresh-before-' + name + '.json'), Guard().refresh('codex'))
            print(json.dumps({'phase': 'starting', 'condition': name}), flush=True)
            experiment.trial(name)
        assert all(hashlib.sha256((ROOT / p).read_bytes()).hexdigest() == h for p, h in hashes.items()), 'Production source changed during suite'
        receipt.update(status='completed', completed_at=now(), conditions=list(JEV_TRIALS))
    except BaseException as exc:
        receipt.update(status='held', failure_type=type(exc).__name__, ended_at=now())
        raise
    finally:
        with StorageBudget(ROOT).allocation(32768, kind='task'):
            with file_lock(_path(ROOT, 'jev-config.lock')):
                current = read(config_path)
                current['authorized_projects'] = sorted((set(current.get('authorized_projects', [])) - set(projects)) | set(original['authorized_projects']))
                write(config_path, current)
        restored = read(config_path)
        write(review / 'jev-config-after.json', restored)
        write(review / 'jev-config-restoration.json', {'at': now(), 'exact_original_restored': restored == original,
            'temporary_projects_removed': not any(p in restored['authorized_projects'] for p in projects),
            'original_projects_preserved': set(original['authorized_projects']) <= set(restored['authorized_projects']),
            'purposes_unchanged': restored['purposes'] == original['purposes'],
            'confidence_unchanged': restored['min_confidence'] == original['min_confidence']})
        write(review / 'jev-usage-after.json', read(_path(ROOT, 'jev-usage.json')))
        write(review / 'allowance-after.json', Guard().status())
        write(review / 'suite-execution.json', receipt)
        print(json.dumps(receipt), flush=True)


if __name__ == '__main__':
    main()
