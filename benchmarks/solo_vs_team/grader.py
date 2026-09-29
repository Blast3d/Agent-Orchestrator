"""Deterministic evaluator. Never supply this file to benchmark participants."""
import argparse
import ast
import builtins
import copy
import hashlib
import json
from pathlib import Path
import random


def integer(value):
    return type(value) is int


def canonical(value):
    return isinstance(value, str) and bool(value) and value == value.strip()


def reference_small(events):
    if not isinstance(events, list):
        raise ValueError()
    rows = {}
    for event in events:
        if not isinstance(event, dict) or not all(k in event for k in ('provider', 'status', 'input_tokens', 'output_tokens')):
            raise ValueError()
        provider, status = event['provider'], event['status']
        counts = event['input_tokens'], event['output_tokens']
        if not isinstance(provider, str) or not provider.strip() or not isinstance(status, str) or status not in ('completed', 'failed', 'skipped'):
            raise ValueError()
        if any(n is not None and (not integer(n) or n < 0) for n in counts):
            raise ValueError()
        if status == 'skipped':
            continue
        provider = provider.strip().casefold()
        row = rows.setdefault(provider, dict(provider=provider, attempts=0, input_tokens=None, output_tokens=None, complete_measurements=0))
        row['attempts'] += 1
        for key in ('input_tokens', 'output_tokens'):
            if event[key] is not None:
                row[key] = (row[key] or 0) + event[key]
        row['complete_measurements'] += all(n is not None for n in counts)
    return [rows[key] for key in sorted(rows)]


def reference_medium(jobs, budgets, completed=()):
    if not isinstance(jobs, list) or not isinstance(budgets, dict) or not isinstance(completed, (list, tuple)):
        raise ValueError()
    if any(not canonical(p) or not integer(n) or n < 0 for p, n in budgets.items()) or any(not canonical(i) for i in completed):
        raise ValueError()
    by_id, done = {}, set(completed)
    for job in jobs:
        if not isinstance(job, dict) or not all(k in job for k in ('id', 'provider', 'estimated_tokens', 'priority', 'depends_on')):
            raise ValueError()
        key, provider, cost, priority, deps = (job[k] for k in ('id', 'provider', 'estimated_tokens', 'priority', 'depends_on'))
        if not canonical(key) or key in by_id or not canonical(provider) or provider not in budgets:
            raise ValueError()
        if not integer(cost) or cost < 0 or not integer(priority) or not isinstance(deps, list) or any(not canonical(d) or d == key for d in deps):
            raise ValueError()
        by_id[key] = job
    if any(d not in by_id and d not in done for job in jobs for d in job['depends_on']):
        raise ValueError()
    # Kahn validation is independent of the selection loop and budget feasibility.
    unresolved = set(by_id) - done
    satisfied = set(done)
    while unresolved:
        layer = {i for i in unresolved if set(by_id[i]['depends_on']) <= satisfied}
        if not layer:
            raise ValueError()
        satisfied.update(layer)
        unresolved.difference_update(layer)
    remaining, scheduled = dict(budgets), []
    pending = set(by_id) - done
    while True:
        eligible = [i for i in pending if set(by_id[i]['depends_on']) <= done and by_id[i]['estimated_tokens'] <= remaining[by_id[i]['provider']]]
        if not eligible:
            break
        key = min(eligible, key=lambda i: (-by_id[i]['priority'], i))
        remaining[by_id[key]['provider']] -= by_id[key]['estimated_tokens']
        scheduled.append(key)
        pending.remove(key)
        done.add(key)
    blocked = {key: 'budget' if set(by_id[key]['depends_on']) <= done else 'dependency' for key in sorted(pending)}
    return dict(scheduled=scheduled, remaining=remaining, blocked=blocked)


def E(provider='p', status='completed', inputs=1, outputs=2):
    return dict(provider=provider, status=status, input_tokens=inputs, output_tokens=outputs)


def J(key, provider='p', cost=1, priority=0, deps=()):
    return dict(id=key, provider=provider, estimated_tokens=cost, priority=priority, depends_on=list(deps))


def cases(task):
    rows = []
    def add(name, args, public=False, expected=None, invalid=False, group='edge'):
        reference = reference_small if task == 'small' else reference_medium
        if invalid:
            try:
                reference(*copy.deepcopy(args))
            except ValueError:
                pass
            else:
                raise AssertionError('Invalid fixture is accepted: ' + name)
        else:
            actual = reference(*copy.deepcopy(args))
            if expected is not None:
                assert actual == expected, (name, actual, expected)
            expected = actual
        rows.append(dict(name=name, args=args, public=public, expected=expected, invalid=invalid, group=group))
    if task == 'small':
        add('empty', [[]], True, [])
        add('public-zero', [[E(' OpenAI ', inputs=0)]], True, [dict(provider='openai',attempts=1,input_tokens=0,output_tokens=2,complete_measurements=1)])
        add('public-unknown-failure', [[E('xAI', 'failed', None, None)]], True, [dict(provider='xai',attempts=1,input_tokens=None,output_tokens=None,complete_measurements=0)])
        add('public-invalid-skipped', [[E(status='skipped', inputs=-1)]], True, invalid=True)
        add('merge-case-and-whitespace', [[E(' OpenAI '),E('OPENAI','failed',3,4),E('openai','skipped',900,900)]])
        add('partial-measurements', [[E(inputs=None),E(inputs=4,outputs=None),E(inputs=None,outputs=None)]])
        add('known-zero-only', [[E(inputs=0,outputs=None)]])
        add('skipped-only', [[E('other','skipped')]])
        add('provider-sort', [[E('z'),E('A'),E('b')]], group='normal')
        add('unicode-casefold', [[E('Straße'),E('STRASSE')]])
        for value in (None,{},'events',True):
            add('invalid-container-'+str(type(value).__name__),[value],invalid=True)
        for key,values in {'provider':['',' ',None,3,[]],'status':['unknown',None,[]], 'input_tokens':[-1,True,1.5,'3'], 'output_tokens':[-1,False,0.0,'0']}.items():
            for index,value in enumerate(values):
                event=E();event[key]=value
                add('invalid-'+key+'-'+str(index),[[E(),event]],invalid=True)
        for key in E():
            event=E(); del event[key]
            add('missing-'+key,[[event]],invalid=True)
        add('invalid-event',[[E(),None]],invalid=True)
        rng=random.Random(513)
        for i in range(12):
            events=[E(rng.choice([' a ','A','B']),rng.choice(['completed','failed','skipped']),rng.choice([None,0,1,8]),rng.choice([None,0,2,7])) for _ in range(12)]
            add('mixed-'+str(i),[events])
    else:
        add('empty',[[],{}],True,dict(scheduled=[],remaining={},blocked={}))
        add('public-dependency-order',[[J('b',cost=2,priority=2,deps=['a']),J('a',priority=1)],{'p':3}],True,dict(scheduled=['a','b'],remaining={'p':0},blocked={}),group='normal')
        add('public-budget-skip',[[J('x',cost=5,priority=9),J('y')],{'p':1}],True,dict(scheduled=['y'],remaining={'p':0},blocked={'x':'budget'}))
        add('public-cycle',[[J('a',cost=0,deps=['b']),J('b',cost=0,deps=['a'])],{'p':0}],True,invalid=True)
        add('public-completed-breaks-cycle',[[J('a',cost=0,deps=['b']),J('b',cost=0,deps=['a'])],{'p':0},['a']],True,dict(scheduled=['b'],remaining={'p':0},blocked={}))
        add('priority-over-order',[[J('a'),J('z',priority=1)],{'p':1}])
        add('lexical-tie',[[J('z'),J('A'),J('a')],{'p':2}])
        add('recompute-priority',[[J('a',cost=0,priority=2),J('b',cost=0,priority=1),J('z',cost=0,priority=9,deps=['a'])],{'p':0}],expected=dict(scheduled=['a','z','b'],remaining={'p':0},blocked={}))
        add('blocked-final-reasons',[[J('a',cost=5),J('b',deps=['a']),J('c',cost=0),J('d',cost=8,deps=['c'])],{'p':1}])
        add('independent-providers',[[J('a','p',4),J('b','q',1),J('c','p',1,2)],{'p':1,'q':2,'unused':9}])
        add('duplicate-deps-and-completed',[[J('a',deps=['external','external'])],{'p':1},['external','external']])
        add('done-no-consumption',[[J('a',cost=100),J('b',deps=['a'])],{'p':1},['a']])
        add('negative-priority',[[J('a',priority=-3),J('b',priority=-1)],{'p':1}])
        add('unaffordable-cycle',[[J('a',cost=10,deps=['b']),J('b',cost=10,deps=['a'])],{'p':0}],invalid=True)
        add('unknown-dependency',[[J('a',deps=['missing'])],{'p':1}],invalid=True)
        add('done-self-cycle',[[J('a',deps=['a'])],{'p':1},['a']],invalid=True)
        add('duplicate-id',[[J('a'),J('a')],{'p':1}],invalid=True)
        add('done-invalid-provider',[[J('a','missing')],{'p':1},['a']],invalid=True)
        for key,values in {'id':['',' a',False,[]], 'provider':['missing','',None,[]], 'estimated_tokens':[-1,True,1.5,'1'], 'priority':[None,True,2.0,'2'], 'depends_on':['a',None,[None],[' ']]}.items():
            for index,value in enumerate(values):
                job=J('b');job[key]=value
                add('invalid-'+key+'-'+str(index),[[J('a'),job],{'p':2}],invalid=True)
        for key in J('a'):
            job=J('a');del job[key]
            add('missing-'+key,[[job],{'p':2}],invalid=True)
        for index,budgets in enumerate((None,[],{'p':True},{'p':-1},{' p':2},{'p':1.5})):
            add('invalid-budgets-'+str(index),[[],budgets],invalid=True)
        for index,completed in enumerate(('a',None,{},[' '],[True])):
            add('invalid-completed-'+str(index),[[],{},completed],invalid=True)
        for index,jobs in enumerate((None,{},'jobs',[None])):
            add('invalid-jobs-'+str(index),[jobs,{}],invalid=True)
        rng=random.Random(20260913)
        for i in range(24):
            jobs=[]
            for n in range(9):
                deps=[str(d) for d in range(n) if rng.random()<.17]
                jobs.append(J(str(n),rng.choice(['p','q']),rng.randrange(5),rng.randrange(-2,4),deps))
            budgets={'p':rng.randrange(10),'q':rng.randrange(10),'unused':1}
            rng.shuffle(jobs)
            add('dag-'+str(i),[jobs,budgets])
            add('permutation-'+str(i),[list(reversed(jobs)),budgets])
    return rows


def load_candidate(source, task):
    tree=ast.parse(source)
    allowed_imports={'collections','heapq','copy','functools','itertools','math','typing'}
    forbidden={'open','eval','exec','compile','input','globals','locals','vars','getattr','setattr','delattr','__import__'}
    for node in ast.walk(tree):
        if isinstance(node,ast.Import) and any(alias.name not in allowed_imports for alias in node.names):
            raise ValueError('unsupported import')
        if isinstance(node,ast.ImportFrom) and (node.level or node.module not in allowed_imports):
            raise ValueError('unsupported import')
        if isinstance(node,ast.Attribute) and node.attr.startswith('__'):
            raise ValueError('dunder access')
        if isinstance(node,ast.Name) and node.id in forbidden:
            raise ValueError('unsupported builtin')
    def safe_import(name,*args,**kwargs):
        if name not in allowed_imports:
            raise ValueError('unsupported import')
        return __import__(name,*args,**kwargs)
    safe={key:value for key,value in vars(builtins).items() if key not in forbidden}
    safe['__import__']=safe_import
    namespace={'__builtins__':safe,'__name__':'candidate'}
    exec(compile(tree,'<candidate>','exec'),namespace)
    return namespace['summarize_usage' if task=='small' else 'allocate_jobs']


def evaluate(function, task, public_only=False):
    results=[]
    for case in cases(task):
        if public_only and not case['public']:
            continue
        args=copy.deepcopy(case['args']); before=copy.deepcopy(args)
        detail=None
        try:
            output=function(*args)
            passed=not case['invalid'] and output==case['expected']
            if not passed: detail='expected ValueError' if case['invalid'] else 'result mismatch'
            if task=='medium' and passed and list(output['blocked']) != sorted(output['blocked']):
                passed=False;detail='blocked order mismatch'
        except Exception as error:
            passed=case['invalid'] and isinstance(error,ValueError)
            if not passed:detail=type(error).__name__
        if args != before:
            passed=False;detail='input mutated'
        results.append({'case':case['name'],'public':case['public'],'group':case['group'],'passed':passed,'detail':detail})
    return {'task':task,'passed':sum(row['passed'] for row in results),'total':len(results),'all_passed':all(row['passed'] for row in results),'cases':results}


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--task',choices=['small','medium'],required=True)
    parser.add_argument('--candidate',type=Path)
    parser.add_argument('--public-only',action='store_true')
    parser.add_argument('--self-test',action='store_true')
    args=parser.parse_args()
    if args.self_test:
        reference=reference_small if args.task=='small' else reference_medium
        good=evaluate(reference,args.task)
        source=(Path(__file__).parent/'fixtures'/f'{args.task}.py').read_text(encoding='utf-8')
        seeded=evaluate(load_candidate(source,args.task),args.task)
        assert good['all_passed'] and not seeded['all_passed']
        print(json.dumps({'reference':good['passed'],'total':good['total'],'seeded_passed':seeded['passed'],'grader_sha256':hashlib.sha256(Path(__file__).read_bytes()).hexdigest()}))
        return
    try:
        source=args.candidate.read_text(encoding='utf-8')
        result=evaluate(load_candidate(source,args.task),args.task,args.public_only)
    except Exception as error:
        selected=[case for case in cases(args.task) if not args.public_only or case['public']]
        result={'task':args.task,'passed':0,'total':len(selected),'all_passed':False,'load_error':type(error).__name__,
                'cases':[{'case':case['name'],'public':case['public'],'group':case['group'],'passed':False,
                          'detail':'candidate unavailable: '+type(error).__name__} for case in selected]}
    print(json.dumps(result))


if __name__=='__main__':
    main()
