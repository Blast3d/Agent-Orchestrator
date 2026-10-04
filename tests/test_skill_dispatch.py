import hashlib
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'app'))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import test_dispatch_worker as base
from brain_store import BrainStore, digest


class SkillDispatchTests(unittest.TestCase):
    setUp = base.DispatchTests.setUp
    run_task = base.DispatchTests.run_task
    canonical = base.DispatchTests.canonical

    def test_reviewed_skills_reach_actual_worker_input(self):
        text = 'Skill instructions: use a reproducible source-backed diagnosis.'
        context = {'plan_id': 'a' * 32, 'project_id': 'openwhispr', 'user_id': 'local',
                   'text': text, 'sha256': hashlib.sha256(text.encode()).hexdigest(),
                   'skills': [{'id': 'debug', 'name': 'Debugging'}], 'execution_requested': False}
        self.args.project = 'openwhispr'
        self.args.assignment_id = 'skill-delivery'
        self.args.no_memory = True
        self.args.skill_plan = 'a' * 32
        with patch('skill_flow.delivery', return_value=context), patch('skill_flow.get_plan', return_value={'review': {'context_sha256': context['sha256']}}):
            result = self.run_task()
        import dispatch_worker
        self.assertIn(text, dispatch_worker.cloud_command.call_args.args[1])
        self.assertTrue(self.canonical(result)['skill_context']['execution_requested'])

    def test_changed_review_prevents_provider_execution(self):
        self.args.project = 'openwhispr'
        self.args.assignment_id = 'skill-stale'
        self.args.no_memory = True
        self.args.skill_plan = 'a' * 32
        with patch('skill_flow.delivery', side_effect=ValueError('Changed skill instructions')), patch('skill_flow.get_plan', return_value={'review': {'context_sha256': 'b' * 64}}):
            result = self.run_task()
        self.assertNotEqual(result['execution_status'], 'succeeded')
        self.invoke.assert_not_called()

    def test_brain_feedback_proof_is_preserved_and_revalidated(self):
        feedback_hash = digest({'rating': 'helped'})
        source = {'type': 'task', 'job_id': 'a' * 32, 'skill_feedback_sha256': feedback_hash}
        proof = {'plain': 'b' * 64, 'reviewed': 'c' * 64, 'skill_feedback_sha256': feedback_hash}
        normalized, binding = BrainStore._task_proof(source, proof)
        self.assertEqual(normalized['skill_feedback_sha256'], feedback_hash)
        self.assertNotEqual(binding, proof['plain'])
        with self.assertRaises(ValueError):
            BrainStore._task_proof(source, dict(proof, skill_feedback_sha256='d' * 64))
