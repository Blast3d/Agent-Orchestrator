"""Offline isolation regression checks; scripted responses make zero provider calls."""
import importlib.util
import inspect
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'app'))
sys.path.insert(0,str(ROOT/'benchmarks/memory_control'))
from brain_store import BrainStore
from coordinator_handoff import Coordinator
from experiment_runs import ExperimentRuns, publish_condition, read
from experiment_view import ExperimentStore
from init_run import create_run
from orchestration_lifecycle import start_run
from task_store import write_json, timestamp
import run_experiment as memory_runner
import transport as memory_transport
import grader as memory_grader


def load(name,path):
    spec=importlib.util.spec_from_file_location(name,path)
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    return module


pilot_runner=load('isolated_solo_pilot',ROOT/'benchmarks/solo_vs_team/run_pilot.py')
pilot_grader=load('isolated_solo_grader',ROOT/'benchmarks/solo_vs_team/grader.py')


def definition(identifier):
    return dict(id=identifier,label=identifier,memory_mode='disabled',
                roster=[dict(id='solo',provider='OpenAI',role='implementation',model='gpt-6-astra')])


def parent(workspace,name='test-parent'):
    run,_=create_run(workspace,name,'Offline experiment isolation check',project_id='test-project')
    return run


class GroupTests(unittest.TestCase):
    def test_children_are_visible_before_calls_with_distinct_scopes_and_no_helpers(self):
        with tempfile.TemporaryDirectory() as folder:
            workspace=Path(folder);run=parent(workspace)
            group=ExperimentRuns(run,'memory_control',[definition('one'),definition('two')],root=workspace)
            self.assertEqual(len(read(run/'experiment.json')['conditions']),2)
            children=[group.child(i) for i in ('one','two')]
            self.assertEqual(len({read(c/'run.json')['project_id'] for c in children}),2)
            for child in children:
                condition=read(child/'condition.json')
                self.assertEqual(condition['helper_count'],0)
                self.assertEqual(condition['contestant_count'],1)
                self.assertEqual(condition['status'],'planned')
                self.assertIsNone(condition['setup_usage']['tokens'])
                self.assertTrue((child/'startup-context.json').exists())
            with group.condition('one') as child:
                self.assertEqual(read(child/'condition.json')['status'],'running')
                with self.assertRaises(RuntimeError):
                    with group.condition('two'):pass
            self.assertEqual(group.status('one'),'completed')
            with group.condition('two'):pass
            self.assertEqual(read(run/'experiment.json')['status'],'completed')
            with self.assertRaises(RuntimeError):
                with group.condition('one'):pass

    def test_crashed_process_lease_is_not_reclaimed(self):
        with tempfile.TemporaryDirectory() as folder:
            workspace=Path(folder);run=parent(workspace)
            conditions=[definition('one'),definition('two')]
            group=ExperimentRuns(run,'memory_control',conditions,root=workspace)
            script=('import json,os,sys;from pathlib import Path;sys.path.insert(0,sys.argv[1]);'
                    'from experiment_runs import ExperimentRuns;'
                    'g=ExperimentRuns(Path(sys.argv[2]),"memory_control",json.loads(sys.argv[3]),root=Path(sys.argv[4]));'
                    'lease=g.condition("one");lease.__enter__();os._exit(17)')
            result=subprocess.run([sys.executable,'-c',script,str(ROOT/'app'),str(run),json.dumps(conditions),str(workspace)],capture_output=True)
            self.assertEqual(result.returncode,17,result.stderr.decode())
            self.assertEqual(read(group.active)['status'],'active')
            self.assertEqual(group.status('one'),'running')
            with self.assertRaises(RuntimeError):
                with group.condition('two'):pass

    def test_failure_keeps_receipts_and_blocks_another_parent(self):
        with tempfile.TemporaryDirectory() as folder:
            workspace=Path(folder)
            first=ExperimentRuns(parent(workspace),'memory_control',[definition('one')],root=workspace)
            second=ExperimentRuns(parent(workspace,'second'),'memory_control',[definition('two')],root=workspace)
            with self.assertRaisesRegex(RuntimeError,'scripted failure'):
                with first.condition('one') as child:
                    directory=child/'calls/failure';directory.mkdir(parents=True)
                    write_json(directory/'record.json',dict(status='uncertain',response='retained partial answer'))
                    (directory/'stdout.jsonl').write_text('raw partial event\n',encoding='utf-8')
                    publish_condition(child,stage='implementation')
                    raise RuntimeError('scripted failure')
            self.assertEqual(first.status('one'),'uncertain')
            self.assertEqual(read(first.active)['status'],'uncertain')
            self.assertEqual(read(first.child('one')/'condition.json')['call_counts']['uncertain'],1)
            with self.assertRaises(RuntimeError):
                with second.condition('two'):pass

    def test_historical_parent_is_rejected_without_new_files(self):
        with tempfile.TemporaryDirectory() as folder:
            workspace=Path(folder);run=parent(workspace)
            (run/'calls').mkdir();(run/'calls/retained.txt').write_text('old evidence')
            before={p.relative_to(run):p.read_bytes() for p in run.rglob('*') if p.is_file()}
            with self.assertRaisesRegex(ValueError,'Historical'):
                ExperimentRuns(run,'memory_control',[definition('one')],root=workspace)
            after={p.relative_to(run):p.read_bytes() for p in run.rglob('*') if p.is_file()}
            self.assertEqual(before,after)
            self.assertFalse((run/'experiment.json').exists())

    def test_malformed_lease_and_preexisting_calls_fail_closed(self):
        with tempfile.TemporaryDirectory() as folder:
            workspace=Path(folder)
            group=ExperimentRuns(parent(workspace),'memory_control',[definition('one')],root=workspace)
            write_json(group.active,{'status':'unknown'})
            with self.assertRaises(RuntimeError):
                with group.condition('one'):pass
            write_json(group.active,{'schema_version':1,'status':'completed'})
            directory=group.child('one')/'calls/old';directory.mkdir(parents=True)
            write_json(directory/'record.json',dict(status='uncertain'))
            with self.assertRaisesRegex(RuntimeError,'already has call evidence'):
                with group.condition('one'):pass


class ScriptedTransport:
    root=None
    observed=[]

    def __init__(self,run):
        self.run=Path(run);self.project=read(self.run/'run.json')['project_id']
        self.coordinator=Coordinator(self.run);self.empty=self.run/'empty-workspace';self.empty.mkdir(exist_ok=True)

    def checkpoint(self,label,memory=False):
        state=self.coordinator.read();checkpoint=state['checkpoint'];checkpoint['next_steps']=[label]
        state=self.coordinator.checkpoint(checkpoint,'astra',state['session'],state['generation'])
        return start_run(run=self.run,owner='astra',session=state['session'],generation=state['generation'],
                         no_memory=not memory,query=memory_runner.QUERY,root=self.root)

    def call(self,identifier,provider,prompt,memory=False):
        meta=read(self.run/'condition.json')
        assert meta['status']=='running'
        recalled=BrainStore(self.root).search(memory_runner.QUERY,self.project) if memory else None
        roles=read(memory_runner.HERE/'fixture-spec.json')['roles']
        owned=list(roles) if '-solo-' in identifier else [role for role in roles if '-'+role+'-' in identifier]
        assert owned
        helpers='\n\n'.join(inspect.getsource(f) for f in (memory_grader.integer,memory_grader.normalized,memory_grader.canonical,memory_grader.fields))
        if identifier.endswith('turn1'):
            actions=[dict(op='read',value=roles[role]['path']) for role in owned]+[dict(op='read',value='docs/contracts.md')]
            files=[]
        else:
            actions=[]
            files=[dict(path=roles[role]['path'],content=helpers+'\n\n'+inspect.getsource(memory_grader.REFERENCES[role]).replace('reference_'+role,roles[role]['function'],1)) for role in owned]
        record=dict(call_id=identifier,run_id=self.run.name,condition_id=meta['id'],parent_run_id=meta['parent_run_id'],
            provider=provider,status='succeeded',response=json.dumps(dict(actions=actions,files=files,memory_used=[],notes='Scripted reference fixture check')),
            actor_id='solo' if '-solo-' in identifier else owned[0],role='all' if '-solo-' in identifier else owned[0],stage=meta['stage'],
            usage={'input_tokens':11,'output_tokens':3,'cache_read_input_tokens':0,'cache_creation_input_tokens':0},
            measurement_mode='scripted',provider_calls=0,scope='contestant',helper_count=0,
            elapsed_seconds=0.0,started_at=timestamp(),ended_at=timestamp(),memory_enabled=memory,
            memory_ids=[r['id'] for r in recalled['results']] if recalled else [],memory_context=recalled['context'] if recalled else '',
            memory_execution_requested=memory)
        directory=self.run/'calls'/identifier;directory.mkdir(parents=True)
        write_json(directory/'record.json',record);publish_condition(self.run)
        self.observed.append((meta['id'],self.project,memory,record['memory_ids']))
        return record


class RunnerTests(unittest.TestCase):
    def test_all_memory_conditions_have_disjoint_calls_and_equal_seed_text(self):
        with tempfile.TemporaryDirectory() as folder:
            workspace=Path(folder);run=parent(workspace)
            ScriptedTransport.root=workspace;ScriptedTransport.observed=[]
            with patch.object(memory_runner,'ROOT',workspace),patch.object(memory_runner,'Transport',ScriptedTransport),patch.object(memory_runner,'Guard') as guard:
                guard.return_value.status.return_value={'status':'offline'}
                experiment=memory_runner.Experiment(run);experiment.initialize()
                with self.assertRaises(ValueError):experiment.seed()
                for identifier in memory_runner.TRIALS[:2]:experiment.trial(identifier)
                for identifier in memory_runner.TRIALS:
                    scope=read(experiment.group.child(identifier)/'run.json')['project_id']
                    self.assertEqual(BrainStore(workspace).list_memories(scope,limit=100),[])
                experiment.seed()
                warm=[]
                for identifier in memory_runner.TRIALS[2:]:
                    child=experiment.group.child(identifier);scope=read(child/'run.json')['project_id']
                    rows=BrainStore(workspace).list_memories(scope,limit=100)
                    self.assertEqual(len(rows),4);warm.append(rows)
                    experiment.trial(identifier)
                self.assertEqual({r['content'] for r in warm[0]},{r['content'] for r in warm[1]})
                self.assertTrue({r['id'] for r in warm[0]}.isdisjoint({r['id'] for r in warm[1]}))
                all_calls=[]
                for identifier in memory_runner.TRIALS:
                    child=experiment.group.child(identifier);summary=read(child/'trials'/identifier/'summary.json')
                    self.assertEqual(summary['final_grade']['passed'],280)
                    self.assertEqual(read(child/'condition.json')['status'],'completed')
                    paths=list((child/'calls').glob('*/record.json'))
                    self.assertEqual(len(paths),3 if identifier.startswith('solo') else 9)
                    self.assertEqual(sum(read(p)['usage']['input_tokens'] for p in paths),33 if identifier.startswith('solo') else 99)
                    self.assertTrue(all(read(p)['condition_id']==identifier and read(p)['run_id']==child.name for p in paths))
                    detail=ExperimentStore(workspace).detail(run.name,identifier)
                    self.assertEqual(detail['run_id'],child.name)
                    self.assertEqual(detail['metrics']['calls'],len(paths))
                    self.assertEqual(detail['metrics']['input_tokens'],33 if identifier.startswith('solo') else 99)
                    self.assertEqual(detail['metrics']['helper_count'],0)
                    all_calls.extend(paths)
                self.assertEqual(len(all_calls),24)
                self.assertFalse((run/'calls').exists())
                self.assertTrue(all(t['status']=='completed' for t in read(run/'run.json')['tasks']))
                observed=ScriptedTransport.observed
                self.assertTrue(all(not row[2] and not row[3] for row in observed[:12]))
                self.assertTrue(all(row[2] and len(row[3])==4 for row in observed[12:]))

    def test_pilot_attempts_use_separate_runs_and_rosters(self):
        with tempfile.TemporaryDirectory() as folder:
            workspace=Path(folder);run=parent(workspace)
            def save(instance,identifier,provider,response):
                directory=instance.run/'calls'/identifier;directory.mkdir(parents=True)
                record=dict(call_id=identifier,provider=provider,response=response,status='succeeded',
                    usage={'input_tokens':7,'output_tokens':2,'cache_read_input_tokens':0,'cache_creation_input_tokens':0},
                    measurement_mode='scripted',provider_calls=0,
                    started_at=timestamp(),ended_at=timestamp(),elapsed_seconds=0.0)
                instance.persist(directory,record)
                return record
            def native(instance,identifier,task,prompt):
                self.assertIn('delegate',prompt)
                helpers='\n\n'.join(inspect.getsource(f) for f in (pilot_grader.integer,pilot_grader.canonical))
                function=pilot_grader.reference_small if task=='small' else pilot_grader.reference_medium
                name='summarize_usage' if task=='small' else 'allocate_jobs'
                code=helpers+'\n\n'+inspect.getsource(function).replace('reference_'+task,name,1)
                return save(instance,identifier,'OpenAI',json.dumps(dict(code=code,notes='Scripted reference, no inference')))
            def hosted(instance,identifier,task,worker,prompt):
                self.assertIn('new agents',prompt)
                return save(instance,identifier,{'claude':'Anthropic','grok':'xAI'}[worker],'No defects in scripted reference.')
            with patch.object(pilot_runner,'ROOT',workspace),patch.object(pilot_runner.ConditionPilot,'native',native),patch.object(pilot_runner.ConditionPilot,'hosted',hosted):
                # The real constructor reads the maintained operating guide.
                (workspace/'app/assets').mkdir(parents=True)
                (workspace/'app/assets/orchestration-context.md').write_bytes((ROOT/'app/assets/orchestration-context.md').read_bytes())
                pilot=pilot_runner.Pilot(run)
                for task,condition,repetition in pilot_runner.ORDER:
                    summary=pilot.attempt(task,condition,repetition)
                    self.assertEqual(summary['final_passed'],summary['total'])
                self.assertEqual(read(run/'experiment.json')['status'],'completed')
                for item in pilot.group.data['conditions']:
                    child=pilot.group.child(item['id']);metadata=read(child/'condition.json')
                    self.assertEqual(metadata['helper_count'],0)
                    expected=1 if '-solo-' in item['id'] else 2 if item['id'].startswith('small') else 3
                    self.assertEqual(metadata['contestant_count'],expected)
                    self.assertEqual(len(list((child/'calls').glob('*/record.json'))),expected+1)
                    detail=ExperimentStore(workspace).detail(run.name,item['id'])
                    self.assertEqual(detail['metrics']['calls'],expected+1)
                    self.assertEqual(detail['metrics']['input_tokens'],7*(expected+1))
                    self.assertEqual(detail['metrics']['contestant_count'],expected)
                self.assertFalse((run/'calls').exists())

    def test_local_preflight_failure_is_visible_without_claiming_provider_execution(self):
        for family in ('memory_control','solo_vs_team'):
            with self.subTest(family=family),tempfile.TemporaryDirectory() as folder:
                workspace=Path(folder);run=parent(workspace)
                (workspace/'app/assets').mkdir(parents=True)
                (workspace/'app/assets/orchestration-context.md').write_bytes((ROOT/'app/assets/orchestration-context.md').read_bytes())
                failed=subprocess.CompletedProcess([],1,b'Brief is missing acceptance criteria',b'')
                if family=='memory_control':
                    with patch.object(memory_runner,'ROOT',workspace),patch.object(memory_transport,'ROOT',workspace),patch.object(memory_runner,'Guard') as guard:
                        guard.return_value.status.return_value={}
                        experiment=memory_runner.Experiment(run);experiment.initialize()
                        identifier='team-cold';call_id=identifier+'-aggregation-turn1'
                        with self.assertRaises(ValueError),experiment.group.condition(identifier) as child:
                            transport=memory_transport.Transport(child)
                            with patch.object(memory_transport.subprocess,'run',return_value=failed) as execute:
                                try:transport.call(call_id,'Anthropic','Invalid brief',False)
                                finally:
                                    self.assertEqual(execute.call_count,1)
                                    self.assertIn('brief-check',execute.call_args.args[0])
                else:
                    with patch.object(pilot_runner,'ROOT',workspace):
                        experiment=pilot_runner.Pilot(run)
                        identifier='small-team-r1';call_id=identifier+'-audit-claude'
                        with self.assertRaises(ValueError),experiment.group.condition(identifier) as child:
                            pilot=pilot_runner.ConditionPilot(child)
                            with patch.object(pilot_runner.subprocess,'run',return_value=failed) as execute:
                                try:pilot.hosted(call_id,'small','claude','Invalid brief')
                                finally:
                                    self.assertEqual(execute.call_count,1)
                                    self.assertIn('brief-check',execute.call_args.args[0])
                child=experiment.group.child(identifier)
                record=read(child/'calls'/call_id/'record.json')
                self.assertEqual(record['status'],'failed')
                self.assertEqual(record['stage'],'local_preflight')
                self.assertTrue(record['preflight_only'])
                self.assertFalse(record['dispatch_started'])
                self.assertEqual(record['provider_calls'],0)
                self.assertEqual(record['actual_models'],[])
                self.assertIsNone(record['usage'])
                self.assertIn('No provider request',record['response'])
                self.assertEqual(ExperimentStore(workspace).detail(run.name,identifier)['metrics']['calls'],1)
                self.assertEqual(read(child/'condition.json')['call_counts']['failed'],1)
                self.assertEqual(experiment.group.status(identifier),'uncertain')


class GradeGroupingTests(unittest.TestCase):
    def test_full_grades_and_load_failures_retain_groups_with_original_totals(self):
        from collections import Counter
        expected={'aggregation':87,'allocation':91,'summary':102,'small':47,'medium':106}
        for role in ('aggregation','allocation','summary'):
            result=memory_grader.evaluate(memory_grader.REFERENCES[role],role)
            counts=Counter(case['group'] for case in result['cases'])
            self.assertEqual(result['total'],expected[role]);self.assertEqual(result['passed'],expected[role])
            self.assertGreater(counts['normal'],0);self.assertGreater(counts['edge'],0)
        for task in ('small','medium'):
            reference=pilot_grader.reference_small if task=='small' else pilot_grader.reference_medium
            result=pilot_grader.evaluate(reference,task)
            counts=Counter(case['group'] for case in result['cases'])
            self.assertEqual(result['total'],expected[task]);self.assertEqual(result['passed'],expected[task])
            self.assertGreater(counts['normal'],0);self.assertGreater(counts['edge'],0)
        with tempfile.TemporaryDirectory() as folder:
            commands=[[sys.executable,'-I',str(ROOT/'benchmarks/memory_control/grader.py'),'--workspace',folder]]
            commands.extend([sys.executable,'-I',str(ROOT/'benchmarks/solo_vs_team/grader.py'),'--task',task,
                             '--candidate',str(Path(folder)/'missing.py')] for task in ('small','medium'))
            for argv in commands:
                process=subprocess.run(argv,capture_output=True,timeout=20)
                report=json.loads(process.stdout)
                for result in report.get('roles',[report]):
                    key=result.get('role',result.get('task'));counts=Counter(case['group'] for case in result['cases'])
                    self.assertEqual(result['total'],expected[key]);self.assertEqual(result['passed'],0)
                    self.assertEqual(len(result['cases']),expected[key])
                    self.assertGreater(counts['normal'],0);self.assertGreater(counts['edge'],0)


if __name__=='__main__':unittest.main()
