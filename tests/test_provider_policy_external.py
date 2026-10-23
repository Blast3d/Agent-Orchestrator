"""Antigravity-authored edge cases, with fixtures bound to the real ledger schema."""
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'app'))
import provider_policy as policy


def fixture(workers):
    ledger = {'contributors': [], 'work_items': [], 'activity': []}
    hosted = {}
    for number, (worker, model, synthetic) in enumerate(workers):
        job = 'job-' + str(number)
        actor = {'id': 'worker:' + worker, 'name': worker,
                 'provider': policy.family_for_worker(worker, [model]) or 'unknown', 'model': model}
        event = {'agent_id': actor['id'], 'kind': 'delegation', 'status': 'succeeded',
                 'task_id': job, 'actual_models': [model]}
        ledger['contributors'].append(actor)
        ledger['activity'].append(event)
        ledger['work_items'].append({'id': job, 'category': 'review', 'weight': 1,
                                    'status': 'accepted', 'allocations': [
            {'agent_id': actor['id'], 'percent': 100}]})
        hosted[job] = {'actor': actor, 'event': event, 'synthetic': synthetic}
    return ledger, hosted


class ProviderPolicyExternalTests(unittest.TestCase):
    def test_same_family_across_brands_and_contradictory_identity(self):
        self.assertEqual(policy.family_for_worker('codex'), 'OpenAI')
        self.assertEqual(policy.family_for_worker('vscode-copilot', ['gpt-6']), 'OpenAI')
        self.assertEqual(policy.family_for_worker('vscode-copilot', ['claude-opus-5-5']), 'Anthropic')
        for worker, models in [('vscode-copilot', ['unknown-model']), ('local-chat', []),
                               ('jev', []), ('claude', ['gpt-6']),
                               ('vscode-copilot', ['gpt-6', 'claude-opus-5-5'])]:
            with self.subTest(worker=worker, models=models):
                self.assertIsNone(policy.family_for_worker(worker, models))
        self.assertEqual(policy.family_for_worker('claude'), 'Anthropic')
        ledger, hosted = fixture([('codex', 'gpt-6-sol', False),
                                  ('vscode-copilot', 'gpt-6', False)])
        self.assertEqual(policy.coverage('sol', ledger, hosted)['families'], ['OpenAI'])

    def test_lead_family_change_changes_cross_family_choices(self):
        sol, claude = policy.plan('sol'), policy.plan('fable')
        self.assertEqual(sol['lead_family'], 'OpenAI')
        self.assertEqual(claude['lead_family'], 'Anthropic')
        self.assertNotIn('codex', sol['cross_family_workers'])
        self.assertIn('codex', claude['cross_family_workers'])
        with self.assertRaises(ValueError):
            policy.plan('unknown-lead')

    def test_synthetic_job_does_not_supply_a_second_family(self):
        ledger, hosted = fixture([('claude', 'claude-opus-5-5', True),
                                  ('grok', 'grok-4.6-build', False)])
        result = policy.coverage('sol', ledger, hosted)
        self.assertEqual(result['families'], ['xAI'])
        self.assertFalse(result['minimum_met'])
        with self.assertRaises(ValueError):
            policy.require_coverage('sol', ledger, hosted)

    def test_legacy_runs_keep_two_as_hard_minimum(self):
        ledger, hosted = fixture([('claude', 'claude-opus-5-5', False),
                                  ('grok', 'grok-4.6-build', False)])
        result = policy.require_coverage('sol', ledger, hosted, policy=policy.legacy_plan('sol'))
        self.assertEqual(result['family_count'], 2)
        self.assertTrue(result['minimum_met'])
        self.assertFalse(result['preferred_met'])
        self.assertTrue(result['warnings'])

    def test_current_runs_hold_until_gemini_also_contributes(self):
        workers = [('claude', 'claude-opus-5-5', False), ('grok', 'grok-4.6-build', False)]
        with self.assertRaisesRegex(ValueError, r'gemini \(Google\)'):
            policy.require_coverage('sol', *fixture(workers))
        result = policy.require_coverage(
            'sol', *fixture(workers + [('gemini', 'gemini-3.8-flash-high', False)]))
        self.assertTrue(result['minimum_met'])
        self.assertTrue(result['balance']['balanced'])


if __name__ == '__main__':
    unittest.main()
