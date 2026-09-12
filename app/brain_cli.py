"""Local brain commands available to ASTRA, Fable and the human operator."""
import argparse
import json
from pathlib import Path
import sys

from paths import ROOT
from brain_store import BrainStore
from storage_budget import StorageLimitError


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root',type=Path,default=ROOT)
    sub=parser.add_subparsers(dest='command',required=True)
    for name in ('init','status'): sub.add_parser(name)
    propose=sub.add_parser('propose');propose.add_argument('--file',type=Path,required=True)
    listing=sub.add_parser('list');listing.add_argument('--project');listing.add_argument('--user',default='local');listing.add_argument('--status');listing.add_argument('--limit',type=int,default=100)
    get=sub.add_parser('get');get.add_argument('memory_id')
    approve=sub.add_parser('approve');approve.add_argument('memory_id');approve.add_argument('--reviewer',required=True);approve.add_argument('--note',required=True)
    forget=sub.add_parser('forget');forget.add_argument('memory_id');forget.add_argument('--actor',required=True);forget.add_argument('--reason',required=True)
    replace=sub.add_parser('supersede');replace.add_argument('old_id');replace.add_argument('new_id');replace.add_argument('--actor',required=True);replace.add_argument('--reason',required=True)
    link=sub.add_parser('relate');link.add_argument('source_id');link.add_argument('target_id');link.add_argument('--relation',required=True);link.add_argument('--actor',required=True)
    search=sub.add_parser('search');search.add_argument('query');search.add_argument('--project',required=True);search.add_argument('--user',default='local');search.add_argument('--limit',type=int,default=6);search.add_argument('--max-chars',type=int,default=8000);search.add_argument('--hops',type=int,default=1);search.add_argument('--context-only',action='store_true')
    capture=sub.add_parser('capture');capture.add_argument('--run',required=True);capture.add_argument('--file',type=Path,required=True)
    capture.add_argument('--owner',required=True);capture.add_argument('--session',required=True);capture.add_argument('--generation',type=int,required=True)
    capture.add_argument('--reviewer',required=True);capture.add_argument('--note',required=True)
    usage=sub.add_parser('use');usage.add_argument('job_id')
    feedback=sub.add_parser('feedback');feedback.add_argument('job_id');feedback.add_argument('--rating',choices=['helped','neutral','harmful'],required=True)
    feedback.add_argument('--reviewer',required=True);feedback.add_argument('--note',required=True)
    for name in ('export','vault','changes'):
        p=sub.add_parser(name);p.add_argument('--project',required=True);p.add_argument('--user',default='local')
        if name=='changes':p.add_argument('--after',type=int,default=0)
    dashboard=sub.add_parser('dashboard');dashboard.add_argument('--port',type=int,default=0);dashboard.add_argument('--open',action='store_true')
    args=parser.parse_args(argv)
    try:
        if args.command=='dashboard':
            from brain_dashboard import main as dashboard_main
            return dashboard_main(['--root',str(args.root),'--port',str(args.port)]+(['--open'] if args.open else []))
        brain=BrainStore(args.root)
        if args.command in ('init','status'):result=brain.status()
        elif args.command=='propose':
            if args.file.stat().st_size>16384:raise ValueError('Candidate file exceeds 16 KiB')
            result=brain.propose(json.loads(args.file.read_text(encoding='utf-8-sig')))
        elif args.command=='list':result=brain.list_memories(args.project,args.user,args.status,args.limit)
        elif args.command=='get':result=brain.get(args.memory_id)
        elif args.command=='capture':
            from memory_bundle import capture_run, read_json
            result=capture_run(args.root,args.run,read_json(args.file.resolve(),args.root.resolve()),owner=args.owner,
                               session=args.session,generation=args.generation,reviewer=args.reviewer,note=args.note)
        elif args.command in ('use','feedback'):
            from task_store import TaskStore
            from memory_usage import summary, record_feedback
            from memory_bundle import read_json
            tasks=TaskStore(args.root/'runs/tasks')
            result=(record_feedback(tasks,args.job_id,args.rating,args.reviewer,args.note) if args.command=='feedback' else
                    summary(read_json(tasks.directory(args.job_id)/'result.json',args.root.resolve(),8*1024**2)))
        elif args.command=='approve':result=brain.approve(args.memory_id,args.reviewer,args.note)
        elif args.command=='forget':result=brain.forget(args.memory_id,args.actor,args.reason)
        elif args.command=='supersede':result=brain.supersede(args.old_id,args.new_id,args.actor,args.reason)
        elif args.command=='relate':result=brain.relate(args.source_id,args.target_id,args.relation,args.actor)
        elif args.command=='search':
            result=brain.search(args.query,args.project,args.user,args.limit,args.max_chars,args.hops)
            if args.context_only:
                print(result['context']);return 0
        elif args.command=='export':result=brain.export(args.project,args.user)
        elif args.command=='vault':result=brain.vault(args.project,args.user)
        elif args.command=='changes':result=brain.changes(args.project,args.user,args.after)
        print(json.dumps(result,ensure_ascii=True));return 0
    except (ValueError,OSError,StorageLimitError) as exc:
        print(json.dumps({'status':'held' if isinstance(exc,StorageLimitError) else 'error','error':str(exc) if isinstance(exc,(ValueError,StorageLimitError)) else type(exc).__name__}));return 2


if __name__=='__main__':
    raise SystemExit(main())
