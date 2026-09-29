"""Controller-only deterministic Tern evaluator; never expose it to contestants.

Run through python -I with a controller deadline. AST checks reduce accidental
side effects; they are not an operating-system security sandbox.
"""
import argparse
import ast
import builtins
import copy
import hashlib
import json
from pathlib import Path
import random


BASE = Path(__file__).resolve().parent
ROLES = {
    'aggregation': ('tern/usage/ledger.py', 'aggregate_attempts'),
    'allocation': ('tern/dispatch/allocator.py', 'allocate_slots'),
    'summary': ('tern/reporting/summary.py', 'render_report'),
}


def integer(value, minimum=0):
    return type(value) is int and value >= minimum


def normalized(value):
    if not isinstance(value, str) or not value.strip():
        raise ValueError('invalid ID')
    return value.strip().casefold()


def canonical(value):
    return normalized(value) == value


def fields(record, required):
    if not isinstance(record, dict) or any(key not in record for key in required):
        raise ValueError('missing record fields')


def reference_aggregation(events):
    if not isinstance(events, list):
        raise ValueError('events must be a list')
    versions, latest = {}, {}
    for event in events:
        fields(event, ('id', 'revision', 'provider', 'status', 'tokens'))
        key, provider = normalized(event['id']), normalized(event['provider'])
        revision, status, tokens = event['revision'], event['status'], event['tokens']
        if not integer(revision) or status not in ('ok', 'error', 'cancelled'):
            raise ValueError('invalid event')
        if tokens is not None and not integer(tokens):
            raise ValueError('invalid tokens')
        value = (provider, status, tokens)
        pair = (key, revision)
        if pair in versions and versions[pair] != value:
            raise ValueError('conflicting revision')
        versions[pair] = value
        if key not in latest or revision > latest[key][0]:
            latest[key] = (revision, value)
    totals = {}
    for _, (provider, status, tokens) in latest.values():
        if status == 'cancelled':
            continue
        row = totals.setdefault(provider, dict(provider=provider, attempts=0,
            failures=0, tokens=None, unknown_attempts=0))
        row['attempts'] += 1
        row['failures'] += int(status == 'error')
        if tokens is None:
            row['unknown_attempts'] += 1
        else:
            row['tokens'] = (0 if row['tokens'] is None else row['tokens']) + tokens
    return [totals[key] for key in sorted(totals)]


def reference_allocation(requests, slots):
    if not isinstance(requests, list) or not integer(slots):
        raise ValueError('invalid allocation input')
    weights, limits = {}, {}
    for request in requests:
        fields(request, ('id', 'weight', 'limit'))
        key = normalized(request['id'])
        weight, limit = request['weight'], request['limit']
        if key in weights or not integer(weight, 1) or not integer(limit):
            raise ValueError('invalid request')
        weights[key], limits[key] = weight, limit
    assigned = {key: 0 for key in weights}
    active = {key for key in weights if limits[key] > 0}
    remaining = slots
    while remaining and active:
        total = sum(weights[key] for key in active)
        capped = {key for key in active if limits[key] * total <= remaining * weights[key]}
        if capped:
            for key in capped:
                assigned[key] = limits[key]
                remaining -= limits[key]
            active -= capped
            continue
        for key in active:
            assigned[key] = remaining * weights[key] // total
        spare = remaining - sum(assigned[key] for key in active)
        ranked = sorted(active, key=lambda key: (-(remaining * weights[key] % total), key))
        for key in ranked[:spare]:
            assigned[key] += 1
        remaining = 0
    return {'allocations': [{'id': key, 'slots': assigned[key]} for key in sorted(weights)],
            'remaining': remaining}


def reference_summary(usage, plan):
    if not isinstance(usage, list):
        raise ValueError('usage must be a list')
    fields(plan, ('allocations', 'remaining'))
    if not isinstance(plan['allocations'], list) or not integer(plan['remaining']):
        raise ValueError('invalid plan')
    by_provider, allocated = {}, {}
    for row in usage:
        fields(row, ('provider', 'attempts', 'failures', 'tokens', 'unknown_attempts'))
        key = row['provider']
        if not canonical(key) or key in by_provider or not integer(row['attempts'], 1):
            raise ValueError('invalid usage')
        if any(not integer(row[k]) or row[k] > row['attempts'] for k in ('failures', 'unknown_attempts')):
            raise ValueError('invalid counts')
        if row['tokens'] is not None and not integer(row['tokens']):
            raise ValueError('invalid tokens')
        if (row['tokens'] is None) != (row['unknown_attempts'] == row['attempts']):
            raise ValueError('inconsistent measurement')
        by_provider[key] = row
    for row in plan['allocations']:
        fields(row, ('id', 'slots'))
        key = row['id']
        if not canonical(key) or key in allocated or not integer(row['slots']):
            raise ValueError('invalid allocation row')
        allocated[key] = row['slots']

    def marker(attempts, unknown, known):
        if attempts == 0:
            return '-'
        if attempts == unknown:
            return '?'
        return str(known) + ('+' if unknown else '')

    def escaped(key):
        return key.replace('\\', '\\\\').replace('\t', '\\t').replace('\r', '\\r').replace('\n', '\\n')

    lines = ['provider\tattempts\tfailures\ttokens\tslots']
    attempts = failures = unknown = known = 0
    for key in sorted(set(by_provider) | set(allocated)):
        row = by_provider.get(key, dict(attempts=0, failures=0, tokens=None, unknown_attempts=0))
        n = 0 if row['tokens'] is None else row['tokens']
        lines.append('\t'.join([escaped(key), str(row['attempts']), str(row['failures']),
            marker(row['attempts'], row['unknown_attempts'], n), str(allocated.get(key, 0))]))
        attempts += row['attempts']
        failures += row['failures']
        unknown += row['unknown_attempts']
        known += n
    lines.append('\t'.join(['TOTAL', str(attempts), str(failures), marker(attempts, unknown, known),
                            str(sum(allocated.values()))]))
    lines.append('UNASSIGNED\t' + str(plan['remaining']))
    return '\n'.join(lines) + '\n'


REFERENCES = {'aggregation': reference_aggregation, 'allocation': reference_allocation,
              'summary': reference_summary}


def E(key='a', revision=0, provider='p', status='ok', tokens=1):
    return dict(id=key, revision=revision, provider=provider, status=status, tokens=tokens)


def R(key='p', weight=1, limit=10):
    return dict(id=key, weight=weight, limit=limit)


def U(key='p', attempts=1, failures=0, tokens=1, unknown=0):
    return dict(provider=key, attempts=attempts, failures=failures,
                tokens=tokens, unknown_attempts=unknown)


def P(rows=(), remaining=0):
    return dict(allocations=[dict(id=key, slots=count) for key, count in rows], remaining=remaining)


def cases(role):
    rows = []
    unset = object()
    def add(name, args, public=False, expected=unset, invalid=False, group='edge'):
        if invalid:
            try:
                REFERENCES[role](*copy.deepcopy(args))
            except ValueError:
                pass
            else:
                raise AssertionError('bad invalid case: ' + name)
            expected = None
        else:
            actual = REFERENCES[role](*copy.deepcopy(args))
            if expected is not unset:
                assert actual == expected, (role, name, actual, expected)
            expected = actual
        rows.append(dict(name=name, args=args, public=public, expected=expected, invalid=invalid, group=group))

    if role == 'aggregation':
        add('empty', [[]], True, [])
        add('known-zero', [[E(' A ', provider=' P ', tokens=0)]], True, [U(tokens=0)])
        add('all-unknown', [[E(tokens=None)]], True, [U(tokens=None, unknown=1)])
        add('revision-beats-order', [[E(revision=3, provider='Q', tokens=7), E(revision=1)]], True, [U('q', tokens=7)])
        add('normalized-duplicate', [[E(' A ', provider=' P '), E('a', provider='p')]], True, [U()])
        add('partial-and-failed', [[E(tokens=0), E('b', status='error', tokens=None)]], True, [U(attempts=2, failures=1, tokens=0, unknown=1)])
        add('cancelled-winner', [[E(), E(revision=1, status='cancelled')]], True, [])
        add('obsolete-conflict', [[E(revision=2), E(tokens=2), E(tokens=3)]], True, invalid=True)
        add('invalid-cancelled', [[E(status='cancelled', tokens=True)]], True, invalid=True)
        add('provider-sort', [[E('a', provider='z'), E('b', provider='a')]], True, [U('a'), U('z')], group='normal')
        add('unicode-casefold', [[E('Stra\u00dfe', provider='Stra\u00dfe'), E('STRASSE', provider='STRASSE')]])
        add('extras-ignored', [[dict(E(), extra={'nested': [1]}), dict(E(), extra=None)]])
        add('same-revision-status-conflict', [[E(), E(status='error')]], invalid=True)
        add('same-revision-provider-conflict', [[E(), E(provider='q')]], invalid=True)
        add('same-revision-zero-unknown-conflict', [[E(tokens=0), E(tokens=None)]], invalid=True)
        add('cancelled-replaced', [[E(status='cancelled'), E(revision=1, status='error')]])
        for key, values in {'id': ['', ' ', None, 3, []], 'provider': ['', ' ', None, False, {}],
                            'revision': [-1, True, 1.0, None, '1'],
                            'status': ['', 'OK', None, [], 1], 'tokens': [-1, True, 0.0, '0', []]}.items():
            for index, value in enumerate(values):
                event = E(); event[key] = value
                add('invalid-' + key + '-' + str(index), [[E('valid'), event]], invalid=True)
        for key in E():
            event = E(); del event[key]
            add('missing-' + key, [[event]], invalid=True)
        for index, value in enumerate([None, {}, 'events', (), [None]]):
            add('bad-container-' + str(index), [value], invalid=True)
        rng = random.Random(87321)
        for index in range(18):
            events = []
            for number in range(8):
                for revision in range(rng.randrange(1, 4)):
                    events.append(E(str(number), revision, rng.choice([' a ', 'A', 'b', 'c']),
                                    rng.choice(['ok', 'error', 'cancelled']), rng.choice([None, 0, 2, 11])))
            rng.shuffle(events)
            add('revision-mixture-' + str(index), [events])
            add('permuted-mixture-' + str(index), [list(reversed(events))])
    elif role == 'allocation':
        add('empty', [[], 3], True, P(remaining=3))
        add('tie-by-canonical-id', [[R(' Z '), R(' A ')], 1], True, P([('a', 1), ('z', 0)]))
        add('weighted-remainders', [[R('a', 3), R('b', 2), R('c', 1)], 7], True, P([('a', 4), ('b', 2), ('c', 1)]), group='normal')
        add('redistribute-capped-share', [[R('a', 9, 1), R('b', 1, 10)], 6], True, P([('a', 1), ('b', 5)]))
        add('all-caps-exhausted', [[R('a', 1, 2), R('b', 7, 0)], 8], True, P([('a', 2), ('b', 0)], 6))
        add('zero-slots-sorted', [[R('b'), R('a')], 0], True, P([('a', 0), ('b', 0)]))
        add('normalized-duplicate-invalid', [[R(' A '), R('a')], 3], True, invalid=True)
        add('validate-even-zero', [[R(weight=False)], 0], True, invalid=True)
        add('iterated-cap-removal', [[R('a', 10, 1), R('b', 2, 3), R('c', 1, 20)], 10], True, P([('a', 1), ('b', 3), ('c', 6)]))
        add('huge-integers', [[R('a', 10**30 + 1, 10**50), R('b', 10**30, 10**50)], 10**30 + 1], True)
        add('unicode-normalization', [[R('Stra\u00dfe'), R('z')], 1])
        add('extras-ignored', [[dict(R(), extra={'deep': [1, 2]})], 2])
        add('all-zero-limits', [[R('b', 10, 0), R('a', 1, 0)], 7])
        add('exact-cap-boundary', [[R('a', 1, 1), R('b', 1, 20)], 2])
        for key, values in {'id': ['', ' ', None, 3, []], 'weight': [0, -1, True, 1.0, None, '1'],
                            'limit': [-1, False, 0.0, None, '2']}.items():
            for index, value in enumerate(values):
                request = R(); request[key] = value
                add('invalid-' + key + '-' + str(index), [[request], 0], invalid=True)
        for key in R():
            request = R(); del request[key]
            add('missing-' + key, [[request], 2], invalid=True)
        for index, value in enumerate([None, {}, 'requests', (), [None]]):
            add('bad-requests-' + str(index), [value, 1], invalid=True)
        for index, value in enumerate([-1, True, 2.0, None, '1']):
            add('bad-slots-' + str(index), [[], value], invalid=True)
        rng = random.Random(43029)
        for index in range(24):
            requests = [R(str(i), rng.randrange(1, 13), rng.randrange(0, 15)) for i in range(rng.randrange(1, 10))]
            rng.shuffle(requests)
            slots = rng.randrange(0, 80)
            add('capped-mixture-' + str(index), [requests, slots])
            add('permuted-mixture-' + str(index), [list(reversed(requests)), slots])
    else:
        header = 'provider\tattempts\tfailures\ttokens\tslots\n'
        add('empty', [[], P()], True, header + 'TOTAL\t0\t0\t-\t0\nUNASSIGNED\t0\n')
        add('zero-measured', [[U(tokens=0)], P([('p', 2)])], True,
            header + 'p\t1\t0\t0\t2\nTOTAL\t1\t0\t0\t2\nUNASSIGNED\t0\n')
        add('join-union-and-partial', [[U('b', 2, 1, 0, 1)], P([('a', 3)], 4)], True,
            header + 'a\t0\t0\t-\t3\nb\t2\t1\t0+\t0\nTOTAL\t2\t1\t0+\t3\nUNASSIGNED\t4\n')
        add('all-unknown', [[U(tokens=None, unknown=1)], P()], True,
            header + 'p\t1\t0\t?\t0\nTOTAL\t1\t0\t?\t0\nUNASSIGNED\t0\n')
        add('escape-provider', [[U('a\\b\tc\rd\ne')], P()], True,
            header + 'a\\\\b\\tc\\rd\\ne\t1\t0\t1\t0\nTOTAL\t1\t0\t1\t0\nUNASSIGNED\t0\n')
        add('mixed-measurement-total', [[U('b', tokens=None, unknown=1), U('a', tokens=2)], P()], True,
            header + 'a\t1\t0\t2\t0\nb\t1\t0\t?\t0\nTOTAL\t2\t0\t2+\t0\nUNASSIGNED\t0\n')
        add('inconsistent-unknown-count', [[U(tokens=0, unknown=1)], P()], True, invalid=True)
        add('canonical-only', [[U(' P ')], P()], True, invalid=True)
        add('duplicate-allocation', [[], P([('p', 1), ('p', 2)])], True, invalid=True)
        add('bool-count-invalid', [[U(attempts=True)], P()], True, invalid=True)
        add('allocation-only', [[], P([('z', 0), ('a', 5)], 7)])
        add('sort-before-escape', [[U('a\nb'), U('a b'), U('a\\b')], P()])
        add('unicode-canonical', [[U('strasse'), U('\u03c3')], P([('\u03c3', 1)])])
        add('huge-counts', [[U(attempts=10**30, tokens=10**60)], P([('p', 10**40)], 10**50)])
        add('extras-ignored', [[dict(U(), extra=[{'n': 1}])], dict(P(), extra=True)], group='normal')
        add('duplicate-usage', [[U(), U()], P()], invalid=True)
        add('none-with-known-attempt', [[U(tokens=None)], P()], invalid=True)
        for key, values in {'provider': ['', ' ', 'P', ' p', None, 3, []],
                            'attempts': [0, -1, False, 1.0, '1', None],
                            'failures': [-1, 2, True, 0.0, None],
                            'unknown_attempts': [-1, 2, True, 0.0, None],
                            'tokens': [-1, True, 0.0, '0', []]}.items():
            for index, value in enumerate(values):
                row = U(); row[key] = value
                add('invalid-usage-' + key + '-' + str(index), [[row], P()], invalid=True)
        for key in U():
            row = U(); del row[key]
            add('missing-usage-' + key, [[row], P()], invalid=True)
        for key, values in {'id': ['', ' ', 'P', None, []], 'slots': [-1, True, 1.0, None]}.items():
            for index, value in enumerate(values):
                plan = P([('p', 1)]); plan['allocations'][0][key] = value
                add('invalid-plan-row-' + key + '-' + str(index), [[], plan], invalid=True)
        for key in ('id', 'slots'):
            plan = P([('p', 1)]); del plan['allocations'][0][key]
            add('missing-allocation-' + key, [[], plan], invalid=True)
        for key in P():
            plan = P(); del plan[key]
            add('missing-plan-' + key, [[], plan], invalid=True)
        for index, value in enumerate([None, {}, 'usage', (), [None]]):
            add('bad-usage-container-' + str(index), [value, P()], invalid=True)
        for index, value in enumerate([None, [], 'plan', {'allocations': (), 'remaining': 0},
                                      {'allocations': [None], 'remaining': 0}]):
            add('bad-plan-container-' + str(index), [[], value], invalid=True)
        for index, value in enumerate([-1, True, 0.0, None, '0']):
            add('bad-remaining-' + str(index), [[], P(remaining=value)], invalid=True)
        rng = random.Random(19204)
        for index in range(24):
            usage, allocations = [], []
            for key in ('a', 'b', 'c', 'd', 'e'):
                if rng.random() < .7:
                    attempts = rng.randrange(1, 7); unknown = rng.randrange(attempts + 1)
                    usage.append(U(key, attempts, rng.randrange(attempts + 1),
                                   None if unknown == attempts else rng.randrange(8), unknown))
                if rng.random() < .7:
                    allocations.append((key, rng.randrange(8)))
            rng.shuffle(usage); rng.shuffle(allocations)
            add('report-mixture-' + str(index), [usage, P(allocations, rng.randrange(4))])
    return rows


SAFE_BUILTINS = ('abs all any bool dict divmod enumerate filter float frozenset int isinstance '
                 'issubclass iter len list map max min next range reversed round set slice '
                 'sorted str sum tuple type zip ValueError TypeError KeyError StopIteration').split()


def load_candidate(source, role):
    if len(source.encode('utf-8')) > 65536:
        raise ValueError('candidate exceeds 64 KiB')
    tree = ast.parse(source)
    nodes = list(ast.walk(tree))
    if len(nodes) > 12000:
        raise ValueError('candidate AST is too large')
    for node in tree.body:
        if isinstance(node, ast.Expr) and isinstance(node.value, ast.Constant) and isinstance(node.value.value, str):
            continue
        if isinstance(node, ast.FunctionDef):
            if node.decorator_list:
                raise ValueError('decorators are unsupported')
            for value in node.args.defaults + [v for v in node.args.kw_defaults if v is not None]:
                ast.literal_eval(value)
            continue
        if isinstance(node, (ast.Assign, ast.AnnAssign)):
            ast.literal_eval(node.value)
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            if not all(isinstance(target, ast.Name) for target in targets):
                raise ValueError('top-level assignment target must be a name')
            continue
        raise ValueError('module-level execution is unsupported')
    for node in nodes:
        if isinstance(node, (ast.Import, ast.ImportFrom, ast.ClassDef, ast.AsyncFunctionDef,
                             ast.Await, ast.Global, ast.Nonlocal)):
            raise ValueError('imports/classes/async/global declarations are unsupported')
        if isinstance(node, ast.Attribute) and node.attr.startswith('_'):
            raise ValueError('private or dunder attribute access is unsupported')
        if isinstance(node, ast.Name) and (node.id.startswith('__') or node.id in {
            'open', 'eval', 'exec', 'compile', 'input', 'globals', 'locals', 'vars', 'getattr',
            'setattr', 'delattr', 'breakpoint', 'help', 'memoryview', 'super', 'object'}):
            raise ValueError('unsupported builtin or dunder name')
    namespace = {'__builtins__': {key: getattr(builtins, key) for key in SAFE_BUILTINS},
                 '__name__': 'tern_candidate'}
    exec(compile(tree, '<tern-candidate>', 'exec'), namespace)
    function = namespace.get(ROLES[role][1])
    if not callable(function):
        raise ValueError('missing required function')
    return function


def same(actual, expected):
    if type(actual) is not type(expected):
        return False
    if isinstance(expected, dict):
        return actual.keys() == expected.keys() and all(same(actual[key], value) for key, value in expected.items())
    if isinstance(expected, (list, tuple)):
        return len(actual) == len(expected) and all(same(a, b) for a, b in zip(actual, expected))
    return actual == expected


def evaluate(function, role, public_only=False):
    results = []
    for case in cases(role):
        if public_only and not case['public']:
            continue
        values = copy.deepcopy(case['args']); before = repr(values)
        detail = None
        try:
            actual = function(*values)
            passed = not case['invalid'] and same(actual, case['expected'])
            if not passed:
                detail = 'expected ValueError' if case['invalid'] else 'result mismatch'
        except Exception as error:
            passed = case['invalid'] and isinstance(error, ValueError)
            if not passed:
                detail = type(error).__name__
        if repr(values) != before:
            passed = False; detail = 'input mutated'
        results.append(dict(case=case['name'], public=case['public'], group=case['group'], passed=passed, detail=detail))
    return dict(role=role, passed=sum(row['passed'] for row in results), total=len(results),
                all_passed=all(row['passed'] for row in results), cases=results)


def self_test():
    summaries = {}
    public = json.loads((BASE / 'fixture/tests/public_cases.json').read_text(encoding='utf-8'))
    for role, (path, _) in ROLES.items():
        reference = evaluate(REFERENCES[role], role)
        source = (BASE / 'fixture' / path).read_text(encoding='utf-8')
        seeded = evaluate(load_candidate(source, role), role)
        intended = {
            'aggregation': {'known-zero', 'revision-beats-order', 'normalized-duplicate', 'obsolete-conflict'},
            'allocation': {'tie-by-canonical-id', 'redistribute-capped-share', 'validate-even-zero', 'huge-integers'},
            'summary': {'empty', 'zero-measured', 'join-union-and-partial', 'escape-provider'},
        }[role]
        failures = {case['case'] for case in seeded['cases'] if not case['passed']}
        assert reference['all_passed'] and intended <= failures, (role, intended - failures)
        assert public[role] == [row for row in cases(role) if row['public']], 'public fixture drift'
        def mutates(*values):
            result = REFERENCES[role](*values)
            if isinstance(values[0], list):
                values[0].append(None)
            return result
        mutation = evaluate(mutates, role, True)
        assert not mutation['all_passed'] and any(row['detail'] == 'input mutated' for row in mutation['cases'])
        summaries[role] = dict(reference_passed=reference['passed'], total=reference['total'],
            public_total=sum(row['public'] for row in reference['cases']), seeded_passed=seeded['passed'],
            intended_defects_detected=sorted(intended), mutation_detected=True)
    for source in ('import os\ndef aggregate_attempts(events): return []',
                   'print(1)\ndef aggregate_attempts(events): return []',
                   'def aggregate_attempts(events): return events.__class__',
                   'def aggregate_attempts(events): return open("x")'):
        try:
            load_candidate(source, 'aggregation')
        except ValueError:
            pass
        else:
            raise AssertionError('unsafe fixture accepted')
    return dict(all_passed=True, roles=summaries, loader_rejections=4,
                grader_sha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest())


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--workspace', type=Path)
    parser.add_argument('--role', choices=['all'] + list(ROLES), default='all')
    parser.add_argument('--public-only', action='store_true')
    parser.add_argument('--self-test', action='store_true')
    args = parser.parse_args()
    if args.self_test:
        print(json.dumps(self_test(), indent=2))
        return 0
    if args.workspace is None:
        parser.error('--workspace is required unless --self-test is set')
    results = []
    root = args.workspace.resolve()
    for role, (relative, _) in ROLES.items():
        if args.role not in ('all', role):
            continue
        try:
            path = (root / relative).resolve()
            if not path.is_relative_to(root):
                raise ValueError('candidate path escapes workspace')
            source = path.read_text(encoding='utf-8')
            result = evaluate(load_candidate(source, role), role, args.public_only)
        except Exception as error:
            selected = [row for row in cases(role) if not args.public_only or row['public']]
            result = dict(role=role, passed=0, total=len(selected), all_passed=False,
                          load_error=type(error).__name__ + ': ' + str(error),
                          cases=[dict(case=row['name'],public=row['public'],group=row['group'],passed=False,
                                      detail='candidate unavailable: '+type(error).__name__) for row in selected])
        results.append(result)
    report = dict(passed=sum(row['passed'] for row in results), total=sum(row['total'] for row in results),
                  all_passed=all(row['all_passed'] for row in results), roles=results)
    print(json.dumps(report, indent=2))
    return 0 if report['all_passed'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
