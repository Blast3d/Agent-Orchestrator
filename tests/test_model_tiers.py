"""Pure unit tests: provider modules and Gemini proof access are replaced."""
from copy import deepcopy
from pathlib import Path
import sys
import tempfile
import types
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'app'))
import model_tiers as mt


def _codex(model):
    if model not in ('astra', 'sol'):
        raise ValueError('Choose astra or sol')
    return 'gpt-6-' + model


def _claude(model):
    if model == 'fable' or model.startswith('claude-fable'):
        raise ValueError('Claude Fable is paused')
    return model


def mix(tier):
    return {'tiers': {family: tier for family in mt.FAMILY_WORKERS}}


def bad_reader(*args):
    raise ValueError('invalid or missing file')


class ModelTierTests(unittest.TestCase):
    def setUp(self):
        for patcher in (
                patch.dict(sys.modules, {
                    'codex_worker': types.SimpleNamespace(select_codex_model=_codex),
                    'claude_models': types.SimpleNamespace(require_model_allowed=_claude)}),
                patch.object(mt, '_gemini_models',
                             lambda: ({'gemini-3.8-flash-high'}, 'gemini-3.8-flash-high'))):
            patcher.start()
            self.addCleanup(patcher.stop)

    def test_precedence(self):
        for explicit, run, global_tier, expected, source in (
                ('economy', 'balanced', 'best', 'economy', 'explicit'),
                (None, 'balanced', 'economy', 'balanced', 'run'),
                (None, None, 'economy', 'economy', 'global'),
                (None, None, None, 'best', 'default')):
            with self.subTest(explicit=explicit, run=run, global_tier=global_tier):
                result = mt.resolve('codex', explicit, config={}, run_dir=Path('run'),
                                    read_run=lambda _: {'current': mix(run)},
                                    read_global=lambda: mix(global_tier))
                self.assertEqual((result['tier'], result['source']), (expected, source))
                self.assertTrue(result['verified'])

    def test_reader_errors_fall_through_and_record_reasons(self):
        result = mt.resolve('grok', config={}, run_dir=Path('missing'),
                            read_run=bad_reader, read_global=bad_reader)
        self.assertEqual(result['source'], 'default')
        self.assertEqual(result['effort'], 'medium')
        self.assertIn('Run tier unavailable', result['reason'])
        self.assertIn('Global tier unavailable', result['reason'])

    def test_explicit_does_not_read_mixes(self):
        def forbidden(*args):
            raise AssertionError('Lower priority mix was read')
        result = mt.resolve('codex', 'best', config={}, run_dir=Path('run'),
                            read_run=forbidden, read_global=forbidden)
        self.assertEqual(result['model'], 'astra')

    def test_malformed_overrides_fall_back(self):
        for overrides in (None, [], {'unknown': {}}, {'codex': {'cheap': {}}},
                          {'codex': {'best': {'effort': 'extreme'}}},
                          {'gemini': {'best': {'effort': 'low'}}},
                          {'claude': {'best': {'surprise': True}}}):
            with self.subTest(overrides=overrides):
                table = mt.ladders({'model_tiers': overrides})
                self.assertEqual(table, mt.DEFAULT_LADDERS)
                self.assertTrue(any('Invalid model_tiers' in reason for reason in table.reasons))

    def test_missing_overrides_have_reason(self):
        self.assertIn('missing', mt.ladders({}).reasons[0])

    def test_partial_override_and_no_mutation(self):
        config = {'model_tiers': {'codex': {'economy': {'effort': 'medium'}}}}
        before = deepcopy(config)
        table = mt.ladders(config)
        self.assertEqual(table['codex']['economy'], {'model': 'sol', 'effort': 'medium'})
        self.assertEqual(config, before)
        table['codex']['best']['model'] = 'changed'
        self.assertEqual(mt.DEFAULT_LADDERS['codex']['best']['model'], 'astra')

    def test_route_rejection_is_visible_and_not_silently_replaced(self):
        for worker, model in (('codex', 'unverified'), ('claude', 'fable'),
                              ('grok', 'grok-unverified'), ('gemini', 'gemini-unverified')):
            with self.subTest(worker=worker):
                result = mt.resolve(worker, 'best',
                                    config={'model_tiers': {worker: {'best': {'model': model}}}})
                self.assertEqual(result['model'], model)
                self.assertIs(result['verified'], False)
                self.assertTrue(result['reason'])

    def test_paused_explicit_claude_refuses(self):
        result = mt.resolve_request('claude', model='fable', config={})
        self.assertFalse(result['verified'])
        self.assertIn('paused', result['reason'])

    def test_grok_explicit_verification_list(self):
        config = {'workers': [{'id': 'grok', 'verified_models': ['grok-approved']}],
                  'model_tiers': {'grok': {'best': {'model': 'grok-approved'}}}}
        self.assertTrue(mt.resolve('grok', 'best', config=config)['verified'])
        self.assertFalse(mt.resolve('grok', 'balanced', config=config)['verified'])

    def test_gemini_verified_but_transport_cannot_select(self):
        with patch.object(mt, '_gemini_models', lambda: ({'gemini-new'}, 'gemini-3.8-flash-high')):
            result = mt.resolve('gemini', 'best',
                                config={'model_tiers': {'gemini': {'best': {'model': 'gemini-new'}}}})
        self.assertFalse(result['verified'])
        self.assertIn('transport', result['reason'])

    def test_unavailable_proof_holds(self):
        with patch.object(mt, '_gemini_models', bad_reader):
            self.assertFalse(mt.resolve('gemini', 'best', config={})['verified'])

    def test_all_best_mix(self):
        for worker, model, effort in (('codex', 'astra', 'medium'), ('claude', 'opus', 'medium'),
                                      ('grok', 'grok-4.6', 'medium'),
                                      ('gemini', 'gemini-3.8-flash-high', None)):
            with self.subTest(worker=worker):
                result = mt.resolve(worker, config={}, read_global=lambda: mix('best'))
                self.assertEqual((result['model'], result['effort']), (model, effort))
                self.assertTrue(result['verified'])

    def test_explicit_model_beats_mix_but_tier_beats_model(self):
        result = mt.resolve_request('codex', model='sol', config={}, read_global=lambda: mix('best'))
        self.assertEqual((result['model'], result['tier'], result['source']), ('sol', None, 'explicit'))
        self.assertEqual(mt.resolve_request('codex', 'best', model='sol', config={})['model'], 'astra')

    def test_explicit_effort_and_tier_effort(self):
        self.assertEqual(mt.resolve_request('claude', 'economy', config={})['effort'], 'low')
        result = mt.resolve_request('claude', 'economy', effort='high', config={})
        self.assertEqual(result['effort'], 'high')
        self.assertTrue(result['verified'])

    def test_labels(self):
        result = mt.labels({})
        self.assertEqual(set(result), set(mt.FAMILY_WORKERS))
        self.assertTrue(all(set(value) == set(mt.TIERS) for value in result.values()))
        self.assertEqual(result['OpenAI']['best'], 'GPT-6-Astra · medium')
        self.assertEqual(result['Anthropic']['economy'], 'Claude Haiku · low')
        self.assertEqual(result['xAI']['best'], 'Grok 4.6 · medium')
        self.assertEqual(result['Google']['economy'], 'Gemini 3.8 Flash High')

    def test_config_read_failure_cannot_authorize_dispatch(self):
        with patch.object(mt, '_configuration', bad_reader):
            self.assertFalse(mt.resolve('grok', 'best')['verified'])

    def test_config_file_loading(self):
        with tempfile.TemporaryDirectory() as directory, patch.object(mt, 'CONFIG', Path(directory)):
            (Path(directory) / 'workers.json').write_text(
                '{"model_tiers":{"codex":{"best":{"model":"sol"}}}}', encoding='utf-8')
            self.assertEqual(mt.resolve('codex', 'best')['model'], 'sol')

    def test_saved_gemini_proof_is_read_from_the_boundary_validation_file(self):
        import antigravity_boundary
        with tempfile.TemporaryDirectory() as directory:
            proof = Path(directory) / 'proof.json'
            proof.write_text('{"models": ["gemini-3.8-flash-high", "gemini-3.8-flash-medium"]}',
                             encoding='utf-8')
            with patch.object(antigravity_boundary, 'VALIDATION', proof):
                models, command = _REAL_GEMINI_MODELS()
        self.assertEqual(models, {'gemini-3.8-flash-high', 'gemini-3.8-flash-medium'})
        self.assertEqual(command, antigravity_boundary.MODEL)


# setUp replaces _gemini_models; keep the real reader for the proof-file test.
_REAL_GEMINI_MODELS = mt._gemini_models


if __name__ == '__main__':
    unittest.main()
