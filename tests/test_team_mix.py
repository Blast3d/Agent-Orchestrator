import contextlib
import copy
import io
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'app'))

import team_mix as tm


def shares(values):
    return dict(zip(tm.FAMILIES, values))


class TeamMixTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        root_patch = patch.object(tm, 'ROOT', self.root)
        root_patch.start()
        self.addCleanup(root_patch.stop)
        self.run = self.root / 'runs' / 'example'
        self.run.mkdir(parents=True)
        self.write(self.run / 'run.json', {'status': 'running'})

    @staticmethod
    def write(path, value):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(value), encoding='utf-8')

    def cli(self, *arguments):
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            code = tm.main(list(arguments))
        return code, json.loads(output.getvalue())

    def test_aliases(self):
        for family, worker in tm.FAMILY_WORKERS.items():
            for alias in (family, family.upper(), worker, worker.upper()):
                self.assertEqual(tm.family_name(alias), family)
        for invalid in ('unknown', None, [], 1):
            with self.subTest(invalid=invalid), self.assertRaises(ValueError):
                tm.family_name(invalid)

    def test_defaults_and_independent_copies(self):
        original = tm.default_mix()
        self.assertEqual(original['shares'], shares((25, 25, 25, 25)))
        self.assertEqual(original['tiers'], dict.fromkeys(tm.FAMILIES, 'best'))
        self.assertEqual(original['tolerance'], 10)
        self.assertEqual(original['source'], 'default')
        normalized = tm.validate_mix(original)
        normalized['shares']['OpenAI'] = 50
        self.assertEqual(original['shares']['OpenAI'], 25)
        original['tiers']['OpenAI'] = 'economy'
        self.assertEqual(tm.default_mix()['tiers']['OpenAI'], 'best')

    def test_validation_rejections(self):
        baseline = tm.default_mix()
        invalid = []
        for key in baseline:
            candidate = copy.deepcopy(baseline)
            del candidate[key]
            invalid.append(candidate)
        candidate = copy.deepcopy(baseline)
        candidate['extra'] = 1
        invalid.append(candidate)
        changes = (
            ('schema_version', True), ('schema_version', 2),
            ('shares', shares((True, 33, 33, 33))),
            ('shares', shares((25, 25, 25, 24))),
            ('shares', shares((4, 32, 32, 32))),
            ('shares', shares((86, 5, 5, 4))),
            ('shares', shares((25.0, 25, 25, 25))),
            ('tolerance', True), ('tolerance', 4), ('tolerance', 26),
            ('tolerance', 10.0), ('preset', 'unknown'),
            ('preset', 'code'), ('preset', []),
            ('source', 'unknown'), ('source', []),
            ('updated_at', None), ('updated_at', 'yesterday'),
            ('updated_at', '2026-11-07T20:00:00'),
            ('updated_at', '2026-11-07T20:00:00+01:00'),
            ('tiers', dict.fromkeys(tm.FAMILIES, 'unknown')),
            ('tiers', dict.fromkeys(tm.FAMILIES, [])),
        )
        for key, value in changes:
            candidate = copy.deepcopy(baseline)
            candidate[key] = value
            invalid.append(candidate)
        for key in ('shares', 'tiers'):
            candidate = copy.deepcopy(baseline)
            del candidate[key]['Google']
            invalid.append(candidate)
            candidate = copy.deepcopy(baseline)
            candidate[key]['extra'] = 25
            invalid.append(candidate)
        invalid.extend((None, [], 'mix'))
        for candidate in invalid:
            with self.subTest(candidate=candidate), self.assertRaises(ValueError):
                tm.validate_mix(candidate)

    def test_pinned_rebalance_vectors(self):
        vectors = (
            ((25, 25, 25, 25), 'Anthropic', 40, (20, 40, 20, 20)),
            ((25, 25, 25, 25), 'OpenAI', 85, (85, 5, 5, 5)),
            ((25, 25, 25, 25), 'xAI', 99, (5, 5, 85, 5)),
            ((25, 25, 25, 25), 'Google', 0, (32, 32, 31, 5)),
            ((30, 40, 15, 15), 'OpenAI', 50, (50, 27, 12, 11)),
            ((85, 5, 5, 5), 'OpenAI', 40, (40, 20, 20, 20)),
            ((40, 20, 15, 25), 'Google', 33, (35, 18, 14, 33)),
            ((30, 40, 15, 15), 'xAI', 16.6, (29, 39, 17, 15)),
        )
        for start, family, value, expected in vectors:
            with self.subTest(start=start, family=family, value=value):
                original = shares(start)
                self.assertEqual(tm.rebalance(original, family, value), shares(expected))
                self.assertEqual(original, shares(start))

    def test_rebalance_grid(self):
        for first in range(5, 86, 10):
            for second in range(5, 86, 10):
                for third in range(5, 86, 10):
                    fourth = 100 - first - second - third
                    if not 5 <= fourth <= 85:
                        continue
                    start = shares((first, second, third, fourth))
                    for family in tm.FAMILIES:
                        for value in (-100, 0, 5, 5.5, 16.6, 25, 40, 84.5, 85, 100):
                            result = tm.rebalance(start, family, value)
                            self.assertEqual(sum(result.values()), 100)
                            self.assertEqual(tuple(result), tm.FAMILIES)
                            self.assertTrue(all(
                                type(item) is int and 5 <= item <= 85
                                for item in result.values()
                            ))

    def test_rebalance_invalid_numbers(self):
        for value in (True, None, '40', float('nan'), float('inf'), -float('inf')):
            with self.subTest(value=value), self.assertRaises(ValueError):
                tm.rebalance(tm.default_mix()['shares'], 'OpenAI', value)

    def test_updates(self):
        baseline = tm.default_mix()
        fixed_time = '2026-11-07T20:00:00+00:00'
        with patch.object(tm, 'timestamp', return_value=fixed_time):
            updated = tm.apply_updates(
                baseline, preset='code', tolerance=15,
                tiers={'CODEX': 'economy', 'gemini': 'balanced'}, source='viewer',
            )
        self.assertEqual(updated['shares'], tm.PRESETS['code'])
        self.assertEqual(updated['preset'], 'code')
        self.assertEqual(updated['updated_at'], fixed_time)
        self.assertEqual(updated['source'], 'viewer')
        self.assertEqual(updated['tolerance'], 15)
        self.assertEqual(updated['tiers']['OpenAI'], 'economy')
        self.assertEqual(updated['tiers']['Google'], 'balanced')
        self.assertEqual(baseline['preset'], 'balanced')
        custom = tm.apply_updates(baseline, family='claude', value=40)
        self.assertIsNone(custom['preset'])
        explicit = tm.apply_updates(custom, shares=tm.PRESETS['research'])
        self.assertEqual(explicit['preset'], 'research')
        unchanged = tm.apply_updates(baseline, family='OpenAI', value=25)
        self.assertEqual(unchanged['preset'], 'balanced')

    def test_invalid_updates(self):
        cases = (
            {'preset': 'code', 'shares': tm.PRESETS['code']},
            {'preset': 'code', 'family': 'claude', 'value': 40},
            {'shares': tm.PRESETS['code'], 'family': 'claude', 'value': 40},
            {'family': 'claude'}, {'value': 40},
            {'tiers': {'claude': 'best', 'Anthropic': 'economy'}},
            {'tiers': []}, {'source': 'other'}, {'tolerance': True},
        )
        for updates in cases:
            with self.subTest(updates=updates), self.assertRaises(ValueError):
                tm.apply_updates(tm.default_mix(), **updates)

    def test_global_storage(self):
        self.assertEqual(tm.global_path(), self.root / 'runtime' / 'team-mix.json')
        self.assertEqual(tm.read_global()['preset'], 'balanced')
        self.assertFalse(tm.global_path().exists())
        mix = tm.apply_updates(tm.default_mix(), preset='visual')
        self.assertEqual(tm.save_global(mix), mix)
        self.assertEqual(tm.read_global(), mix)
        alternate = self.root / 'alternate'
        tm.save_global(mix, alternate)
        self.assertEqual(tm.read_global(alternate), mix)
        self.write(tm.global_path(), {'invalid': True})
        with self.assertRaises(ValueError):
            tm.read_global()
        tm.global_path().write_text('{', encoding='utf-8')
        with self.assertRaises(ValueError):
            tm.read_global()
        with patch.object(Path, 'read_text', side_effect=PermissionError):
            with self.assertRaises(ValueError):
                tm.read_global()

    def test_run_roundtrip_and_idempotence(self):
        first = tm.init_run_mix(self.run, tm.default_mix())
        self.assertEqual(first['current']['source'], 'startup')
        self.assertEqual(first['history'], [first['current']])
        before = tm.run_path(self.run).read_bytes()
        second = tm.init_run_mix(
            self.run, tm.apply_updates(tm.default_mix(), preset='code')
        )
        self.assertEqual(second, first)
        self.assertEqual(tm.run_path(self.run).read_bytes(), before)
        updated = tm.retune_run(self.run, family='claude', value=40)
        self.assertEqual(len(updated['history']), 2)
        self.assertEqual(updated['history'][0], first['current'])
        self.assertEqual(updated['current'], updated['history'][-1])
        self.assertEqual(updated, tm.read_run_mix(self.run))

    def test_history_trimming(self):
        initial = tm.init_run_mix(self.run, tm.default_mix())
        for index in range(202):
            state = tm.retune_run(self.run, tolerance=5 + index % 21)
        self.assertEqual(len(state['history']), 200)
        self.assertEqual(state['history'][0], initial['current'])
        self.assertEqual(state['current'], state['history'][-1])
        self.assertEqual(state['history'][1]['tolerance'], 8)
        self.assertEqual(state['current']['tolerance'], 17)

    def test_completed_and_missing_metadata_refuse_changes(self):
        tm.init_run_mix(self.run, tm.default_mix())
        before = tm.run_path(self.run).read_bytes()
        self.write(self.run / 'run.json', {'status': 'completed'})
        with self.assertRaises(ValueError):
            tm.retune_run(self.run, preset='code')
        self.assertEqual(tm.run_path(self.run).read_bytes(), before)
        (self.run / 'run.json').unlink()
        with self.assertRaises(ValueError):
            tm.retune_run(self.run, preset='code')

    def test_corrupted_run_files(self):
        valid = tm.init_run_mix(self.run, tm.default_mix())
        invalid = [None, {}, dict(valid, run_id='another')]
        invalid.append(dict(valid, schema_version=True))
        invalid.append(dict(valid, history=[]))
        invalid.append(dict(valid, history=[valid['current']] * 201))
        invalid.append(dict(valid, current=tm.default_mix()))
        invalid.append(dict(valid, history=[tm.default_mix()]))
        invalid.append(dict(valid, extra=True))
        for candidate in invalid:
            with self.subTest(candidate=candidate):
                self.write(tm.run_path(self.run), candidate)
                with self.assertRaises(ValueError):
                    tm.read_run_mix(self.run)
                with self.assertRaises(ValueError):
                    tm.init_run_mix(self.run, tm.default_mix())
        tm.run_path(self.run).write_text('{', encoding='utf-8')
        with self.assertRaises(ValueError):
            tm.read_run_mix(self.run)
        tm.run_path(self.run).unlink()
        with self.assertRaises(ValueError):
            tm.read_run_mix(self.run)

    def test_describe_and_lead(self):
        summary = tm.describe()
        self.assertEqual(summary['scope'], 'global')
        self.assertIsNone(summary['run_id'])
        self.assertIsNone(summary['lead_family'])
        self.assertEqual(summary['history_count'], 0)
        self.assertEqual(summary['model_calls'], 0)
        self.assertEqual(summary['families'], list(tm.FAMILIES))
        tm.init_run_mix(self.run, tm.default_mix())
        self.assertIsNone(tm.lead_family(self.run))
        for owner, expected in (
            ('astra', 'OpenAI'), ('sol', 'OpenAI'),
            ('fable', 'Anthropic'), ('unknown', None), ([], None),
        ):
            self.write(self.run / 'coordinator.json', {'owner': owner})
            summary = tm.describe(self.run)
            self.assertEqual(summary['lead_family'], expected)
            self.assertEqual(summary['history_count'], 1)
            self.assertEqual(summary['run_id'], self.run.name)
        (self.run / 'coordinator.json').write_text('{', encoding='utf-8')
        self.assertIsNone(tm.lead_family(self.run))

    def test_cli_slider_and_show(self):
        code, result = self.cli('set', '--family', 'claude', '--value', '40', '--json')
        self.assertEqual(code, 0)
        self.assertEqual(result['mix']['shares'], shares((20, 40, 20, 20)))
        self.assertEqual(result['model_calls'], 0)
        code, shown = self.cli('show', '--json')
        self.assertEqual(code, 0)
        self.assertEqual(shown, result)

    def test_cli_presets_shares_and_tiers(self):
        code, result = self.cli('presets', '--json')
        self.assertEqual(code, 0)
        self.assertEqual(result, tm.PRESETS)
        code, result = self.cli(
            'set', '--shares', 'O=30,A=40,X=15,G=15',
            '--tier', 'codex=economy', 'gemini=balanced',
            '--tier', 'claude=best',
            '--tolerance', '12', '--source', 'visualizer', '--json',
        )
        self.assertEqual(code, 0)
        self.assertEqual(result['mix']['preset'], 'code')
        self.assertEqual(result['mix']['tiers']['Google'], 'balanced')
        self.assertEqual(result['mix']['tiers']['OpenAI'], 'economy')
        self.assertEqual(result['mix']['source'], 'visualizer')

    def test_cli_run_and_errors(self):
        tm.init_run_mix(self.run, tm.default_mix())
        code, result = self.cli(
            'set', '--run', str(self.run), '--preset', 'research', '--json'
        )
        self.assertEqual(code, 0)
        self.assertEqual(result['history_count'], 2)
        self.assertEqual(result['mix']['preset'], 'research')
        code, shown = self.cli('show', '--run', str(self.run), '--json')
        self.assertEqual((code, shown), (0, result))
        cases = (
            ('set', '--family', 'claude'),
            ('set', '--value', '40'),
            ('set', '--family', 'unknown', '--value', '40'),
            ('set', '--family', 'claude', '--value', 'nan'),
            ('set', '--family', 'claude', '--value', 'bad'),
            ('set', '--preset', 'unknown'),
            ('set', '--tolerance', '4'),
            ('set', '--tolerance', 'no'),
            ('set', '--shares', 'O=25,A=25,X=25,G=24'),
            ('set', '--shares', 'O=25,O=25,X=25,G=25'),
            ('set', '--tier', 'claude=unknown'),
            ('set', '--tier', 'claude=best', 'Anthropic=economy'),
            ('set', '--preset', 'code', '--family', 'claude', '--value', '40'),
            ('set', '--source', 'startup'),
            ('unknown',),
            (),
        )
        for arguments in cases:
            with self.subTest(arguments=arguments):
                code, result = self.cli(*arguments, '--json')
                self.assertEqual(code, 2)
                self.assertEqual(set(result), {'error'})
                self.assertIsInstance(result['error'], str)


if __name__ == '__main__':
    unittest.main()
