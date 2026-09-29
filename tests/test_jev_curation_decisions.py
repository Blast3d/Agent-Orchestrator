"""Tests for the bounded Jev curation decision builders.

No network, filesystem or model access: every builder under test is pure.
"""

import json
import unittest

from app.jev_curation_decisions import (
    MAX_QUESTIONS,
    MAX_REQUEST_BYTES,
    CurationInputError,
    build,
    request_size_bytes,
)


def memory(memory_id, content="a reviewed statement about the scheduler", title=None, episode=None):
    record = {"id": memory_id, "content": content}
    if title is not None:
        record["title"] = title
    if episode is not None:
        record["episode"] = episode
    return record


A = "72c48b2047f84127930f96be7db4ec20"
B = "3a1ecd37de334038b6b479874bef14f8"
C = "8586edd561f0484ebdcf6483441917f9"


class SharedContractTests(unittest.TestCase):
    def test_unknown_workflow_rejected(self):
        with self.assertRaises(CurationInputError):
            build("merge", {"memories": [memory(A)]})

    def test_non_mapping_payload_rejected(self):
        with self.assertRaises(CurationInputError):
            build("duplicates", [memory(A)])

    def test_missing_memories_rejected(self):
        with self.assertRaises(CurationInputError):
            build("duplicates", {"pairs": [[A, B]]})

    def test_control_characters_rejected(self):
        payload = {
            "memories": [memory(A, content="bad\x07text"), memory(B)],
            "pairs": [[A, B]],
        }
        with self.assertRaises(CurationInputError):
            build("duplicates", payload)

    def test_invalid_memory_id_rejected(self):
        payload = {"memories": [memory("not a valid id"), memory(B)], "pairs": [["not a valid id", B]]}
        with self.assertRaises(CurationInputError):
            build("duplicates", payload)

    def test_over_long_content_rejected_not_truncated(self):
        payload = {"memories": [memory(A, content="x" * 3001), memory(B)], "pairs": [[A, B]]}
        with self.assertRaises(CurationInputError):
            build("duplicates", payload)

    def test_state_is_json_serialisable_and_questions_are_a_keyed_map(self):
        state, questions = build(
            "duplicates", {"memories": [memory(A), memory(B)], "pairs": [[A, B]]}
        )
        self.assertIsInstance(questions, dict)
        json.dumps({"state": state, "questions": questions})
        self.assertLessEqual(request_size_bytes(state, questions), MAX_REQUEST_BYTES)
        self.assertEqual(state["request_bytes"], request_size_bytes(state, questions) )

    def test_question_schemas_use_only_allowed_keys(self):
        for workflow, payload in (
            ("duplicates", {"memories": [memory(A), memory(B)], "pairs": [[A, B]]}),
            (
                "relations",
                {
                    "memories": [memory(A), memory(B)],
                    "pairs": [[A, B]],
                    "allowed_relations": ["supersedes"],
                },
            ),
            (
                "stale",
                {"memories": [memory(A)], "deterministic_checks": {A: ["age_days = 30"]}},
            ),
            ("durability", {"memories": [memory(A)], "claim": "warm retrieval stays near one second"}),
        ):
            state, questions = build(workflow, payload)
            self.assertTrue(questions)
            self.assertLessEqual(len(questions), MAX_QUESTIONS)
            self.assertEqual(len(state["question_index"]), len(questions))
            for index, question in enumerate(questions.values()):
                self.assertIn(question["type"], {"choice", "score", "noul"})
                self.assertTrue(question["instructions"].strip())
                if question["type"] == "choice":
                    self.assertEqual(set(question), {"type", "instructions", "criteria"})
                    self.assertGreaterEqual(len(question["criteria"]), 2)
                    self.assertIsInstance(question["criteria"], dict)
                elif question["type"] == "score":
                    self.assertIsInstance(question["criteria"], list)
                    self.assertEqual(len(question["criteria"]), 2)
                self.assertEqual(state["question_index"][index]["index"], index)


class DuplicatesTests(unittest.TestCase):
    def test_one_question_per_supplied_pair(self):
        payload = {
            "memories": [memory(A), memory(B), memory(C)],
            "pairs": [[A, B], {"first": B, "second": C}],
        }
        state, questions = build("duplicates", payload)
        self.assertEqual(len(questions), 2)
        self.assertEqual(
            [entry["question"] for entry in state["question_index"]],
            ["duplicate_judgement", "duplicate_judgement"],
        )

    def test_criteria_ids_are_exact(self):
        state, questions = build(
            "duplicates", {"memories": [memory(A), memory(B)], "pairs": [[A, B]]}
        )
        self.assertEqual(
            set(list(questions.values())[0]["criteria"]),
            {"duplicate", "related", "distinct", "insufficient"},
        )
        self.assertNotIn("merge", list(questions.values())[0]["criteria"])

    def test_instructions_quote_both_exact_ids(self):
        _state, questions = build(
            "duplicates", {"memories": [memory(A), memory(B)], "pairs": [[A, B]]}
        )
        instructions = list(questions.values())[0]["instructions"]
        self.assertIn('"%s"' % A, instructions)
        self.assertIn('"%s"' % B, instructions)

    def test_state_carries_only_pair_endpoints(self):
        payload = {"memories": [memory(A), memory(B), memory(C)], "pairs": [[A, B]]}
        state, _questions = build("duplicates", payload)
        self.assertEqual([record["id"] for record in state["memories"]], [A, B])

    def test_episode_included_when_supplied(self):
        payload = {
            "memories": [memory(A, episode={"outcome": "all four passed"}), memory(B)],
            "pairs": [[A, B]],
        }
        state, _questions = build("duplicates", payload)
        self.assertEqual(state["memories"][0]["episode"], {"outcome": "all four passed"})
        self.assertNotIn("episode", state["memories"][1])

    def test_reversed_pair_is_a_duplicate_pair(self):
        payload = {"memories": [memory(A), memory(B)], "pairs": [[A, B], [B, A]]}
        with self.assertRaises(CurationInputError):
            build("duplicates", payload)

    def test_self_pair_rejected(self):
        with self.assertRaises(CurationInputError):
            build("duplicates", {"memories": [memory(A)], "pairs": [[A, A]]})

    def test_unknown_endpoint_rejected(self):
        with self.assertRaises(CurationInputError):
            build("duplicates", {"memories": [memory(A)], "pairs": [[A, B]]})

    def test_missing_pairs_rejected_no_pair_generation(self):
        with self.assertRaises(CurationInputError):
            build("duplicates", {"memories": [memory(A), memory(B)]})

    def test_more_than_six_pairs_rejected(self):
        ids = ["mem%02d" % index for index in range(14)]
        payload = {
            "memories": [memory(value) for value in ids],
            "pairs": [[ids[index], ids[index + 1]] for index in range(0, 14, 2)],
        }
        with self.assertRaises(CurationInputError):
            build("duplicates", payload)

    def test_repeated_memory_id_rejected(self):
        with self.assertRaises(CurationInputError):
            build("duplicates", {"memories": [memory(A), memory(A)], "pairs": [[A, A]]})


class RelationsTests(unittest.TestCase):
    def base(self, **extra):
        payload = {
            "memories": [memory(A), memory(B)],
            "pairs": [[A, B]],
            "allowed_relations": ["supersedes", "supports"],
        }
        payload.update(extra)
        return payload

    def test_criteria_are_allowed_relations_plus_none(self):
        _state, questions = build("relations", self.base())
        self.assertEqual(set(list(questions.values())[0]["criteria"]), {"supersedes", "supports", "none"})

    def test_mapping_relations_keep_caller_meanings(self):
        payload = self.base(allowed_relations={"supersedes": "the first replaces the second"})
        state, questions = build("relations", payload)
        self.assertEqual(
            list(questions.values())[0]["criteria"]["supersedes"], "the first replaces the second"
        )
        self.assertIn("supersedes", state["allowed_relations"])

    def test_direction_is_recorded_and_no_edge_is_created(self):
        state, questions = build("relations", self.base())
        self.assertEqual(state["direction"], "first_to_second")
        self.assertEqual(state["question_index"][0]["from"], A)
        self.assertEqual(state["question_index"][0]["to"], B)
        self.assertIn("none", state["edge_creation"])
        self.assertIn('"%s"' % A, list(questions.values())[0]["instructions"])

    def test_reversed_pair_allowed_but_exact_repeat_rejected(self):
        state, questions = build("relations", self.base(pairs=[[A, B], [B, A]]))
        self.assertEqual(len(questions), 2)
        self.assertEqual(state["question_index"][1]["from"], B)
        with self.assertRaises(CurationInputError):
            build("relations", self.base(pairs=[[A, B], [A, B]]))

    def test_missing_or_empty_allowed_relations_rejected(self):
        payload = self.base()
        payload.pop("allowed_relations")
        with self.assertRaises(CurationInputError):
            build("relations", payload)
        with self.assertRaises(CurationInputError):
            build("relations", self.base(allowed_relations=[]))

    def test_none_cannot_be_an_approved_relation(self):
        with self.assertRaises(CurationInputError):
            build("relations", self.base(allowed_relations=["none"]))

    def test_invalid_relation_name_rejected(self):
        with self.assertRaises(CurationInputError):
            build("relations", self.base(allowed_relations=["Supersedes All"]))

    def test_episode_not_carried_for_relations(self):
        payload = self.base(
            memories=[memory(A, episode={"outcome": "passed"}), memory(B)]
        )
        state, _questions = build("relations", payload)
        self.assertNotIn("episode", state["memories"][0])


class StaleTests(unittest.TestCase):
    def test_two_independent_questions_per_memory(self):
        payload = {
            "memories": [memory(A), memory(B)],
            "deterministic_checks": {A: {"age_days": 30, "version": "1.13"}, B: ["age_days = 2"]},
        }
        state, questions = build("stale", payload)
        self.assertEqual(len(questions), 4)
        self.assertEqual(
            [entry["question"] for entry in state["question_index"]],
            ["applicability", "needs_recheck", "applicability", "needs_recheck"],
        )
        self.assertEqual(
            [entry["memory_id"] for entry in state["question_index"]], [A, A, B, B]
        )

    def test_checks_are_normalised_and_bound_to_ids(self):
        payload = {
            "memories": [memory(A)],
            "deterministic_checks": {A: {"age_days": 30}},
        }
        state, _questions = build("stale", payload)
        self.assertEqual(state["deterministic_checks"][A], ["age_days = 30"])

    def test_memory_without_checks_gets_empty_list(self):
        payload = {"memories": [memory(A)], "deterministic_checks": {}}
        state, _questions = build("stale", payload)
        self.assertEqual(state["deterministic_checks"][A], [])

    def test_missing_deterministic_checks_rejected(self):
        with self.assertRaises(CurationInputError):
            build("stale", {"memories": [memory(A)]})

    def test_unknown_check_key_rejected(self):
        with self.assertRaises(CurationInputError):
            build("stale", {"memories": [memory(A)], "deterministic_checks": {B: ["age_days = 1"]}})

    def test_no_deletion_option_and_no_arithmetic_request(self):
        state, questions = build(
            "stale", {"memories": [memory(A)], "deterministic_checks": {A: ["age_days = 30"]}}
        )
        for question in questions.values():
            for option in question["criteria"]:
                self.assertNotIn("delete", option)
                self.assertNotIn("forget", option)
            self.assertIn("do not", question["instructions"].lower())
        self.assertIn("Do not perform arithmetic", state["deterministic_checks_note"])

    def test_instructions_quote_the_exact_memory_id(self):
        _state, questions = build(
            "stale", {"memories": [memory(A)], "deterministic_checks": {A: []}}
        )
        for question in questions.values():
            self.assertIn('"%s"' % A, question["instructions"])

    def test_too_many_memories_rejected(self):
        ids = ["mem%03d" % index for index in range(25)]
        payload = {
            "memories": [memory(value) for value in ids],
            "deterministic_checks": {},
        }
        with self.assertRaises(CurationInputError):
            build("stale", payload)

    def test_oversized_request_rejected_rather_than_truncated(self):
        ids = ["mem%03d" % index for index in range(8)]
        payload = {
            "memories": [memory(value, content="y" * 2000) for value in ids],
            "deterministic_checks": {},
        }
        with self.assertRaises(CurationInputError) as caught:
            build("stale", payload)
        self.assertIn("byte limit", str(caught.exception))


class DurabilityTests(unittest.TestCase):
    def base(self, **extra):
        payload = {
            "memories": [
                memory(A, episode={"outcome": "all four answers passed"}),
                memory(B),
            ],
            "claim": "memory plus Jev falls back to baseline order under low confidence",
        }
        payload.update(extra)
        return payload

    def test_separate_score_and_novelty_questions(self):
        state, questions = build("durability", self.base())
        self.assertEqual(len(questions), 2)
        self.assertEqual(list(questions.values())[0]["type"], "score")
        self.assertEqual(list(questions.values())[1]["type"], "choice")
        self.assertEqual(
            set(list(questions.values())[1]["criteria"]), {"novel", "duplicate", "insufficient"}
        )
        self.assertEqual(
            [entry["question"] for entry in state["question_index"]],
            ["durability_score", "novelty_choice"],
        )

    def test_score_criteria_are_low_then_high(self):
        _state, questions = build("durability", self.base())
        criteria = list(questions.values())[0]["criteria"]
        self.assertEqual(len(criteria), 2)
        self.assertTrue(criteria[0].startswith("low"))
        self.assertTrue(criteria[1].startswith("high"))

    def test_both_questions_quote_every_outcome_id(self):
        _state, questions = build("durability", self.base())
        for question in questions.values():
            self.assertIn('"%s"' % A, question["instructions"])
            self.assertIn('"%s"' % B, question["instructions"])

    def test_claim_is_carried_in_state_not_in_instructions(self):
        state, questions = build("durability", self.base())
        self.assertIn("fallback" in state["claim"] or "falls back" in state["claim"], (True,))
        for question in questions.values():
            self.assertNotIn(state["claim"], question["instructions"])
            self.assertIn("state.claim", question["instructions"])

    def test_missing_or_empty_claim_rejected(self):
        payload = self.base()
        payload.pop("claim")
        with self.assertRaises(CurationInputError):
            build("durability", payload)
        with self.assertRaises(CurationInputError):
            build("durability", self.base(claim="   "))

    def test_episode_outcomes_are_carried(self):
        state, _questions = build("durability", self.base())
        self.assertEqual(state["memories"][0]["episode"]["outcome"], "all four answers passed")


if __name__ == "__main__":
    unittest.main()
