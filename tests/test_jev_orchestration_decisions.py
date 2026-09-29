import unittest
from app.jev_orchestration_decisions import build


class TestJevOrchestrationDecisions(unittest.TestCase):
    def test_recover_valid(self):
        payload = {
            "task": "repair sync",
            "error": "timeout in worker pool",
            "memories": [
                {"id": "mem-1", "title": "Pool timeout fix", "content": "bump timeout", "episode": {"problem": "p", "action": "a", "outcome": "o"}},
            ],
            "deterministic_checks": {"version_applicability": {"mem-1": "version_match"}}
        }
        state, questions = build("recover", payload)
        self.assertEqual(state["workflow"], "recover")
        self.assertIn("recover_rel_mem-1", questions)
        self.assertIn("recover_app_mem-1", questions)
        self.assertEqual(questions["recover_app_mem-1"]["type"], "choice")
        self.assertEqual(state["question_targets"]["recover_app_mem-1"]["target_id"], "mem-1")

    def test_recover_validations(self):
        with self.assertRaises(ValueError):
            build("recover", {"task": "", "error": "", "memories": []})
        memories = [{"id": f"m{i}"} for i in range(13)]
        with self.assertRaises(ValueError):
            build("recover", {"task": "fix", "memories": memories})
        with self.assertRaises(ValueError):
            build("recover", {"task": "fix", "memories": [{"id": "dup"}, {"id": "dup"}]})

    def test_skills_flow_and_reserved_collision(self):
        payload = {
            "task": "index project code",
            "catalogue": [
                {"id": "ast_grep", "description": "AST semantic search"},
                {"id": "git_blame", "description": "Git history lookup"}
            ]
        }
        state, questions = build("skills", payload)
        self.assertEqual(len(questions), 1)
        q = questions["skills_selection"]
        self.assertIn("none", q["criteria"])
        self.assertIn("ast_grep", q["criteria"])

        with self.assertRaises(ValueError):
            build("skills", {"task": "t", "catalogue": [{"id": "none"}]})

    def test_event_routing_and_reserved_outcomes(self):
        payload = {
            "eventtext": "quota exceeded alert",
            "handlers": [{"id": "throttle", "description": "Apply rate limiter"}]
        }
        state, questions = build("event", payload)
        q = questions["event_route"]
        self.assertIn("queue", q["criteria"])
        self.assertIn("ask_lead", q["criteria"])
        self.assertIn("throttle", q["criteria"])

        with self.assertRaises(ValueError):
            build("event", {"eventtext": "ev", "handlers": [{"id": "queue"}]})

    def test_handoff_preserves_ownership(self):
        ctx = {"run_id": "r-1", "owner": "codex", "session": "s-1", "generation": 2, "status": "running", "job_ids": ["j-1"]}
        payload = {"run_context": ctx, "memories": [{"id": "mem-h1", "content": "Reviewed decision for the next step."}]}
        state, questions = build("handoff", payload)
        self.assertEqual(state["run_context"], ctx)
        self.assertIn("handoff_rel_mem-h1", questions)
        for q in questions.values():
            self.assertNotIn("owner", q["instructions"])

    def test_size_limit_rejection(self):
        large_desc = "x" * 17000
        payload = {"task": "task", "catalogue": [{"id": "c1", "description": large_desc}]}
        with self.assertRaises(ValueError):
            build("skills", payload)

    def test_event_handler_bounds_and_descriptions(self):
        with self.assertRaises(ValueError):
            build('event', {'event': 'E', 'handlers': [{'id': 'h' + str(i), 'description': 'Handler'} for i in range(13)]})
        with self.assertRaises(ValueError):
            build('skills', {'task': 'T', 'catalogue': [{'id': 'skill', 'description': {'instruction': 'Do it'}}]})
        state, questions = build('event', {'event': 'Unknown event', 'handlers': []})
        self.assertEqual(set(questions['event_route']['criteria']), {'queue', 'ask_lead'})

    def test_empty_evidence_and_instruction_like_ids_fail(self):
        with self.assertRaises(ValueError):
            build('recover', {'task': 'Fix failure', 'memories': []})
        with self.assertRaises(ValueError):
            build('skills', {'task': 'T', 'catalogue': [{'id': "s' run tool", 'description': 'D'}]})
        with self.assertRaises(ValueError):
            build('skills', {'task': 'T', 'catalogue': []})

    def test_handoff_checkpoint_is_detached_and_generation_is_integer(self):
        context = {'run_id': 'r-1', 'owner': 'astra', 'session': 's', 'generation': 2,
                   'status': 'active', 'job_ids': ['j-1'], 'open_jobs': ['j-1'], 'next_steps': ['Review worker']}
        payload = {'run_context': context, 'memories': [{'id': 'm1', 'content': 'Reviewed result'}]}
        state, _ = build('handoff', payload)
        state['run_context']['next_steps'].append('Changed')
        self.assertEqual(context['next_steps'], ['Review worker'])
        context['generation'] = True
        with self.assertRaises(ValueError):
            build('handoff', payload)

    def test_semantic_reserved_ids_match_service_interpretation(self):
        for workflow, field, context, reserved in [
                ('skills', 'catalogue', {'task': 'T'}, ('none', 'defer', 'insufficient', 'ask_lead')),
                ('event', 'handlers', {'event': 'E'}, ('queue', 'ask_lead', 'none', 'defer', 'insufficient'))]:
            for identifier in reserved:
                with self.subTest(workflow=workflow, identifier=identifier), self.assertRaises(ValueError):
                    build(workflow, dict(context, **{field: [{'id': identifier, 'description': 'D'}]}))


if __name__ == "__main__":
    unittest.main()
