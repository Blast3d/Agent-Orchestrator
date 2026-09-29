"""Create and cross-audit Harbor using the six measured contestant identities."""
import argparse
from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path
import sys
import tempfile
import time

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT/'app'),str(ROOT/'benchmarks/memory_control')]
from assignment_receipts import AssignmentReceipts
from experiment_runs import ExperimentRuns, publish_condition
from task_store import TaskStore
from usage_guard import Guard, file_lock
from quota_tui import run_grok
from transport import Transport, now, read, write, parse_response
from six_bot_roster import declaration
from spec import COMMON, MODULES, QUERY, AUDIT_TARGET

def refresh(run):
    def one(provider):
        try:
            if provider=='grok':
                result=run_grok(workspace=Path(tempfile.gettempdir())/'orchestrator-quota-grok')
                Guard().observe(result['windows'],'grok',complete=True,memberships={'grok':['grok-weekly']})
                return result
            return Guard().refresh(provider)
        except Exception as exc:
            return {'status':'error','reason':str(exc)}
    with ThreadPoolExecutor(max_workers=3) as pool:
        results=dict(zip(('codex','claude','grok'),pool.map(one,('codex','claude','grok'))))
    write(run/'review'/('allowance-'+str(time.time_ns())+'.json'),results)
    print(json.dumps({'allowance_refresh':{k:('error' if isinstance(v,dict) and
      (v.get('status')=='error' or v.get(k,{}).get('ok') is False) else 'collected')
      for k,v in results.items()}}),flush=True)
    return results

def initialize_assignments(child):
    receipts=AssignmentReceipts(TaskStore(ROOT/'runs/tasks'))
    project=read(child/'run.json')['project_id']
    index,lock=receipts._locations(project)
    with file_lock(lock):
        if index.exists():return
        if receipts._existing_assignment(project):
            raise RuntimeError('Existing assignment evidence must be reconciled before fresh initialization')
        write(index,{'schema_version':1,'project_id':project,'assignments':{}})

def group(parent):
    manifest=read(parent/'run.json')
    manifest['authorization']={'providers':['OpenAI','Anthropic','xAI'],
      'scope':'Synthetic Harbor benchmark only. Six bots author and cross-audit complete synthetic artifacts. Scored calls receive only frozen public fixture and, in warm condition, six approved partial audit memories. No private files, billable fallback, or helper bots.'}
    write(parent/'run.json',manifest)
    labels={'authoring':'Six-bot benchmark creation','audit':'Same six bots - cross-audit',
            'team-cold':'Six bots / memory disabled','team-warm':'Six bots / audited Brain hints'}
    result=ExperimentRuns(parent,'six_bot_memory',[dict(id=i,label=label,
        memory_mode='seeded' if i=='team-warm' else 'disabled',roster=declaration()) for i,label in labels.items()])
    for row in result.data['conditions']:initialize_assignments(result.child(row['id']))
    return result

def author_prompt(member):
    role=member['role']
    path,fn,contract=MODULES[role]
    return f"""# Objective
Create your one module of the synthetic Harbor medium coding benchmark. You are {member['id']}, role {role}. Six authors work independently. Follow the fixed contract, do not change it.
# Inputs
Common rules: {COMMON}
Target module: {path}
Contract: {contract}
# Required output
Use supplied text only, no tools, browsing, memory, helpers, or OS access. Return one JSON object with keys actions (empty array), files, memory_used (empty array), notes (string).
Exactly four files with path/content string fields:
{role}/reference.py - complete correct pure Python implementation, including function {fn}.
{role}/seed.py - runnable buggy implementation with four realistic independent defects, no TODO or unconditional failure. Pass ordinary cases and fail meaningful edge cases.
{role}/cases.json - JSON object with role and cases. Exactly 40 distinct literal test cases; each has name (unique string), args (positional arguments list), invalid (bool), expected (JSON value; null if invalid), group ("normal" or "edge"), public (bool). At least 10 normal and at least 20 edge cases. Exactly 10 public cases, including both groups. Remaining cases are withheld during repairs. Cover invalid input, zeros, empty, ties, permutations, normalization, and integer precision where relevant. No executable test code or generators.
{role}/notes.json - JSON object with defects (four descriptions), rationale, partial_memory_hint (at most 280 characters: module path and one subtle convention; no code, full solution, or enumeration of defects).
# Acceptance criteria
Reference must pass all expected results. Seed must pass ordinary public cases but fail at least four edge cases. All code is self-contained builtins only with same-module helpers, no imports, annotations needing imports, classes, decorators, module-level calls, private attributes, or I/O. No input mutation. Correct literal expected results are essential. Return full file contents with valid JSON escaping. Test creation is outside measured cold/warm time. Do not solve other modules.
"""

def run_authoring(parent):
    experiment=group(parent)
    child=experiment.child('authoring')
    refresh(child)
    transports={r['id']:Transport(child,actor_id=r['id'],query=QUERY) for r in declaration()}
    with experiment.condition('authoring'):
        started=now();clock=time.monotonic()
        publish_condition(child,stage='creating_fixture')
        transports['openai_a'].checkpoint('Six fixed bots author six independent frozen-contract benchmark modules',False)
        def author(member):
            actor,role=member['id'],member['role']
            record=transports[actor].call('authoring-'+actor+'-create',member['provider'],author_prompt(member),False)
            result=parse_response(record['response'])
            expected={role+'/'+name for name in ('reference.py','seed.py','cases.json','notes.json')}
            if result['actions'] or result['memory_used'] or len(result['files'])!=4 or {f['path'] for f in result['files']}!=expected:
                raise ValueError('Author output file contract failed: '+actor)
            for file in result['files']:
                target=child/'trials/authoring/final'/file['path']
                target.parent.mkdir(parents=True,exist_ok=True)
                target.write_text(file['content'],encoding='utf-8')
            return record
        with ThreadPoolExecutor(max_workers=6) as pool:
            calls=list(pool.map(author,declaration()))
        summary=dict(id='authoring',status='completed',started_at=started,ended_at=now(),
          wall_seconds=time.monotonic()-clock,call_path_seconds=max(c['elapsed_seconds'] for c in calls),
          calls=[c['call_id'] for c in calls],purpose='Creation only; excluded from repair timing')
        write(child/'trials/authoring/summary.json',summary)
        print(json.dumps(summary),flush=True)

if __name__=='__main__':
    parser=argparse.ArgumentParser()
    parser.add_argument('--run',type=Path,required=True)
    args=parser.parse_args()
    run_authoring(args.run.resolve(strict=True))
