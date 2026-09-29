"""Durable, sequential experiment conditions with separate dashboard/run scopes.

This module never invokes a provider. An interrupted lease is deliberately not
reclaimed by PID or elapsed time: reconcile its saved calls before clearing it.
"""
from contextlib import contextmanager
import hashlib
import json
import os
from pathlib import Path
import re
import uuid

from coordinator_handoff import Coordinator
from orchestration_lifecycle import start_run
from task_store import timestamp, write_json
from usage_guard import file_lock

JEV_CONDITIONS = ('medium-cold-r1', 'medium-cold-r2', 'medium-jev-r1', 'medium-jev-r2')
JEV_ROSTER = (('implementer', 'Anthropic', 'Scheduler implementation'),)


def jev_conditions():
    """Declare the fixed two-arm, four-session scheduler comparison."""
    return [dict(id=key, label=('Medium scheduler - ' + ('Jev-assisted recall' if '-jev-' in key
                 else 'no memory') + ' - repetition ' + key[-1]),
                 memory_mode='seeded' if '-jev-' in key else 'disabled',
                 roster=[dict(id=actor, provider=provider, role=role, model='opus', effort='medium')
                         for actor, provider, role in JEV_ROSTER]) for key in JEV_CONDITIONS]


def validate_jev_condition(item):
    if not isinstance(item, dict) or item.get('id') not in JEV_CONDITIONS:
        raise ValueError('Unknown Jev comparison condition')
    expected = next(row for row in jev_conditions() if row['id'] == item['id'])
    roster = item.get('roster')
    if (item.get('memory_mode') != expected['memory_mode'] or not isinstance(roster, list)
            or len(roster) != 1 or not isinstance(roster[0], dict)
            or any(roster[0].get(key) != value for key, value in expected['roster'][0].items())
            or not isinstance(item.get('label'), str) or not 1 <= len(item['label']) <= 200
            or ('-jev-' in item['id'] and 'jev' not in item['label'].casefold())):
        raise ValueError('Jev comparison needs its declared Claude Opus/medium roster and explicit memory mode')


def read(path):
    return json.loads(Path(path).read_text(encoding='utf-8-sig'))


def publish_condition(run, *, stage=None, **changes):
    """Publish bounded current state atomically, including concurrent team calls."""
    run = Path(run)
    path = run / 'condition.json'
    if not path.exists():
        raise ValueError('A scored call requires isolated condition metadata')
    with file_lock(run / 'condition.lock'):
        value = read(path)
        value.update(changes)
        if stage is not None:
            value['stage'] = stage
        value['updated_at'] = timestamp()
        calls = [read(p) for p in sorted((run / 'calls').glob('*/record.json'))]
        value['call_counts'] = {name: sum(c.get('status') == name for c in calls)
                                for name in ('running', 'succeeded', 'failed', 'uncertain', 'held')}
        value['observed_call_count'] = len(calls)
        write_json(path, value)
    return value


class ExperimentRuns:
    """Use only on a fresh parent; historical combined runs remain immutable."""
    def __init__(self, parent, family, conditions, *, root=None, starter=start_run):
        self.parent = Path(parent).resolve(strict=True)
        self.workspace = self.parent.parent.parent
        self.root = Path(root).resolve() if root else self.workspace
        self.path = self.parent / 'experiment.json'
        self.active = self.root / '.orchestration/experiment-active.json'
        self.gate = self.active.with_suffix('.lock')
        state = Coordinator(self.parent).read()
        # A fresh parent keeps the identity start created: its run name is the session.
        if state['session'] != self.parent.name:
            raise ValueError('Select the exact authorized experiment parent owner/session')
        if family not in ('memory_control', 'solo_vs_team', 'six_bot_memory', 'jev_memory'):
            raise ValueError('Unknown experiment family')
        ids = [item['id'] for item in conditions]
        if len(set(ids)) != len(ids) or not ids or any(not re.fullmatch(r'[a-z0-9-]{1,50}', i) for i in ids):
            raise ValueError('Conditions need unique bounded slug identities')
        if family == 'jev_memory':
            if tuple(ids) != JEV_CONDITIONS:
                raise ValueError('Jev comparison requires the four declared cold-then-Jev conditions')
            for item in conditions:
                validate_jev_condition(item)
        # Check before taking a new lock so even rejected historical runs get no
        # extra files. Repeat under the lock to close the creation race.
        def require_fresh():
            if (any((self.parent / name).exists() for name in ('calls', 'trials', 'attempts', 'condition.json'))
                    or any((self.parent / 'review' / name).exists() for name in ('experiment-lock.json', 'benchmark-lock.json'))
                    or read(self.parent / 'run.json').get('tasks')):
                raise ValueError('Historical/combined evidence must remain unchanged; start a fresh parent run')
        if not self.path.exists():
            require_fresh()
        with file_lock(self.parent / 'experiment.lock'):
            if self.path.exists():
                data = read(self.path)
                if data.get('schema_version') != 1 or data.get('family') != family or [r['id'] for r in data['conditions']] != ids:
                    raise ValueError('Saved experiment identity/order changed')
                self.data = data
                for row, requested in zip(data['conditions'], conditions):
                    child = self.child(row['id'])
                    metadata = read(child / 'condition.json')
                    if any(metadata.get(key) != requested.get(key) for key in ('id', 'label', 'memory_mode', 'roster')):
                        raise ValueError('Saved condition protocol changed')
                return
            require_fresh()
            manifest = read(self.parent / 'run.json')
            self.data = dict(schema_version=1, family=family, parent_run_id=self.parent.name,
                             project_id=manifest.get('project_id'), created_at=timestamp(), status='preparing',
                             conditions=[], setup_usage={'included': False, 'tokens': None, 'status': 'unknown',
                                 'note': 'Controller/setup agents are outside scored contestants; their usage is not zero.'})
            write_json(self.path, self.data)
            for item in conditions:
                project = 'experiment-' + hashlib.sha256(self.parent.name.encode()).hexdigest()[:16] + '-' + item['id']
                packet = starter(workspace=self.workspace, name=item['id'],
                    objective='Isolated ' + family + ' condition: ' + item['label'], project=project,
                    no_memory=True, root=self.root)
                child = Path(packet['run']).resolve(strict=True)
                metadata = dict(schema_version=1, family=family, parent_run_id=self.parent.name,
                    run_id=child.name, project_id=project, id=item['id'], label=item['label'],
                    status='planned', stage='waiting', memory_mode=item['memory_mode'],
                    roster=item['roster'], contestant_count=len(item['roster']), helper_count=0,
                    helper_policy='No contestant may launch helper agents or delegate outside its declared roster.',
                    setup_usage=self.data['setup_usage'], created_at=timestamp(), updated_at=timestamp(),
                    observed_call_count=0, call_counts={})
                write_json(child / 'condition.json', metadata)
                child_manifest = read(child / 'run.json')
                # start_run records the caller's Codex thread as lineage by
                # default. It is the experiment controller, not the contestant.
                # Keeping that native usage link would charge setup to every arm.
                child_manifest.pop('native_parent_session_id', None)
                child_manifest.update(display_name=item['label'], experiment_parent_run_id=self.parent.name,
                    experiment_condition_id=item['id'], providers=list(dict.fromkeys(r['provider'] for r in item['roster'])),
                    authorization=manifest.get('authorization', {}), native_work=True)
                write_json(child / 'run.json', child_manifest)
                self.data['conditions'].append({'id': item['id'], 'run_id': child.name, 'label': item['label']})
                write_json(self.path, self.data)
            self.data['status'] = 'planned'
            write_json(self.path, self.data)
            manifest.update(display_name='Experiment comparison: ' + family, experiment_family=family,
                final_artifacts=list(dict.fromkeys(manifest.get('final_artifacts', []) + ['experiment.json'])))
            write_json(self.parent / 'run.json', manifest)

    def child(self, identifier):
        row = next((r for r in self.data['conditions'] if r['id'] == identifier), None)
        if row is None or Path(row['run_id']).name != row['run_id']:
            raise ValueError('Unknown isolated condition')
        child = self.parent.parent / row['run_id']
        if child.resolve().parent != self.parent.parent or child.is_symlink():
            raise ValueError('Condition resolves outside the experiment workspace')
        metadata = read(child / 'condition.json')
        manifest = read(child / 'run.json')
        if (metadata.get('parent_run_id') != self.parent.name or metadata.get('id') != identifier
                or metadata.get('run_id') != child.name or manifest.get('project_id') != metadata.get('project_id')):
            raise ValueError('Condition identity or memory scope changed')
        return child

    def status(self, identifier):
        return read(self.child(identifier) / 'condition.json')['status']

    @contextmanager
    def condition(self, identifier):
        child = self.child(identifier)
        lease = uuid.uuid4().hex
        with file_lock(self.gate):
            if self.active.exists():
                previous = read(self.active)
                if previous.get('schema_version') != 1 or previous.get('status') != 'completed':
                    raise RuntimeError('Another experiment is active, uncertain or malformed; reconcile its exact saved calls before continuing')
            if self.status(identifier) != 'planned':
                raise RuntimeError('Condition is already completed, active or uncertain; no automatic rerun')
            if self.data['family'] == 'jev_memory':
                preceding = JEV_CONDITIONS[:JEV_CONDITIONS.index(identifier)]
                if any(self.status(key) != 'completed' for key in preceding):
                    raise RuntimeError('Complete earlier declared Jev conditions before starting this one')
            if any((child / 'calls').glob('*/record.json')):
                raise RuntimeError('Condition already has call evidence; reconcile it explicitly instead of resuming inference')
            # A durable active receipt survives a killed Python process and OS lock release.
            write_json(self.active, dict(schema_version=1, status='active', lease_id=lease,
                parent_run_id=self.parent.name, condition_id=identifier, run_id=child.name,
                pid=os.getpid(), started_at=timestamp()))
            publish_condition(child, status='running', stage='starting', started_at=timestamp())
        self._parent_status('running')
        try:
            yield child
        except BaseException as exc:
            for path in (child / 'calls').glob('*/record.json'):
                call = read(path)
                if call.get('status') == 'running':
                    call.update(status='uncertain', failure_type=type(exc).__name__, ended_at=timestamp())
                    write_json(path, call)
            publish_condition(child, status='uncertain', stage='reconciliation_required',
                              failure_type=type(exc).__name__, ended_at=timestamp())
            with file_lock(self.gate):
                active = read(self.active)
                if active.get('lease_id') == lease:
                    active.update(status='uncertain', ended_at=timestamp())
                    write_json(self.active, active)
            self._parent_status('uncertain')
            raise
        else:
            publish_condition(child, status='completed', stage='completed', ended_at=timestamp())
            with file_lock(self.gate):
                active = read(self.active)
                if active.get('lease_id') != lease:
                    raise RuntimeError('Experiment lease identity changed')
                active.update(status='completed', ended_at=timestamp())
                write_json(self.active, active)
            self._parent_status('completed' if all(self.status(r['id']) == 'completed' for r in self.data['conditions']) else 'planned')

    def _parent_status(self, status):
        with file_lock(self.parent / 'experiment.lock'):
            data = read(self.path)
            # Another process may have started the next condition immediately
            # after the lease was released. Derive status from the durable
            # children instead of overwriting it with an older caller's state.
            states = [read(self.child(row['id']) / 'condition.json')['status'] for row in data['conditions']]
            status = ('uncertain' if 'uncertain' in states else 'running' if 'running' in states else
                      'completed' if states and all(s == 'completed' for s in states) else 'planned')
            data.update(status=status, updated_at=timestamp())
            write_json(self.path, data)
            self.data = data
