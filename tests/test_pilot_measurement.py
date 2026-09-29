"""Protect benchmark accounting from cache double-counting and invented usage."""
import importlib.util
from pathlib import Path
import unittest

path=Path(__file__).resolve().parents[1]/'benchmarks/solo_vs_team/report.py'
spec=importlib.util.spec_from_file_location('pilot_report',path)
report=importlib.util.module_from_spec(spec)
spec.loader.exec_module(report)


class AccountingTests(unittest.TestCase):
    def test_openai_cache_is_subset_of_input(self):
        result=report.usage({'provider':'OpenAI','usage':{'input_tokens':100,'cached_input_tokens':80,'output_tokens':12}})
        self.assertEqual(result['input_tokens'],100)
        self.assertEqual(result['cached_input_tokens'],80)

    def test_anthropic_cache_components_are_additive(self):
        result=report.usage({'provider':'Anthropic','usage':{'input_tokens':2,'cache_creation_input_tokens':100,'cache_read_input_tokens':50,'output_tokens':9}})
        self.assertEqual(result['input_tokens'],152)
        self.assertEqual(result['output_tokens'],9)

    def test_missing_components_are_unknown_not_zero(self):
        result=report.usage({'provider':'Anthropic','usage':{'input_tokens':2,'output_tokens':0}})
        self.assertIsNone(result['input_tokens'])
        self.assertEqual(result['output_tokens'],0)
        self.assertIsNone(report.total([{'input_tokens':5},{'input_tokens':None}],'input_tokens'))

    def test_provider_estimate_does_not_become_actual_subscription_charge(self):
        result=report.usage({'provider':'xAI','usage':{'inputTokens':50,'outputTokens':10,'cost':3.99}})
        self.assertEqual(result['input_tokens'],50)
        self.assertIsNone(result['additional_subscription_charge_usd'])

    def test_grok_cache_reconciles_to_reported_total(self):
        result=report.usage({'provider':'xAI','usage':{'input_tokens':8455,'cache_read_input_tokens':256,'cache_creation_input_tokens':0,'output_tokens':3231,'reasoning_tokens':2845,'total_tokens':11942}})
        self.assertEqual(result['input_tokens'],8711)
        self.assertEqual(result['output_tokens'],3231)

    def test_grok_inclusive_cache_is_not_added_twice(self):
        result=report.usage({'provider':'xAI','usage':{'input_tokens':100,'cache_read_input_tokens':20,'cache_creation_input_tokens':0,'output_tokens':10,'total_tokens':110}})
        self.assertEqual(result['input_tokens'],100)

    def test_inconsistent_grok_total_remains_unknown(self):
        result=report.usage({'provider':'xAI','usage':{'input_tokens':100,'cache_read_input_tokens':20,'cache_creation_input_tokens':0,'output_tokens':10,'total_tokens':500}})
        self.assertIsNone(result['input_tokens'])


if __name__=='__main__':unittest.main()
