"""Run explicit public contract examples; no third-party test framework needed."""
import argparse
import copy
import importlib.util
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
ROLES = {
    'aggregation': ('tern/usage/ledger.py', 'aggregate_attempts'),
    'allocation': ('tern/dispatch/allocator.py', 'allocate_slots'),
    'summary': ('tern/reporting/summary.py', 'render_report'),
}


def same(a, b):
    if type(a) is not type(b):
        return False
    if isinstance(b, dict):
        return a.keys() == b.keys() and all(same(a[k], v) for k, v in b.items())
    if isinstance(b, list):
        return len(a) == len(b) and all(same(x, y) for x, y in zip(a, b))
    return a == b


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--role', choices=['all'] + list(ROLES), default='all')
    args = parser.parse_args()
    cases = json.loads((ROOT / 'tests/public_cases.json').read_text(encoding='utf-8'))
    results = []
    for role, (path, name) in ROLES.items():
        if args.role not in ('all', role):
            continue
        spec = importlib.util.spec_from_file_location(role, ROOT / path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        function = getattr(module, name)
        for case in cases[role]:
            values = copy.deepcopy(case['args'])
            before = repr(values)
            try:
                actual = function(*values)
                passed = not case.get('invalid', False) and same(actual, case['expected'])
            except Exception as error:
                passed = case.get('invalid', False) and isinstance(error, ValueError)
            passed = bool(passed and repr(values) == before)
            results.append({'role': role, 'case': case['name'], 'passed': passed})
    print(json.dumps({'passed': sum(row['passed'] for row in results),
                      'total': len(results), 'cases': results}, indent=2))
    return 0 if all(row['passed'] for row in results) else 1


if __name__ == '__main__':
    raise SystemExit(main())
