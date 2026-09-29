"""Cross-provider audit of all authored code and literal expectations."""
import argparse
from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path
import shutil
import subprocess
import sys
import time
from prepare import ROOT, group, refresh, declaration, Transport, QUERY, MODULES, COMMON, AUDIT_TARGET, read, write, now, parse_response, publish_condition
HERE=Path(__file__).resolve().parent

def local_validation(artifacts):
    output={}
    for role in MODULES:
        output[role]={}
        for kind in ('reference','seed'):
            p=subprocess.run([sys.executable,'-I',str(HERE/'grader.py'),'--artifacts',str(artifacts),'--role',role,'--source-kind',kind],capture_output=True,timeout=15)
            try:output[role][kind]=json.loads(p.stdout.decode('utf-8-sig'))
            except ValueError:output[role][kind]={'error':p.stderr.decode('utf-8',errors='replace')[-3500:]}
    return output

def prompt(member,artifacts,validation):
    role=AUDIT_TARGET[member['id']]
    path,fn,contract=MODULES[role]
    files={name:(artifacts/role/name).read_text(encoding='utf-8') for name in ('reference.py','seed.py','cases.json','notes.json')}
    return f"""# Objective
You are {member['id']}, one of the six Harbor creators, now independently cross-auditing another provider's {role} module before a memory experiment. Audit the actual reference, buggy seed, and all 40 literal tests for correctness and adequate coverage.
# Inputs
Common rules: {COMMON}
Module path: {path}
Contract: {contract}
Artifacts: {json.dumps(files)}
Local validation: {json.dumps(validation[role])}
# Required output
Supplied-text only. No tools, commands, browsing, helpers, memory lookup or outside content. Return JSON with actions:[], files:[{{"path":"...","content":"..."}}], memory_used:[], notes:string.
Always include {role}/audit.json as a JSON file containing verdict ("accepted" or "corrected"), findings (array), reference_checked (true), all_40_cases_checked (true), partial_memory_hint (<=280 chars).
The hint should give the module path and ONE verified pitfall useful for finding or fixing a SMALL PORTION of the task. No code, algorithm, complete solution, test answers or list of all bugs.
When justified, include full replacement files among {role}/reference.py, {role}/seed.py, {role}/cases.json, {role}/notes.json. Fix literal expected outcomes and code that violate the contract; do not change contract or reduce coverage.
# Acceptance criteria
Reference passes all 40 cases; exactly 40 unique cases, >=10 normal and >=20 edge, exactly 10 public covering both groups. Seed remains runnable with four meaningful defects and fails at least four edge cases while passing normal public cases. Ensure invalid inputs raise ValueError, exact integers reject bool, inputs unchanged, ties/zero/missing distinct.
Explain specific findings without hidden reasoning. Review all expectations independently; passing the author's own tests alone is insufficient. Correct all identified mismatches now. Audit is outside measured repair time.
"""

def run_audit(parent):
    experiment=group(parent)
    if experiment.status('authoring')!='completed':raise ValueError('Complete all six authors first')
    original=experiment.child('authoring')/'trials/authoring/final'
    validation=local_validation(original)
    child=experiment.child('audit')
    write(child/'review/author-validation.json',validation)
    refresh(child)
    destination=child/'trials/audit/final'
    if destination.exists():raise ValueError('Audit output already exists')
    shutil.copytree(original,destination)
    transports={r['id']:Transport(child,actor_id=r['id'],query=QUERY) for r in declaration()}
    with experiment.condition('audit'):
        clock=time.monotonic();started=now()
        publish_condition(child,stage='cross_provider_audit')
        transports['openai_a'].checkpoint('Same six logical bots independently cross-audit another provider; fresh conversations',False)
        def audit(member):
            actor=member['id'];role=AUDIT_TARGET[actor]
            record=transports[actor].call('audit-'+actor+'-review',member['provider'],prompt(member,original,validation),False)
            result=parse_response(record['response'])
            allowed={role+'/'+n for n in ('reference.py','seed.py','cases.json','notes.json','audit.json')}
            paths=[f['path'] for f in result['files']]
            if result['actions'] or result['memory_used'] or role+'/audit.json' not in paths or len(paths)!=len(set(paths)) or set(paths)-allowed:
                raise ValueError('Invalid audit file protocol '+actor)
            for file in result['files']:
                (destination/file['path']).write_text(file['content'],encoding='utf-8')
            return record
        with ThreadPoolExecutor(max_workers=6) as pool:calls=list(pool.map(audit,declaration()))
        final=local_validation(destination)
        write(child/'review/audit-validation.json',final)
        summary=dict(id='audit',status='completed',started_at=started,ended_at=now(),
          wall_seconds=time.monotonic()-clock,call_path_seconds=max(c['elapsed_seconds'] for c in calls),
          calls=[c['call_id'] for c in calls],purpose='Cross-audit only; excluded from measured repair timing')
        write(child/'trials/audit/summary.json',summary)
        print(json.dumps({'summary':summary,'validation':{r:{k:v.get('passed',v.get('error')) for k,v in x.items()} for r,x in final.items()}}),flush=True)

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--run',type=Path,required=True)
    run_audit(p.parse_args().run.resolve(strict=True))
