"""Disposable git worktrees and reviewed patches for worker assignments."""

from __future__ import annotations

import hashlib
import os
import stat
import subprocess
from datetime import datetime, timezone
from pathlib import Path

from scoped_files import protected_reason
from task_store import write_json

TIMEOUT_SECONDS = 120
PATCH_LIMIT = 8 * 1024 * 1024
EMPTY_HOOKS = Path(__file__).resolve().parents[1] / "output" / "state" / "git-empty-hooks"
_CREATE_NO_WINDOW = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
_REPARSE = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
_STRIP_ENV = frozenset({
    "GIT_DIR", "GIT_WORK_TREE", "GIT_INDEX_FILE", "GIT_OBJECT_DIRECTORY",
    "GIT_ALTERNATE_OBJECT_DIRECTORIES", "GIT_CONFIG_PARAMETERS", "GIT_CONFIG_COUNT",
    "GIT_EXEC_PATH", "GIT_ATTR_SOURCE",
})
# Explicit prefixes and no renames keep the patch independent of user diff
# settings and list both sides of a move, so each path gets its own check.
_DIFF = ["diff", "--cached", "--no-renames", "--no-ext-diff", "--no-textconv",
         "--no-color", "--no-relative"]


class WorktreeError(RuntimeError):
    """A short failure that is safe to show."""


def _git_env():
    env = {key: value for key, value in os.environ.items()
           if key not in _STRIP_ENV and not key.startswith(("GIT_CONFIG_KEY_", "GIT_CONFIG_VALUE_"))}
    env.update(GIT_TERMINAL_PROMPT="0", GIT_OPTIONAL_LOCKS="0")
    return env


def _empty_hooks():
    EMPTY_HOOKS.mkdir(parents=True, exist_ok=True)
    if any(EMPTY_HOOKS.iterdir()):
        raise WorktreeError("The empty git hooks folder is not empty.")
    return EMPTY_HOOKS


def _git(args, cwd, *, attr_source=None, input=None, check=True):
    """Run git with hooks, fsmonitor, maintenance and network transports disabled."""
    command = ["git", "-c", f"core.hooksPath={_empty_hooks()}", "-c", "core.fsmonitor=false",
               "-c", "core.symlinks=false", "-c", "protocol.allow=never", "-c", "gc.auto=0",
               "-c", "maintenance.auto=false", "--no-pager"]
    if attr_source:
        # Worker-written .gitattributes must not select filter commands.
        command.append(f"--attr-source={attr_source}")
    stdin = {"input": input} if input is not None else {"stdin": subprocess.DEVNULL}
    try:
        process = subprocess.run(command + args, cwd=str(cwd), env=_git_env(), capture_output=True,
                                 timeout=TIMEOUT_SECONDS, creationflags=_CREATE_NO_WINDOW, **stdin)
    except subprocess.TimeoutExpired as exc:
        raise WorktreeError(f"git {args[0]} timed out.") from exc
    except OSError as exc:
        raise WorktreeError("git is unavailable.") from exc
    if check and process.returncode:
        detail = process.stderr.decode("utf-8", "replace").strip().splitlines()
        raise WorktreeError(f"git {args[0]} failed: {detail[0][:160] if detail else 'no detail'}")
    return process


def _text(args, cwd):
    return _git(args, cwd).stdout.decode("utf-8", "replace").strip()


def _toplevel(repo):
    try:
        top = _text(["rev-parse", "--show-toplevel"], repo)
    except (WorktreeError, OSError) as exc:
        raise WorktreeError("Not a git work tree.") from exc
    if not top:
        raise WorktreeError("Not a git work tree.")
    return Path(top)


def _unsupported_entries(source, commit):
    for entry in _git(["ls-tree", "-r", "-z", "--full-tree", commit], source).stdout.split(b"\0"):
        if entry.split(b" ", 1)[0] in (b"120000", b"160000"):
            return "Symbolic links and submodules are not supported."
    return None


def _scan(root):
    """Return why a worktree is unsafe to hand to git, or None."""
    root = Path(root)
    for current, dirs, files in os.walk(root):
        for name in dirs + files:
            path = Path(current, name)
            if path == root / ".git":
                continue
            if name.casefold() == ".git":
                return "Nested repositories are not supported."
            if stat.S_ISLNK((st := os.lstat(path)).st_mode) or getattr(st, "st_file_attributes", 0) & _REPARSE:
                return "Links are not supported."
    return None


def _remove(source, path):
    process = _git(["worktree", "remove", "--force", str(path)], source, check=False)
    _git(["worktree", "prune"], source, check=False)
    if Path(path).exists():
        detail = process.stderr.decode("utf-8", "replace").strip().splitlines()
        raise WorktreeError(f"git worktree remove failed: {detail[0][:160] if detail else 'no detail'}")


def create(source_repo, ref, destination) -> dict:
    source = _toplevel(source_repo)
    ref = str(ref)
    if not ref or ref.startswith("-"):
        raise WorktreeError("Invalid ref.")
    destination = Path(destination).absolute()
    if os.path.lexists(destination):
        raise WorktreeError("The worktree destination already exists.")
    if not destination.parent.is_dir():
        raise WorktreeError("The worktree destination folder does not exist.")
    commit = _text(["rev-parse", "--verify", "--end-of-options", f"{ref}^{{commit}}"], source)
    problem = _unsupported_entries(source, commit)
    if problem:
        raise WorktreeError(problem)
    try:
        _git(["worktree", "add", "--detach", str(destination), commit], source)
        problem = _scan(destination)
    except OSError:
        problem = "The new worktree could not be inspected."
    except WorktreeError:
        # A checkout can fail after git registered or populated the destination.
        try:
            _remove(source, destination)
        except WorktreeError:
            pass
        raise
    if problem:
        _remove(source, destination)
        raise WorktreeError(problem)
    return {
        "schema_version": 1, "source_repo": str(source), "ref": ref, "base_commit": commit,
        "path": str(destination), "created_at": datetime.now(timezone.utc).isoformat(),
    }


def _path(raw):
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError:
        raise WorktreeError("A changed path is not valid UTF-8.") from None


def _numstat(raw):
    """Yield (added, deleted, paths) from `--numstat -z` output."""
    items = raw.split(b"\0")
    index = 0
    while index < len(items) and items[index]:
        added, deleted, path = _path(items[index]).split("\t", 2)
        index += 1
        paths = [path]
        if not path:  # a rename or copy lists the old and new paths next
            paths = [_path(item) for item in items[index:index + 2]]
            index += 2
        yield added, deleted, paths


def _patch_paths(patch, cwd):
    return [path for _, _, paths in _numstat(_git(["apply", "--numstat", "-z"], cwd, input=patch).stdout)
            for path in paths]


def collect(record, output_dir) -> dict:
    work = Path(record["path"])
    base = record["base_commit"]
    problem = _scan(work)
    if problem:
        raise WorktreeError(problem)
    output = Path(output_dir).absolute()
    if output.resolve().is_relative_to(work.resolve()):
        raise WorktreeError("The review output folder must be outside the worktree.")
    output.mkdir(parents=True, exist_ok=True)
    _git(["add", "-A", "--", "."], work, attr_source=base)
    patch = _git(_DIFF + ["--binary", "--full-index", "--src-prefix=a/", "--dst-prefix=b/", base],
                 work, attr_source=base).stdout
    if len(patch) > PATCH_LIMIT:
        raise WorktreeError("The worker patch exceeds 8 MiB.")
    names = _git(_DIFF + ["--name-status", "-z", base], work, attr_source=base).stdout.split(b"\0")
    files = [{"path": _path(path), "status": status.decode("ascii", "replace"),
              "protected": protected_reason(_path(path)) is not None}
             for status, path in zip(names[0::2], names[1::2]) if status]
    insertions = deletions = 0
    binary_files = []
    for added, deleted, paths in _numstat(_git(_DIFF + ["--numstat", "-z", base], work, attr_source=base).stdout):
        if added == "-":
            binary_files.extend(paths)
        else:
            insertions += int(added)
            deletions += int(deleted)
    patch_path = output / "worker.patch"
    patch_path.write_bytes(patch)
    collected = {
        "patch_path": str(patch_path), "patch_sha256": hashlib.sha256(patch).hexdigest(),
        "files": files, "insertions": insertions, "deletions": deletions,
        "binary_files": binary_files,
        "protected_paths": [item["path"] for item in files if item["protected"]],
        "empty": not files,
    }
    write_json(output / "collect.json", collected)
    return collected


def apply(record, collected, target_repo, *, check_only=False) -> dict:
    """Check, then stage the reviewed patch in the target; never commits."""
    if collected.get("protected_paths"):
        raise WorktreeError("The patch changes protected paths; apply refused.")
    patch = Path(collected["patch_path"]).read_bytes()
    if hashlib.sha256(patch).hexdigest() != collected.get("patch_sha256"):
        raise WorktreeError("The patch changed after review; apply refused.")
    target = _toplevel(target_repo)
    # Check and report the bytes git will apply, not only the collected summary.
    paths = _patch_paths(patch, target)
    if any(protected_reason(path) for path in paths):
        raise WorktreeError("The patch changes protected paths; apply refused.")
    if sorted(paths) != sorted(item["path"] for item in collected.get("files") or []):
        raise WorktreeError("The patch does not match the reviewed file list; apply refused.")
    # No --3way: a diverged target fails cleanly instead of running merge drivers.
    _git(["apply", "--check", "--index"], target, input=patch)
    if not check_only:
        _git(["apply", "--index"], target, input=patch)
    return {"applied": not check_only, "checked": True, "target_repo": str(target), "files": paths}


def remove(record) -> dict:
    _remove(Path(record["source_repo"]), Path(record["path"]))
    return {"removed": True}
