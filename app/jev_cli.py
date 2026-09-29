"""Inspect or use the optional Jev decision connector through OpenRouter."""
import argparse
import hashlib
import json
from pathlib import Path

from paths import ROOT
import jev_openrouter
from jev_profiles import PROFILES


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, default=ROOT)
    commands = parser.add_subparsers(dest='command', required=True)
    commands.add_parser('status')
    configure = commands.add_parser('configure', help='Save explicit project/purpose settings; never saves the API key.')
    configure.add_argument('--project', action='append', required=True)
    configure.add_argument('--purpose', action='append', choices=jev_openrouter.PURPOSES, required=True)
    configure.add_argument('--enable', action='store_true', help='Authorize API calls for these projects and purposes.')
    configure.add_argument('--env-file', default='.env', help='Local credential file; may point at OpenWhispr .env. No key is copied.')
    configure.add_argument('--daily-calls', type=int, choices=range(1, 1001), default=100, metavar='1..1000')
    configure.add_argument('--cache', action='store_true', help='Reuse unchanged decisions after scope and source validation.')
    configure.add_argument('--cache-ttl', type=int, default=3600, metavar='60..86400')
    commands.add_parser('disable')
    route = commands.add_parser('route')
    route.add_argument('--project', required=True)
    route.add_argument('--task', required=True)
    route.add_argument('--workers-file', type=Path, required=True,
                       help='JSON map of currently permitted worker IDs to capability descriptions.')
    recall = commands.add_parser('recall')
    recall.add_argument('query')
    recall.add_argument('--project', required=True)
    recall.add_argument('--strategy', choices=['auto', 'keyword', 'graph', 'semantic'], default='auto')
    recall.add_argument('--profile', choices=PROFILES, default='general')
    recall.add_argument('--depth', choices=['compact','balanced','deep'], default='compact')
    packet = commands.add_parser('packet', help='Prepare bounded memory for a named recipient; does not deliver or launch it.')
    packet.add_argument('query')
    packet.add_argument('--project', required=True)
    packet.add_argument('--profile', choices=PROFILES, default='general')
    packet.add_argument('--depth', choices=['compact','balanced','deep'], default='compact')
    packet.add_argument('--recipient', required=True)
    smoke = commands.add_parser('smoke')
    smoke.add_argument('--project', default='jev-demo')
    from jev_workflow_cli import add_arguments, execute
    add_arguments(commands)
    args = parser.parse_args(argv)
    try:
        if args.command == 'status':
            result = jev_openrouter.load_config(args.root)
        elif args.command in ('workflows', 'workflow'):
            result = execute(args, args.root)
        elif args.command in ('configure', 'disable'):
            from brain_store import scope
            from usage_guard import write_json, file_lock
            from storage_budget import StorageBudget
            settings = {'schema_version': 1, 'enabled': False}
            if args.command == 'configure':
                if not 60 <= args.cache_ttl <= 86400:
                    raise ValueError('Cache lifetime must be between 60 and 86400 seconds.')
                # Validate the credential location before replacing a working config.
                jev_openrouter._env_path(args.root, args.env_file)
                settings.update(enabled=args.enable, model=jev_openrouter.MODEL,
                    api_key_env='OPENROUTER_API_KEY', env_file=args.env_file,
                    authorized_projects=sorted({scope(p) for p in args.project}),
                    purposes=sorted(set(args.purpose)), timeout_seconds=10,
                    max_requests_per_day=args.daily_calls, min_confidence=.8,
                    cache_enabled=args.cache, cache_ttl_seconds=args.cache_ttl)
            path = jev_openrouter._path(args.root, 'jev-config.json')
            with StorageBudget(args.root).allocation(32768, kind='task'):
                with file_lock(jev_openrouter._path(args.root, 'jev-config.lock')):
                    write_json(path, settings)
            result = jev_openrouter.load_config(args.root)
        elif args.command == 'route':
            from jev_routing import advise
            with args.workers_file.open('rb') as handle:
                raw = handle.read(16385)
            if len(raw) > 16384:
                raise ValueError('Worker descriptions exceed 16 KiB.')
            result = advise(args.root, args.project, args.task, json.loads(raw.decode('utf-8-sig')))
        elif args.command in ('recall', 'packet'):
            from brain_store import BrainStore
            recipient = None
            if args.command == 'packet':
                from brain_store import scope
                recipient = scope(args.recipient, 'Recipient')
            result = BrainStore(args.root).search(args.query, args.project,
                strategy=getattr(args, 'strategy', 'auto'), profile=args.profile, depth=args.depth)
            if args.command == 'packet':
                from brain_retrieval_metadata import public_retrieval
                result = {'recipient': recipient, 'project_id': result['project_id'], 'profile': args.profile,
                    'context': result['context'], 'ids': [item['id'] for item in result['results']],
                    'sha256': hashlib.sha256(result['context'].encode('utf-8')).hexdigest(),
                    'trace_id': result.get('trace_id'), 'retrieval': public_retrieval(result.get('retrieval')),
                    'delivery_status': 'prepared_only'}
        else:
            result = jev_openrouter.evaluate(args.root, args.project, 'route',
                {'request': 'Please inspect a small code patch for defects.'}, {
                    'role': {'type': 'choice', 'instructions': 'Which role fits the request?',
                             'criteria': {'review': 'Inspect existing work for defects.', 'write': 'Create new prose.'}}
                })
            result['synthetic_input'] = True
        print(json.dumps(result, ensure_ascii=True, allow_nan=False))
        return 0 if (args.command in ('status', 'recall', 'packet', 'configure', 'disable')
                     or result.get('status') in ('ok', 'partial', 'low_confidence', 'insufficient_evidence')) else 2
    except (OSError, ValueError, UnicodeError):
        print(json.dumps({'status': 'error', 'reason': 'Input could not be read or validated. No credential is included in this error.'}))
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
