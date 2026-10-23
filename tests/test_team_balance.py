"""Run balance: counts only real run work and always offers the least-used eligible family."""
import contextlib
import io
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'app'))

import team_balance
from provider_policy import legacy_plan, plan


def allow(worker, size):
    return {"allowed": True, "reasons": []}


def deny(worker, size):
    return {"allowed": False, "reasons": ["Unavailable"]}


def ready():
    return None


class TeamBalanceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.run = self.root / ".orchestration" / "test-run"
        self.run.mkdir(parents=True)
        self.tasks = self.root / "runs" / "tasks"
        self.tasks.mkdir(parents=True)
        self.sequence = 0
        self.write(self.run / "coordinator.json", {
            "owner": "fable",
            "session": "test",
            "generation": 1,
            "status": "active",
        })
        self.receipt(plan("fable"))

    def write(self, path, value):
        path.write_text(json.dumps(value), encoding="utf-8")

    def receipt(self, policy):
        self.write(self.run / "startup-context.json", {
            "run_id": self.run.name,
            "provider_policy": policy,
        })

    def task(self, worker="gemini", **changes):
        self.sequence += 1
        job_id = f"{self.sequence:032x}"
        folder = self.tasks / job_id
        folder.mkdir()
        record = {
            "job_id": job_id,
            "worker": worker,
            "run_id": self.run.name,
            "status": "complete",
            "execution_status": "succeeded",
            "review_status": "accepted",
            "size": "small",
        }
        record.update(changes)
        path = folder / "record.json"
        self.write(path, record)
        return path

    def select(self, **kwargs):
        kwargs.setdefault("check", allow)
        kwargs.setdefault("boundary", ready)
        return team_balance.select_worker(self.root, self.run, **kwargs)

    def report(self):
        return team_balance.balance_report(
            self.root, self.run, check=allow, boundary=ready
        )

    def symlink(self, link, target, directory=False):
        try:
            link.symlink_to(target, target_is_directory=directory)
        except (OSError, NotImplementedError) as exc:
            self.skipTest(f"Symlinks unavailable: {exc}")

    def test_empty_then_gemini_then_grok(self):
        self.assertEqual(self.select()["worker"], "gemini")
        self.task("gemini")
        self.assertEqual(self.select()["worker"], "grok")
        self.task("grok")
        self.assertEqual(self.select()["worker"], "codex")

    def test_excluded_records_and_statuses(self):
        self.task(review_status="rejected")
        self.task(execution_status="held")
        self.task(execution_status="failed")
        self.task(synthetic_input=True)
        self.task(count_as_build_worker=False)
        self.task(run_id="another-run")
        self.task(run_id=None)
        self.task(execution_status="pending", review_status="pending")

        tally = team_balance.run_counts(self.tasks, self.run.name)
        metrics = tally["workers"]["gemini"]
        self.assertEqual(metrics["dispatched"], 0)
        self.assertEqual(metrics["accepted"], 0)
        self.assertEqual(metrics["rejected"], 1)
        self.assertEqual(metrics["held"], 1)
        self.assertEqual(metrics["failed"], 1)
        self.assertEqual(tally["records"], 4)
        self.assertEqual(self.select()["worker"], "gemini")

    def test_running_uncertain_and_pending_review_count_as_dispatched(self):
        self.task(execution_status="running", review_status="pending")
        self.task(execution_status="uncertain", review_status="pending")
        self.task(review_status="pending")
        self.task(execution_status="uncertain", review_status="rejected")
        metrics = self.select()["counts"]["gemini"]
        self.assertEqual(metrics["dispatched"], 3)
        self.assertEqual(metrics["accepted"], 0)
        self.assertEqual(metrics["uncertain"], 2)
        self.assertEqual(metrics["rejected"], 1)

    def test_gemini_boundary_reason(self):
        report = self.select(boundary=lambda: "Route not verified")
        self.assertEqual(report["worker"], "grok")
        self.assertEqual(report["ineligible"]["gemini"], "Route not verified")
        self.assertNotIn("gemini", report["eligible"])

    def test_check_and_boundary_exceptions(self):
        def broken(worker, size):
            raise RuntimeError("private details")

        eligible, reasons = team_balance.eligible_workers(
            ["codex", "gemini"], "small", check=broken, boundary=ready
        )
        self.assertEqual(eligible, [])
        self.assertEqual(reasons, {
            "codex": "RuntimeError", "gemini": "RuntimeError"
        })

        def broken_boundary():
            raise LookupError("private details")

        eligible, reasons = team_balance.eligible_workers(
            ["gemini", "grok"], "small",
            check=allow, boundary=broken_boundary,
        )
        self.assertEqual(eligible, ["grok"])
        self.assertEqual(reasons["gemini"], "LookupError")

    def test_nothing_eligible_and_cli_exit_two(self):
        report = self.select(check=deny)
        self.assertIsNone(report["worker"])
        self.assertEqual(report["eligible"], [])
        self.assertIn("Unavailable", team_balance.render(report))

        with patch.object(team_balance, "balance_report", return_value=report):
            output = io.StringIO()
            with contextlib.redirect_stdout(output):
                code = team_balance.main(["--run", str(self.run), "--json"])
        self.assertEqual(code, 2)
        self.assertIsNone(json.loads(output.getvalue())["worker"])

    def test_cli_success(self):
        report = self.report()
        with patch.object(team_balance, "balance_report", return_value=report):
            output = io.StringIO()
            with contextlib.redirect_stdout(output):
                code = team_balance.main(["--run", str(self.run)])
        self.assertEqual(code, 0)
        self.assertIn("Next worker: gemini", output.getvalue())
        self.assertIn("gemini: dispatched=0 accepted=0", output.getvalue())

    def test_legacy_receipt(self):
        policy = legacy_plan("fable")
        self.receipt(policy)
        report = self.report()
        self.assertEqual(report["policy_schema"], 1)
        self.assertIn(report["worker"], policy["cross_family_workers"])
        self.assertEqual(
            set(report["eligible"]), set(policy["cross_family_workers"])
        )
        for field in ("accepted", "spread", "allowed_spread", "balanced", "missing"):
            self.assertNotIn(field, report)

    def test_recorded_policy_errors_and_fallback(self):
        path = self.run / "startup-context.json"
        self.assertEqual(
            team_balance.recorded_policy(self.run, "fable"), plan("fable")
        )
        for contents in ("{", "[]", "{}", " " * (64 * 1024 + 1)):
            with self.subTest(contents_length=len(contents)):
                path.write_text(contents, encoding="utf-8")
                self.assertIsNone(team_balance.recorded_policy(self.run, "fable"))
                self.assertEqual(self.select()["worker"], "gemini")
        path.unlink()
        self.assertIsNone(team_balance.recorded_policy(self.run, "fable"))

    def test_symlinked_receipt_is_rejected(self):
        path = self.run / "startup-context.json"
        target = self.root / "receipt.json"
        path.replace(target)
        self.symlink(path, target)
        self.assertIsNone(team_balance.recorded_policy(self.run, "fable"))

    def test_oversized_and_malformed_records_are_skipped(self):
        self.task().write_bytes(b" " * (512 * 1024 + 1))
        self.task().write_text("{", encoding="utf-8")
        self.task().write_text("[]", encoding="utf-8")
        missing = self.task()
        missing.unlink()
        tally = team_balance.run_counts(self.tasks, self.run.name)
        self.assertEqual(tally["records"], 0)
        self.assertEqual(tally["workers"]["gemini"]["dispatched"], 0)

    def test_symlinked_record_is_skipped(self):
        record = self.task()
        target = self.root / "external-record.json"
        record.replace(target)
        self.symlink(record, target)
        self.assertEqual(
            team_balance.run_counts(self.tasks, self.run.name)["records"], 0
        )

    def test_symlinked_task_folder_is_skipped(self):
        record = self.task()
        folder = record.parent
        target = self.root / "external-task"
        folder.rename(target)
        self.symlink(folder, target, directory=True)
        self.assertEqual(
            team_balance.run_counts(self.tasks, self.run.name)["records"], 0
        )

    def test_scan_limit(self):
        for _ in range(3):
            self.task()
        tally = team_balance.run_counts(self.tasks, self.run.name, limit=2)
        self.assertTrue(tally["scan_truncated"])
        self.assertEqual(tally["records"], 2)
        self.assertFalse(
            team_balance.run_counts(self.tasks, self.run.name, limit=4)[
                "scan_truncated"
            ]
        )
        # Reading exactly every candidate is complete, not truncated.
        self.assertFalse(
            team_balance.run_counts(self.tasks, self.run.name, limit=3)[
                "scan_truncated"
            ]
        )

    def test_records_written_before_the_run_began_are_skipped_unread(self):
        import os, time
        old = self.task("gemini")
        self.task("grok")
        stale = time.time() - 3600
        os.utime(old, (stale, stale))
        tally = team_balance.run_counts(self.tasks, self.run.name, since=time.time() - 60)
        self.assertEqual(tally["records"], 1)
        self.assertEqual(tally["workers"]["grok"]["dispatched"], 1)
        self.assertEqual(tally["workers"]["gemini"]["dispatched"], 0)

    def test_a_stale_receipt_reloads_as_its_own_policy_generation(self):
        # After a lead handoff the receipt names the old lead: a legacy run stays
        # legacy, a current one moves to the new lead's current policy.
        self.receipt(legacy_plan("astra"))
        self.assertEqual(team_balance.recorded_policy(self.run, "fable"), legacy_plan("fable"))
        self.receipt(plan("astra"))
        self.assertEqual(team_balance.recorded_policy(self.run, "fable"), plan("fable"))
        self.write(self.run / "startup-context.json", {"run_id": self.run.name})
        self.assertIsNone(team_balance.recorded_policy(self.run, "fable"))

    def test_balance_three_three_two_then_three_three_one(self):
        for worker, count in (("codex", 3), ("grok", 3), ("gemini", 2)):
            for _ in range(count):
                self.task(worker)
        report = self.report()
        self.assertEqual(sorted(report["accepted"].values()), [2, 3, 3])
        self.assertEqual(report["spread"], 1)
        self.assertEqual(report["allowed_spread"], 1)
        self.assertTrue(report["balanced"])
        self.assertEqual(report["missing"], [])

        last_record = self.tasks / f"{self.sequence:032x}" / "record.json"
        value = json.loads(last_record.read_text(encoding="utf-8"))
        value["review_status"] = "rejected"
        self.write(last_record, value)
        report = self.report()
        self.assertEqual(sorted(report["accepted"].values()), [1, 3, 3])
        self.assertEqual(report["spread"], 2)
        self.assertEqual(report["allowed_spread"], 1)
        self.assertFalse(report["balanced"])

    def test_allowed_spread_rounds_up_and_missing_prevents_balance(self):
        empty = self.report()
        self.assertFalse(empty["balanced"])
        self.assertEqual(
            set(empty["missing"]), set(plan("fable")["required_worker_families"])
        )
        for worker, count in (("codex", 8), ("grok", 8), ("gemini", 6)):
            for _ in range(count):
                self.task(worker)
        report = self.report()
        self.assertEqual(report["allowed_spread"], 2)
        self.assertEqual(report["spread"], 2)
        self.assertTrue(report["balanced"])

    def test_codex_leads_select_claude_as_third_worker(self):
        for owner in ("astra", "sol"):
            with self.subTest(owner=owner):
                self.write(self.run / "coordinator.json", {"owner": owner})
                self.receipt(plan(owner))
                report = self.select()
                self.assertEqual(set(report["eligible"]), {"claude", "grok", "gemini"})
                self.assertNotIn("codex", report["eligible"])


if __name__ == "__main__":
    unittest.main()
