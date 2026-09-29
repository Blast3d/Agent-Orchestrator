"""Offline matched-suite controls. All writes are temporary; providers are mocked."""
import hashlib
import json
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import run_experiment as runner
import transport
from comparison_protocol import conditions, JEV_TRIALS, memory_telemetry, ranking_policy, validate_retrieval
from orchestration_lifecycle import start_run
from experiment_runs import publish_condition


def config(projects=()):
    return dict(status='ready', enabled=True, key_present=True, authorized_projects=list(projects),
                purposes=['memory_rank', 'memory_passage_review'], min_confidence=.8,
                cache_enabled=True, cache_ttl_seconds=3600)


def recall():
    return dict(results=[{'id': 'seed-' + str(i)} for i in range(4)], context='four frozen notes',
        lookup_ms=18.5, elapsed_ms=20.5, trace_id='trace-123', trace_status='recorded',
        retrieval=dict(schema_version=1, candidate_count=4, provider_calls=1,
            timings_ms={'trace': 2, 'total': 20.5, 'jev': 15},
            jev=dict(status='ok', applied=True, order_changed=False, promoted_count=0,
                candidate_count=4, scored_candidate_count=4, purpose='memory_passage_review',
                provider_calls=1, input_tokens=20, output_tokens=8, cost_usd=None, elapsed_ms=15,
                cache={'status': 'miss'}, batches=[{'request_id': 'request-1', 'usage': {'input_tokens': 20}}])))


class DeclarationTests(unittest.TestCase):
    def test_unchanged_public_contract_and_280_reference_cases(self):
        import grader
        public=json.loads((HERE/'fixture/tests/public_cases.json').read_text(encoding='utf-8'))
        total=0
        for role in grader.ROLES:
            expected=[{key:value for key,value in row.items() if key!='group'}
                      for row in grader.cases(role) if row['public']]
            self.assertEqual(public[role], expected)
            result=grader.evaluate(grader.REFERENCES[role], role)
            self.assertTrue(result['all_passed'])
            total+=result['total']
        self.assertEqual(total, 280)

    def test_four_condition_default_and_six_condition_route_parity(self):
        default=conditions()
        matched=conditions(True)
        self.assertEqual([r['id'] for r in default], list(runner.TRIALS))
        self.assertEqual([r['id'] for r in matched], list(JEV_TRIALS))
        self.assertEqual([r['memory_ranking_mode'] for r in matched],
                         ['disabled', 'disabled', 'ordinary', 'ordinary', 'jev', 'jev'])
        for row in matched:
            self.assertEqual(row['roster'], default[1 if row['id'].startswith('team') else 0]['roster'])

    def test_exact_project_scope_is_the_switch(self):
        cold, _, ordinary, _, jev, _=conditions(True)
        for row in (cold, ordinary):
            ranking_policy(row, 'exact-project', config(['another-project']))
            with self.assertRaisesRegex(ValueError, 'must not be authorized'):
                ranking_policy(row, 'exact-project', config(['exact-project']))
        with self.assertRaisesRegex(ValueError, 'exact project authorization'):
            ranking_policy(jev, 'exact-project', config(['another-project']))
        receipt=ranking_policy(jev, 'exact-project', config(['exact-project']))
        self.assertTrue(receipt['project_authorized'])
        for changed in (dict(config(['exact-project']), status='unavailable'),
                        dict(config(['exact-project']), purposes=['citations'])):
            with self.assertRaises(ValueError):ranking_policy(jev, 'exact-project', changed)

    def test_native_and_hosted_keep_equivalent_full_telemetry(self):
        value=recall()
        native=memory_telemetry(value, native=True)
        hosted=dict(value, ids=[r['id'] for r in value['results']], execution_requested=True)
        hosted.pop('results')
        exported=memory_telemetry(hosted)
        for key in ('memory_ids', 'memory_sha256', 'memory_lookup_ms', 'memory_elapsed_ms',
                    'memory_trace_id', 'memory_trace_status', 'memory_retrieval'):
            self.assertEqual(native[key], exported[key])
        jev=native['memory_retrieval']['jev']
        self.assertTrue(jev['applied'])
        self.assertFalse(jev['order_changed'])
        self.assertIsNone(jev['cost_usd'])
        self.assertEqual(jev['batches'], value['retrieval']['jev']['batches'])
        validate_retrieval(conditions(True)[4], native)
        with self.assertRaises(ValueError):validate_retrieval(conditions(True)[2], native)
        with self.assertRaises(ValueError):validate_retrieval(conditions(True)[4], {'memory_retrieval': {}})


class IsolatedSuiteTests(unittest.TestCase):
    def setUp(self):
        temporary=tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root=Path(temporary.name)
        (self.root/'app/assets').mkdir(parents=True)
        (self.root/'app/assets/orchestration-context.md').write_bytes(
            (HERE.parents[1]/'app/assets/orchestration-context.md').read_bytes())
        self.parent=Path(start_run(workspace=self.root, name='offline', objective='Offline synthetic controls',
                                  project='offline-parent', no_memory=True, root=self.root)['run'])
        for module in (runner, transport):
            p=patch.object(module, 'ROOT', self.root);p.start();self.addCleanup(p.stop)
        p=patch.object(runner, 'Guard');self.guard=p.start();self.addCleanup(p.stop)
        self.guard.return_value.status.return_value={'offline': True}
        p=patch.object(runner, 'BrainStore');self.brain=p.start();self.addCleanup(p.stop)
        self.brain.return_value.list_memories.return_value=[]
        self.experiment=runner.Experiment(self.parent, True)
        self.experiment.initialize()

    def transport_for(self, identifier):
        child=self.experiment.group.child(identifier)
        publish_condition(child, status='running')
        return transport.Transport(child)

    def test_six_scopes_are_fresh_and_independent(self):
        paths=[self.experiment.group.child(name) for name in JEV_TRIALS]
        projects=[transport.read(path/'run.json')['project_id'] for path in paths]
        self.assertEqual(len(set(paths)), 6)
        self.assertEqual(len(set(projects)), 6)
        self.assertEqual(transport.read(self.experiment.lock)['order'], list(JEV_TRIALS))
        for path in paths:
            self.assertEqual(transport.read(path/'startup-context.json')['provider_calls'], 0)

    def test_seeding_and_warm_calls_wait_for_both_cold_conditions(self):
        with patch.object(runner, 'capture_run') as capture:
            with self.assertRaisesRegex(ValueError, 'Both cold'):self.experiment.seed()
            publish_condition(self.experiment.group.child('solo-cold'), status='completed')
            with self.assertRaisesRegex(ValueError, 'Both cold'):self.experiment.seed()
            with self.assertRaisesRegex(ValueError, 'Both cold'):self.experiment.trial('solo-warm')
            capture.assert_not_called()

    def test_all_warm_scopes_get_identical_frozen_seed_text(self):
        for name in JEV_TRIALS[:2]:publish_condition(self.experiment.group.child(name), status='completed')
        captures=[]
        def capture(root, run, bundle, **kwargs):
            captures.append(bundle)
            return {'memory_outcome': {'status': 'remembered', 'memory_ids': [run+str(i) for i in range(4)]}}
        fake=Mock()
        fake.coordinator.read.return_value=dict(session='offline', generation=1)
        def create(child):
            fake.project=transport.read(child/'run.json')['project_id']
            return fake
        with patch.object(runner, 'Transport', side_effect=create), patch.object(runner, 'capture_run', side_effect=capture):
            self.experiment.seed()
        self.assertEqual(len(captures), 4)
        self.assertEqual(len({b['project_id'] for b in captures}), 4)
        self.assertTrue(all(b['memories']==captures[0]['memories'] for b in captures))
        self.assertEqual(len(captures[0]['memories']), 4)
        self.assertFalse(any('answer' in b.get('capture_id', '') for b in captures))

    def test_source_change_holds_before_trial(self):
        with patch.object(runner, 'freeze_hashes', return_value={'changed': 'hash'}):
            with self.assertRaisesRegex(ValueError, 'Frozen experimental inputs changed'):
                self.experiment.trial('solo-cold')

    def test_real_seed_capture_allows_first_hosted_assignment(self):
        from assignment_receipts import AssignmentReceipts
        from task_store import TaskStore
        from brain_store import BrainStore
        for name in JEV_TRIALS[:2]:publish_condition(self.experiment.group.child(name), status='completed')
        # Real capture_run, real temporary SQLite Brain, and real canonical import.
        # No contestant/provider execution is involved in the following claim.
        captured=self.experiment.seed()
        store=TaskStore(self.root/'runs/tasks')
        contract=dict(worker='claude', prompt_sha256=hashlib.sha256(b'offline brief').hexdigest(),
                      size='small', category='memory-experiment', claude_model='opus', claude_effort='medium',
                      require_brief_check=True, memory_policy='explicit', memory_query=transport.QUERY)
        for name in JEV_TRIALS[2:]:
            project=transport.read(self.experiment.group.child(name)/'run.json')['project_id']
            self.assertEqual(captured[name]['memory_outcome']['status'], 'remembered')
            imported=store.directory(captured[name]['job_id'])/'result.json'
            self.assertTrue(transport.read(imported)['imported_completed_artifact'])
            self.assertEqual(len(BrainStore(self.root).list_memories(project, limit=100)), 4)
            record, reused=AssignmentReceipts(store).claim(project, 'first-hosted-call', contract,
                {**{key:contract[key] for key in ('worker', 'prompt_sha256', 'size', 'category')},
                 'task':'Offline assignment identity check', 'prompt_bytes':13})
            self.assertFalse(reused)
            self.assertEqual(record['assignment_project_id'], project)

    def test_checkpoint_never_does_background_warm_recall(self):
        contestant=self.transport_for('solo-jev-warm')
        packet={'operating_context': {'context': contestant.guide}}
        with patch.object(transport, 'start_run', return_value=packet) as start:
            contestant.checkpoint('warm round', memory=True)
        self.assertTrue(start.call_args.kwargs['no_memory'])

    def test_uncertain_call_is_never_retried(self):
        contestant=self.transport_for('solo-cold')
        path=contestant.run/'calls/uncertain/record.json'
        transport.write(path, {'status': 'uncertain'})
        with patch.object(contestant, 'native') as native, patch.object(transport, 'load_config') as load:
            with self.assertRaisesRegex(RuntimeError, 'reconciliation'):
                contestant.call('uncertain', 'OpenAI', 'prompt', False)
        native.assert_not_called();load.assert_not_called()

    def test_native_call_keeps_jev_receipt_and_pinned_route(self):
        contestant=self.transport_for('solo-jev-warm')
        calls=[]
        def popen(argv, **kwargs):
            calls.append(argv)
            for event in ({'type': 'item.completed', 'item': {'type': 'agent_message', 'text': '{}'}},
                          {'type': 'turn.completed', 'usage': {'input_tokens': 10, 'output_tokens': 2}}):
                kwargs['stdout'].write((json.dumps(event)+'\n').encode())
            return SimpleNamespace(pid=123, returncode=0, communicate=lambda *a, **kw: None)
        guard=Mock();guard.check.return_value={'allowed': True, 'reservation_id': 'offline-token'}
        with patch.object(transport, 'load_config', return_value=config([contestant.project])), \
             patch.object(transport, 'BrainStore') as brain, patch.object(transport, 'Guard', return_value=guard), \
             patch.object(transport, 'find_codex', return_value='offline-codex'), \
             patch.object(transport.subprocess, 'Popen', side_effect=popen):
            brain.return_value.search.return_value=recall()
            result=contestant.call('native-one', 'OpenAI', 'same frozen prompt', True)
        self.assertEqual(result['memory_retrieval'], recall()['retrieval'])
        self.assertEqual(result['memory_trace_id'], 'trace-123')
        self.assertTrue(result['memory_execution_requested'])
        self.assertEqual(calls[0][calls[0].index('--model')+1], 'gpt-6-astra')
        self.assertIn('model_reasoning_effort=medium', calls[0])
        self.assertEqual(result['status'], 'succeeded')

    def test_hosted_call_keeps_same_full_receipt_without_second_lookup(self):
        contestant=self.transport_for('team-jev-warm')
        recalled=recall();recalled['ids']=[r['id'] for r in recalled.pop('results')]
        recalled.update(execution_requested=True, sha256=hashlib.sha256(recalled['context'].encode()).hexdigest())
        calls=[]
        def execute(argv, **kwargs):
            calls.append(argv)
            if 'brief-check' in argv:return SimpleNamespace(returncode=0, stdout=b'{}', stderr=b'')
            output=Path(argv[argv.index('--output')+1])
            transport.write(output, dict(execution_status='succeeded', job_id='offline-job', response='{}',
                memory_context=recalled, execution_configuration={'reasoning_effort': 'medium'}))
            return SimpleNamespace(returncode=0)
        with patch.object(transport, 'load_config', return_value=config([contestant.project])), \
             patch.object(transport.subprocess, 'run', side_effect=execute), \
             patch.object(transport, 'BrainStore') as brain:
            result=contestant.call('hosted-one', 'Anthropic', 'same frozen prompt', True)
        brain.assert_not_called()
        self.assertEqual(result['memory_retrieval'], recall()['retrieval'])
        self.assertEqual(result['memory_elapsed_ms'], 20.5)
        argv=calls[-1]
        self.assertEqual(argv[argv.index('--claude-model')+1], 'opus')
        self.assertEqual(argv[argv.index('--claude-effort')+1], 'medium')
        self.assertIn('--no-auto-fallback', argv)

    def test_preflight_does_not_construct_child_or_search(self):
        before={p.relative_to(self.root):p.read_bytes() for p in self.root.rglob('*') if p.is_file()}
        with patch.object(runner, 'load_config', return_value=config()), \
             patch.object(runner, 'ExperimentRuns', side_effect=AssertionError('child creation')), \
             patch.object(runner, 'BrainStore', side_effect=AssertionError('Brain access')), \
             patch.object(runner, 'Guard', side_effect=AssertionError('quota access')):
            result=runner.preflight(self.parent, True, 'solo-cold')
        self.assertEqual(result['status'], 'ready')
        self.assertEqual(result['provider_calls'], 0)
        self.assertEqual(before, {p.relative_to(self.root):p.read_bytes() for p in self.root.rglob('*') if p.is_file()})


if __name__ == '__main__':unittest.main()
