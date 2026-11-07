"""Tests for app/cli_updates.py."""

import io
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "app"))
from cli_updates import CLIS, accept, main, read_version, status  # noqa: E402


class MockRunner:
    """Mock process runner recording invocations and returning canned responses."""

    def __init__(self, outputs=None, returncode=0, error=None):
        self.outputs = outputs or {}
        self.returncode = returncode
        self.error = error
        self.calls = []

    def __call__(self, cmd, **kwargs):
        self.calls.append({"cmd": list(cmd), "kwargs": kwargs})
        if self.error:
            raise self.error
        exe = str(cmd[0])
        out = "1.0.0\n"
        for k, v in self.outputs.items():
            if k in exe or (len(cmd) > 1 and k in str(cmd[1])):
                out = v
                break
        return subprocess.CompletedProcess(
            args=cmd,
            returncode=self.returncode,
            stdout=out,
            stderr="",
        )


def make_mock_hasher(hashes=None):
    hashes = hashes or {}

    def _hasher(path):
        p_str = str(path)
        for k, v in hashes.items():
            if k in p_str:
                return v
        return f"hash_{Path(path).name}"

    return _hasher


class TestCLIUpdates(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)
        self.runtime_dir = self.root / "runtime"
        self.runtime_dir.mkdir(parents=True, exist_ok=True)
        self.config_dir = self.root / "config"
        self.config_dir.mkdir(parents=True, exist_ok=True)
        self.bin_dir = self.root / "bin"
        self.bin_dir.mkdir(parents=True, exist_ok=True)

        self.fake_paths = {
            "antigravity": self.bin_dir / "agy.exe",
            "grok": self.bin_dir / "grok.exe",
            "claude": self.bin_dir / "claude.exe",
            "codex": self.bin_dir / "codex.exe",
        }
        for p in self.fake_paths.values():
            p.write_text("binary-stub", encoding="utf-8")

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_read_version_success_and_truncation(self):
        long_line = "v2.0.0 " + ("x" * 200)
        runner = MockRunner(outputs={"codex": f"\n\n  {long_line}\nSecond line\n"})
        res = read_version("codex", self.fake_paths["codex"], runner=runner)
        self.assertLessEqual(len(res), 120)
        self.assertTrue(res.startswith("v2.0.0"))
        self.assertNotIn("Second line", res)

    def test_read_version_failure_raises(self):
        runner = MockRunner(returncode=1)
        with self.assertRaises(RuntimeError):
            read_version("codex", self.fake_paths["codex"], runner=runner)

        runner_empty = MockRunner(outputs={"codex": "   \n\n"})
        with self.assertRaises(RuntimeError):
            read_version("codex", self.fake_paths["codex"], runner=runner_empty)

    def test_update_suppression_present_in_every_version_command(self):
        runner = MockRunner()
        for name, p in self.fake_paths.items():
            read_version(name, p, runner=runner)

        calls = {c["cmd"][0]: c for c in runner.calls}

        # antigravity: AGY_CLI_DISABLE_AUTO_UPDATE=true in env
        agy_call = calls[str(self.fake_paths["antigravity"])]
        self.assertEqual(
            agy_call["kwargs"]["env"].get("AGY_CLI_DISABLE_AUTO_UPDATE"), "true"
        )
        self.assertFalse(agy_call["kwargs"]["shell"])
        self.assertEqual(agy_call["kwargs"]["timeout"], 20)

        # claude: DISABLE_AUTOUPDATER=1 in env
        claude_call = calls[str(self.fake_paths["claude"])]
        self.assertEqual(
            claude_call["kwargs"]["env"].get("DISABLE_AUTOUPDATER"), "1"
        )
        self.assertFalse(claude_call["kwargs"]["shell"])
        self.assertEqual(claude_call["kwargs"]["timeout"], 20)

        # grok: --no-auto-update in command args
        grok_call = calls[str(self.fake_paths["grok"])]
        self.assertIn("--no-auto-update", grok_call["cmd"])
        self.assertFalse(grok_call["kwargs"]["shell"])
        self.assertEqual(grok_call["kwargs"]["timeout"], 20)

        # codex: no shell, 20s timeout
        codex_call = calls[str(self.fake_paths["codex"])]
        self.assertFalse(codex_call["kwargs"]["shell"])
        self.assertEqual(codex_call["kwargs"]["timeout"], 20)

    def test_status_all_current(self):
        proof = {
            "cli_version": "1.5.3",
            "executable_sha256": "hash_agy.exe",
            "published_at": "2026-11-07T00:00:00Z",
        }
        (self.config_dir / "antigravity-boundary.json").write_text(
            json.dumps(proof), encoding="utf-8"
        )

        verification = {
            "schema_version": 1,
            "clis": {
                "grok": {"version": "3.0.0", "sha256": "hash_grok.exe"},
                "claude": {"version": "0.7.0", "sha256": "hash_claude.exe"},
                "codex": {"version": "1.0.0", "sha256": "hash_codex.exe"},
            },
        }
        (self.runtime_dir / "cli-verification.json").write_text(
            json.dumps(verification), encoding="utf-8"
        )

        runner = MockRunner(
            outputs={
                "agy": "1.5.3\n",
                "grok": "3.0.0\n",
                "claude": "0.7.0\n",
                "codex": "1.0.0\n",
            }
        )
        hasher = make_mock_hasher()

        rep = status(
            root=self.root, runner=runner, hasher=hasher, paths=self.fake_paths
        )
        self.assertEqual(rep["model_calls"], 0)
        for name, info in rep["clis"].items():
            self.assertEqual(info["state"], "current", f"{name} should be current")
            self.assertTrue(info["installed"])

    def test_status_updated_when_hash_differs(self):
        proof = {
            "cli_version": "1.5.3",
            "executable_sha256": "old_hash_agy",
            "published_at": "2026-11-07T00:00:00Z",
        }
        (self.config_dir / "antigravity-boundary.json").write_text(
            json.dumps(proof), encoding="utf-8"
        )

        verification = {
            "schema_version": 1,
            "clis": {
                "grok": {"version": "3.0.0", "sha256": "old_hash_grok"},
            },
        }
        (self.runtime_dir / "cli-verification.json").write_text(
            json.dumps(verification), encoding="utf-8"
        )

        runner = MockRunner(outputs={"agy": "1.5.4\n", "grok": "3.1.0\n"})
        hasher = make_mock_hasher(
            {"agy.exe": "new_hash_agy", "grok.exe": "new_hash_grok"}
        )

        rep = status(
            root=self.root, runner=runner, hasher=hasher, paths=self.fake_paths
        )
        self.assertEqual(rep["clis"]["antigravity"]["state"], "updated")
        self.assertIn(
            "updates verify antigravity", rep["clis"]["antigravity"]["action"]
        )

        self.assertEqual(rep["clis"]["grok"]["state"], "updated")
        self.assertIn("updates accept grok", rep["clis"]["grok"]["action"])

    def test_status_unverified_when_no_record(self):
        runner = MockRunner()
        hasher = make_mock_hasher()
        rep = status(
            root=self.root, runner=runner, hasher=hasher, paths=self.fake_paths
        )
        self.assertEqual(rep["clis"]["claude"]["state"], "unverified")
        self.assertIn("updates accept claude", rep["clis"]["claude"]["action"])
        self.assertEqual(rep["clis"]["antigravity"]["state"], "unverified")
        self.assertIn(
            "updates verify antigravity", rep["clis"]["antigravity"]["action"]
        )

    def test_status_missing_executable(self):
        missing_paths = dict(self.fake_paths)
        missing_paths["codex"] = self.root / "nonexistent" / "codex.exe"

        runner = MockRunner()
        hasher = make_mock_hasher()
        rep = status(
            root=self.root, runner=runner, hasher=hasher, paths=missing_paths
        )
        self.assertEqual(rep["clis"]["codex"]["state"], "missing")
        self.assertFalse(rep["clis"]["codex"]["installed"])
        self.assertIsNone(rep["clis"]["codex"]["version"])
        self.assertIsNone(rep["clis"]["codex"]["sha256"])

    def test_status_version_command_failure_sets_error_never_crashes(self):
        runner = MockRunner(error=subprocess.SubprocessError("exec timeout"))
        hasher = make_mock_hasher()
        rep = status(
            root=self.root, runner=runner, hasher=hasher, paths=self.fake_paths
        )
        for name in rep["clis"]:
            self.assertEqual(rep["clis"][name]["state"], "error")
            self.assertIsNone(rep["clis"][name]["version"])

    def test_antigravity_reads_authoritative_boundary_proof(self):
        proof = {
            "cli_version": "1.5.3",
            "executable_sha256": "boundary_proof_hash_xyz",
        }
        (self.config_dir / "antigravity-boundary.json").write_text(
            json.dumps(proof), encoding="utf-8"
        )

        verification = {
            "schema_version": 1,
            "clis": {
                "antigravity": {
                    "version": "bogus_version",
                    "sha256": "bogus_hash",
                }
            },
        }
        (self.runtime_dir / "cli-verification.json").write_text(
            json.dumps(verification), encoding="utf-8"
        )

        runner = MockRunner(outputs={"agy": "1.5.3\n"})
        hasher = make_mock_hasher({"agy.exe": "boundary_proof_hash_xyz"})
        rep = status(
            root=self.root, runner=runner, hasher=hasher, paths=self.fake_paths
        )

        agy = rep["clis"]["antigravity"]
        self.assertEqual(agy["verified_version"], "1.5.3")
        self.assertEqual(agy["verified_sha256"], "boundary_proof_hash_xyz")
        self.assertEqual(agy["state"], "current")

    def test_accept_refuses_antigravity(self):
        with self.assertRaises(ValueError) as cm:
            accept("antigravity", "some evidence", root=self.root)
        self.assertIn("boundary verification", str(cm.exception))

    def test_accept_records_others_atomically(self):
        runner = MockRunner(outputs={"grok": "3.2.0\n"})
        hasher = make_mock_hasher({"grok.exe": "hash_grok_320"})

        entry = accept(
            "grok",
            "verified on task T1",
            root=self.root,
            runner=runner,
            hasher=hasher,
            paths=self.fake_paths,
        )
        self.assertEqual(entry["version"], "3.2.0")
        self.assertEqual(entry["sha256"], "hash_grok_320")
        self.assertEqual(entry["evidence"], "verified on task T1")
        self.assertEqual(entry["method"], "manual_accept")

        store_file = self.runtime_dir / "cli-verification.json"
        self.assertTrue(store_file.exists())
        data = json.loads(store_file.read_text(encoding="utf-8"))
        self.assertEqual(data["schema_version"], 1)
        self.assertIn("grok", data["clis"])
        self.assertEqual(data["clis"]["grok"]["version"], "3.2.0")

    def test_cli_json_output_and_exit_codes(self):
        # 1. Status exit code 1 when unverified
        buf = io.StringIO()
        with patch("sys.stdout", buf), patch(
            "cli_updates.CLIS",
            {
                k: dict(v, resolver=lambda p=self.fake_paths[k]: p)
                for k, v in CLIS.items()
            },
        ):
            code = main(["status", "--json", "--root", str(self.root)])
        self.assertEqual(code, 1)
        parsed = json.loads(buf.getvalue())
        self.assertEqual(parsed["model_calls"], 0)
        self.assertIn("clis", parsed)

        # 2. Exit code 0 when all current
        proof = {
            "cli_version": "1.0.0",
            "executable_sha256": "hash_fake",
        }
        (self.config_dir / "antigravity-boundary.json").write_text(
            json.dumps(proof), encoding="utf-8"
        )
        verification = {
            "schema_version": 1,
            "clis": {
                "grok": {"version": "1.0.0", "sha256": "hash_fake"},
                "claude": {"version": "1.0.0", "sha256": "hash_fake"},
                "codex": {"version": "1.0.0", "sha256": "hash_fake"},
            },
        }
        (self.runtime_dir / "cli-verification.json").write_text(
            json.dumps(verification), encoding="utf-8"
        )

        with patch("cli_updates.read_version", return_value="1.0.0"), patch(
            "cli_updates.default_hasher", return_value="hash_fake"
        ), patch(
            "cli_updates.CLIS",
            {
                k: dict(v, resolver=lambda p=self.fake_paths[k]: p)
                for k, v in CLIS.items()
            },
        ):
            code_cur = main(["status", "--root", str(self.root)])
            self.assertEqual(code_cur, 0)

        # 3. Bad inputs give exit code 2
        self.assertEqual(main(["invalid_command"]), 2)
        self.assertEqual(main([]), 2)
        self.assertEqual(
            main(["accept", "grok"]), 2
        )  # missing required --evidence

        # 4. Accept antigravity CLI refusal gives exit code 1
        self.assertEqual(
            main(["accept", "antigravity", "--evidence", "task1"]), 1
        )



class GeminiAdmissionTests(unittest.TestCase):
    DRIFT = "Antigravity executable or permission handler changed; revalidation is required."

    def admission(self, states, verify=None, enabled=True):
        import cli_updates
        states = list(states)
        calls = []

        def boundary():
            return states.pop(0) if len(states) > 1 else states[0]

        def fake_verify():
            calls.append(1)
            return verify() if callable(verify) else (verify or {"status": "published"})
        return cli_updates.gemini_admission_error(verify=fake_verify, boundary=boundary, enabled=enabled), calls

    def test_current_boundary_never_verifies(self):
        self.assertEqual(self.admission([None]), (None, []))

    def test_other_holds_are_returned_unchanged(self):
        result, calls = self.admission(["Antigravity tool-boundary verification is missing or incomplete."])
        self.assertEqual((result, calls), ("Antigravity tool-boundary verification is missing or incomplete.", []))

    def test_changed_binary_is_verified_once_then_admitted(self):
        self.assertEqual(self.admission([self.DRIFT, None]), (None, [1]))

    def test_failed_verification_keeps_gemini_held_with_the_reason(self):
        result, calls = self.admission([self.DRIFT, self.DRIFT], verify={"status": "failed"})
        self.assertEqual(calls, [1])
        self.assertIn("Automatic verification result: failed", result)

    def test_switch_off_only_points_to_the_command(self):
        result, calls = self.admission([self.DRIFT], enabled=False)
        self.assertEqual(calls, [])
        self.assertIn("updates verify antigravity", result)

    def test_verifier_crash_is_reported_not_raised(self):
        def broken():
            raise RuntimeError("probe failed")
        result, calls = self.admission([self.DRIFT], verify=broken)
        self.assertIn("Automatic verification stopped: RuntimeError: probe failed", result)

    def test_switch_reads_workers_policy(self):
        import cli_updates
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "workers.json"
            path.write_text('{"policy": {"auto_verify_cli_updates": false}}', encoding="utf-8")
            self.assertFalse(cli_updates.auto_verify_enabled(path))
            path.write_text('{"policy": {}}', encoding="utf-8")
            self.assertTrue(cli_updates.auto_verify_enabled(path))
            self.assertTrue(cli_updates.auto_verify_enabled(Path(directory) / "missing.json"))


if __name__ == "__main__":
    unittest.main()
