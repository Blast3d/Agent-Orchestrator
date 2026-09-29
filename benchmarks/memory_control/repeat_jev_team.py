"""Explicit fresh repeat after a reconciled pre-provider admission hold."""
import argparse
import hashlib
from pathlib import Path

from run_experiment import Experiment, freeze_hashes, HERE, ROOT, Workspace
from comparison_protocol import conditions
from transport import Transport, read, write, now
from experiment_runs import ExperimentRuns, publish_condition
from orchestration_lifecycle import start_run
from coordinator_handoff import Coordinator
from usage_guard import Guard, file_lock
from storage_budget import StorageBudget
from jev_openrouter import _path


class Repeat(Experiment):
    def __init__(self, parent, original):
        self.parent = parent
        self.original = original
        self.jev_comparison = True
        self.trials = ('team-warm',)
        declaration = dict(conditions(True)[-1], id='team-warm', label='OpenAI + Claude + Grok / Brain + JEV / fresh repeat')
        self.group = ExperimentRuns(parent, 'memory_control', [declaration], root=ROOT)
        child = self.group.child('team-warm')
        publish_condition(child, suite_mode='jev_comparison', memory_ranking_mode='jev',
            repeated_source_condition='team-jev-warm', repeated_source_parent=original.name)
        self.run = child
        self.transport = Transport(child)
        self.project = self.transport.project
        self.spec = read(HERE / 'fixture-spec.json')
        self.view = Workspace(HERE / 'fixture')
        self.lock = parent / 'review/experiment-lock.json'
        frozen = read(original / 'review/experiment-lock.json')
        assert frozen['sha256'] == freeze_hashes()
        write(self.lock, dict(frozen, order=['team-warm'], source_parent=original.name,
            repeat_runner_sha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest()))

    def initialize(self):
        assert read(self.lock)['sha256'] == freeze_hashes(), 'Original controls changed'
        assert read(self.lock)['repeat_runner_sha256'] == hashlib.sha256(Path(__file__).read_bytes()).hexdigest()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--run', required=True, type=Path)
    args = parser.parse_args()
    original = args.run.resolve(strict=True)
    assert not (original / 'review/fresh-repeat.json').exists(), 'Do not launch a repeat twice'
    experiment = read(original / 'experiment.json')
    failed_id = next(row['run_id'] for row in experiment['conditions'] if row['id'] == 'team-jev-warm')
    failed = original.parent / failed_id
    calls = [read(p) for p in (failed / 'calls').glob('*/record.json')]
    held = [row for row in calls if row['status'] == 'held']
    assert len(held) == 1 and held[0]['provider'] == 'OpenAI'
    assert held[0]['admission']['allowed'] is False and not held[0].get('reservation_id') and not held[0].get('pid')
    assert all(row['status'] in ('succeeded', 'held') for row in calls)
    for row in calls:
        if row.get('job_id'):
            canonical = read(ROOT / 'runs/tasks' / row['job_id'] / 'result.json')
            assert canonical['execution_status'] == 'succeeded'
    quota = Guard().check('codex', 'small')
    assert quota['allowed'] and any(w['freshness'] == 'fresh' for w in quota['windows'])
    proof = {'at': now(), 'status': 'reconciled_incomplete', 'original_condition': failed.name,
        'completed_calls': sum(r['status'] == 'succeeded' for r in calls), 'held_call_id': held[0]['call_id'],
        'held_contestant_provider_calls': 0, 'uncertain_provider_calls': 0,
        'official_quota_after_refresh': quota,
        'reason': 'Completed Codex reservation estimates accumulated against an old reading. Official refresh retired them; original held call never acquired a reservation or process. Other provider calls completed. Preserve attempt and start a new independent repeat.'}
    write(failed / 'review/hold-reconciliation.json', proof)
    with file_lock(ROOT / '.orchestration/experiment-active.lock'):
        active = read(ROOT / '.orchestration/experiment-active.json')
        assert active['status'] == 'uncertain' and active['run_id'] == failed.name
        write(failed / 'review/original-active-lease.json', active)
        active.update(status='completed', reconciled_at=now(), reconciliation=str(failed / 'review/hold-reconciliation.json'))
        write(ROOT / '.orchestration/experiment-active.json', active)
    write(failed / 'review/original-condition.json', read(failed / 'condition.json'))
    publish_condition(failed, status='failed', stage='reconciled_incomplete')
    experiment['status'] = 'completed_with_failed_condition'
    write(original / 'experiment.json', experiment)
    manifest = read(original / 'run.json')
    for task in manifest['tasks']:
        if task.get('id') == 'team-jev-warm':
            task.update(status='failed', note='Preserved interrupted attempt; clean repeat linked separately.')
    write(original / 'run.json', manifest)
    packet = start_run(workspace=ROOT, name='sol-xhigh-jev-team-fresh-repeat', project='sol-xhigh-jev-team-repeat-20260926',
        objective='Fresh team Brain+JEV Tern repeat after verified local admission hold; identical frozen fixture, seed text, roster, grading and rounds; Sol xhigh remains lead.', no_memory=True, root=ROOT)
    parent = Path(packet['run'])
    assert packet['operating_context']['context'] == (ROOT / 'app/assets/orchestration-context.md').read_text(encoding='utf-8')
    manifest = read(parent / 'run.json')
    manifest.update(authorization=read(original / 'run.json')['authorization'],
        actual_lead_model='gpt-6-sol', actual_lead_effort='xhigh')
    write(parent / 'run.json', manifest)
    write(parent / 'review/lead-model-receipt.json', read(original / 'review/lead-model-receipt.json'))
    coord = Coordinator(parent); state = coord.read(); cp = state['checkpoint']
    cp.update(completed=['Read unchanged operating guide; empty repeat-project recall. Reconciled seven completed calls and one pre-provider hold. Fresh official quota allows work.'],
        next_steps=['Run one independent team JEV repeat; preserve interrupted attempt separately.'],
        authorization=read(original / 'coordinator.json')['checkpoint']['authorization'])
    state = coord.checkpoint(cp, state['owner'], state['session'], state['generation'])
    start_run(run=parent, owner=state['owner'], session=state['session'], generation=state['generation'], no_memory=True)
    repeat = Repeat(parent, original)
    link = {'at': now(), 'source_parent': original.name, 'failed_run_id': failed.name,
        'repeat_parent_run_id': parent.name, 'repeat_run_id': repeat.run.name,
        'source_condition_id': 'team-warm', 'comparison_condition_id': 'team-jev-warm',
        'reason': proof['reason'], 'status': 'prepared', 'extra_attempt_usage_is_separate': True}
    write(original / 'review/fresh-repeat.json', link)
    write(parent / 'review/source-link.json', link)
    cold_rows = [row for row in experiment['conditions'] if row['id'] in ('solo-cold', 'team-cold')]
    assert all(read(original.parent / row['run_id'] / 'condition.json')['status'] == 'completed' for row in cold_rows)
    manifest = read(parent / 'run.json')
    manifest.update(display_name='GPT-6 Sol xhigh: fresh team Brain+JEV repeat', tasks=[{'id':'team-warm','status':'pending'}])
    write(parent / 'run.json', manifest)
    repeat._seed_condition()
    config_path = _path(ROOT, 'jev-config.json'); original_config = read(config_path)
    write(parent / 'review/jev-config-before.json', original_config)
    try:
        with StorageBudget(ROOT).allocation(32768, kind='task'), file_lock(_path(ROOT, 'jev-config.lock')):
            current = read(config_path); assert current == original_config
            current['authorized_projects'] = sorted(set(current['authorized_projects']) | {repeat.project})
            write(config_path, current)
        repeat.initialize()
        with repeat.group.condition('team-warm'):
            write(repeat.run / 'review/experiment-lock.json', read(repeat.lock))
            result = repeat._trial('team-warm')
        link.update(status='completed', wall_seconds=result['wall_seconds'], ended_at=now())
    finally:
        with StorageBudget(ROOT).allocation(32768, kind='task'), file_lock(_path(ROOT, 'jev-config.lock')):
            current = read(config_path)
            current['authorized_projects'] = sorted((set(current['authorized_projects']) - {repeat.project}) | set(original_config['authorized_projects']))
            write(config_path, current)
        write(parent / 'review/jev-config-restoration.json', {'at':now(), 'exact_original_restored':read(config_path)==original_config})
        write(original / 'review/fresh-repeat.json', link)
        write(parent / 'review/source-link.json', link)
    print('Fresh repeat complete: ' + str(parent), flush=True)


if __name__ == '__main__':
    main()
