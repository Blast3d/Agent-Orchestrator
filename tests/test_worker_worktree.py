"""Tests for disposable worker worktrees and reviewed patches."""

import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "app"))

import worker_worktree as ww

CREATE_NO_WINDOW = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0


def git(cwd, *args, input=None):
    process = subprocess.run(["git", *args], cwd=str(cwd), capture_output=True, input=input,
                             timeout=60, creationflags=CREATE_NO_WINDOW)
    if process.returncode:
        raise RuntimeError(process.stderr.decode("utf-8", "replace"))
    return process.stdout.decode("utf-8", "replace").strip()


@unittest.skipUnless(shutil.which("git"), "git is unavailable")
class WorkerWorktreeTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.base = Path(temporary.name)
        hooks = ww.EMPTY_HOOKS
        ww.EMPTY_HOOKS = self.base / "empty-hooks"
        self.addCleanup(setattr, ww, "EMPTY_HOOKS", hooks)
        self.source = self.base / "source"
        self.source.mkdir()
        git(self.source, "init", "-q")
        for key, value in (("user.email", "t@example.com"), ("user.name", "T"),
                           ("core.autocrlf", "false")):
            git(self.source, "config", key, value)
        (self.source / "README.md").write_text("one\n", encoding="utf-8")
        (self.source / "keep.txt").write_text("keep\n" * 20, encoding="utf-8")
        (self.source / "gone.txt").write_text("gone\n", encoding="utf-8")
        (self.source / ".claude").mkdir()
        (self.source / ".claude" / "notes.txt").write_text("protected\n", encoding="utf-8")
        git(self.source, "add", "-A")
        git(self.source, "commit", "-qm", "first")
        self.old = git(self.source, "rev-parse", "HEAD")
        (self.source / "README.md").write_text("two\n", encoding="utf-8")
        git(self.source, "commit", "-qam", "second")
        self.head = git(self.source, "rev-parse", "HEAD")
        self.marker = self.base / "hook-marker.txt"
        (self.source / ".git" / "hooks" / "post-checkout").write_text(
            f"#!/bin/sh\necho fired > '{self.marker.as_posix()}'\n", encoding="utf-8")

    def create(self, name="wt", ref="HEAD"):
        record = ww.create(self.source, ref, self.base / name)
        self.addCleanup(ww.remove, record)
        return record

    def target(self):
        target = self.base / "target"
        git(self.base, "clone", "-q", str(self.source), str(target))
        return target

    def commit_entry(self, mode, path):
        blob = git(self.source, "hash-object", "-w", "--stdin", input=b"README.md")
        oid = self.head if mode == "160000" else blob
        git(self.source, "update-index", "--add", "--cacheinfo", f"{mode},{oid},{path}")
        git(self.source, "commit", "-qm", f"add {mode}")

    def test_create_at_head_and_older_commit_without_hooks(self):
        record = self.create()
        self.assertEqual(record["base_commit"], self.head)
        self.assertEqual(record["schema_version"], 1)
        self.assertFalse(self.marker.exists())
        older = self.create("wt-old", self.old)
        self.assertEqual((Path(older["path"]) / "README.md").read_text(encoding="utf-8"), "one\n")

    def test_bad_refs_and_existing_destination(self):
        for ref in ("no-such-ref", "-x", "--output=x", ""):
            with self.subTest(ref=ref), self.assertRaises(ww.WorktreeError):
                ww.create(self.source, ref, self.base / "wt-bad")
        (self.base / "exists").mkdir()
        with self.assertRaises(ww.WorktreeError):
            ww.create(self.source, "HEAD", self.base / "exists")
        with self.assertRaises(ww.WorktreeError):
            ww.create(self.base, "HEAD", self.base / "wt-norepo")

    def test_symlinks_and_submodules_in_the_commit_are_refused(self):
        for mode, path in (("120000", "link.txt"), ("160000", "module")):
            with self.subTest(mode=mode):
                self.commit_entry(mode, path)
                with self.assertRaises(ww.WorktreeError):
                    ww.create(self.source, "HEAD", self.base / f"wt-{mode}")
                self.assertFalse((self.base / f"wt-{mode}").exists())
                git(self.source, "reset", "-q", "--hard", self.head)

    def test_collect_reports_every_change(self):
        record = self.create()
        work = Path(record["path"])
        (work / "README.md").write_text("changed\n", encoding="utf-8")
        (work / "added.txt").write_text("new\n", encoding="utf-8")
        (work / "gone.txt").unlink()
        os.replace(work / "keep.txt", work / "renamed.txt")
        (work / "blob.bin").write_bytes(b"\x00\x01\xffdata")
        collected = ww.collect(record, self.base / "out")
        statuses = {item["path"]: item["status"] for item in collected["files"]}
        self.assertEqual(statuses, {"README.md": "M", "added.txt": "A", "gone.txt": "D",
                                    "keep.txt": "D", "renamed.txt": "A", "blob.bin": "A"})
        self.assertEqual(collected["binary_files"], ["blob.bin"])
        self.assertEqual((collected["insertions"], collected["deletions"]), (22, 22))
        self.assertFalse(collected["empty"])
        self.assertEqual(json.loads((self.base / "out" / "collect.json").read_text(encoding="utf-8")),
                         collected)
        self.assertTrue(ww.collect(self.create("wt-empty"), self.base / "out-empty")["empty"])

    def test_protected_paths_flagged_including_moves_out(self):
        record = self.create()
        work = Path(record["path"])
        (work / "AGENTS.md").write_text("hi\n", encoding="utf-8")
        os.replace(work / ".claude" / "notes.txt", work / "notes.txt")
        collected = ww.collect(record, self.base / "out")
        self.assertEqual(sorted(collected["protected_paths"]), [".claude/notes.txt", "AGENTS.md"])

    def test_nested_repository_refused_before_git_runs(self):
        record = self.create()
        nested = Path(record["path"]) / "sub" / ".git"
        nested.mkdir(parents=True)
        (nested / "config").write_text("[core]\n\tfsmonitor = evil\n", encoding="utf-8")
        with self.assertRaises(ww.WorktreeError):
            ww.collect(record, self.base / "out")

    def test_worker_gitattributes_cannot_select_a_filter(self):
        git(self.source, "config", "filter.evil.clean", f"echo fired > '{self.marker.as_posix()}'; cat")
        record = self.create()
        (Path(record["path"]) / ".gitattributes").write_text("* filter=evil\n", encoding="utf-8")
        (Path(record["path"]) / "added.txt").write_text("new\n", encoding="utf-8")
        collected = ww.collect(record, self.base / "out")
        self.assertFalse(self.marker.exists())
        self.assertIn(".gitattributes", collected["protected_paths"])

    def test_apply_check_only_then_real_with_exact_bytes(self):
        git(self.source, "config", "diff.noprefix", "true")
        record = self.create()
        latin = "caf\xe9 na\xefve\r\n".encode("latin-1")
        (Path(record["path"]) / "added.txt").write_bytes(latin)
        (Path(record["path"]) / "README.md").write_text("three\n", encoding="utf-8")
        collected = ww.collect(record, self.base / "out")
        target = self.target()
        checked = ww.apply(record, collected, target, check_only=True)
        self.assertEqual((checked["applied"], checked["checked"]), (False, True))
        self.assertFalse((target / "added.txt").exists())
        self.assertTrue(ww.apply(record, collected, target)["applied"])
        self.assertEqual((target / "added.txt").read_bytes(), latin)
        self.assertEqual(git(target, "diff", "--cached", "--name-only").split(), ["README.md", "added.txt"])
        self.assertEqual(git(target, "rev-parse", "HEAD"), self.head)

    def test_apply_refuses_protected_tampered_and_relabelled_patches(self):
        target = self.target()
        record = self.create()
        (Path(record["path"]) / "AGENTS.md").write_text("no\n", encoding="utf-8")
        collected = ww.collect(record, self.base / "out")
        with self.assertRaises(ww.WorktreeError):
            ww.apply(record, collected, target)
        with self.assertRaises(ww.WorktreeError):
            ww.apply(record, {**collected, "protected_paths": []}, target)
        self.assertFalse((target / "AGENTS.md").exists())

        other = self.create("wt-other")
        (Path(other["path"]) / "added.txt").write_text("x\n", encoding="utf-8")
        tampered = ww.collect(other, self.base / "out-other")
        Path(tampered["patch_path"]).write_text("not a patch\n", encoding="utf-8")
        with self.assertRaises(ww.WorktreeError):
            ww.apply(other, tampered, target)

    def test_review_output_must_be_outside_the_worktree(self):
        record = self.create()
        (Path(record["path"]) / "added.txt").write_text("x\n", encoding="utf-8")
        with self.assertRaises(ww.WorktreeError):
            ww.collect(record, Path(record["path"]) / "review")

    def test_apply_reports_patch_paths_and_refuses_a_relabelled_list(self):
        target = self.target()
        record = self.create()
        (Path(record["path"]) / "added.txt").write_text("x\n", encoding="utf-8")
        collected = ww.collect(record, self.base / "out")
        relabelled = {**collected, "files": [{"path": "notes.txt", "status": "A", "protected": False}]}
        with self.assertRaises(ww.WorktreeError):
            ww.apply(record, relabelled, target, check_only=True)
        self.assertEqual(ww.apply(record, collected, target, check_only=True)["files"], ["added.txt"])

    def test_diverged_target_fails_without_running_a_merge_driver(self):
        target = self.target()
        (target / ".gitattributes").write_text("* merge=evil\n", encoding="utf-8")
        git(target, "config", "merge.evil.driver", f"echo fired > '{self.marker.as_posix()}'")
        (target / "README.md").write_text("target edit\n", encoding="utf-8")
        git(target, "add", "-A")
        git(target, "-c", "user.email=t@example.com", "-c", "user.name=T", "commit", "-qm", "diverge")
        record = self.create()
        (Path(record["path"]) / "README.md").write_text("worker edit\n", encoding="utf-8")
        collected = ww.collect(record, self.base / "out")
        with self.assertRaises(ww.WorktreeError):
            ww.apply(record, collected, target)
        self.assertFalse(self.marker.exists())
        self.assertEqual((target / "README.md").read_text(encoding="utf-8"), "target edit\n")

    def test_remove_twice_and_never_an_unregistered_folder(self):
        record = ww.create(self.source, "HEAD", self.base / "wt")
        self.assertTrue(ww.remove(record)["removed"])
        self.assertTrue(ww.remove(record)["removed"])
        self.assertFalse(Path(record["path"]).exists())
        stranger = self.base / "stranger"
        stranger.mkdir()
        with self.assertRaises(ww.WorktreeError):
            ww.remove({**record, "path": str(stranger)})
        self.assertTrue(stranger.exists())

    def test_a_populated_empty_hooks_folder_stops_git(self):
        ww.EMPTY_HOOKS.mkdir(parents=True, exist_ok=True)
        (ww.EMPTY_HOOKS / "post-checkout").write_text("#!/bin/sh\n", encoding="utf-8")
        with self.assertRaises(ww.WorktreeError):
            ww.create(self.source, "HEAD", self.base / "wt")
        self.assertFalse((self.base / "wt").exists())


if __name__ == "__main__":
    unittest.main()
