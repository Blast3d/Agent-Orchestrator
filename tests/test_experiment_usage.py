'''Tests for app/experiment_usage.py (standard library only, no I/O).'''

import copy
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'app'))

from experiment_usage import normalize_usage, summarize_usage  # noqa: E402


def make_call(**overrides):
    record = {
        'call_id': 'c1',
        'provider': 'OpenAI',
        'worker_id': 'w1',
        'status': 'succeeded',
        'usage': None,
        'actual_models': [],
        'requested_model': None,
    }
    record.update(overrides)
    return record


class NormalizeOpenAITests(unittest.TestCase):
    def test_cached_input_is_not_added_to_input(self):
        record = make_call(usage={
            'input_tokens': 1000,
            'output_tokens': 200,
            'cached_input_tokens': 800,
        })
        result = normalize_usage(record)
        self.assertEqual(result['input_tokens'], 1000)
        self.assertEqual(result['output_tokens'], 200)
        self.assertEqual(result['cached_input_tokens'], 800)
        self.assertEqual(result['known_total_tokens'], 1200)
        self.assertIsNone(result['additional_charge_usd'])

    def test_alias_keys_are_recognized(self):
        camel = normalize_usage(make_call(usage={'inputTokens': 12, 'outputTokens': 3}))
        self.assertEqual((camel['input_tokens'], camel['output_tokens']), (12, 3))
        ollama = normalize_usage(make_call(usage={'prompt_eval_count': 7, 'eval_count': 9}))
        self.assertEqual((ollama['input_tokens'], ollama['output_tokens']), (7, 9))

    def test_missing_usage_stays_unknown_not_zero(self):
        result = normalize_usage(make_call(status='failed', usage=None))
        self.assertIsNone(result['input_tokens'])
        self.assertIsNone(result['output_tokens'])
        self.assertIsNone(result['cached_input_tokens'])
        self.assertIsNone(result['known_total_tokens'])

    def test_malformed_usage_does_not_raise(self):
        for bad in ('not-a-dict', 17, [1, 2, 3]):
            result = normalize_usage(make_call(usage=bad))
            self.assertIsNone(result['input_tokens'])
            self.assertIsNone(result['output_tokens'])
        self.assertIsNone(normalize_usage('not-a-record')['input_tokens'])


class NormalizeInvalidValueTests(unittest.TestCase):
    def test_bool_negative_fractional_and_nonfinite_rejected(self):
        bad_values = [True, False, -5, 1.5, float('nan'), float('inf'),
                      float('-inf'), '100', None]
        for bad in bad_values:
            result = normalize_usage(make_call(usage={'input_tokens': bad, 'output_tokens': 4}))
            self.assertIsNone(result['input_tokens'], msg=repr(bad))
            self.assertEqual(result['output_tokens'], 4)
            self.assertEqual(result['known_total_tokens'], 4)

    def test_integral_float_accepted_as_int(self):
        result = normalize_usage(make_call(usage={'input_tokens': 10.0, 'output_tokens': 2}))
        self.assertEqual(result['input_tokens'], 10)
        self.assertIsInstance(result['input_tokens'], int)


class NormalizeAnthropicTests(unittest.TestCase):
    def test_input_sums_uncached_and_cache_parts(self):
        result = normalize_usage(make_call(provider='Anthropic', usage={
            'input_tokens': 100,
            'cache_creation_input_tokens': 20,
            'cache_read_input_tokens': 30,
            'output_tokens': 50,
        }))
        self.assertEqual(result['input_tokens'], 150)
        self.assertEqual(result['cached_input_tokens'], 30)
        self.assertEqual(result['output_tokens'], 50)
        self.assertEqual(result['known_total_tokens'], 200)

    def test_input_unknown_when_a_cache_part_is_missing_or_invalid(self):
        partial = normalize_usage(make_call(provider='Anthropic', usage={
            'input_tokens': 100,
            'cache_creation_input_tokens': 20,
            'output_tokens': 50,
        }))
        self.assertIsNone(partial['input_tokens'])
        self.assertEqual(partial['output_tokens'], 50)
        self.assertEqual(partial['known_total_tokens'], 50)

        invalid = normalize_usage(make_call(provider='Anthropic', usage={
            'input_tokens': 100,
            'cache_creation_input_tokens': True,
            'cache_read_input_tokens': 30,
            'output_tokens': 50,
        }))
        self.assertIsNone(invalid['input_tokens'])


class NormalizeXaiTests(unittest.TestCase):
    def test_total_consistent_with_inclusive_input(self):
        result = normalize_usage(make_call(provider='xAI', usage={
            'input_tokens': 100,
            'cache_creation_input_tokens': 0,
            'cache_read_input_tokens': 40,
            'output_tokens': 10,
            'total_tokens': 110,
        }))
        self.assertEqual(result['input_tokens'], 100)
        self.assertEqual(result['cached_input_tokens'], 40)

    def test_total_consistent_with_additive_cache(self):
        result = normalize_usage(make_call(provider='xAI', usage={
            'input_tokens': 60,
            'cache_creation_input_tokens': 10,
            'cache_read_input_tokens': 30,
            'output_tokens': 10,
            'total_tokens': 110,
        }))
        self.assertEqual(result['input_tokens'], 100)
        self.assertEqual(result['known_total_tokens'], 110)

    def test_inconsistent_total_yields_unknown_input(self):
        result = normalize_usage(make_call(provider='xAI', usage={
            'input_tokens': 60,
            'cache_creation_input_tokens': 10,
            'cache_read_input_tokens': 30,
            'output_tokens': 10,
            'total_tokens': 500,
        }))
        self.assertIsNone(result['input_tokens'])
        self.assertEqual(result['output_tokens'], 10)
        summary = summarize_usage([make_call(provider='xAI', usage={
            'input_tokens': 60, 'output_tokens': 10, 'total_tokens': 500,
        })])
        self.assertTrue(any('inconsistent' in note for note in summary['notes']))

    def test_without_total_raw_input_is_retained(self):
        result = normalize_usage(make_call(provider='xAI', usage={
            'input_tokens': 60,
            'cache_creation_input_tokens': 10,
            'cache_read_input_tokens': 30,
            'output_tokens': 10,
        }))
        self.assertEqual(result['input_tokens'], 60)
        self.assertEqual(result['cached_input_tokens'], 30)


class ChargeTests(unittest.TestCase):
    def test_verified_billing_sets_charge(self):
        record = make_call(billing={
            'verified': True, 'additional_charge_usd': 1.25, 'currency': 'USD',
        })
        self.assertEqual(normalize_usage(record)['additional_charge_usd'], 1.25)

    def test_estimates_and_unverified_billing_stay_unknown(self):
        cases = [
            make_call(usage={'input_tokens': 1, 'output_tokens': 1, 'cost_usd': 4.2}),
            make_call(estimated_cost_usd=9.99),
            make_call(billing={'additional_charge_usd': 3.0, 'currency': 'USD'}),
            make_call(billing={'verified': 'yes', 'additional_charge_usd': 3.0,
                               'currency': 'USD'}),
            make_call(billing={'verified': True, 'additional_charge_usd': -1.0,
                               'currency': 'USD'}),
            make_call(billing={'verified': True, 'additional_charge_usd': True,
                               'currency': 'USD'}),
            make_call(billing={'verified': True,
                               'additional_charge_usd': float('inf'),
                               'currency': 'USD'}),
            make_call(billing={'verified': True, 'additional_charge_usd': 2.0,
                               'currency': 'EUR'}),
            make_call(plan='free'),
        ]
        for record in cases:
            self.assertIsNone(normalize_usage(record)['additional_charge_usd'])

    def test_summary_charge_sums_only_when_every_call_is_verified(self):
        verified = [
            make_call(call_id='a', billing={'verified': True,
                                            'additional_charge_usd': 0.5,
                                            'currency': 'USD'}),
            make_call(call_id='b', billing={'verified': True,
                                            'additional_charge_usd': 1.25,
                                            'currency': 'USD'}),
        ]
        self.assertEqual(summarize_usage(verified)['totals']['additional_charge_usd'], 1.75)
        mixed = verified + [make_call(call_id='c', usage={'cost_usd': 7.0})]
        self.assertIsNone(summarize_usage(mixed)['totals']['additional_charge_usd'])


class SummaryTests(unittest.TestCase):
    def test_billing_overflow_stays_unknown_and_json_safe(self):
        import json
        extreme = make_call(billing={'verified':True,'currency':'USD','additional_charge_usd':10**400})
        self.assertIsNone(normalize_usage(extreme)['additional_charge_usd'])
        large = make_call(billing={'verified':True,'currency':'USD','additional_charge_usd':1e308})
        summary = summarize_usage([large,large])
        self.assertIsNone(summary['totals']['additional_charge_usd'])
        json.dumps(summary,allow_nan=False)

    def test_strict_totals_unknown_while_known_totals_sum_observed(self):
        records = [
            make_call(call_id='a', usage={'input_tokens': 10, 'output_tokens': 5,
                                          'cached_input_tokens': 2}),
            make_call(call_id='b', status='failed', usage=None),
            make_call(call_id='c', status='running',
                      usage={'input_tokens': 7, 'output_tokens': 1,
                             'cached_input_tokens': 0}),
        ]
        totals = summarize_usage(records)['totals']
        self.assertEqual(totals['calls'], 3)
        self.assertEqual(totals['succeeded_calls'], 1)
        self.assertIsNone(totals['input_tokens'])
        self.assertIsNone(totals['output_tokens'])
        self.assertIsNone(totals['cached_input_tokens'])
        self.assertEqual(totals['known_input_tokens'], 17)
        self.assertEqual(totals['known_output_tokens'], 6)
        self.assertEqual(totals['known_total_tokens'], 23)
        self.assertEqual(totals['unknown_usage_calls'], 1)
        self.assertIsNone(totals['additional_charge_usd'])

    def test_fully_observed_condition_reports_strict_totals(self):
        records = [
            make_call(call_id='a', usage={'input_tokens': 10, 'output_tokens': 5,
                                          'cached_input_tokens': 4}),
            make_call(call_id='b', usage={'input_tokens': 1, 'output_tokens': 2,
                                          'cached_input_tokens': 0}),
        ]
        totals = summarize_usage(records)['totals']
        self.assertEqual(totals['input_tokens'], 11)
        self.assertEqual(totals['output_tokens'], 7)
        self.assertEqual(totals['cached_input_tokens'], 4)
        self.assertEqual(totals['unknown_usage_calls'], 0)

    def test_empty_condition_counts_zero_but_charges_unknown(self):
        summary = summarize_usage([])
        totals = summary['totals']
        for field in ('calls', 'succeeded_calls', 'input_tokens', 'output_tokens',
                      'cached_input_tokens', 'known_input_tokens',
                      'known_output_tokens', 'known_total_tokens',
                      'unknown_usage_calls'):
            self.assertEqual(totals[field], 0, msg=field)
        self.assertIsNone(totals['additional_charge_usd'])
        self.assertEqual(summary['by_provider'], [])
        self.assertEqual(summary['by_worker'], [])

    def test_same_worker_id_on_two_providers_never_merges(self):
        records = [
            make_call(call_id='a', provider='OpenAI', worker_id='w1',
                      usage={'input_tokens': 10, 'output_tokens': 1,
                             'cached_input_tokens': 0}),
            make_call(call_id='b', provider='Anthropic', worker_id='w1',
                      usage={'input_tokens': 5, 'cache_creation_input_tokens': 0,
                             'cache_read_input_tokens': 0, 'output_tokens': 2}),
        ]
        summary = summarize_usage(records)
        by_worker = summary['by_worker']
        self.assertEqual(len(by_worker), 2)
        pairs = {(row['worker_id'], row['provider']): row for row in by_worker}
        self.assertEqual(pairs[('w1', 'OpenAI')]['known_input_tokens'], 10)
        self.assertEqual(pairs[('w1', 'Anthropic')]['known_input_tokens'], 5)
        providers = {row['provider']: row for row in summary['by_provider']}
        self.assertEqual(sorted(providers), ['Anthropic', 'OpenAI'])
        self.assertEqual(providers['OpenAI']['calls'], 1)

    def test_missing_provider_and_worker_are_labelled_unknown(self):
        summary = summarize_usage([
            {'call_id': 'a', 'status': 'uncertain'},
            'garbage',
        ])
        self.assertEqual(summary['totals']['calls'], 1)
        self.assertEqual(summary['by_provider'][0]['provider'], 'unknown')
        self.assertEqual(summary['by_worker'][0]['worker_id'], 'unknown')
        self.assertTrue(any('not a dictionary' in note for note in summary['notes']))

    def test_no_invented_models_or_scores_in_output(self):
        summary = summarize_usage([make_call(usage={'input_tokens': 1, 'output_tokens': 1,
                                                   'cached_input_tokens': 0},
                                            requested_model='gpt-test')])
        for row in [summary['totals']] + summary['by_provider'] + summary['by_worker']:
            for key in row:
                self.assertNotIn('model', key)
                self.assertNotIn('effort', key)

    def test_input_records_are_not_mutated(self):
        records = [
            make_call(call_id='a', usage={'input_tokens': 10, 'output_tokens': 5}),
            make_call(call_id='b', provider='xAI', usage={'input_tokens': 60,
                                                         'output_tokens': 10,
                                                         'total_tokens': 500}),
            make_call(call_id='c', billing={'verified': True,
                                           'additional_charge_usd': 1.0,
                                           'currency': 'USD'}),
        ]
        snapshot = copy.deepcopy(records)
        normalize_usage(records[0])
        summarize_usage(records)
        self.assertEqual(records, snapshot)


if __name__ == '__main__':
    unittest.main()
