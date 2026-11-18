"""Show or apply the collected patch of a scoped worker task; never runs a model.

Applying is the lead's explicit step: the patch is checked against its recorded
hash and file list, protected paths are refused, and changes are only staged.
"""
import argparse
import json
import sys
from pathlib import Path

import worker_worktree
from paths import TASKS
from task_store import TaskStore


def scoped_task(job_id):
    result = json.loads((TaskStore(TASKS).directory(job_id) / 'result.json').read_text(encoding='utf-8'))
    scoped = result.get('scoped') or {}
    if not scoped.get('collected') or not scoped.get('worktree'):
        raise ValueError('This task has no collected scoped patch.')
    return result, scoped


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    actions = parser.add_subparsers(dest='action', required=True)
    show = actions.add_parser('show', help='Summarize the collected patch')
    show.add_argument('job_id')
    apply = actions.add_parser('apply', help='Stage the patch in a repository (never commits)')
    apply.add_argument('job_id')
    apply.add_argument('--target', type=Path, help='Repository to change; defaults to the task source repository')
    apply.add_argument('--check', action='store_true', help='Only check that the patch applies')
    args = parser.parse_args(argv)
    result, scoped = scoped_task(args.job_id)
    collected = scoped['collected']
    if args.action == 'show':
        print(json.dumps({'job_id': args.job_id, 'status': result.get('status'),
                          'review_status': result.get('review_status'), 'repo': scoped['repo'],
                          'ref': scoped['ref'], 'base_commit': scoped['worktree']['base_commit'],
                          **{key: collected.get(key) for key in (
                              'patch_path', 'files', 'insertions', 'deletions', 'binary_files',
                              'protected_paths', 'empty')}}, indent=2))
        return 0
    if result.get('review_status') == 'rejected':
        raise ValueError('This task was rejected; its patch is not applied.')
    if collected.get('empty'):
        raise ValueError('The worker made no changes.')
    applied = worker_worktree.apply(scoped['worktree'], collected, args.target or scoped['repo'],
                                    check_only=args.check)
    print(json.dumps(dict(applied, job_id=args.job_id), indent=2))
    return 0


if __name__ == '__main__':
    try:
        sys.exit(main())
    except (OSError, ValueError, worker_worktree.WorktreeError) as exc:
        print(json.dumps({'status': 'refused', 'error': str(exc)}))
        sys.exit(1)
