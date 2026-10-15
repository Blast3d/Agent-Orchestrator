"""Real local startup and closeout family checks; no model/provider requests."""
import hashlib
import json
from pathlib import Path
import sys
from types import SimpleNamespace
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parent))
import test_orchestration_lifecycle as fixture
from contributions import write_report
from memory_bundle import capture_run
from orchestration_context import OperatingContextError, verify_run_startup
from orchestration_lifecycle import start_run
from task_store import write_json


class ProviderFamilyGateTests(unittest.TestCase):
    setUp = fixture.LifecycleTests.setUp
    read = fixture.LifecycleTests.read
    manifest = fixture.LifecycleTests.manifest
    task = fixture.LifecycleTests.task
    audit = fixture.LifecycleTests.audit
    close = fixture.LifecycleTests.close
    held = fixture.LifecycleTests.held

    def hosted(self, worker='claude', model='claude-opus-5', **changes):
        return self.task(worker=worker, model=model, **changes)

    def family_check(self, result):
        return next(row for row in result['checks'] if row['check'] == 'provider_family_coverage')

    def native_capture(self):
        (self.run / 'review/family-evidence.md').write_text(
            'Native lead verified retained policy behavior with local checks.', encoding='utf-8')
        bundle = {'capture_id': 'native-family-review', 'project_id': 'alpha',
                  'evidence': ['review/family-evidence.md'],
                  'memories': [{'key': 'family-gate', 'kind': 'procedure',
                                'title': 'Review canonical family evidence',
                                'content': 'Canonical accepted model-family work determines coverage.'}]}
        outcome = capture_run(self.root, self.run.name, bundle, **self.identity,
                              reviewer='Tester', note='Verified retained native policy review and evidence locally.')
        self.assertEqual(outcome['memory_outcome']['status'], 'remembered')
        return outcome

    def append_native_credit(self, family='OpenAI'):
        path = self.run / 'contributions-ledger.json'
        ledger = self.read(path)
        identifier = 'native:' + family
        ledger['contributors'].append({'id': identifier, 'name': 'Native ' + family,
                                       'provider': family, 'model': 'native'})
        ledger['work_items'].append({'id': identifier, 'label': 'Native retained review',
                                    'category': 'review', 'status': 'accepted', 'weight': 1,
                                    'allocations': [{'agent_id': identifier, 'percent': 100,
                                                     'evidence': 'Verified retained native review'}]})
        write_json(path, ledger)
        write_report(self.run, ledger)

    def replace_receipt_policy_and_recompute_hash(self):
        path = self.run / 'startup-context.json'
        receipt = self.read(path)
        receipt['provider_policy']['cross_family_workers'].reverse()
        policy_text = json.dumps(receipt['provider_policy'], sort_keys=True)
        receipt['provider_policy_sha256'] = hashlib.sha256(policy_text.encode('utf-8')).hexdigest()
        self.assertNotIn(policy_text, (self.run / 'startup-context.md').read_text(encoding='utf-8'))
        write_json(path, receipt)

    def test_recomputed_policy_hash_cannot_replace_prepared_packet_policy_at_dispatch(self):
        self.replace_receipt_policy_and_recompute_hash()
        args = SimpleNamespace(output=self.run / 'drafts/answer.json', project='alpha')
        with self.assertRaises(OperatingContextError):
            verify_run_startup(args, fixture.operating())

    def test_recomputed_policy_hash_cannot_replace_prepared_packet_policy_at_closeout(self):
        self.hosted()
        self.hosted(worker='grok', model='grok-4.6-build')
        self.audit()
        self.replace_receipt_policy_and_recompute_hash()
        self.held(self.close(), 'startup_context')

    def test_start_provides_policy_in_bound_receipt_and_worker_packet(self):
        result = start_run(run=self.run, root=self.root, **self.identity)
        policy = result['provider_policy']
        self.assertEqual(policy['minimum_families'], 2)
        self.assertEqual(policy['preferred_families'], 3)
        self.assertEqual(policy['lead_family'], 'OpenAI')
        self.assertEqual(self.read(self.run / 'startup-context.json')['provider_policy'], policy)
        packet = (self.run / 'startup-context.md').read_text(encoding='utf-8')
        self.assertIn('family', packet.lower())
        self.assertIn('claude', packet.lower())

    def test_single_hosted_family_holds_even_if_lead_is_another_family(self):
        self.hosted()
        self.audit()
        self.held(self.close(), 'provider_family_coverage')

    def test_native_only_holds_with_genuine_capture_and_multiple_openai_agents(self):
        self.audit()
        self.native_capture()
        self.append_native_credit()
        result = self.close()
        self.held(result, 'provider_family_coverage')
        self.assertIn('2', self.family_check(result)['reason'])

    def test_two_actual_hosted_families_complete_and_encourage_three(self):
        self.hosted()
        self.hosted(worker='grok', model='grok-4.6-build')
        self.audit()
        result = self.close()
        self.assertEqual(result['status'], 'completed', result)
        check = self.family_check(result)
        self.assertEqual(check['status'], 'passed')
        self.assertEqual(check['detail']['families'], ['Anthropic', 'xAI'])
        self.assertTrue(check['detail']['warnings'])

    def test_three_actual_hosted_families_complete_without_coverage_warning(self):
        self.hosted()
        self.hosted(worker='grok', model='grok-4.6-build')
        self.hosted(worker='gemini', model='gemini-3.8-flash-medium')
        self.audit()
        result = self.close()
        self.assertEqual(result['status'], 'completed', result)
        self.assertEqual(self.family_check(result)['detail']['family_count'], 3)
        self.assertEqual(self.family_check(result)['detail']['warnings'], [])

    def test_native_lead_and_one_accepted_other_family_complete(self):
        self.hosted()
        self.audit()
        self.native_capture()
        self.append_native_credit()
        result = self.close()
        self.assertEqual(result['status'], 'completed', result)
        self.assertEqual(self.family_check(result)['detail']['families'], ['Anthropic', 'OpenAI'])

    def test_fabricated_native_anthropic_label_cannot_supply_other_family(self):
        self.audit()
        self.native_capture()
        self.append_native_credit('Anthropic')
        self.held(self.close(), 'provider_family_coverage')

    def test_copilot_gpt_and_codex_count_as_one_actual_family(self):
        self.hosted(worker='codex', model='gpt-6-sol')
        self.hosted(worker='vscode-copilot', model='gpt-6')
        self.audit()
        self.held(self.close(), 'provider_family_coverage')

    def test_synthetic_success_cannot_supply_another_family(self):
        self.hosted(worker='codex', model='gpt-6-sol')
        self.hosted(synthetic_input=True)
        self.audit()
        self.held(self.close(), 'provider_family_coverage')

    def test_synthetic_remapped_hosted_actor_cannot_become_native_lead_work(self):
        synthetic, _ = self.hosted(worker='codex', model='gpt-6-sol', synthetic_input=True)
        self.hosted()
        self.audit()
        self.native_capture()
        path = self.run / 'contributions-ledger.json'
        ledger = self.read(path)
        old, new = 'worker:codex', 'reviewer:ASTRA'
        next(row for row in ledger['contributors'] if row['id'] == old)['id'] = new
        next(row for row in ledger['activity'] if row['task_id'] == synthetic['job_id'])['agent_id'] = new
        next(row for row in ledger['work_items'] if row['id'] == synthetic['job_id'])['allocations'][0]['agent_id'] = new
        write_json(path, ledger)
        write_report(self.run, ledger)
        self.held(self.close(), 'provider_family_coverage')

    def test_jev_execution_and_typesafe_label_cannot_supply_another_family(self):
        self.hosted(worker='codex', model='gpt-6-sol')
        self.hosted(worker='jev', model='TypeSafe')
        self.audit()
        self.held(self.close(), 'provider_family_coverage')

    def test_missing_or_downgraded_startup_policy_holds_closeout(self):
        self.hosted()
        self.hosted(worker='grok', model='grok-4.6-build')
        self.audit()
        path = self.run / 'startup-context.json'
        original = self.read(path)
        for change in ('missing', 'minimum', 'preferred', 'family', 'reordered'):
            with self.subTest(change=change):
                receipt = json.loads(json.dumps(original))
                if change == 'missing':
                    receipt.pop('provider_policy', None)
                else:
                    policy = receipt.setdefault('provider_policy', {'schema_version': 1,
                        'minimum_families': 2, 'preferred_families': 3, 'lead_family': 'OpenAI',
                        'cross_family_workers': ['claude', 'grok', 'gemini']})
                    if change == 'reordered':
                        policy['cross_family_workers'] = list(reversed(policy['cross_family_workers']))
                    else:
                        policy[{'minimum': 'minimum_families', 'preferred': 'preferred_families',
                                'family': 'lead_family'}[change]] = {'minimum': 1, 'preferred': 2,
                                                                  'family': 'Anthropic'}[change]
                write_json(path, receipt)
                self.held(self.close(), 'startup_context')


if __name__ == '__main__':
    unittest.main()
