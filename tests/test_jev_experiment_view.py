"""Temporary Jev comparison evidence; no providers, credentials, or Brain access."""
from copy import deepcopy
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'app'))
from experiment_page import PAGE
from experiment_runs import ExperimentRuns, JEV_CONDITIONS, jev_conditions
from experiment_view import ExperimentStore


def write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value), encoding='utf-8')


class JevExperimentViewTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.parent = self.root / '.orchestration/suite'
        write(self.parent / 'run.json', {'project_id': 'synthetic-parent', 'tasks': []})
        self.coordinator_patch = patch('experiment_runs.Coordinator')
        self.coordinator_patch.start().return_value.read.return_value = {'owner': 'astra', 'session': 'suite'}
        self.addCleanup(self.coordinator_patch.stop)
        self.start_calls = []

        def starter(**kwargs):
            self.start_calls.append(kwargs)
            child = self.root / '.orchestration' / (kwargs['name'] + '-child')
            write(child / 'run.json', {'project_id': kwargs['project'], 'native_parent_session_id': 'controller'})
            return {'run': str(child)}

        self.runs = ExperimentRuns(self.parent, 'jev_memory', jev_conditions(), root=self.root, starter=starter)
        self.store = ExperimentStore(self.root)

    def call(self, key, **updates):
        child = self.runs.child(key)
        name = key + '-implementer-1'
        memory = '-jev-' in key
        record = {'call_id': name, 'provider': 'Anthropic', 'actor_id': 'implementer',
                  'role': 'Scheduler implementation', 'requested_model': 'opus', 'requested_effort': 'medium',
                  'actual_models': ['claude-opus-synthetic'], 'status': 'succeeded',
                  'run_id': child.name, 'parent_run_id': self.parent.name, 'condition_id': key,
                  'scope': 'contestant', 'helper_count': 0, 'provider_calls': 1,
                  'memory_enabled': memory, 'memory_ids': ['a' * 32] if memory else [],
                  'memory_context': 'Synthetic reviewed evidence.' if memory else '',
                  'memory_lookup_ms': 12.5 if memory else None,
                  'usage': {'input_tokens': 20, 'output_tokens': 7,
                            'cache_read_input_tokens': 0, 'cache_creation_input_tokens': 0}, 'elapsed_seconds': 34,
                  'started_at': '2026-09-25T18:00:00Z', 'ended_at': '2026-09-25T18:00:34Z',
                  'response': 'def allocate_jobs(jobs, budgets, completed=()): pass'}
        record.update(updates)
        write(child / 'calls' / name / 'record.json', record)
        (child / 'calls' / name / 'prompt.md').write_text('Frozen synthetic scheduler task.', encoding='utf-8')
        return child, name, record

    def test_declares_exact_four_conditions_and_separate_scopes_without_contestant_calls(self):
        self.assertEqual(tuple(row['id'] for row in self.runs.data['conditions']), JEV_CONDITIONS)
        self.assertEqual(len({row['project'] for row in self.start_calls}), 4)
        self.assertTrue(all(row['no_memory'] for row in self.start_calls))
        for key in JEV_CONDITIONS:
            child = self.runs.child(key)
            self.assertNotIn('native_parent_session_id', json.loads((child / 'run.json').read_text()))
            detail = self.store.detail('suite', key)
            self.assertEqual(detail['family'], 'jev_memory')
            self.assertEqual(detail['metrics']['contestant_count'], 1)
            self.assertEqual(detail['metrics']['provider_count'], 1)
            self.assertEqual(detail['metrics']['helper_count'], 0)
            self.assertEqual(detail['metrics']['calls'], 0)
            self.assertEqual(detail['roster'][0]['worker_id'], 'implementer')
            self.assertEqual(detail['roster'][0]['provider'], 'Anthropic')
            self.assertEqual(detail['roster'][0]['requested_model'], 'opus')
            self.assertEqual(detail['roster'][0]['requested_effort'], 'medium')
            self.assertEqual(detail['roster'][0]['role'], 'Scheduler implementation')
            self.assertEqual(detail['stage'], 'waiting')
            if '-jev-' in key:
                self.assertIn('Jev', detail['label'])
                self.assertEqual(detail['memory']['mode'], 'seeded')
            else:
                self.assertEqual(detail['memory']['mode'], 'disabled')
        listing = self.store.listing()
        self.assertEqual(listing['errors'], [])
        self.assertEqual(len(listing['experiments'][0]['conditions']), 4)
        self.assertEqual(listing['model_calls'], 0)

    def test_detail_keeps_106_cases_tokens_timings_and_unknown_cost_per_condition(self):
        child, name, _ = self.call('medium-jev-r1')
        self.call('medium-cold-r1', usage={'input_tokens': 999, 'output_tokens': 3})
        folder = child / 'trials/medium-jev-r1'
        cases = [{'case': 'case-' + str(i), 'group': 'normal' if i < 60 else 'edge',
                  'passed': i != 17, 'public': i < 6} for i in range(106)]
        write(folder / 'final-grade.json', {'passed': 105, 'total': 106, 'cases': cases})
        write(folder / 'summary.json', {'id': 'medium-jev-r1', 'calls': [name],
                                       'wall_seconds': 38.5, 'call_path_seconds': 34})
        before = {str(path): path.read_bytes() for path in self.root.rglob('*') if path.is_file()}
        detail = self.store.detail('suite', 'medium-jev-r1')
        self.assertEqual(detail['metrics']['input_tokens'], 20)
        self.assertEqual(detail['metrics']['output_tokens'], 7)
        self.assertEqual(detail['metrics']['wall_seconds'], 38.5)
        self.assertEqual(detail['metrics']['call_path_seconds'], 34)
        self.assertEqual(detail['memory']['lookup_ms'], 12.5)
        self.assertEqual(detail['memory']['project_id'], self.start_calls[2]['project'])
        self.assertEqual(detail['calls'][0]['elapsed_seconds'], 34)
        self.assertEqual(detail['calls'][0]['requested_effort'], 'medium')
        self.assertEqual(detail['grades']['final']['total'], 106)
        self.assertEqual(len(detail['grades']['final']['cases']), 106)
        self.assertEqual(detail['grades']['final']['groups'], [
            {'name': 'normal', 'passed': 59, 'total': 60}, {'name': 'edge', 'passed': 46, 'total': 46}])
        self.assertIsNone(detail['usage']['totals']['additional_charge_usd'])
        self.assertIn('does not isolate the incremental effect of Jev', ' '.join(detail['notes']))
        self.assertIn('Jev-assisted', detail['memory']['note'])
        self.assertEqual(before, {str(path): path.read_bytes() for path in self.root.rglob('*') if path.is_file()})

    def test_unknown_usage_and_uncertain_execution_remain_unknown(self):
        self.call('medium-jev-r2', status='uncertain', usage=None, elapsed_seconds=None)
        detail = self.store.detail('suite', 'medium-jev-r2')
        self.assertIsNone(detail['metrics']['input_tokens'])
        self.assertIsNone(detail['metrics']['output_tokens'])
        self.assertIsNone(detail['calls'][0]['elapsed_seconds'])
        self.assertIsNone(detail['usage']['totals']['additional_charge_usd'])

    def test_exact_actor_call_model_effort_scope_and_cold_memory_are_enforced(self):
        child, name, record = self.call('medium-cold-r1')
        changes = [('provider', 'OpenAI'), ('actor_id', 'auditor'), ('role', 'Other'),
                   ('run_id', 'other-child'), ('condition_id', 'medium-jev-r1'),
                   ('parent_run_id', 'other-parent'), ('scope', 'setup'), ('helper_count', 1),
                   ('requested_model', 'sonnet'), ('requested_effort', 'high'),
                   ('memory_enabled', True), ('memory_enabled', 0),
                   ('memory_ids', ['private']), ('memory_context', 'Unexpected memory.')]
        for field, value in changes:
            with self.subTest(field=field):
                write(child / 'calls' / name / 'record.json', dict(record, **{field: value}))
                with self.assertRaises(ValueError):
                    self.store.detail('suite', 'medium-cold-r1')
        write(child / 'calls' / name / 'record.json', record)
        second = 'medium-cold-r1-implementer-2'
        write(child / 'calls' / second / 'record.json', dict(record, call_id=second))
        with self.assertRaisesRegex(ValueError, 'one declared'):
            self.store.detail('suite', 'medium-cold-r1')

    def test_other_arm_cannot_be_added_or_relabelled_as_normal_brain(self):
        parent = self.parent / 'experiment.json'
        original = json.loads(parent.read_text())
        altered = deepcopy(original)
        altered['conditions'][2]['id'] = 'medium-brain-r1'
        write(parent, altered)
        with self.assertRaises(ValueError):
            self.store.detail('suite', 'medium-cold-r1')
        write(parent, original)
        child = self.runs.child('medium-jev-r1')
        data = json.loads((child / 'condition.json').read_text())
        data['label'] = 'Ordinary Brain recall'
        write(child / 'condition.json', data)
        with self.assertRaises(ValueError):
            self.store.detail('suite', 'medium-jev-r1')

    def test_call_and_artifact_outputs_stay_inside_selected_condition(self):
        child, name, _ = self.call('medium-jev-r1')
        _, other, _ = self.call('medium-cold-r1')
        folder = child / 'trials/medium-jev-r1'
        write(folder / 'summary.json', {'calls': [name]})
        (folder / 'final').mkdir()
        (folder / 'final/scheduler.py').write_text('def allocate_jobs(): pass', encoding='utf-8')
        self.assertIn('allocate_jobs', self.store.output('suite', 'medium-jev-r1', 'final')['content'])
        self.assertIn('Synthetic', self.store.output('suite', 'medium-jev-r1', 'call:' + name + ':memory')['content'])
        with self.assertRaises(ValueError):
            self.store.output('suite', 'medium-jev-r1', 'call:' + other + ':response')

    def test_leases_require_cold_first_and_never_automatically_repeat(self):
        with self.assertRaisesRegex(RuntimeError, 'earlier declared'):
            with self.runs.condition('medium-jev-r1'):
                self.fail('Warm condition started before cold conditions')
        for key in JEV_CONDITIONS:
            with self.runs.condition(key):
                self.assertEqual(self.runs.status(key), 'running')
            self.assertEqual(self.runs.status(key), 'completed')
        with self.assertRaisesRegex(RuntimeError, 'already completed'):
            with self.runs.condition('medium-cold-r1'):
                self.fail('Completed condition was rerun')

    def test_runs_reject_changed_protocol_before_mutation(self):
        before = (self.parent / 'experiment.json').read_bytes()
        for change in ('order', 'roster', 'mode'):
            conditions = jev_conditions()
            if change == 'order':
                conditions.reverse()
            elif change == 'roster':
                conditions[0]['roster'][0]['effort'] = 'high'
            else:
                conditions[0]['memory_mode'] = 'seeded'
            with self.subTest(change=change), self.assertRaises(ValueError):
                ExperimentRuns(self.parent, 'jev_memory', conditions, root=self.root)
        self.assertEqual(before, (self.parent / 'experiment.json').read_bytes())

    def test_page_uses_family_specific_fresh_claude_roster_and_requested_effort(self):
        self.assertIn("if(family==='jev_memory') return 'One Claude Opus", PAGE)
        self.assertIn('fresh supplied-text session per condition', PAGE)
        self.assertIn('rosterNote(m,d.family)', PAGE)
        self.assertIn('fmt(r.requested_effort)', PAGE)


if __name__ == '__main__':
    unittest.main()
