"""Standard-library tests for the adaptive Brain retrieval policy."""

import copy
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "app"))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

try:
    from app import brain_retrieval as br
except ImportError:  # running with app/ directly on the path
    import brain_retrieval as br


def row(rid, title, content="", tags=None, episode=None):
    return {"id": rid, "title": title, "content": content,
            "tags": tags, "episode": episode}


class QueryTermsTests(unittest.TestCase):
    def test_fillers_removed_identifiers_kept(self):
        terms = br.query_terms("Please find me the file app/brain_retrieval.py")
        self.assertNotIn("please", terms)
        self.assertNotIn("file", terms)
        self.assertNotIn("the", terms)
        self.assertIn("app/brain_retrieval.py", terms)

    def test_meaningful_nouns_survive(self):
        terms = br.query_terms("where is the quota codex dashboard")
        self.assertEqual(terms, ["quota", "codex", "dashboard"])

    def test_fallback_when_everything_is_a_stop_word(self):
        self.assertEqual(br.query_terms("what is the file"),
                         ["what", "is", "the", "file"])

    def test_dedupe_is_stable_and_capped(self):
        terms = br.query_terms("alpha Alpha beta " + " ".join(
            "tok%d" % i for i in range(40)))
        self.assertEqual(terms[:2], ["alpha", "beta"])
        self.assertEqual(len(terms), br.MAX_TERMS)


class MatchQualityTests(unittest.TestCase):
    def test_exact_full_title(self):
        rows = [row("m1", "Quota Codex Rollout")]
        got = br.match_quality("quota codex rollout", rows)
        self.assertEqual(got["level"], "exact")
        self.assertEqual(got["matched_id"], "m1")

    def test_exact_file_path_literal(self):
        rows = [row("m2", "Viewer notes",
                    "see app/coordinator_viewer.py for details")]
        got = br.match_quality("where is app/coordinator_viewer.py", rows)
        self.assertEqual(got["level"], "exact")
        self.assertEqual(got["matched_id"], "m2")

    def test_exact_quoted_literal(self):
        rows = [row("m3", "Notes", "the run had a root cause of retries")]
        got = br.match_quality('find "root cause"', rows)
        self.assertEqual(got["level"], "exact")

    def test_identifier_substring_does_not_match(self):
        rows = [row("m4", "Helpers", "foobar helper lives here")]
        got = br.match_quality("foo", rows)
        self.assertEqual(got["level"], "none")
        self.assertIsNone(got["matched_id"])
        self.assertEqual(got["coverage"], 0.0)

    def test_strong_requires_all_terms_in_one_row(self):
        rows = [row("a", "Retry budget", "budget only"),
                row("b", "Operational notes", "retry budget tuning notes")]
        got = br.match_quality("retry budget tuning", rows)
        self.assertEqual(got["level"], "strong")
        self.assertEqual(got["matched_id"], "b")

    def test_split_across_rows_is_weak(self):
        rows = [row("a", "Retry", "retry notes"), row("b", "Budget", "budget")]
        got = br.match_quality("retry budget tuning", rows)
        self.assertEqual(got["level"], "weak")
        self.assertTrue(0.0 < got["coverage"] < 1.0)

    def test_paraphrase_without_literal_overlap_is_not_a_lexical_match(self):
        rows = [row("p", "Token accounting", "we track token spend per run")]
        got = br.match_quality("how do we count usage of tokens", rows)
        self.assertEqual(got["level"], "none")
        self.assertEqual(br.match_quality("count token usage", rows)["level"], "weak")

    def test_partial_filename_constraints_do_not_suppress_fallback(self):
        rows = [row("dev", "Development config.json", "Development configuration only")]
        for query in ("Where is production config.json", "Compare config.json and database.ini"):
            quality=br.match_quality(query,rows)
            self.assertEqual(quality['level'],'weak')
            self.assertTrue(br.plan(query,quality=quality)['use_semantic'])

    def test_quoted_substrings_and_capitalized_fillers(self):
        self.assertEqual(br.match_quality('"foo"',[row('x','foobar')])['level'],'none')
        self.assertEqual(br.query_terms('Where is config.json'),['config.json'])
        self.assertEqual(br.match_quality('Where is config.json',[row('x','Notes','C:/app/config.json')])['level'],'exact')

    def test_repeated_common_words_cannot_make_strong(self):
        rows = [row("c", "The file", "the file is the file")]
        got = br.match_quality("where is the file for widgets", rows)
        self.assertEqual(got["level"], "none")

    def test_rows_are_not_mutated(self):
        rows = [row("m5", "Alpha", "beta", tags='["gamma"]',
                    episode='{"note": "delta"}')]
        snapshot = copy.deepcopy(rows)
        br.match_quality("alpha gamma delta", rows)
        self.assertEqual(rows, snapshot)

    def test_malformed_and_null_values_do_not_raise(self):
        rows = [
            row("m6", None, None, tags="{not json", episode="[broken"),
            row("m7", "Fine", "fine", tags={"k": "v"}, episode={"n": None}),
            "not-a-row",
            None,
        ]
        got = br.match_quality("fine", rows)
        self.assertEqual(got["level"], "exact")
        self.assertEqual(br.match_quality("", [])["level"], "none")


class PlanTests(unittest.TestCase):
    def test_relationship_query_requests_one_hop(self):
        got = br.plan("what depends on the quota codex module",
                      quality={"level": "strong"})
        self.assertEqual(got["graph_hops"], 1)
        self.assertFalse(got["use_semantic"])

    def test_lookup_query_skips_graph(self):
        got = br.plan("where is the viewer config file",
                      quality={"level": "exact"})
        self.assertEqual(got["graph_hops"], 0)
        self.assertFalse(got["use_semantic"])
        self.assertEqual(got["requested_strategy"], "auto")

    def test_weak_quality_requests_semantic_only_in_auto(self):
        weak = br.plan("token accounting somewhere", quality={"level": "weak"})
        self.assertTrue(weak["use_semantic"])
        none = br.plan("token accounting", quality={"level": "none"})
        self.assertTrue(none["use_semantic"])
        self.assertFalse(br.plan("x", quality={"level": "exact"})["use_semantic"])
        self.assertFalse(br.plan("x")["use_semantic"])

    def test_explicit_modes(self):
        kw = br.plan("why did it break", strategy="keyword",
                     quality={"level": "none"})
        self.assertEqual((kw["graph_hops"], kw["use_semantic"]), (0, False))
        sem = br.plan("anything", strategy="semantic", quality={"level": "exact"})
        self.assertEqual((sem["graph_hops"], sem["use_semantic"]), (0, True))
        gr = br.plan("where is the file", strategy="graph")
        self.assertEqual((gr["graph_hops"], gr["use_semantic"]), (1, False))
        self.assertEqual(br.plan("q", strategy="graph", hops=2)["graph_hops"], 2)

    def test_hops_clamped_and_overridden_in_auto(self):
        self.assertEqual(br.plan("lookup", hops=9)["graph_hops"], 2)
        self.assertEqual(br.plan("lookup", hops=-4)["graph_hops"], 0)
        self.assertEqual(br.plan("why did it break", hops=0)["graph_hops"], 0)

    def test_invalid_inputs_rejected(self):
        with self.assertRaises(ValueError):
            br.plan("q", strategy="vector")
        with self.assertRaises(TypeError):
            br.plan("q", hops=True)
        with self.assertRaises(TypeError):
            br.plan("q", hops="1")
        with self.assertRaises(TypeError):
            br.plan("q", quality="strong")

    def test_mode_hop_conflicts_rejected(self):
        with self.assertRaises(ValueError):
            br.plan("q", strategy="keyword", hops=1)
        with self.assertRaises(ValueError):
            br.plan("q", strategy="semantic", hops=2)
        self.assertEqual(br.plan("q", strategy="keyword", hops=0)["graph_hops"], 0)

    def test_empty_result_does_no_work(self):
        got = br.plan("what depends on retries", empty=True,
                      quality={"level": "none"}, hops=2)
        self.assertEqual(got["graph_hops"], 0)
        self.assertFalse(got["use_semantic"])
        self.assertIn("empty", got["reason"])

    def test_plan_with_unknown_quality_does_not_invent_a_match(self):
        decision = br.plan("why did the run fail")
        self.assertEqual(decision['graph_hops'],1)
        self.assertFalse(decision['use_semantic'])
        self.assertIn('unknown',decision['reason'])


if __name__ == "__main__":
    unittest.main()
