"""Bounded input commands for source-backed Jev advisory workflows."""
import argparse
import json
from pathlib import Path

import jev_openrouter
from brain_store import safe_path
from jev_workflows import MAX_PAYLOAD_BYTES, run_workflow, workflow_catalog
from paths import ROOT


def add_arguments(commands):
    commands.add_parser('workflows', help='List advisory workflows and their required inputs.')
    workflow = commands.add_parser('workflow', help='Prepare source-backed advice; never applies an action.')
    workflow.add_argument('workflow', choices=[item['workflow'] for item in workflow_catalog()])
    workflow.add_argument('--project', required=True)
    workflow.add_argument('--file', type=Path, required=True,
                          help='UTF-8 JSON under this application root; maximum 24 KiB.')


def execute(args, root):
    if args.command == 'workflows':
        return {'status': 'ok', 'advisory_only': True, 'workflows': workflow_catalog()}
    root = Path(root).resolve()
    requested = args.file
    if '..' in requested.parts or (requested.anchor and not requested.is_absolute()):
        raise ValueError('Payload paths must remain inside the application root.')
    path = safe_path(root, requested if requested.is_absolute() else root / requested)
    if path.suffix.lower() != '.json' or not path.is_file():
        raise ValueError('Select a JSON payload file inside the application root.')
    payload = jev_openrouter._read(path, MAX_PAYLOAD_BYTES)
    return run_workflow(root, args.project, args.workflow, payload)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, default=ROOT)
    add_arguments(parser.add_subparsers(dest='command', required=True))
    args = parser.parse_args(argv)
    try:
        result = execute(args, args.root)
    except (OSError, ValueError, UnicodeError):
        result = {'status': 'invalid_input', 'reason': 'Input file could not be safely read or validated.'}
    print(json.dumps(result, ensure_ascii=True, allow_nan=False))
    return 0 if result['status'] in ('ok', 'partial', 'low_confidence', 'insufficient_evidence') else 2


if __name__ == '__main__':
    raise SystemExit(main())
