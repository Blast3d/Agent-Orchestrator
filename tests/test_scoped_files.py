"""Tests for the worker file scope."""

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "app"))

from scoped_files import Limits, Scope, ScopeError


class ScopedFilesTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.base = Path(temporary.name)
        self.root = self.base / "assignment"
        self.root.mkdir()
        self.scope = Scope(self.root)

    def test_traversal_and_outside_paths(self):
        for path in ("../escape", "sub/../file", str(self.base / "outside")):
            with self.subTest(path=path):
                with self.assertRaises(ScopeError):
                    self.scope.resolve(path)
        for path in ("Z:\\outside\\file", "\\\\server\\share\\file",
                     "\\\\?\\C:\\file", "\\\\.\\C:\\file"):
            with self.subTest(path=path):
                with self.assertRaises(ScopeError):
                    self.scope.resolve(path)
        if os.name == "nt":
            with self.assertRaises(ScopeError):
                self.scope.resolve("\\rooted")
        else:
            with self.assertRaises(ScopeError):
                self.scope.resolve("/outside")

    def test_absolute_inside_and_relative(self):
        file = self.root / "sub" / "file.txt"
        file.parent.mkdir()
        file.write_text("hello", encoding="utf-8")
        self.assertEqual(self.scope.resolve(str(file)), file)
        self.assertEqual(self.scope.relative(file), "sub/file.txt")
        self.assertEqual(self.scope.check_path("sub\\file.txt"), "sub/file.txt")

    def test_invalid_components(self):
        for path in (
            "a.txt:x", "nul", "COM1.txt", "LPT¹", "nul .txt", "conin$.log", "name.",
            "name ", "ABCDEF~1.TXT", "a?b", "a*b", "a\nb",
        ):
            with self.subTest(path=path):
                with self.assertRaises(ScopeError):
                    self.scope.resolve(path)

    def test_git_denied_for_reads_and_writes(self):
        for path in (".git/config", "sub/.GIT/config"):
            with self.subTest(path=path):
                for write in (False, True):
                    with self.assertRaises(ScopeError):
                        self.scope.resolve(path, for_write=write)

    def test_write_protected_names_and_directories_are_readable(self):
        dirs = (
            ".agents", ".claude", ".codex", ".grok", ".gemini",
            ".cursor", ".vscode", ".idea", ".github",
        )
        files = (
            ".mcp.json", ".gitmodules", ".gitattributes", "AGENTS.md",
            "CLAUDE.md", "CLAUDE.local.md", "GEMINI.md",
            ".cursorrules", ".windsurfrules",
        )
        for directory in dirs:
            target = self.root / "nested" / directory.upper() / "file.txt"
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text("readable", encoding="utf-8")
            rel = target.relative_to(self.root).as_posix()
            with self.subTest(path=rel):
                self.assertEqual(self.scope.read_file(rel)["text"], "readable")
                with self.assertRaises(ScopeError):
                    self.scope.write_file(rel, "blocked")
        for filename in files:
            target = self.root / "nested" / filename
            target.write_text("readable", encoding="utf-8")
            rel = target.relative_to(self.root).as_posix()
            with self.subTest(path=rel):
                self.assertEqual(self.scope.read_file(rel)["text"], "readable")
                with self.assertRaises(ScopeError):
                    self.scope.write_file(rel, "blocked")

    def test_links_and_hard_links(self):
        outside = self.base / "outside.txt"
        outside.write_text("outside", encoding="utf-8")
        link = self.root / "link.txt"
        try:
            link.symlink_to(outside)
        except (OSError, NotImplementedError):
            pass
        else:
            with self.assertRaises(ScopeError):
                self.scope.read_file("link.txt")
            with self.assertRaises(ScopeError):
                self.scope.write_file("link.txt", "blocked")

        first = self.root / "first.txt"
        second = self.root / "second.txt"
        first.write_text("original", encoding="utf-8")
        try:
            os.link(first, second)
        except (OSError, NotImplementedError):
            self.skipTest("hard links unavailable")
        with self.assertRaises(ScopeError):
            self.scope.write_file("first.txt", "blocked")

    def test_binary_read(self):
        (self.root / "binary.dat").write_bytes(b"abc\0def")
        result = self.scope.call("read_file", {"path": "binary.dat"})
        self.assertFalse(result["ok"])
        self.assertIn("binary", result["error"])

    def test_read_limits_and_newlines(self):
        limits = Limits(read_window=4, source_file=12)
        scope = Scope(self.root, limits=limits)
        (self.root / "small.txt").write_bytes(b"ab\ncd\nef\n")
        result = scope.read_file("small.txt")
        self.assertEqual(result["text"], "ab\nc")
        self.assertTrue(result["truncated"])
        self.assertEqual(result["total_lines"], 3)
        (self.root / "large.txt").write_bytes(b"x" * 13)
        with self.assertRaises(ScopeError):
            scope.read_file("large.txt")

    def test_write_limits_and_atomic_cleanup(self):
        limits = Limits(write_call=4, write_total=6, files_touched=2)
        scope = Scope(self.root, limits=limits)
        with self.assertRaises(ScopeError):
            scope.write_file("too-large.txt", "12345")
        self.assertEqual(scope.write_file("a/b.txt", "1234")["bytes"], 4)
        self.assertEqual(scope.write_file("c.txt", "12")["bytes"], 2)
        with self.assertRaises(ScopeError):
            scope.write_file("d.txt", "x")
        with self.assertRaises(ScopeError):
            scope.write_file("c.txt", "x")
        self.assertEqual((self.root / "a" / "b.txt").read_bytes(), b"1234")
        self.assertFalse(list(self.root.rglob(".scoped-*")))

        touched = Scope(self.root, limits=Limits(files_touched=1))
        touched.write_file("one.txt", "x")
        with self.assertRaises(ScopeError):
            touched.write_file("two.txt", "x")

    def test_list_limit_sort_and_git_omission(self):
        (self.root / ".git").mkdir()
        (self.root / "adir").mkdir()
        (self.root / "b.txt").write_text("b", encoding="utf-8")
        (self.root / "a.txt").write_text("a", encoding="utf-8")
        result = Scope(self.root, limits=Limits(list_entries=2)).list_dir()
        self.assertEqual([entry["name"] for entry in result["entries"]],
                         ["adir", "a.txt"])
        self.assertTrue(result["truncated"])

    def test_find_and_search_limits(self):
        for name in ("a.py", "b.py", "c.py"):
            (self.root / name).write_text("needle long line\n", encoding="utf-8")
        (self.root / "binary.py").write_bytes(b"x\0needle")
        (self.root / ".git").mkdir()
        (self.root / ".git" / "hidden.py").write_text(
            "needle", encoding="utf-8"
        )
        limits = Limits(walk_files=3, results=1, line_chars=6,
                        query_chars=10, walk_file_size=20)
        scope = Scope(self.root, limits=limits)
        found = scope.find_files("**/*.py")
        self.assertEqual(len(found["matches"]), 1)
        self.assertTrue(found["truncated"])
        searched = scope.search_text("needle", glob="**/*.py")
        self.assertEqual(len(searched["matches"]), 1)
        self.assertEqual(searched["matches"][0]["text"], "needle")
        self.assertTrue(searched["truncated"])
        with self.assertRaises(ScopeError):
            scope.find_files("x" * 11)
        with self.assertRaises(ScopeError):
            scope.search_text("x" * 11)
        with self.assertRaises(ScopeError):
            scope.search_text("(", regex=True)

        walk = Scope(self.root, limits=Limits(walk_files=1))
        self.assertTrue(walk.find_files("**/*.py")["truncated"])
        size = Scope(self.root, limits=Limits(walk_file_size=2))
        self.assertEqual(size.search_text("needle")["matches"], [])

    def test_walk_and_list_skip_names_the_policy_refuses(self):
        (self.root / "ok.txt").write_text("needle", encoding="utf-8")
        for name in ("NUL.txt", "ABCDEF~1.TXT"):
            try:
                (self.root / name).write_text("needle", encoding="utf-8")
            except OSError:
                continue
        found = [match["path"] for match in self.scope.search_text("needle")["matches"]]
        self.assertEqual(found, ["ok.txt"])
        self.assertEqual([entry["name"] for entry in self.scope.list_dir()["entries"]], ["ok.txt"])

    def test_crlf_edit_uniqueness_and_replace_all(self):
        target = self.root / "edit.txt"
        target.write_bytes(b"one\r\ntwo\r\none\r\ntwo\r\n")
        with self.assertRaises(ScopeError):
            self.scope.edit_file("edit.txt", "one\ntwo", "new")
        result = self.scope.edit_file(
            "edit.txt", "one\ntwo", "new\nline", replace_all=True
        )
        self.assertEqual(result["replacements"], 2)
        self.assertEqual(target.read_bytes(), b"new\r\nline\r\nnew\r\nline\r\n")
        with self.assertRaises(ScopeError):
            self.scope.edit_file("edit.txt", "missing", "x")

    def test_read_only_and_call_validation(self):
        (self.root / "file.txt").write_text("ok", encoding="utf-8")
        scope = Scope(self.root, read_only=True)
        self.assertTrue(scope.call("read_file", {"path": "file.txt"})["ok"])
        self.assertFalse(scope.call(
            "write_file", {"path": "file.txt", "content": "no"}
        )["ok"])
        self.assertFalse(scope.call("unknown", {})["ok"])
        self.assertFalse(scope.call(None, {"path": "file.txt"})["ok"])
        self.assertFalse(scope.call(
            "read_file", {"path": "file.txt", "extra": 1}
        )["ok"])
        self.assertFalse(scope.call(
            "read_file", {"path": "file.txt", "start_line": True}
        )["ok"])
        self.assertFalse(scope.call("read_file", {})["ok"])

    def test_audit_location_lines_and_failure_independence(self):
        with self.assertRaises(ScopeError):
            Scope(self.root, audit_path=self.root / "audit.ndjson")
        audit = self.base / "audit.ndjson"
        scope = Scope(self.root, audit_path=audit)
        self.assertTrue(scope.call(
            "write_file", {"path": "file.txt", "content": "hello"}
        )["ok"])
        self.assertFalse(scope.call(
            "read_file", {"path": "../outside"}
        )["ok"])
        lines = [json.loads(line) for line in audit.read_text(
            encoding="utf-8"
        ).splitlines()]
        self.assertEqual(len(lines), 2)
        self.assertEqual(lines[0]["path"], "file.txt")
        self.assertEqual(lines[0]["bytes"], 5)
        self.assertIsNone(lines[1]["path"])
        self.assertFalse(lines[1]["ok"])
        self.assertIn("at", lines[0])

        unavailable = self.base / "missing" / "audit.ndjson"
        unaffected = Scope(self.root, audit_path=unavailable)
        self.assertTrue(unaffected.call(
            "read_file", {"path": "file.txt"}
        )["ok"])


if __name__ == "__main__":
    unittest.main()
