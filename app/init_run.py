"""Create an isolated orchestration record without invoking any provider."""
import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import stat
import uuid


def _plain_stat(path, label):
    """Configuration and its directory must not redirect to another location."""
    value = path.lstat()
    if stat.S_ISLNK(value.st_mode) or getattr(value, 'st_file_attributes', 0) & 0x400:
        raise ValueError(label + ' must not be a symlink, junction, or reparse point.')
    return value


def _project_identity(parent, explicit):
    from brain_store import scope
    if explicit is not None:
        # An explicit scope deliberately overrides the optional workspace default.
        return scope(explicit)
    path = parent / 'project.json'
    try:
        before = _plain_stat(path, 'Workspace project configuration')
    except FileNotFoundError:
        return None
    if not stat.S_ISREG(before.st_mode):
        raise ValueError('Workspace project configuration must be a regular file.')
    if before.st_size > 8192:
        raise ValueError('Workspace project configuration exceeds 8192 bytes.')
    with path.open('rb') as stream:
        if not os.path.samestat(before, os.fstat(stream.fileno())):
            raise ValueError('Workspace project configuration changed while opening.')
        raw = stream.read(8193)
    after = _plain_stat(path, 'Workspace project configuration')
    if (not os.path.samestat(before, after) or before.st_size != after.st_size
            or before.st_mtime_ns != after.st_mtime_ns):
        raise ValueError('Workspace project configuration changed while reading.')
    if len(raw) > 8192:
        raise ValueError('Workspace project configuration exceeds 8192 bytes.')
    try:
        value = json.loads(raw.decode('utf-8'))
    except (ValueError, UnicodeError):
        raise ValueError('Workspace project configuration must contain UTF-8 JSON.') from None
    if (not isinstance(value, dict) or type(value.get('schema_version')) is not int
            or value['schema_version'] != 1 or 'project_id' not in value):
        raise ValueError('Workspace project configuration needs schema_version 1 and project_id.')
    return scope(value['project_id'])


def create_run(workspace, name, objective, audience="Infer from current request", deliverables=None, *,
               native_parent_session_id=None, project_id=None):
    if not re.fullmatch(r"[a-z0-9]+(?:-[a-z0-9]+)*", name) or len(name) > 60:
        raise ValueError("Name must be a short lowercase slug, using letters, digits, and hyphens.")
    if not objective.strip():
        raise ValueError("Objective must not be blank.")
    if native_parent_session_id is not None and (not isinstance(native_parent_session_id, str) or
            not re.fullmatch(r'[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}', native_parent_session_id)):
        raise ValueError('Native parent session must be an exact Codex conversation UUID.')
    workspace = Path(workspace).resolve(strict=True)
    if not workspace.is_dir():
        raise ValueError("Workspace must be an existing directory.")
    parent = workspace / ".orchestration"
    # Refuse a symlink/junction that would redirect writes out of the workspace.
    if not parent.resolve().is_relative_to(workspace):
        raise ValueError("Orchestration directory resolves outside the selected workspace.")
    try:
        parent_stat = _plain_stat(parent, 'Orchestration directory')
    except FileNotFoundError:
        parent_stat = None
    if parent_stat is not None and not stat.S_ISDIR(parent_stat.st_mode):
        raise ValueError('Orchestration directory must be a directory.')
    project_id = _project_identity(parent, project_id)
    parent.mkdir(exist_ok=True)
    now = datetime.now(timezone.utc)
    run_id = name + "-" + now.strftime("%Y%m%dT%H%M%SZ") + "-" + uuid.uuid4().hex[:8]
    run = parent / run_id
    run.mkdir(exist_ok=False)
    for folder in ("drafts", "review", "deliverables"):
        (run / folder).mkdir()
    record = {
        "schema_version": 1,
        "run_id": run_id,
        "created_utc": now.isoformat(),
        "objective": objective.strip(),
        "audience": audience,
        "requested_deliverables": deliverables or [],
        "status": "planning",
        "source_snapshot": "not-recorded",
        "authorization": {"status": "resolve-from-current-session", "provider_content_scopes": []},
        "cost_policy": {"preference": "free-local-or-existing-included-quota", "new_billable_routes_authorized": False, "explicit_user_budget": None},
        "providers": [],
        "tasks": [],
        "validation": [],
        "final_artifacts": [],
        "contribution_audit": {"required": True, "status": "pending", "ledger": "contributions-ledger.json", "report": "contribution-audit.md"},
    }
    if project_id is not None:
        record['project_id'] = project_id
    if native_parent_session_id:
        # Durable metadata lineage remains useful after a lead handoff. This
        # does not bind history, open a conversation, or start another agent.
        record['native_parent_session_id'] = native_parent_session_id
    (run / "run.json").write_text(json.dumps(record, indent=2) + "\n", encoding="utf-8")
    (run / 'contributions-ledger.json').write_text(json.dumps({
        'schema_version': 1, 'scope_id': run_id, 'title': objective.strip(),
        'basis': 'Estimated share of accepted work, using reviewed work-item weights and evidence. Usage is recorded separately.',
        'contributors': [], 'work_items': [], 'activity': []}, indent=2) + '\n', encoding='utf-8')
    brief = f"""# Shared project brief

## Outcome
{objective.strip()}

## Audience
{audience}

## Requested deliverables
{chr(10).join('- ' + x for x in (deliverables or [])) or 'Derive from the current user request.'}

## Sources and snapshot
Record relevant source revisions/dates and the approved excerpts used by workers.

## Facts, inferences, and open questions
Keep these distinguishable when adding the task's factual basis.

## Acceptance criteria
Record observable requirements before assigning dependent production work.

## Boundaries and budget
Carry forward existing session authorization and constraints into run.json.
"""
    (run / "brief.md").write_text(brief, encoding="utf-8")
    (run / "review" / "decisions.md").write_text("# Integration decisions\n\nRecord consequential findings, evidence, accepted corrections, and remaining limits.\n", encoding="utf-8")
    from coordinator_handoff import Coordinator
    Coordinator(run).initialize('astra', run_id)
    return run, record


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workspace", type=Path, required=True)
    parser.add_argument("--name", required=True)
    parser.add_argument("--objective", required=True)
    parser.add_argument("--audience", default="Infer from current request")
    parser.add_argument("--deliverable", action="append", default=[])
    parser.add_argument("--project", help="Stable memory project; overrides .orchestration/project.json")
    args = parser.parse_args()
    try:
        run, _ = create_run(args.workspace, args.name, args.objective, args.audience, args.deliverable,
                            native_parent_session_id=os.environ.get('CODEX_THREAD_ID') or None,
                            project_id=args.project)
    except (OSError, ValueError) as exc:
        # Avoid dumping user paths from OS exceptions into recording output.
        message = str(exc) if isinstance(exc, ValueError) else type(exc).__name__
        parser.exit(1, f"Could not initialize run: {message}\n")
    print(json.dumps({"created": True, "run": run.relative_to(args.workspace.resolve()).as_posix(), "provider_calls": 0}))


if __name__ == "__main__":
    main()
