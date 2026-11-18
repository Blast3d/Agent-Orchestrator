"""Confined file operations for workers assigned one disposable folder."""

from __future__ import annotations

import json
import ntpath
import os
import re
import stat
import tempfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath


class ScopeError(ValueError):
    """A refusal that is safe to show to a worker."""


@dataclass(frozen=True)
class Limits:
    read_window: int = 256 * 1024
    source_file: int = 8 * 1024 * 1024
    write_call: int = 1024 * 1024
    write_total: int = 16 * 1024 * 1024
    files_touched: int = 500
    list_entries: int = 1000
    walk_files: int = 20000
    walk_file_size: int = 2 * 1024 * 1024
    results: int = 200
    line_chars: int = 400
    query_chars: int = 500


READ_TOOLS = frozenset({"read_file", "list_dir", "find_files", "search_text"})
WRITE_TOOLS = frozenset({"write_file", "edit_file"})

_PROTECTED_DIRS = frozenset({
    ".agents", ".claude", ".codex", ".grok", ".gemini",
    ".cursor", ".vscode", ".idea", ".github",
})
_PROTECTED_FILES = frozenset({
    ".mcp.json", ".gitmodules", ".gitattributes", "agents.md",
    "claude.md", "claude.local.md", "gemini.md",
    ".cursorrules", ".windsurfrules",
})
_DEVICES = frozenset({
    "con", "prn", "aux", "nul", "conin$", "conout$",
    *(f"com{i}" for i in (*range(10), "¹", "²", "³")),
    *(f"lpt{i}" for i in (*range(10), "¹", "²", "³")),
})
_SHORT_NAME = re.compile(r"^[^~]{1,6}~\d+(\.[^.]*)?$", re.IGNORECASE)
_REPARSE = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)


def _is_link(st):
    return stat.S_ISLNK(st.st_mode) or bool(
        getattr(st, "st_file_attributes", 0) & _REPARSE
    )


def protected_reason(relative_path):
    """Why workers may not change this root-relative path, or None."""
    parts = [part.casefold() for part in str(relative_path).replace("\\", "/").split("/") if part]
    if ".git" in parts:
        return ".git is not available to workers"
    if any(part in _PROTECTED_DIRS for part in parts) or (parts and parts[-1] in _PROTECTED_FILES):
        return "tool or agent configuration"
    return None


def _bad_component(part):
    return (
        any(ch in ':<>"|?*' or ord(ch) < 0x20 for ch in part)
        or part.endswith((".", " "))
        or part.split(".", 1)[0].rstrip(" ").casefold() in _DEVICES
        or bool(_SHORT_NAME.fullmatch(part))
    )


def _inside(path, root):
    path = os.path.normcase(os.path.normpath(os.fspath(path)))
    root = os.path.normcase(os.path.normpath(os.fspath(root)))
    try:
        return os.path.commonpath((path, root)) == root
    except ValueError:
        return False


def _schema(properties, required=()):
    return {
        "type": "object",
        "properties": properties,
        "required": list(required),
        "additionalProperties": False,
    }


_STRING = {"type": "string"}
_INTEGER = {"type": "integer", "minimum": 1}
_BOOLEAN = {"type": "boolean"}

TOOLS = [
    {
        "name": "read_file",
        "description": "Read a bounded window of a text file in the assigned folder.",
        "input_schema": _schema(
            {"path": _STRING, "start_line": _INTEGER, "max_lines": _INTEGER},
            ("path",),
        ),
    },
    {
        "name": "list_dir",
        "description": "List a directory in the assigned folder.",
        "input_schema": _schema({"path": _STRING}),
    },
    {
        "name": "find_files",
        "description": "Find files by a root-relative glob pattern.",
        "input_schema": _schema({"pattern": _STRING, "path": _STRING}, ("pattern",)),
    },
    {
        "name": "search_text",
        "description": "Search text files in the assigned folder.",
        "input_schema": _schema(
            {
                "query": _STRING, "path": _STRING, "glob": _STRING,
                "regex": _BOOLEAN, "ignore_case": _BOOLEAN,
            },
            ("query",),
        ),
    },
    {
        "name": "write_file",
        "description": "Atomically write a UTF-8 file in the assigned folder.",
        "input_schema": _schema(
            {"path": _STRING, "content": _STRING}, ("path", "content")
        ),
    },
    {
        "name": "edit_file",
        "description": "Replace text in a file in the assigned folder.",
        "input_schema": _schema(
            {
                "path": _STRING, "old_text": _STRING,
                "new_text": _STRING, "replace_all": _BOOLEAN,
            },
            ("path", "old_text", "new_text"),
        ),
    },
]


class Scope:
    def __init__(self, root, *, audit_path=None, read_only=False, limits=None):
        supplied_root = Path(root)
        root_stat = os.lstat(supplied_root)
        if _is_link(root_stat):
            raise ScopeError("Denied: assigned folder is a link.")
        self.root = supplied_root.resolve(strict=True)
        if not self.root.is_dir():
            raise ScopeError("Denied: assigned folder is not a directory.")
        self.limits = limits if limits is not None else Limits()
        if not isinstance(self.limits, Limits):
            raise TypeError("limits must be a Limits instance")
        if type(read_only) is not bool:
            raise TypeError("read_only must be a boolean")
        self.read_only = read_only
        self._written = 0
        self._touched = set()
        self.audit_path = None
        if audit_path is not None:
            audit = Path(audit_path).absolute()
            if _inside(audit, self.root) or _inside(
                audit.resolve(strict=False), self.root
            ):
                raise ScopeError("Denied: audit file must be outside the assigned folder.")
            self.audit_path = audit

    def _lexical(self, requested):
        if not isinstance(requested, str) or not 1 <= len(requested) <= 1024:
            raise ScopeError("Denied: invalid path.")
        if any(ord(ch) < 0x20 for ch in requested):
            raise ScopeError("Denied: invalid path.")
        path = requested.replace("\\", "/")
        if path.startswith("//"):
            raise ScopeError("Denied: outside the assigned folder.")

        if os.name == "nt":
            # Python 3.13 no longer treats a drive-less rooted path as absolute.
            if path.startswith("/"):
                raise ScopeError("Denied: outside the assigned folder.")
            if ntpath.isabs(path):
                if not _inside(path, self.root):
                    raise ScopeError("Denied: outside the assigned folder.")
                path = os.path.relpath(path, self.root).replace("\\", "/")
            elif ntpath.splitdrive(path)[0]:
                raise ScopeError("Denied: outside the assigned folder.")
        else:
            if ntpath.splitdrive(path)[0]:
                raise ScopeError("Denied: outside the assigned folder.")
            if path.startswith("/"):
                if not _inside(path, self.root):
                    raise ScopeError("Denied: outside the assigned folder.")
                path = os.path.relpath(path, self.root).replace("\\", "/")

        parts = []
        for part in path.split("/"):
            if part in ("", "."):
                continue
            if part == "..":
                raise ScopeError("Denied: traversal is not allowed.")
            if _bad_component(part):
                raise ScopeError("Denied: invalid path component.")
            parts.append(part)
        return parts

    def _checked(self, requested, *, for_write=False):
        parts = self._lexical(requested)
        rel = "/".join(parts) or "."
        folded = [part.casefold() for part in parts]
        if ".git" in folded:
            raise ScopeError("Denied: .git is not available to workers.")
        if for_write:
            if self.read_only:
                raise ScopeError("Denied: this assignment is read-only.")
            if protected_reason(rel):
                raise ScopeError(
                    f"Denied: {rel} is write-protected (tool or agent configuration)."
                )

        current = self.root
        for part in parts:
            current = current / part
            try:
                st = os.lstat(current)
            except FileNotFoundError:
                continue
            if _is_link(st):
                raise ScopeError(f"Denied: {rel} is a link.")
            if for_write and stat.S_ISREG(st.st_mode) and st.st_nlink > 1:
                raise ScopeError(f"Denied: {rel} has multiple hard links.")

        if not _inside(os.path.realpath(current), self.root):
            raise ScopeError("Denied: outside the assigned folder.")
        return current, rel

    def resolve(self, requested, *, for_write=False) -> Path:
        return self._checked(requested, for_write=for_write)[0]

    def relative(self, path) -> str:
        target, rel = self._checked(os.fspath(path))
        if not _inside(target, self.root):
            raise ScopeError("Denied: outside the assigned folder.")
        return rel

    def check_path(self, requested, *, for_write=False) -> str:
        return self._checked(requested, for_write=for_write)[1]

    def _file(self, path, *, for_write=False):
        target, rel = self._checked(path, for_write=for_write)
        if not target.exists():
            raise ScopeError(f"Not found: {rel}")
        if not target.is_file():
            raise ScopeError(f"Denied: {rel} is not a file.")
        return target, rel

    def _text(self, target, rel, size_limit):
        if target.stat().st_size > size_limit:
            raise ScopeError(f"Limit exceeded: {rel} is too large.")
        data = target.read_bytes()
        if b"\0" in data[:8192]:
            raise ScopeError("Denied: binary file not supported.")
        return data.decode("utf-8", errors="replace")

    def read_file(self, path, start_line=1, max_lines=2000) -> dict:
        if type(start_line) is not int or start_line < 1:
            raise ScopeError("Denied: start_line must be a positive integer.")
        if type(max_lines) is not int or max_lines < 1:
            raise ScopeError("Denied: max_lines must be a positive integer.")
        target, rel = self._file(path)
        source = self._text(target, rel, self.limits.source_file)
        lines = source.splitlines(keepends=True)
        selected = []
        used = 0
        first = start_line - 1
        for line in lines[first:first + max_lines]:
            remaining = self.limits.read_window - used
            if remaining <= 0:
                break
            encoded = line.encode("utf-8")
            if len(encoded) > remaining:
                selected.append(encoded[:remaining].decode("utf-8", errors="replace"))
                used = self.limits.read_window
                break
            selected.append(line)
            used += len(encoded)
        end_line = first + len(selected)
        return {
            "path": rel,
            "text": "".join(selected),
            "start_line": start_line,
            "end_line": end_line,
            "total_lines": len(lines),
            "truncated": end_line < len(lines) or (
                bool(selected)
                and used == self.limits.read_window
                and len("".join(selected).encode("utf-8")) < len(
                    "".join(lines[first:end_line]).encode("utf-8")
                )
            ),
        }

    def list_dir(self, path=".") -> dict:
        target, rel = self._checked(path)
        if not target.exists():
            raise ScopeError(f"Not found: {rel}")
        if not target.is_dir():
            raise ScopeError(f"Denied: {rel} is not a directory.")
        entries = []
        for child in target.iterdir():
            if child.name.casefold() == ".git" or _bad_component(child.name):
                continue
            st = os.lstat(child)
            if _is_link(st):
                continue
            if stat.S_ISDIR(st.st_mode):
                kind = "dir"
            elif stat.S_ISREG(st.st_mode):
                kind = "file"
            else:
                continue
            entries.append({"name": child.name, "type": kind, "size": st.st_size})
        entries.sort(key=lambda item: (item["type"] != "dir", item["name"].casefold()))
        cap = self.limits.list_entries
        return {"path": rel, "entries": entries[:cap], "truncated": len(entries) > cap}

    def _walk(self, path):
        target, rel = self._checked(path)
        if not target.exists():
            raise ScopeError(f"Not found: {rel}")
        if not target.is_dir():
            raise ScopeError(f"Denied: {rel} is not a directory.")
        count = 0
        stack = [target]
        while stack:
            directory = stack.pop()
            children = sorted(directory.iterdir(), key=lambda item: item.name.casefold())
            for child in children:
                # Names the path policy refuses (devices, aliases) are never opened.
                if child.name.casefold() == ".git" or _bad_component(child.name):
                    continue
                st = os.lstat(child)
                if _is_link(st):
                    continue
                if stat.S_ISDIR(st.st_mode):
                    stack.append(child)
                elif stat.S_ISREG(st.st_mode):
                    count += 1
                    if count > self.limits.walk_files:
                        yield None, None, True
                        return
                    yield child, st, False

    def _pattern(self, pattern):
        if not isinstance(pattern, str) or not 1 <= len(pattern) <= self.limits.query_chars:
            raise ScopeError("Denied: invalid pattern.")
        normalized = pattern.replace("\\", "/")
        if normalized.startswith("/") or ntpath.splitdrive(normalized)[0]:
            raise ScopeError("Denied: outside the assigned folder.")
        if ".." in normalized.split("/") or ".git" in (
            part.casefold() for part in normalized.split("/")
        ):
            raise ScopeError("Denied: invalid pattern.")
        return normalized

    @staticmethod
    def _matches(rel, pattern):
        candidate = PurePosixPath(rel)
        if candidate.match(pattern):
            return True
        return pattern.startswith("**/") and candidate.match(pattern[3:])

    def find_files(self, pattern, path=".") -> dict:
        pattern = self._pattern(pattern)
        matches = []
        truncated = False
        for target, st, capped in self._walk(path):
            if capped:
                truncated = True
                break
            if st.st_size > self.limits.walk_file_size:
                continue
            rel = target.relative_to(self.root).as_posix()
            if self._matches(rel, pattern):
                try:
                    self._text(target, rel, self.limits.walk_file_size)
                except ScopeError:
                    continue
                if len(matches) >= self.limits.results:
                    truncated = True
                    break
                matches.append(rel)
        return {"matches": matches, "truncated": truncated}

    def search_text(
        self, query, path=".", glob=None, regex=False, ignore_case=False
    ) -> dict:
        if not isinstance(query, str) or not 1 <= len(query) <= self.limits.query_chars:
            raise ScopeError("Denied: invalid query.")
        if type(regex) is not bool or type(ignore_case) is not bool:
            raise ScopeError("Denied: search flags must be booleans.")
        pattern = self._pattern(glob) if glob is not None else None
        try:
            needle = re.compile(
                query if regex else re.escape(query),
                re.IGNORECASE if ignore_case else 0,
            )
        except re.error:
            raise ScopeError("Denied: invalid regular expression.") from None
        matches = []
        truncated = False
        for target, st, capped in self._walk(path):
            if capped:
                truncated = True
                break
            if st.st_size > self.limits.walk_file_size:
                continue
            rel = target.relative_to(self.root).as_posix()
            if pattern is not None and not self._matches(rel, pattern):
                continue
            try:
                source = self._text(target, rel, self.limits.walk_file_size)
            except ScopeError:
                continue
            for number, line in enumerate(source.splitlines(), 1):
                if needle.search(line):
                    if len(matches) >= self.limits.results:
                        truncated = True
                        break
                    matches.append({
                        "path": rel, "line": number,
                        "text": line[:self.limits.line_chars],
                    })
            if truncated:
                break
        return {"matches": matches, "truncated": truncated}

    def _write(self, path, content):
        if not isinstance(content, str):
            raise ScopeError("Denied: content must be text.")
        target, rel = self._checked(path, for_write=True)
        if rel == ".":
            raise ScopeError("Denied: path must name a file.")
        encoded = content.encode("utf-8")
        size = len(encoded)
        key = os.path.normcase(str(target))
        if size > self.limits.write_call or self._written + size > self.limits.write_total:
            raise ScopeError("Limit exceeded: write size.")
        if key not in self._touched and len(self._touched) >= self.limits.files_touched:
            raise ScopeError("Limit exceeded: files touched.")
        created = not target.exists()
        if not created and not target.is_file():
            raise ScopeError(f"Denied: {rel} is not a file.")

        parent = self.root
        for part in target.relative_to(self.root).parts[:-1]:
            parent = parent / part
            self._checked(str(parent), for_write=True)
            if not parent.exists():
                parent.mkdir()
            self._checked(str(parent), for_write=True)

        temporary = None
        try:
            with tempfile.NamedTemporaryFile(
                mode="w", encoding="utf-8", newline="",
                dir=target.parent, prefix=".scoped-", delete=False
            ) as stream:
                temporary = Path(stream.name)
                stream.write(content)
            self._checked(path, for_write=True)
            os.replace(temporary, target)
        finally:
            if temporary is not None:
                try:
                    temporary.unlink()
                except FileNotFoundError:
                    pass
        self._written += size
        self._touched.add(key)
        return {"path": rel, "bytes": size, "created": created}

    def write_file(self, path, content) -> dict:
        return self._write(path, content)

    def edit_file(self, path, old_text, new_text, replace_all=False) -> dict:
        if not isinstance(old_text, str) or not old_text:
            raise ScopeError("Denied: old_text must be non-empty text.")
        if not isinstance(new_text, str) or type(replace_all) is not bool:
            raise ScopeError("Denied: invalid edit arguments.")
        target, rel = self._file(path, for_write=True)
        source = self._text(target, rel, self.limits.source_file)
        count = source.count(old_text)
        if count == 0 and "\r\n" in source:
            old_text = old_text.replace("\n", "\r\n").replace("\r\r\n", "\r\n")
            new_text = new_text.replace("\n", "\r\n").replace("\r\r\n", "\r\n")
            count = source.count(old_text)
        if count == 0:
            raise ScopeError(f"Not found: text in {rel}")
        if count != 1 and not replace_all:
            raise ScopeError("Denied: edit text is not unique.")
        updated = source.replace(old_text, new_text, -1 if replace_all else 1)
        self._write(path, updated)
        return {"path": rel, "replacements": count if replace_all else 1}

    def _audit(self, tool, arguments, result):
        if self.audit_path is None:
            return
        rel = None
        candidate = arguments.get("path")
        if isinstance(candidate, str):
            try:
                rel = "/".join(self._lexical(candidate)) or "."
            except ScopeError:
                pass
        if result.get("ok") and isinstance(result.get("path"), str):
            rel = result["path"]
        record = {
            "at": datetime.now(timezone.utc).isoformat(),
            "tool": tool, "path": rel, "ok": result["ok"],
            "error": result.get("error"),
            "bytes": result.get("bytes", 0),
        }
        try:
            with self.audit_path.open("a", encoding="utf-8", newline="\n") as stream:
                stream.write(json.dumps(record, ensure_ascii=False) + "\n")
        except OSError:
            pass

    def call(self, name, arguments) -> dict:
        if not isinstance(name, str):
            return {"ok": False, "error": "Denied: unknown tool."}
        if not isinstance(arguments, dict):
            result = {"ok": False, "error": "Denied: arguments must be an object."}
            self._audit(name, {}, result)
            return result
        spec = next((tool for tool in TOOLS if tool["name"] == name), None)
        if spec is None:
            result = {"ok": False, "error": "Denied: unknown tool."}
            self._audit(name, arguments, result)
            return result
        schema = spec["input_schema"]
        if set(arguments) - set(schema["properties"]) or (
            set(schema["required"]) - set(arguments)
        ):
            result = {"ok": False, "error": "Denied: invalid tool arguments."}
            self._audit(name, arguments, result)
            return result
        for key, value in arguments.items():
            expected = schema["properties"][key]["type"]
            valid = (
                isinstance(value, str) if expected == "string"
                else type(value) is bool if expected == "boolean"
                else type(value) is int and value >= 1
            )
            if not valid:
                result = {"ok": False, "error": "Denied: invalid tool arguments."}
                self._audit(name, arguments, result)
                return result
        try:
            data = getattr(self, name)(**arguments)
            result = {"ok": True, **data}
        except (ScopeError, OSError, UnicodeError) as exc:
            message = str(exc) if isinstance(exc, ScopeError) else "Denied: file operation failed."
            result = {"ok": False, "error": message}
        self._audit(name, arguments, result)
        return result
