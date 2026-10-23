"""Policy v2: every non-lead family is required and shares accepted worker tasks near-equally."""
import copy
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'app'))
import unittest

from provider_policy import (
    LEGACY_MINIMUM_FAMILIES,
    LEGACY_PREFERRED_FAMILIES,
    MINIMUM_FAMILIES,
    PREFERRED_FAMILIES,
    coverage,
    legacy_plan,
    next_worker,
    plan,
    require_coverage,
    validate_plan,
)


MODELS = {
    "codex": "gpt-5",
    "claude": "claude-sonnet",
    "grok": "grok-4",
    "gemini": "gemini-2.5-pro",
}


def fixture(owner="astra", counts=(1, 1, 1)):
    ledger = {"contributors": [], "activity": [], "work_items": []}
    hosted = {}
    for worker, count in zip(plan(owner)["cross_family_workers"], counts):
        for _ in range(count):
            add_work(ledger, hosted, worker)
    return ledger, hosted


def add_work(ledger, hosted, worker, *, model=None, status="succeeded",
             work_status="accepted", percent=100, weight=1, synthetic=False,
             alias=None):
    job = "job-%s" % len(hosted)
    actor = {"id": "worker:" + worker}
    agent = alias or actor["id"]
    event = {
        "kind": "delegation",
        "task_id": job,
        "agent_id": agent,
        "status": status,
        "actual_models": [model or MODELS.get(worker, "gpt-5")],
        "input_tokens": 10,
        "output_tokens": 10,
    }
    ledger["contributors"].append(actor)
    ledger["activity"].append(copy.deepcopy(event))
    ledger["work_items"].append({
        "id": job,
        "category": "review",
        "status": work_status,
        "weight": weight,
        "allocations": [{"agent_id": agent, "percent": percent}],
    })
    hosted[job] = {
        "actor": actor,
        "event": event,
        "synthetic": synthetic,
    }
    return job


class ProviderPolicyBalanceTests(unittest.TestCase):
    def test_plans(self):
        self.assertEqual((MINIMUM_FAMILIES, PREFERRED_FAMILIES), (3, 4))
        self.assertEqual(
            (LEGACY_MINIMUM_FAMILIES, LEGACY_PREFERRED_FAMILIES), (2, 3)
        )
        for owner, lead, worker, family in (
            ("astra", "OpenAI", "claude", "Anthropic"),
            ("sol", "OpenAI", "claude", "Anthropic"),
            ("fable", "Anthropic", "codex", "OpenAI"),
        ):
            with self.subTest(owner=owner):
                expected = {
                    "schema_version": 2,
                    "minimum_families": 3,
                    "preferred_families": 4,
                    "lead_family": lead,
                    "cross_family_workers": [worker, "grok", "gemini"],
                    "required_worker_families": [family, "xAI", "Google"],
                    "balance": {
                        "measure": "accepted_hosted_tasks",
                        "max_spread_ratio": 0.15,
                        "min_spread": 1,
                    },
                }
                self.assertEqual(plan(owner), expected)
                self.assertEqual(validate_plan(expected, owner), expected)

    def test_receipt_round_trip(self):
        for owner in ("astra", "sol", "fable"):
            for factory in (legacy_plan, plan):
                receipt = factory(owner)
                receipt["cross_family_workers"].reverse()
                if receipt["schema_version"] == 2:
                    receipt["required_worker_families"].reverse()
                before = json.dumps(receipt, sort_keys=True)
                self.assertEqual(
                    json.dumps(validate_plan(receipt, owner), sort_keys=True),
                    before,
                )
                self.assertEqual(json.dumps(receipt, sort_keys=True), before)

    def test_legacy_coverage(self):
        ledger, hosted = fixture(counts=(1, 1, 0))
        expected = {
            "schema_version": 1,
            "minimum_families": 2,
            "preferred_families": 3,
            "lead_family": "OpenAI",
            "families": ["Anthropic", "xAI"],
            "family_count": 2,
            "minimum_met": True,
            "preferred_met": False,
            "warnings": [
                "Prefer three contributing AI families; observed 2. "
                "The hard minimum remains two."
            ],
        }
        self.assertEqual(
            require_coverage("astra", ledger, hosted, policy=legacy_plan("astra")),
            expected,
        )
        self.assertFalse(coverage("astra", ledger, hosted)["minimum_met"])
        ledger, hosted = fixture(counts=(1, 0, 0))
        with self.assertRaisesRegex(ValueError, "required 2"):
            require_coverage("astra", ledger, hosted, policy=legacy_plan("astra"))

    def test_legacy_subset_remains_valid(self):
        receipt = legacy_plan("astra")
        receipt["cross_family_workers"] = ["gemini"]
        self.assertEqual(validate_plan(receipt, "astra"), receipt)

    def test_spoofed_plans_rejected(self):
        base = plan("astra")
        invalid = []
        for key, value in (
            ("minimum_families", 2),
            ("preferred_families", 3),
            ("schema_version", 1),
            ("schema_version", True),
            ("lead_family", "Anthropic"),
            ("required_worker_families", ["Google", "xAI", "Anthropic"]),
            ("cross_family_workers", ["codex", "grok", "gemini"]),
            ("cross_family_workers", ["claude", "grok"]),
            ("cross_family_workers", ["claude", "grok", "grok"]),
        ):
            receipt = copy.deepcopy(base)
            receipt[key] = value
            invalid.append(receipt)
        for key in base:
            receipt = copy.deepcopy(base)
            del receipt[key]
            invalid.append(receipt)
        for key, value in (
            ("max_spread_ratio", 0.20),
            ("min_spread", True),
            ("measure", "allocation_percent"),
        ):
            receipt = copy.deepcopy(base)
            receipt["balance"][key] = value
            invalid.append(receipt)
        receipt = copy.deepcopy(base)
        receipt["extra"] = True
        invalid.append(receipt)
        receipt = copy.deepcopy(base)
        receipt["balance"]["extra"] = True
        invalid.append(receipt)
        for receipt in invalid:
            with self.subTest(receipt=receipt):
                with self.assertRaises(ValueError):
                    validate_plan(receipt, "astra")

    def test_missing_family(self):
        ledger, hosted = fixture(counts=(2, 1, 0))
        result = coverage("astra", ledger, hosted)
        self.assertEqual(
            result["worker_task_counts"], {"Anthropic": 2, "xAI": 1, "Google": 0}
        )
        self.assertEqual(result["missing_worker_families"], ["Google"])
        with self.assertRaisesRegex(
            ValueError, r"assign accepted work to gemini \(Google\)"
        ):
            require_coverage("astra", ledger, hosted)

    def test_all_missing_families_named(self):
        ledger, hosted = fixture(counts=(0, 0, 0))
        with self.assertRaises(ValueError) as caught:
            require_coverage("astra", ledger, hosted)
        for worker, family in zip(
            plan("astra")["cross_family_workers"],
            plan("astra")["required_worker_families"],
        ):
            self.assertIn(
                "assign accepted work to %s (%s)" % (worker, family),
                str(caught.exception),
            )

    def test_balanced_counts(self):
        for owner in ("astra", "sol", "fable"):
            for counts, spread, allowed in (
                ((1, 1, 1), 0, 1),
                ((2, 2, 1), 1, 1),
                ((10, 10, 8), 2, 2),
            ):
                with self.subTest(owner=owner, counts=counts):
                    ledger, hosted = fixture(owner, counts)
                    result = require_coverage(owner, ledger, hosted)
                    expected = dict(zip(plan(owner)["required_worker_families"], counts))
                    self.assertEqual(result["worker_task_counts"], expected)
                    self.assertEqual(result["balance"], {
                        "counts": expected,
                        "spread": spread,
                        "allowed_spread": allowed,
                        "balanced": True,
                    })
                    self.assertTrue(result["minimum_met"])
                    self.assertFalse(result["preferred_met"])

    def test_unbalanced(self):
        ledger, hosted = fixture(counts=(10, 10, 7))
        result = coverage("astra", ledger, hosted)
        self.assertTrue(result["minimum_met"])
        self.assertFalse(result["balance"]["balanced"])
        self.assertEqual(result["balance"]["spread"], 3)
        self.assertEqual(result["balance"]["allowed_spread"], 2)
        with self.assertRaises(ValueError) as caught:
            require_coverage("astra", ledger, hosted)
        self.assertIn(
            "{'Anthropic': 10, 'xAI': 10, 'Google': 7}", str(caught.exception)
        )
        self.assertIn("gemini (Google)", str(caught.exception))

    def test_invalid_work_never_counts(self):
        for options in (
            {"synthetic": True},
            {"work_status": "rejected"},
            {"status": "failed"},
            {"percent": 0},
            {"percent": True},
            {"weight": 0},
            {"weight": True},
            {"model": "gpt-5"},
        ):
            with self.subTest(options=options):
                ledger, hosted = fixture(counts=(1, 1, 0))
                add_work(ledger, hosted, "gemini", **options)
                result = coverage("astra", ledger, hosted)
                self.assertEqual(result["worker_task_counts"]["Google"], 0)
        ledger, hosted = fixture(counts=(0, 0, 0))
        add_work(ledger, hosted, "jev", model="gemini-2.5-pro")
        self.assertEqual(coverage("astra", ledger, hosted)["family_count"], 0)

    def test_memory_curation_and_forged_delegation(self):
        for mutation in ("memory", "tokens", "allocation"):
            ledger, hosted = fixture(counts=(1, 1, 0))
            add_work(ledger, hosted, "gemini")
            if mutation == "memory":
                ledger["work_items"][-1]["category"] = "memory-curation"
            elif mutation == "tokens":
                ledger["activity"][-1]["input_tokens"] += 1
            else:
                ledger["work_items"][-1]["allocations"][0]["agent_id"] = "forged"
            self.assertEqual(
                coverage("astra", ledger, hosted)["worker_task_counts"]["Google"], 0
            )

    def test_duplicate_jobs_count_once_and_aliases_count(self):
        ledger, hosted = fixture(counts=(1, 1, 0))
        add_work(ledger, hosted, "gemini", alias="reviewer:google")
        ledger["work_items"].append(copy.deepcopy(ledger["work_items"][-1]))
        self.assertEqual(
            require_coverage("astra", ledger, hosted)["worker_task_counts"],
            {"Anthropic": 1, "xAI": 1, "Google": 1},
        )

    def test_copilot_uses_actual_family(self):
        for model, expected in (
            ("gemini-2.5-pro", 1),
            ("gpt-5", 0),
            ("unknown", 0),
        ):
            ledger, hosted = fixture(counts=(1, 1, 0))
            add_work(ledger, hosted, "vscode-copilot", model=model)
            self.assertEqual(
                coverage("astra", ledger, hosted)["worker_task_counts"]["Google"],
                expected,
            )

    def test_lead_work_is_excluded_from_balance(self):
        ledger, hosted = fixture()
        for _ in range(12):
            add_work(ledger, hosted, "codex")
        result = require_coverage("astra", ledger, hosted)
        self.assertTrue(result["preferred_met"])
        self.assertEqual(
            result["worker_task_counts"], {"Anthropic": 1, "xAI": 1, "Google": 1}
        )

    def test_native_lead_requires_capture(self):
        ledger, hosted = fixture()
        ledger["contributors"].append({"id": "native:lead", "provider": "OpenAI"})
        ledger["work_items"].append({
            "id": "native:review",
            "status": "accepted",
            "category": "review",
            "weight": 1,
            "allocations": [{"agent_id": "native:lead", "percent": 100}],
        })
        self.assertFalse(coverage("astra", ledger, hosted)["preferred_met"])
        self.assertTrue(
            coverage("astra", ledger, hosted, native_capture=True)["preferred_met"]
        )

    def test_native_foreign_family_cannot_replace_hosted_work(self):
        ledger, hosted = fixture(counts=(1, 1, 0))
        ledger["contributors"].append({"id": "native:foreign", "provider": "Google"})
        ledger["work_items"].append({
            "id": "native:foreign-review",
            "status": "accepted",
            "category": "review",
            "weight": 1,
            "allocations": [{"agent_id": "native:foreign", "percent": 100}],
        })
        self.assertFalse(
            coverage("astra", ledger, hosted, native_capture=True)["minimum_met"]
        )

    def test_next_worker(self):
        for owner, first_worker, lead_worker in (
            ("astra", "claude", "codex"),
            ("sol", "claude", "codex"),
            ("fable", "codex", "claude"),
        ):
            with self.subTest(owner=owner):
                self.assertEqual(next_worker(owner, {}), "gemini")
                self.assertEqual(next_worker(owner, {"gemini": 1}), "grok")
                self.assertEqual(
                    next_worker(owner, {"gemini": 1, "grok": 1}), first_worker
                )
                self.assertEqual(
                    next_worker(owner, {}, eligible=[first_worker, "grok"]), "grok"
                )
                self.assertEqual(
                    next_worker(owner, {}, eligible=[first_worker]), first_worker
                )
                self.assertIsNone(next_worker(owner, {}, eligible=[lead_worker]))
                self.assertIsNone(next_worker(owner, {}, eligible=[]))
                self.assertIsNone(next_worker(owner, {}, eligible=["jev"]))
                policy = plan(owner)
                policy["cross_family_workers"].reverse()
                policy["required_worker_families"].reverse()
                self.assertEqual(next_worker(owner, {}, policy=policy), "gemini")

    def test_policy_for_keeps_a_runs_policy_generation(self):
        from provider_policy import policy_for
        for owner in ("astra", "sol", "fable"):
            with self.subTest(owner=owner):
                self.assertEqual(policy_for(owner), plan(owner))
                self.assertEqual(policy_for(owner, plan("astra")), plan(owner))
                self.assertEqual(policy_for(owner, legacy_plan("astra")), legacy_plan(owner))
                self.assertEqual(policy_for(owner, {"schema_version": "1"}), plan(owner))

    def test_next_worker_legacy_subset(self):
        policy = legacy_plan("astra")
        policy["cross_family_workers"] = ["claude", "grok"]
        self.assertEqual(next_worker("astra", {}, policy=policy), "grok")


if __name__ == "__main__":
    unittest.main()
