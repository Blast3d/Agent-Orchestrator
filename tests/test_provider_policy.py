"""Family policy uses retained canonical work, never actor or connector count."""
from copy import deepcopy
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'app'))
from provider_policy import (MINIMUM_FAMILIES, PREFERRED_FAMILIES, coverage,
                             family_for_worker, plan, require_coverage, validate_plan)


def native_ledger(*families):
    actors = [{'id': 'native:' + str(index), 'name': 'Native ' + str(index),
               'provider': family, 'model': 'native'} for index, family in enumerate(families)]
    return {'schema_version': 1, 'scope_id': 'test-run', 'title': 'Policy tests',
            'basis': 'Retained bounded review work.', 'contributors': actors,
            'work_items': [{'id': 'native:' + str(index), 'label': 'Retained native review',
                            'category': 'review', 'status': 'accepted', 'weight': 1,
                            'allocations': [{'agent_id': actor['id'], 'percent': 100,
                                             'evidence': 'Reviewed retained result'}]}
                           for index, actor in enumerate(actors)], 'activity': []}


def hosted_work(ledger, hosted, worker, *, model=None, status='succeeded',
                work_status='accepted', percent=100, weight=1, synthetic=False,
                alias=None):
    index = len(hosted)
    job = 'job-' + str(index)
    identifier = 'worker:' + worker
    provider = {'codex': 'OpenAI', 'claude': 'Anthropic', 'grok': 'xAI',
                'gemini': 'Google', 'vscode-copilot': 'GitHub Copilot'}.get(worker, 'TypeSafe')
    models = [] if model is None else [model]
    canonical_actor = {'id': identifier, 'name': worker, 'provider': provider,
                       'model': model or 'unknown'}
    actor = dict(canonical_actor, id=alias or identifier)
    if not any(row['id'] == actor['id'] for row in ledger['contributors']):
        ledger['contributors'].append(actor)
    event = {'id': job + ':delegation', 'kind': 'delegation', 'agent_id': actor['id'],
             'task_id': job, 'status': status, 'actual_models': models,
             'input_tokens': None, 'output_tokens': None}
    ledger['activity'].append(event)
    ledger['work_items'].append({'id': job, 'label': 'Retained worker review',
                                 'category': 'review', 'status': work_status, 'weight': weight,
                                 'allocations': [{'agent_id': actor['id'], 'percent': percent,
                                                  'evidence': 'Reviewed retained worker result'}]})
    hosted[job] = {'actor': canonical_actor, 'event': dict(event, agent_id=identifier),
                   'synthetic': synthetic}
    return job


class FamilyIdentityTests(unittest.TestCase):
    def test_thresholds_and_lead_plan_are_immutable(self):
        self.assertEqual((MINIMUM_FAMILIES, PREFERRED_FAMILIES), (2, 3))
        for owner, family, workers in (
                ('astra', 'OpenAI', ['claude', 'grok', 'gemini']),
                ('sol', 'OpenAI', ['claude', 'grok', 'gemini']),
                ('fable', 'Anthropic', ['codex', 'grok', 'gemini'])):
            with self.subTest(owner=owner):
                value = plan(owner)
                self.assertEqual(value, {'schema_version': 1, 'minimum_families': 2,
                                        'preferred_families': 3, 'lead_family': family,
                                        'cross_family_workers': workers})
                self.assertEqual(validate_plan(value, owner), value)
        with self.assertRaises(ValueError):
            plan('imaginary-lead')

    def test_plan_rejects_missing_downgraded_or_spoofed_requirements(self):
        expected = plan('sol')
        variants = [None, {}, dict(expected, minimum_families=1),
                    dict(expected, preferred_families=2), dict(expected, lead_family='Anthropic'),
                    dict(expected, schema_version=True), dict(expected, minimum_families=True),
                    dict(expected, cross_family_workers=[]),
                    dict(expected, cross_family_workers=['codex']),
                    dict(expected, cross_family_workers=['jev']),
                    dict(expected, cross_family_workers=['claude', 'claude']),
                    dict(expected, cross_family_workers=['claude', 'fake-provider']),
                    dict(expected, disabled=True)]
        for value in variants:
            with self.subTest(value=value), self.assertRaises(ValueError):
                validate_plan(value, 'sol')
        reordered = dict(expected, cross_family_workers=list(reversed(expected['cross_family_workers'])))
        self.assertEqual(validate_plan(reordered, 'sol')['cross_family_workers'], reordered['cross_family_workers'])

    def test_official_routes_and_observed_models_identify_families(self):
        for worker, models, expected in (
                ('codex', (), 'OpenAI'), ('codex', ('gpt-6-sol',), 'OpenAI'),
                ('claude', (), 'Anthropic'), ('claude', ('opus', 'haiku'), 'Anthropic'),
                ('grok', ('grok-4.6-build',), 'xAI'), ('gemini', ('gemini-3.8-flash-medium',), 'Google'),
                ('codex', ('claude-opus-5',), None), ('claude', ('gpt-6',), None),
                ('gemini', ('grok-4.6',), None), ('grok', ('gemini-3.8',), None),
                ('jev', ('TypeSafe',), None), ('local-chat', ('qwen3',), None),
                ('unknown', ('claude-opus-5',), None)):
            with self.subTest(worker=worker, models=models):
                self.assertEqual(family_for_worker(worker, models), expected)

    def test_copilot_uses_actual_model_family_not_github_brand(self):
        for models, expected in (
                (('gpt-5',), 'OpenAI'), (('o1',), 'OpenAI'), (('o3-mini',), 'OpenAI'),
                (('o4-mini',), 'OpenAI'), (('claude-opus-5',), 'Anthropic'),
                (('gemini-3',), 'Google'), (('grok-4',), 'xAI'), ((), None),
                (('auto',), None), (('gpt-5', 'claude-opus-5'), None),
                (('gpt-5', 'unknown-model'), None)):
            with self.subTest(models=models):
                self.assertEqual(family_for_worker('vscode-copilot', models), expected)


class RetainedFamilyCoverageTests(unittest.TestCase):
    def test_native_lead_and_other_canonical_family_pass_and_encourage_three(self):
        ledger, hosted = native_ledger('OpenAI'), {}
        hosted_work(ledger, hosted, 'claude', model='claude-opus-5')
        result = require_coverage('sol', ledger, hosted, native_capture=True)
        self.assertEqual(result['families'], ['Anthropic', 'OpenAI'])
        self.assertEqual(result['family_count'], 2)
        self.assertTrue(result['minimum_met'])
        self.assertFalse(result['preferred_met'])
        self.assertTrue(result['warnings'])
        hosted_work(ledger, hosted, 'grok', model='grok-4.6-build')
        result = require_coverage('sol', ledger, hosted, native_capture=True)
        self.assertEqual(result['family_count'], 3)
        self.assertTrue(result['preferred_met'])
        self.assertEqual(result['warnings'], [])

    def test_two_canonical_hosted_families_pass_without_native_credit(self):
        ledger, hosted = native_ledger(), {}
        hosted_work(ledger, hosted, 'claude', model='claude-opus-5')
        hosted_work(ledger, hosted, 'grok', model='grok-4.6-build')
        self.assertEqual(require_coverage('astra', ledger, hosted)['families'], ['Anthropic', 'xAI'])

    def test_lead_identity_alone_and_same_family_workers_do_not_satisfy_minimum(self):
        ledger, hosted = native_ledger('OpenAI', 'OpenAI', 'OpenAI'), {}
        hosted_work(ledger, hosted, 'codex', model='gpt-6-sol')
        hosted_work(ledger, hosted, 'vscode-copilot', model='gpt-6')
        result = coverage('sol', ledger, hosted, native_capture=True)
        self.assertEqual(result['families'], ['OpenAI'])
        self.assertFalse(result['minimum_met'])
        with self.assertRaisesRegex(ValueError, '(?i)(famil|provider)'):
            require_coverage('sol', ledger, hosted, native_capture=True)
        hosted_only = native_ledger()
        only = {}
        hosted_work(hosted_only, only, 'claude', model='claude-opus-5')
        self.assertEqual(coverage('sol', hosted_only, only)['families'], ['Anthropic'])

    def test_native_capture_and_lead_family_retention_are_both_required(self):
        ledger, hosted = native_ledger('OpenAI'), {}
        self.assertEqual(coverage('sol', ledger, hosted)['families'], [])
        self.assertEqual(coverage('sol', native_ledger(), hosted, native_capture=True)['families'], [])
        fabricated = native_ledger('OpenAI', 'Anthropic', 'Google', 'xAI', 'TypeSafe')
        self.assertEqual(coverage('sol', fabricated, hosted, native_capture=True)['families'], ['OpenAI'])
        ledger['work_items'][0]['allocations'][0]['percent'] = 0
        self.assertEqual(coverage('sol', ledger, hosted, native_capture=True)['families'], [])

    def test_native_unretained_work_and_memory_import_cannot_be_lead_contribution(self):
        for changes in ({'status': 'pending'}, {'status': 'rejected'}, {'status': 'failed'},
                        {'weight': 0}, {'category': 'memory-curation'}):
            with self.subTest(changes=changes):
                ledger = native_ledger('OpenAI')
                ledger['work_items'][0].update(changes)
                self.assertEqual(coverage('sol', ledger, {}, native_capture=True)['families'], [])

    def test_jev_and_typesafe_are_advice_not_worker_families(self):
        ledger, hosted = native_ledger('OpenAI'), {}
        hosted_work(ledger, hosted, 'jev', model='TypeSafe')
        self.assertEqual(coverage('sol', ledger, hosted, native_capture=True)['families'], ['OpenAI'])

    def test_unretained_failed_pending_or_synthetic_work_never_qualifies(self):
        for changes in ({'work_status': 'pending'}, {'work_status': 'rejected'},
                        {'work_status': 'failed'}, {'status': 'failed'}, {'status': 'running'},
                        {'percent': 0}, {'weight': 0}, {'synthetic': True}):
            with self.subTest(changes=changes):
                ledger, hosted = native_ledger('OpenAI'), {}
                hosted_work(ledger, hosted, 'claude', model='claude-opus-5', **changes)
                self.assertEqual(coverage('sol', ledger, hosted, native_capture=True)['families'], ['OpenAI'])

    def test_exact_job_and_positive_worker_allocation_are_required(self):
        base, hosted = native_ledger('OpenAI'), {}
        job = hosted_work(base, hosted, 'claude', model='claude-opus-5')
        for variant in ('wrong-job', 'different-actor', 'no-allocation', 'wrong-delegation'):
            with self.subTest(variant=variant):
                ledger = deepcopy(base)
                if variant == 'wrong-job':
                    ledger['work_items'][-1]['id'] = 'unrelated-work'
                elif variant == 'different-actor':
                    ledger['work_items'][-1]['allocations'][0]['agent_id'] = 'native:0'
                elif variant == 'no-allocation':
                    ledger['work_items'][-1]['allocations'] = []
                else:
                    ledger['activity'][-1]['task_id'] = 'another-job'
                    ledger['activity'][-1]['agent_id'] = 'native:0'
                    ledger['work_items'][-1]['allocations'][0]['agent_id'] = 'native:0'
                self.assertEqual(coverage('sol', ledger, hosted, native_capture=True)['families'], ['OpenAI'])
        self.assertEqual(require_coverage('sol', base, hosted, native_capture=True)['family_count'], 2)
        self.assertIn(job, hosted)

    def test_canonical_worker_can_be_remapped_by_matching_delegation(self):
        ledger, hosted = native_ledger('OpenAI'), {}
        hosted_work(ledger, hosted, 'claude', model='claude-opus-5', alias='reviewer:Claude')
        self.assertEqual(require_coverage('sol', ledger, hosted, native_capture=True)['family_count'], 2)

    def test_remapped_synthetic_hosted_lead_family_is_not_native_lead_work(self):
        ledger, hosted = native_ledger(), {}
        hosted_work(ledger, hosted, 'codex', model='gpt-6-sol', alias='reviewer:ASTRA', synthetic=True)
        hosted_work(ledger, hosted, 'claude', model='claude-opus-5')
        self.assertEqual(coverage('sol', ledger, hosted, native_capture=True)['families'], ['Anthropic'])

    def test_copilot_unknown_and_contradictory_official_models_cannot_add_family(self):
        for worker, model in (('vscode-copilot', 'auto'), ('vscode-copilot', 'gpt-6'),
                              ('claude', 'gpt-6'), ('codex', 'claude-opus-5')):
            with self.subTest(worker=worker, model=model):
                ledger, hosted = native_ledger('OpenAI'), {}
                hosted_work(ledger, hosted, worker, model=model)
                self.assertEqual(coverage('sol', ledger, hosted, native_capture=True)['families'], ['OpenAI'])


if __name__ == '__main__':
    unittest.main()
