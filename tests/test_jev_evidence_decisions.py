"""Focused tests for app.jev_evidence_decisions builders."""

import json
import unittest

from app.jev_evidence_decisions import (LIMITS, MAX_PAYLOAD_BYTES, MAX_QUESTIONS,
                                        EvidenceDecisionError, build)


def mem(index, content="scheduler retries are capped at three attempts"):
    return {"id": f"mem-{index:02d}", "title": f"Note {index}", "content": content,
            "episode": {"problem": "p"}, "source": {"type": "task", "job_id": "j"}}


def memories(count=3):
    return [mem(i) for i in range(count)]


class SchemaTests(unittest.TestCase):
    def assert_valid(self, questions):
        self.assertTrue(questions)
        self.assertLessEqual(len(questions), MAX_QUESTIONS)
        for question in questions.values():
            self.assertIn(question["type"], ("choice", "score", "noul"))
            self.assertIsInstance(question["instructions"], str)
            self.assertTrue(question["instructions"].strip())
            if question["type"] == "choice":
                self.assertIsInstance(question["criteria"], dict)
                self.assertGreaterEqual(len(question["criteria"]), 2)
                self.assertTrue(all(v.strip() for v in question["criteria"].values()))
            elif question["type"] == "score":
                self.assertIsInstance(question["criteria"], list)
                self.assertEqual(len(question["criteria"]), 2)
            else:
                self.assertNotIn("criteria", question)

    def test_all_workflows_emit_valid_schemas(self):
        cases = {
            "evidence_review": {"query": "retry policy", "answer": "three attempts",
                                 "memories": memories()},
            "instruction_scan": {"memories": memories()},
            "memory_support": {"claim": "retries are capped", "memories": memories()},
            "sufficiency": {"query": "retry policy", "answer": "three attempts", "memories": memories()},
            "sensitivity": {"memories": memories()},
            "citations": {"memories": memories(),
                           "citations": [{"memory_id": "mem-01", "quote": "capped at three",
                                          "claim": "retries are capped"}]},
        }
        for workflow, payload in cases.items():
            with self.subTest(workflow=workflow):
                state, questions = build(workflow, payload)
                self.assert_valid(questions)
                self.assertEqual(state["workflow"], workflow)
                self.assertEqual(len(state["question_targets"]), len(questions))


class EvidenceReviewTests(unittest.TestCase):
    def test_four_independent_judgments_per_memory_with_exact_ids(self):
        payload = {"query": "retry policy", "answer": "three attempts",
                   "memories": memories(6)}
        state, questions = build("evidence_review", payload)
        self.assertEqual(len(questions), 24)
        aspects = [t["aspect"] for t in list(state["question_targets"].values())[:4]]
        self.assertEqual(aspects, ["relevant", "answer_evidence", "conflict_query",
                                   "instruction_signal"])
        self.assertEqual([q["type"] for q in list(questions.values())[:4]],
                         ["score", "choice", "choice", "noul"])
        for target, question in zip(state["question_targets"].values(), questions.values()):
            self.assertIn(f'"{target["memory_id"]}"', question["instructions"])
        # relevance and evidence questions are distinct judgements, not one merged ask
        self.assertNotEqual(list(questions.values())[0]["instructions"], list(questions.values())[1]["instructions"])

    def test_state_drops_source_metadata_retains_relevant_episode(self):
        state, _ = build("evidence_review", {"query": "q", "claim": "c",
                                             "memories": memories(2)})
        for record in state["memories"]:
            self.assertEqual(set(record), {"id", "title", "content", "episode"})

    def test_query_only_evidence_review_has_independent_usable_evidence_judgment(self):
        state, questions = build("evidence_review", {"query": "q", "memories": memories(1)})
        self.assertEqual(len(questions), 4)
        self.assertIn('usable as evidence', list(questions.values())[1]['instructions'])


class SufficiencyAndSupportTests(unittest.TestCase):
    def test_sufficiency_keeps_two_separate_choices(self):
        state, questions = build("sufficiency", {"query": "retry policy", "answer": "three attempts",
                                                 "memories": memories(2)})
        self.assertEqual(len(questions), 2)
        self.assertEqual([t["aspect"] for t in state["question_targets"].values()],
                         ["answer_supported", "sufficient_evidence"])
        self.assertNotEqual(list(questions.values())[0]["instructions"], list(questions.values())[1]["instructions"])
        for question in questions.values():
            self.assertEqual(set(question["criteria"]), {"yes", "no", "insufficient"})

    def test_memory_support_is_one_choice_over_quoted_ids(self):
        state, questions = build("memory_support", {"claim": "retries are capped",
                                                    "memories": memories(3)})
        self.assertEqual(len(questions), 1)
        self.assertEqual(set(list(questions.values())[0]["criteria"]),
                         {"supported", "contradicted", "insufficient"})
        for record in state["memories"]:
            self.assertIn(f'"{record["id"]}"', list(questions.values())[0]["instructions"])


class CitationTests(unittest.TestCase):
    def base(self, citations):
        return {"memories": memories(3), "citations": citations}

    def test_one_choice_per_citation_with_exact_memory_id(self):
        cits = [{"memory_id": "mem-00", "quote": "capped", "claim": "a"},
                {"memory_id": "mem-02", "quote": "three attempts", "claim": "b"}]
        state, questions = build("citations", self.base(cits))
        self.assertEqual(len(questions), 2)
        self.assertIn('"mem-00"', list(questions.values())[0]["instructions"])
        self.assertIn('"mem-02"', list(questions.values())[1]["instructions"])
        self.assertNotIn("three attempts", list(questions.values())[1]["instructions"])
        self.assertEqual(state["citations"][1]["quote"], "three attempts")
        self.assertEqual([t["citation_index"] for t in state["question_targets"].values()], [0, 1])

    def test_rejects_unknown_id_empty_quote_and_duplicates(self):
        bad = [
            [{"memory_id": "mem-99", "quote": "q", "claim": "c"}],
            [{"memory_id": "mem-00", "quote": "   ", "claim": "c"}],
            [{"memory_id": "mem-00", "quote": "q", "claim": "c"},
             {"memory_id": "mem-00", "quote": "q", "claim": "c"}],
            [{"memory_id": "mem-00", "quote": "q"}],
            [],
        ]
        for citations in bad:
            with self.subTest(citations=citations):
                with self.assertRaises(EvidenceDecisionError):
                    build("citations", self.base(citations))


class ValidationTests(unittest.TestCase):
    def test_invalid_workflows_and_payloads(self):
        with self.assertRaises(EvidenceDecisionError):
            build("rank_all", {"memories": memories(1)})
        with self.assertRaises(EvidenceDecisionError):
            build("sensitivity", ["not", "a", "dict"])
        with self.assertRaises(EvidenceDecisionError):
            build("sensitivity", {"memories": memories(1), "approve": True})
        with self.assertRaises(EvidenceDecisionError):
            build("memory_support", {"memories": memories(1)})

    def test_memory_bounds_and_uniqueness(self):
        with self.assertRaises(EvidenceDecisionError):
            build("sensitivity", {"memories": []})
        with self.assertRaises(EvidenceDecisionError):
            build("sensitivity", {"memories": memories(13)})
        dupes = [mem(0), mem(0)]
        with self.assertRaises(EvidenceDecisionError):
            build("sensitivity", {"memories": dupes})
        with self.assertRaises(EvidenceDecisionError):
            build("sensitivity", {"memories": [{"id": "a", "title": "t", "content": "  "}]})
        with self.assertRaises(EvidenceDecisionError):
            build("sensitivity", {"memories": [{"id": 7, "title": "t", "content": "c"}]})

    def test_oversized_strings_are_rejected_not_truncated(self):
        long_content = "x" * (LIMITS["content"] + 1)
        with self.assertRaises(EvidenceDecisionError):
            build("sensitivity", {"memories": [mem(0, long_content)]})
        with self.assertRaises(EvidenceDecisionError):
            build("memory_support", {"claim": "y" * (LIMITS["claim"] + 1),
                                     "memories": memories(1)})
        with self.assertRaises(EvidenceDecisionError):
            build("citations", {"memories": memories(1),
                                "citations": [{"memory_id": "mem-00",
                                               "quote": "z" * (LIMITS["quote"] + 1),
                                               "claim": "c"}]})

    def test_payload_fits_or_is_rejected_at_16kib(self):
        small, questions = build("evidence_review", {"query": "q", "answer": "a",
                                                     "memories": memories(6)})
        encoded = json.dumps({"state": small, "questions": questions},
                             ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        self.assertLessEqual(len(encoded), MAX_PAYLOAD_BYTES)
        big = [mem(i, "c" * LIMITS["content"]) for i in range(6)]
        cits = [{"memory_id": f"mem-{i:02d}", "quote": "q" * LIMITS["quote"],
                 "claim": f"claim {i}"} for i in range(6)]
        with self.assertRaises(EvidenceDecisionError):
            build("citations", {"memories": big, "citations": cits})


if __name__ == "__main__":
    unittest.main()
