"""Task-scoped skill plans using the same flow as the local dashboard."""
import argparse
import json
from pathlib import Path

from paths import ROOT
import skill_flow as flow


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, default=ROOT)
    subs = parser.add_subparsers(dest='action', required=True)
    for action in ('catalog', 'plans', 'recommend', 'review', 'show', 'context', 'use', 'feedback'):
        sub = subs.add_parser(action)
        sub.add_argument('--project', required=True)
        if action in ('show', 'context', 'review'):
            sub.add_argument('--plan', required=True)
        if action in ('use', 'feedback'):
            sub.add_argument('--job', required=True)
        if action == 'recommend':
            sub.add_argument('--task', required=True)
            sub.add_argument('--jev', action='store_true', help='Explicit billed JEV recommendation; default is manual')
        if action == 'review':
            sub.add_argument('--skill', action='append', required=True)
            sub.add_argument('--manifest', required=True)
        if action in ('review', 'feedback'):
            sub.add_argument('--reviewer', required=True)
            sub.add_argument('--note', required=True)
        if action == 'feedback':
            sub.add_argument('--rating', choices=['helped', 'neutral', 'harmful'], required=True)
            sub.add_argument('--source', required=True)
            sub.add_argument('--context', required=True)
    args = parser.parse_args(argv)
    try:
        if args.action == 'catalog':
            result = flow.catalog(args.root, args.project)
        elif args.action == 'plans':
            result = flow.plans(args.root, args.project)
        elif args.action == 'recommend':
            result = flow.recommend(args.root, args.project, args.task, jev=args.jev)
        elif args.action == 'review':
            result = flow.review(args.root, args.project, args.plan, args.skill, args.manifest, args.reviewer, args.note)
        elif args.action == 'show':
            result = flow.get_plan(args.root, args.project, args.plan)
        elif args.action == 'context':
            result = flow.delivery(args.root, args.project, args.plan)
        elif args.action == 'use':
            result = flow.use_evidence(args.root, args.project, args.job)
        else:
            result = flow.feedback(args.root, args.project, args.job, args.rating, args.reviewer, args.note, args.source, args.context)
        print(json.dumps(result, ensure_ascii=True))
        return 0
    except (OSError, ValueError, KeyError) as exc:
        print(json.dumps({'status': 'error', 'error': str(exc)}))
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
