"""Controller-owned literal-case evaluator with bounded candidate execution."""
import argparse
import copy
import importlib.util
import json
from pathlib import Path
import sys
HERE=Path(__file__).resolve().parent
sys.path.insert(0,str(HERE))
from spec import MODULES
legacy_spec=importlib.util.spec_from_file_location('bounded_grader',HERE.parent/'memory_control/grader.py')
bounded=importlib.util.module_from_spec(legacy_spec);legacy_spec.loader.exec_module(bounded)
bounded.ROLES={role:row[:2] for role,row in MODULES.items()}

def case_set(source,role):
    data=json.loads(Path(source).read_text(encoding='utf-8-sig'))
    rows=data['cases']
    if data.get('role')!=role or not isinstance(rows,list) or len(rows)!=40:
        raise ValueError('Expected 40 literal cases for '+role)
    names=set()
    for row in rows:
        if set(row)!={'name','args','invalid','expected','group','public'}:
            raise ValueError('Case fields mismatch')
        if not isinstance(row['name'],str) or not row['name'] or row['name'] in names:
            raise ValueError('Case names must be distinct')
        names.add(row['name'])
        if not isinstance(row['args'],list) or type(row['invalid']) is not bool or type(row['public']) is not bool or row['group'] not in ('normal','edge'):
            raise ValueError('Invalid case fields')
        if row['invalid'] and row['expected'] is not None:raise ValueError('Invalid cases expect null')
    if sum(c['public'] for c in rows)!=10 or {c['group'] for c in rows if c['public']}!={'normal','edge'}:
        raise ValueError('Exactly 10 public cases including both groups required')
    if sum(c['group']=='normal' for c in rows)<10 or sum(c['group']=='edge' for c in rows)<20:
        raise ValueError('Insufficient normal/edge coverage')
    return rows

def evaluate(source,role,rows,public=False):
    selected=[c for c in rows if not public or c['public']]
    try:fn=bounded.load_candidate(source,role)
    except Exception as exc:
        return dict(role=role,passed=0,total=len(selected),all_passed=False,
          cases=[dict(case=c['name'],group=c['group'],public=c['public'],passed=False,detail='candidate: '+str(exc)) for c in selected])
    results=[]
    for case in selected:
        values=copy.deepcopy(case['args']);before=repr(values);detail=None
        try:
            actual=fn(*values)
            passed=not case['invalid'] and bounded.same(actual,case['expected'])
            if not passed:detail='expected ValueError' if case['invalid'] else 'result mismatch'
        except Exception as exc:
            passed=case['invalid'] and isinstance(exc,ValueError)
            if not passed:detail=type(exc).__name__
        if repr(values)!=before:passed=False;detail='input mutated'
        results.append(dict(case=case['name'],group=case['group'],public=case['public'],passed=passed,detail=detail))
    return dict(role=role,passed=sum(c['passed'] for c in results),total=len(results),all_passed=all(c['passed'] for c in results),cases=results)

def grade(workspace,artifacts,role='all',public=False,source_kind=None):
    output=[]
    for key in MODULES if role=='all' else [role]:
        source=Path(artifacts)/key/(source_kind+'.py') if source_kind else Path(workspace)/MODULES[key][0]
        output.append(evaluate(source.read_text(encoding='utf-8-sig'),key,case_set(Path(artifacts)/key/'cases.json',key),public))
    return dict(passed=sum(r['passed'] for r in output),total=sum(r['total'] for r in output),
      all_passed=all(r['all_passed'] for r in output),roles=output)

if __name__=='__main__':
    p=argparse.ArgumentParser()
    p.add_argument('--workspace',type=Path)
    p.add_argument('--artifacts',type=Path,required=True)
    p.add_argument('--role',choices=['all']+list(MODULES),default='all')
    p.add_argument('--public-only',action='store_true')
    p.add_argument('--source-kind',choices=['reference','seed'])
    a=p.parse_args()
    print(json.dumps(grade(a.workspace,a.artifacts,a.role,a.public_only,a.source_kind)))
