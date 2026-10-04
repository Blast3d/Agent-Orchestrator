"""Exercise scoped skill review, delivery and feedback without a provider."""
import hashlib
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'app'))
import skill_flow as flow
from task_store import TaskStore, timestamp


class SkillFlowTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.catalog = {'manifest_sha256': 'a' * 64, 'packs': [{
            'id': 'engineering', 'label': 'Engineering', 'description': 'Debug and verify',
            'skills': [{'id': 'debug', 'name': 'Debugging', 'pack_id': 'engineering',
                        'description': 'Investigate a failing test', 'sha256': 'b' * 64}]}]}
        self.loaded = {'text': 'Read evidence before diagnosing a failure.',
                       'skills': self.catalog['packs'][0]['skills'], 'bytes': 43,
                       'sha256': hashlib.sha256(b'Read evidence before diagnosing a failure.').hexdigest(),
                       'manifest_sha256': self.catalog['manifest_sha256']}
        for target, value in [('skill_flow.catalog', self.catalog), ('skill_flow.load_selected', self.loaded)]:
            p = patch(target, return_value=value)
            p.start()
            self.addCleanup(p.stop)

    def plan(self):
        return flow.recommend(self.root, 'openwhispr', 'Investigate a failing test', jev=False)

    def reviewed(self):
        plan = self.plan()
        return flow.review(self.root, 'openwhispr', plan['id'], ['debug'],
                           plan['manifest_sha256'], 'Lead', 'Read the instructions and checked their fit for this task.')

    def test_manual_mode_never_calls_jev(self):
        with patch('skill_flow.evaluate') as call:
            plan = self.plan()
        call.assert_not_called()
        self.assertEqual(plan['recommendation']['status'], 'manual')
        self.assertIsNone(plan['context'])

    def test_low_confidence_keeps_manual_selection_and_does_not_load(self):
        answer = {'status': 'ok', 'answers': {'skills_selection': {
            'type': 'choice', 'choice': 'engineering', 'confidence': .4}}, 'provider_calls': 1}
        with patch('skill_flow.evaluate', return_value=answer) as call:
            plan = flow.recommend(self.root, 'openwhispr', 'Investigate a failing test', jev=True)
        self.assertEqual(call.call_count, 1)
        self.assertEqual(plan['recommendation']['status'], 'low_confidence')
        self.assertIsNone(plan['context'])

    def test_review_and_delivery_are_exact_and_project_scoped(self):
        plan = self.reviewed()
        context = flow.delivery(self.root, 'openwhispr', plan['id'])
        self.assertEqual(context['sha256'], self.loaded['sha256'])
        self.assertFalse(context['execution_requested'])
        self.assertEqual(context['plan_id'], plan['id'])
        with self.assertRaises(ValueError):
            flow.delivery(self.root, 'agent-orchestrator', plan['id'])

    def test_unreviewed_and_stale_plans_cannot_be_delivered(self):
        with self.assertRaises(ValueError):
            flow.delivery(self.root, 'openwhispr', self.plan()['id'])
        plan = self.reviewed()
        with patch('skill_flow.load_selected', side_effect=ValueError('Skill changed')):
            with self.assertRaises(ValueError):
                flow.delivery(self.root, 'openwhispr', plan['id'])

    def test_feedback_requires_accepted_answer_and_matching_delivery(self):
        plan = self.reviewed()
        context = flow.delivery(self.root, 'openwhispr', plan['id'])
        context['execution_requested'] = True
        store = TaskStore(self.root / 'runs/tasks')
        result = store.create(assignment_project_id='openwhispr', task='Diagnose a test', worker='claude')
        result.update(status='awaiting_review', execution_status='succeeded', finalized_at=timestamp(),
                      response='The observed exception was caused by the missing fixture.', skill_context=context)
        store.save(result['job_id'], result)
        with self.assertRaises(ValueError):
            flow.use_evidence(self.root, 'openwhispr', result['job_id'])
        result.update(status='accepted', review_status='accepted', review={
            'reviewer': 'Lead', 'note': 'Reproduced the missing fixture and verified the corrected result.',
            'reviewed_at': timestamp()})
        store.save(result['job_id'], result)
        evidence = flow.use_evidence(self.root, 'openwhispr', result['job_id'])
        with self.assertRaises(ValueError):
            flow.feedback(self.root, 'openwhispr', result['job_id'], 'helped', 'Lead',
                          'The evidence-first procedure identified the missing fixture.', '0' * 64,
                          evidence['context_sha256'])
        with patch('skill_flow._remember_feedback', return_value={'status': 'remembered', 'memory_id': 'example'}):
            saved = flow.feedback(self.root, 'openwhispr', result['job_id'], 'helped', 'Lead',
                                  'The evidence-first procedure identified the missing fixture.',
                                  evidence['source_sha256'], evidence['context_sha256'])
        self.assertEqual(saved['memory_outcome']['status'], 'remembered')
        self.assertEqual(flow.use_evidence(self.root, 'openwhispr', result['job_id'])['feedback']['rating'], 'helped')

    def test_invalid_input_does_not_create_plans(self):
        for task in ('', None, ['task'], 'x' * 4001):
            with self.assertRaises(ValueError):
                flow.recommend(self.root, 'openwhispr', task, jev=False)

    def test_repeated_execute_does_not_start_a_second_worker(self):
        plan = self.reviewed()
        self.addCleanup(lambda: flow._release(plan['id']))
        with patch('skill_flow.Thread') as thread:
            first = flow.execute(self.root, 'openwhispr', plan['id'], 'claude')
            second = flow.execute(self.root, 'openwhispr', plan['id'], 'claude')
        self.assertEqual(first['execution']['status'], 'starting')
        self.assertEqual(second['id'], first['id'])
        self.assertEqual(thread.call_count, 1)

    def test_saved_inflight_execution_is_uncertain_after_restart(self):
        plan = self.reviewed()
        plan['execution'] = {'status': 'running', 'worker': 'claude'}
        flow._save(self.root, plan)
        self.assertEqual(flow.get_plan(self.root, 'openwhispr', plan['id'])['execution']['status'], 'uncertain')

    def test_long_task_keeps_full_prompt_with_bounded_startup_query(self):
        task = ('Investigate a failing test. ' * 40).strip()
        plan = flow.recommend(self.root, 'openwhispr', task, jev=False)
        flow.review(self.root, 'openwhispr', plan['id'], ['debug'], plan['manifest_sha256'],
                    'Lead', 'Read the instructions and checked this longer task.')
        run = self.root / '.orchestration' / 'skill-task-fixture'
        (run / 'drafts').mkdir(parents=True)
        (run / 'run.json').write_text(json.dumps({'objective': task}), encoding='utf-8')
        with patch('skill_flow.Thread'):
            flow.execute(self.root, 'openwhispr', plan['id'], 'codex')
        self.addCleanup(lambda: flow._release(plan['id']))

        def dispatch(command, **kwargs):
            prompt = Path(command[command.index('--prompt-file') + 1]).read_text(encoding='utf-8')
            self.assertIn(task, prompt)
            self.assertEqual(command[command.index('--run') + 1], str(run))
            Path(command[command.index('--output') + 1]).write_text(json.dumps({
                'job_id': 'fixture-job', 'execution_status': 'succeeded',
                'review_status': 'pending', 'response': 'Evidence-first plan.'}), encoding='utf-8')
            return type('Completed', (), {'returncode': 0})()

        with patch('orchestration_lifecycle.start_run', return_value={'run': str(run)}) as startup, \
                patch('subprocess.run', side_effect=dispatch):
            flow._execute(self.root, 'openwhispr', plan['id'], 'codex')
        self.assertEqual(startup.call_args.kwargs['objective'], task)
        self.assertEqual(startup.call_args.kwargs['query'], task[:500])
        self.assertEqual(flow.get_plan(self.root, 'openwhispr', plan['id'])['execution']['status'], 'finished')


if __name__ == '__main__':
    unittest.main()
