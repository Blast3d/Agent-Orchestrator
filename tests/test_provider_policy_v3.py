"""Provider policy schema 3: mix-weighted participation."""
import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "app"))

import provider_policy as pp  # noqa: E402
from team_planner import allocate  # noqa: E402


MIX = {
    "schema_version": 1,
    "shares": {"OpenAI": 30, "Anthropic": 40, "xAI": 15, "Google": 15},
    "tolerance": 10,
    "tiers": {"OpenAI": "best", "Anthropic": "best", "xAI": "best", "Google": "best"},
    "preset": "code",
    "updated_at": "2026-11-07T20:00:00+00:00",
    "source": "viewer",
}


def _item(job, weight, allocations, status="accepted", category="implementation"):
    return {
        "id": job,
        "status": status,
        "category": category,
        "weight": weight,
        "allocations": allocations,
    }


def _hosted(job, worker, models=()):
    return {
        "synthetic": False,
        "actor": {"id": "worker:%s" % worker},
        "event": {
            "kind": "delegation",
            "status": "succeeded",
            "task_id": job,
            "actual_models": list(models),
            "input_tokens": 1,
            "output_tokens": 1,
        },
    }


def _activity(job, agent, models=()):
    return {
        "kind": "delegation",
        "task_id": job,
        "agent_id": agent,
        "status": "succeeded",
        "actual_models": list(models),
        "input_tokens": 1,
        "output_tokens": 1,
    }


def _ledger(items, activity, contributors=None):
    return {
        "work_items": items,
        "activity": activity,
        "contributors": contributors or [],
    }


def _worker_job(job, worker, agent, models=(), weight=10):
    item = _item(job, weight, [{"agent_id": agent, "percent": 100, "evidence": "run"}])
    hosted = {job: _hosted(job, worker, models)}
    activity = [_activity(job, agent, models)]
    return item, hosted, activity


class PlanValidationTests(unittest.TestCase):
    def test_v3_plan_shape(self):
        plan = pp.plan("fable")
        self.assertEqual(plan["schema_version"], 3)
        self.assertNotIn("balance", plan)
        self.assertEqual(
            plan["participation"],
            {"measure": "accepted_contribution_share", "mix_file": "team-mix.json"},
        )
        self.assertEqual(plan["required_worker_families"], ["OpenAI", "xAI", "Google"])
        self.assertEqual(pp.validate_plan(plan, "fable"), plan)

    def test_rejects_wrong_schema_and_fields(self):
        with self.assertRaises(ValueError):
            pp.validate_plan({**pp.plan("fable"), "schema_version": 4}, "fable")
        extra = dict(pp.plan("fable"))
        extra["balance"] = {"measure": "accepted_hosted_tasks"}
        with self.assertRaises(ValueError):
            pp.validate_plan(extra, "fable")
        missing = dict(pp.plan("fable"))
        missing.pop("participation")
        with self.assertRaises(ValueError):
            pp.validate_plan(missing, "fable")
        bad_block = dict(pp.plan("fable"))
        bad_block["participation"] = {"measure": "accepted_hosted_tasks", "mix_file": "team-mix.json"}
        with self.assertRaises(ValueError):
            pp.validate_plan(bad_block, "fable")
        extra_part = dict(pp.plan("fable"))
        extra_part["participation"] = dict(extra_part["participation"], extra=1)
        with self.assertRaises(ValueError):
            pp.validate_plan(extra_part, "fable")

    def test_v1_v2_receipts_and_policy_for(self):
        v1 = pp.legacy_plan("fable")
        v2 = pp.plan_v2("fable")
        self.assertEqual(pp.validate_plan(v1, "fable"), v1)
        self.assertEqual(pp.validate_plan(v2, "fable"), v2)
        self.assertEqual(pp.policy_for("fable", v1)["schema_version"], 1)
        self.assertEqual(pp.policy_for("fable", v2)["schema_version"], 2)
        self.assertEqual(pp.policy_for("fable")["schema_version"], 3)
        self.assertEqual(pp.policy_for("fable", {"schema_version": 3})["schema_version"], 3)


class CoverageV3Tests(unittest.TestCase):
    def setUp(self):
        self.policy = pp.plan("fable")
        jobs = []
        hosted = {}
        activity = []
        specs = [
            ("j1", "codex", "agent-codex", ()),
            ("j2", "grok", "agent-grok", ("grok-4.6-build",)),
            ("j3", "gemini", "agent-gemini", ("gemini-3.8-flash-high",)),
        ]
        for job, worker, agent, models in specs:
            item, rec, act = _worker_job(job, worker, agent, models, weight=10)
            jobs.append(item)
            hosted.update(rec)
            activity.extend(act)
        lead = _item(
            "j-lead",
            40,
            [{"agent_id": "lead-claude", "percent": 100, "evidence": "native"}],
        )
        jobs.append(lead)
        self.contributors = [
            {"id": "lead-claude", "name": "fable", "provider": "Anthropic"},
            {"id": "agent-codex", "name": "codex"},
            {"id": "agent-grok", "name": "grok"},
            {"id": "agent-gemini", "name": "gemini"},
        ]
        self.ledger = _ledger(jobs, activity, self.contributors)
        self.hosted = hosted

    def test_requires_mix(self):
        with self.assertRaises(ValueError):
            pp.coverage("fable", self.ledger, self.hosted, policy=self.policy)

    def test_met_with_native_lead(self):
        # 40 of 70 accepted weight is the native lead (57.1%); each worker has 10 (14.3%).
        mix = dict(MIX, preset=None, shares={"OpenAI": 15, "Anthropic": 55, "xAI": 15, "Google": 15})
        result = pp.coverage(
            "fable", self.ledger, self.hosted,
            native_capture=True, policy=self.policy, mix=mix,
        )
        self.assertTrue(result["minimum_met"])
        self.assertTrue(result["preferred_met"])
        self.assertTrue(result["participation"]["met"])
        self.assertEqual(result["warnings"], [])
        coded = pp.coverage("fable", self.ledger, self.hosted,
                            native_capture=True, policy=self.policy, mix=MIX)
        self.assertFalse(coded["participation"]["met"])
        self.assertIn("delegate more", " ".join(coded["warnings"]))
        self.assertEqual(result["worker_task_counts"]["OpenAI"], 1)
        self.assertEqual(result["participation"]["actual"]["Anthropic"], 57.1)
        self.assertEqual(result["participation"]["actual"]["OpenAI"], 14.3)
        self.assertEqual(result["participation"]["unverified_pct"], 0.0)

    def test_lead_over(self):
        mix = dict(MIX)
        mix["shares"] = {"OpenAI": 30, "Anthropic": 10, "xAI": 30, "Google": 30}
        mix["tolerance"] = 5
        result = pp.coverage(
            "fable", self.ledger, self.hosted,
            native_capture=True, policy=self.policy, mix=mix,
        )
        self.assertFalse(result["participation"]["met"])
        self.assertTrue(any("delegate more" in item for item in result["warnings"]))
        with self.assertRaises(ValueError):
            pp.require_coverage(
                "fable", self.ledger, self.hosted,
                native_capture=True, policy=self.policy, mix=mix,
            )

    def test_family_under(self):
        mix = dict(MIX)
        mix["shares"] = {"OpenAI": 50, "Anthropic": 20, "xAI": 15, "Google": 15}
        mix["tolerance"] = 5
        result = pp.coverage(
            "fable", self.ledger, self.hosted,
            native_capture=True, policy=self.policy, mix=mix,
        )
        self.assertFalse(result["participation"]["within"]["OpenAI"])
        self.assertTrue(any("assign more accepted work" in item and "OpenAI" in item for item in result["warnings"]))

    def test_family_over(self):
        mix = dict(MIX)
        mix["shares"] = {"OpenAI": 5, "Anthropic": 40, "xAI": 30, "Google": 25}
        mix["tolerance"] = 2
        result = pp.coverage(
            "fable", self.ledger, self.hosted,
            native_capture=True, policy=self.policy, mix=mix,
        )
        self.assertFalse(result["participation"]["within"]["OpenAI"])
        self.assertTrue(any("OpenAI actual share" in item and "exceeds target" in item for item in result["warnings"]))

    def test_missing_family(self):
        ledger = _ledger(self.ledger["work_items"][:2] + [self.ledger["work_items"][-1]],
                         self.ledger["activity"][:2], self.contributors)
        hosted = {key: self.hosted[key] for key in ("j1", "j2")}
        result = pp.coverage(
            "fable", ledger, hosted,
            native_capture=True, policy=self.policy, mix=MIX,
        )
        self.assertIn("Google", result["missing_worker_families"])
        self.assertFalse(result["minimum_met"])
        self.assertFalse(result["participation"]["met"])

    def test_unverified_weight(self):
        item = _item("j-u", 20, [{"agent_id": "stranger", "percent": 100, "evidence": "x"}])
        ledger = _ledger(self.ledger["work_items"] + [item], self.ledger["activity"], self.contributors)
        result = pp.coverage(
            "fable", ledger, self.hosted,
            native_capture=True, policy=self.policy, mix=MIX,
        )
        self.assertGreater(result["participation"]["unverified_pct"], 0)
        self.assertEqual(result["participation"]["unverified_pct"], 22.2)

    def test_zero_weight(self):
        result = pp.coverage(
            "fable", _ledger([], []), self.hosted,
            policy=self.policy, mix=MIX,
        )
        self.assertFalse(result["participation"]["met"])
        self.assertEqual(result["participation"]["total_weight"], 0.0)
        self.assertTrue(any("no accepted contribution weight" in item for item in result["warnings"]))

    def test_v1_v2_paths_unchanged_without_mix(self):
        v2 = pp.plan_v2("fable")
        v2_result = pp.coverage("fable", self.ledger, self.hosted, policy=v2)
        self.assertEqual(v2_result["schema_version"], 2)
        self.assertIn("balance", v2_result)
        self.assertNotIn("participation", v2_result)
        v1 = pp.legacy_plan("fable")
        v1_result = pp.coverage("fable", self.ledger, self.hosted, policy=v1)
        self.assertEqual(v1_result["schema_version"], 1)
        self.assertNotIn("worker_task_counts", v1_result)


class NextWorkerAllocateTests(unittest.TestCase):
    def test_weighted_deficit_and_ties(self):
        policy = pp.plan("fable")
        mix = MIX
        counts = {"codex": 0, "grok": 0, "gemini": 0}
        # Equal counts: largest target among required workers is OpenAI/codex (30 vs 15).
        self.assertEqual(pp.next_worker("fable", counts, policy=policy, mix=mix), "codex")
        # Tie on deficit: rotation gemini before grok when their targets match and counts match.
        even = {"shares": {"OpenAI": 20, "Anthropic": 40, "xAI": 20, "Google": 20}, "tolerance": 10}
        self.assertEqual(
            pp.next_worker("fable", {"codex": 1, "grok": 0, "gemini": 0}, policy=policy, mix=even),
            "gemini",
        )
        # Ineligible gemini skips to next deficit.
        self.assertEqual(
            pp.next_worker(
                "fable",
                {"codex": 3, "grok": 0, "gemini": 0},
                eligible=["grok", "codex"],
                policy=policy,
                mix=mix,
            ),
            "grok",
        )
        # Without mix, least-count with gemini-first tie break.
        self.assertEqual(
            pp.next_worker("fable", {"codex": 1, "grok": 1, "gemini": 1}, policy=policy),
            "gemini",
        )

    def test_allocate_sums_to_total(self):
        workers = ("gemini", "grok", "codex")
        shares = MIX["shares"]
        for total in range(1, 20):
            result = allocate(total, workers, shares)
            self.assertEqual(sum(result.values()), total)
            self.assertEqual(set(result), set(workers))
        equal = allocate(5, workers)
        self.assertEqual(sum(equal.values()), 5)


if __name__ == "__main__":
    unittest.main()
