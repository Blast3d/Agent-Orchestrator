"""Generate a contribution audit or attach reviewed attribution to a saved task."""
import argparse
import json
from pathlib import Path

from contributions import build_report, write_report
from paths import TASKS
from task_store import TaskStore, write_json
from usage_guard import file_lock

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--ledger', type=Path, help='Reviewed JSON work allocation ledger')
    parser.add_argument('--output-dir', type=Path, help='Project run folder for a team audit')
    parser.add_argument('--task', help='Attach a ledger to this saved task, or refresh its automatic audit')
    parser.add_argument('--require-complete', action='store_true', help='Hold final delivery if accepted work attribution is missing')
    args = parser.parse_args()
    if args.task:
        if args.output_dir:
            parser.error('--task cannot be combined with --output-dir')
        ledger = json.loads(args.ledger.read_text(encoding='utf-8')) if args.ledger else None
        result = TaskStore(TASKS).audit(args.task, ledger=ledger)
        report = json.loads(Path(result['contribution_audit']['json']).read_text(encoding='utf-8'))
    else:
        if not args.ledger or not args.output_dir:
            parser.error('A team audit requires --ledger and --output-dir')
        ledger = json.loads(args.ledger.read_text(encoding='utf-8'))
        report = build_report(ledger)
        # Always save the honest incomplete audit; never silently invent credits.
        write_report(args.output_dir, ledger)
        manifest = args.output_dir / 'run.json'
        if manifest.is_file():
            with file_lock(args.output_dir / 'run.lock'):
                run = json.loads(manifest.read_text(encoding='utf-8'))
                if run.get('run_id') == report['scope_id']:
                    run['contribution_audit'] = {'required': True, 'status': report['status'],
                        'ledger': str(args.ledger.resolve()), 'report': 'contribution-audit.md',
                        'attribution_complete': report['attribution_complete']}
                    write_json(manifest, run)
        try:
            from project_visuals import refresh_views
            refresh_views(args.output_dir / 'contribution-audit.json')
            visual_error = None
        except Exception as exc:
            # Preserve the completed report if the optional picture cannot update.
            visual_error = type(exc).__name__
    complete = report['attribution_complete'] and report['accepted_weight'] > 0
    print(json.dumps({'scope_id': report['scope_id'], 'attribution_complete': report['attribution_complete'],
                      'accepted_weight': report['accepted_weight'], 'by_agent': report['by_agent'],
                      'delivery_held': args.require_complete and not complete,
                      'visual_update_error': visual_error if not args.task else None}))
    return 2 if args.require_complete and not complete else 0

if __name__ == '__main__':
    raise SystemExit(main())
