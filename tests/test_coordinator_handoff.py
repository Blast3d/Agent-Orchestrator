"""Coordinator continuity without model calls, real accounts or real project data."""
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'app'))
from coordinator_handoff import Coordinator, read_object, validate_checkpoint
from init_run import create_run
from task_store import write_json


class Quota:
    def __init__(self, fresh=True, allowed=True):
        self.fresh = fresh
        self.allowed = allowed
        self.calls = []

    def refresh(self, provider):
        self.calls.append(('refresh', provider))
        return {provider: {'ok': self.fresh}}

    def check(self, worker, size):
        self.calls.append(('check', worker, size))
        return {'allowed': self.allowed, 'worker': worker, 'windows': []}


class CoordinatorTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.workspace = Path(temp.name)
        self.run, self.manifest = create_run(self.workspace, 'demo', 'Finish the synthetic project')
        self.coordinator = Coordinator(self.run)
        self.initial = self.coordinator.read()
        self.session = self.initial['session']
        self.checkpoint = deepcopy(self.initial['checkpoint'])
        self.checkpoint.update(completed=['Accepted parser'], next_steps=['Inspect uncertain job, then finish guide'],
                               decisions=['Keep existing worker output'], authorization=[{'provider': 'claude', 'scope': 'synthetic'}],
                               open_jobs=[{'job_id': 'a' * 32, 'status': 'uncertain', 'reservation_id': 'keep-held'}],
                               validation=['Focused parser checks passed'], artifacts=['parser.py'])

    def checkpoint_once(self):
        return self.coordinator.checkpoint(self.checkpoint, 'astra', self.session, 1)

    def prepare(self):
        self.checkpoint_once()
        return self.coordinator.prepare('fable', 'usage-limit', 2, True)

    def claim(self, prepared, session='fable-session', quota=None):
        return self.coordinator.claim(prepared['handoff']['id'], session, prepared['generation'], quota or Quota())

    def test_new_run_has_initial_durable_checkpoint(self):
        self.assertEqual(self.initial['owner'], 'astra')
        self.assertEqual(self.initial['generation'], 1)
        self.assertEqual(self.initial['checkpoint']['objective'], self.manifest['objective'])
        self.assertEqual(self.initial['checkpoint']['completed'], [])

    def test_initialize_never_overwrites_existing_record(self):
        original = self.coordinator.path.read_bytes()
        with self.assertRaises(ValueError):
            self.coordinator.initialize('astra', 'different')
        self.assertEqual(original, self.coordinator.path.read_bytes())

    def test_abrupt_stop_recovers_persisted_progress_without_old_session(self):
        self.checkpoint_once()
        recovered = Coordinator(self.run)
        prepared = recovered.prepare('fable', 'usage-limit', 2, True)
        claimed = recovered.claim(prepared['handoff']['id'], 'new-claude-session', 3, Quota())
        self.assertEqual(claimed['owner'], 'fable')
        self.assertEqual(claimed['checkpoint'], self.checkpoint)
        self.assertEqual(claimed['generation'], 4)
        self.assertEqual(self.manifest, read_object(self.run / 'run.json'))

    def test_checkpoint_requires_current_owner_session_and_generation(self):
        for owner, session, generation in [('fable', self.session, 1), ('astra', 'wrong', 1), ('astra', self.session, 0)]:
            with self.subTest(owner=owner, session=session, generation=generation):
                with self.assertRaises(ValueError):
                    self.coordinator.checkpoint(self.checkpoint, owner, session, generation)
        self.checkpoint_once()
        with self.assertRaises(ValueError):
            self.checkpoint_once()

    def test_prepare_requires_stop_attestation_and_current_generation(self):
        for generation, stopped in [(1, False), (0, True)]:
            with self.assertRaises(ValueError):
                self.coordinator.prepare('fable', 'usage-limit', generation, stopped)
        self.assertEqual(self.coordinator.read(), self.initial)

    def test_prepare_retries_reuse_frozen_handoff(self):
        prepared = self.prepare()
        repeated = self.coordinator.prepare('fable', 'usage-limit', 3, True)
        self.assertEqual(prepared, repeated)
        with self.assertRaises(ValueError):
            self.coordinator.prepare('astra', 'manual', 3, True)

    def test_old_owner_cannot_checkpoint_pending_or_claimed_handoff(self):
        prepared = self.prepare()
        with self.assertRaises(ValueError):
            self.coordinator.checkpoint(self.checkpoint, 'astra', self.session, 3)
        self.claim(prepared)
        with self.assertRaises(ValueError):
            self.coordinator.checkpoint(self.checkpoint, 'astra', self.session, 4)

    def test_fable_can_continue_checkpointing_after_claim(self):
        prepared = self.prepare()
        self.claim(prepared)
        updated = deepcopy(self.checkpoint)
        updated['completed'].append('Guide verified by Fable')
        result = self.coordinator.checkpoint(updated, 'fable', 'fable-session', 4)
        self.assertEqual(result['generation'], 5)
        self.assertEqual(result['checkpoint'], updated)

    def test_receiving_quota_is_refreshed_and_checked_without_reservation_mutation(self):
        prepared = self.prepare()
        quota = Quota()
        self.claim(prepared, quota=quota)
        self.assertEqual(quota.calls, [('refresh', 'claude'), ('check', 'claude', 'small')])
        self.assertEqual(self.coordinator.read()['checkpoint']['open_jobs'][0]['reservation_id'], 'keep-held')

    def test_failed_refresh_or_held_quota_keeps_exact_pending_state(self):
        prepared = self.prepare()
        before = self.coordinator.path.read_bytes()
        for quota in (Quota(fresh=False), Quota(allowed=False)):
            with self.assertRaises(ValueError):
                self.claim(prepared, quota=quota)
            self.assertEqual(before, self.coordinator.path.read_bytes())

    def test_corrupt_usage_state_cannot_claim(self):
        prepared = self.prepare()
        quota = Quota()
        with patch.object(quota, 'refresh', side_effect=ValueError('invalid usage JSON')):
            with self.assertRaises(ValueError):
                self.claim(prepared, quota=quota)
        self.assertEqual(self.coordinator.read(), prepared)

    def test_same_session_claim_retry_is_idempotent_and_does_not_refresh_again(self):
        prepared = self.prepare()
        quota = Quota()
        first = self.claim(prepared, quota=quota)
        second = self.claim(prepared, quota=quota)
        self.assertEqual(first, second)
        self.assertEqual(len(quota.calls), 2)

    def test_different_session_cannot_reuse_claim_or_wrong_id(self):
        prepared = self.prepare()
        self.claim(prepared)
        with self.assertRaises(ValueError):
            self.claim(prepared, session='another-session')
        with self.assertRaises(ValueError):
            self.coordinator.claim('wrong-id', 'fable-session', 3, Quota())

    def test_competing_claims_have_exactly_one_winner(self):
        prepared = self.prepare()
        def claim(session):
            try:
                return Coordinator(self.run).claim(prepared['handoff']['id'], session, 3, Quota())['session']
            except ValueError:
                return None
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(claim, ['first', 'second']))
        self.assertEqual(sum(value is not None for value in results), 1)
        self.assertIn(self.coordinator.read()['session'], results)

    def test_return_to_astra_requires_another_explicit_handoff(self):
        prepared = self.prepare()
        self.claim(prepared)
        returning = self.coordinator.prepare('astra', 'manual', 4, True)
        quota = Quota()
        result = self.coordinator.claim(returning['handoff']['id'], 'new-astra-session', 5, quota)
        self.assertEqual(result['owner'], 'astra')
        self.assertEqual(quota.calls[0], ('refresh', 'codex'))
        self.assertEqual(len(result['history']), 2)

    def test_tampered_pending_checkpoint_fails_closed(self):
        state = self.prepare()
        state['checkpoint']['next_steps'] = ['Tampered']
        write_json(self.coordinator.path, state)
        with self.assertRaises(ValueError):
            self.coordinator.read()

    def test_corrupt_coordinator_is_preserved(self):
        for content in ('', '{}', '{"schema_version":1,"schema_version":2}'):
            self.coordinator.path.write_text(content, encoding='utf-8')
            with self.assertRaises(ValueError):
                self.coordinator.read()
            with self.assertRaises(ValueError):
                self.coordinator.initialize('astra', 'rescue')
            self.assertEqual(self.coordinator.path.read_text(), content)

    def test_checkpoint_requires_all_recovery_sections(self):
        for field in ('objective', 'authorization', 'open_jobs', 'validation', 'next_steps'):
            value = deepcopy(self.checkpoint)
            del value[field]
            with self.assertRaises(ValueError):
                validate_checkpoint(value)

    def test_oversize_input_is_rejected_before_replacing_checkpoint(self):
        value = deepcopy(self.checkpoint)
        value['objective'] = 'x' * (512 * 1024)
        with self.assertRaises(ValueError):
            self.coordinator.checkpoint(value, 'astra', self.session, 1)
        self.assertEqual(self.coordinator.read(), self.initial)

    def test_atomic_write_failure_retains_previous_checkpoint(self):
        before = self.coordinator.path.read_bytes()
        with patch('task_store.os.replace', side_effect=OSError('simulated interruption')):
            with self.assertRaises(OSError):
                self.checkpoint_once()
        self.assertEqual(before, self.coordinator.path.read_bytes())
        self.assertEqual(list(self.run.glob('*.tmp')), [])

    def test_prompt_contains_resume_evidence_and_no_claim_of_automatic_transfer(self):
        prompt = self.coordinator.render(self.prepare())
        for text in ('Accepted parser', 'keep-held', 'Receiving Claude model: opus', 'uncertain', 'coordinator.json'):
            self.assertIn(text, prompt)

    def test_manual_handoff_freezes_configured_opus_model(self):
        prepared = self.prepare()
        self.assertEqual(prepared['handoff']['receiving_model'], 'opus')
        with patch('claude_models.select_model', side_effect=AssertionError('Use the frozen model')):
            claimed = self.claim(prepared)
        self.assertEqual(claimed['handoff']['receiving_model'], 'opus')

    def test_legacy_fable_handoff_cannot_claim_during_pause(self):
        prepared = self.prepare()
        del prepared['handoff']['receiving_model']
        self.coordinator.save(prepared)
        before = self.coordinator.path.read_bytes()
        quota = Quota()
        with patch('claude_models._configuration', return_value={'policy': {'paused_claude_model_families': ['fable']}}):
            with self.assertRaisesRegex(ValueError, 'paused'):
                self.claim(prepared, quota=quota)
        self.assertEqual(quota.calls, [])
        self.assertEqual(self.coordinator.path.read_bytes(), before)

    def test_cli_status_omits_private_checkpoint_and_prompt_exports_it_explicitly(self):
        self.checkpoint['objective'] = 'PRIVATE synthetic objective marker'
        self.checkpoint_once()
        entry = Path(__file__).resolve().parents[1] / 'orchestrator.py'
        def run(action):
            return subprocess.run([sys.executable, str(entry), 'lead', action, '--run', str(self.run)],
                                  capture_output=True, text=True, encoding='utf-8', timeout=15)
        status = run('status')
        self.assertEqual(status.returncode, 0, status.stderr)
        self.assertNotIn('PRIVATE', status.stdout)
        self.assertEqual(json.loads(status.stdout)['generation'], 2)
        prompt = run('prompt')
        self.assertEqual(prompt.returncode, 0, prompt.stderr)
        self.assertIn('PRIVATE', prompt.stdout)

    def test_cli_corrupt_json_reports_error_without_leaking_content(self):
        self.coordinator.path.write_text('PRIVATE broken JSON', encoding='utf-8')
        entry = Path(__file__).resolve().parents[1] / 'orchestrator.py'
        result = subprocess.run([sys.executable, str(entry), 'lead', 'status', '--run', str(self.run)],
                                capture_output=True, text=True, encoding='utf-8', timeout=15)
        self.assertEqual(result.returncode, 2)
        self.assertNotIn('PRIVATE', result.stdout + result.stderr)

    def test_arbitrary_directory_is_not_a_run(self):
        with self.assertRaises(ValueError):
            Coordinator(self.workspace)


    def test_near_zero_trigger_uses_actual_remainder_not_worker_floor(self):
        quota = Quota()
        for remaining, expected in [(20, 'continue_astra'), (10, 'continue_astra'), (5, 'handoff_due'), (0, 'handoff_due')]:
            with patch.object(quota, 'check', return_value={
                'allowed': False, 'reasons': ['weekly: task plus safety buffer exceeds available quota'],
                'windows': [{'remaining_pct': remaining, 'available_pct': 0}]}):
                result = self.coordinator.readiness(guard=quota)
            self.assertEqual(result['status'], expected)
            self.assertEqual(self.coordinator.read()['owner'], 'astra')

    def test_exhausted_only_threshold_and_shared_windows(self):
        quota = Quota()
        with patch.object(quota, 'check', return_value={'reasons': [], 'windows': [{'remaining_pct': 99}, {'remaining_pct': 1}]}):
            self.assertEqual(self.coordinator.readiness(0, quota)['status'], 'continue_astra')
            self.assertEqual(self.coordinator.readiness(5, quota)['remaining_pct'], 1)

    def test_unknown_or_stale_quota_never_triggers_near_zero_takeover(self):
        quota = Quota()
        for check in ({'reasons': ['weekly: reading is stale'], 'windows': [{'remaining_pct': 0}]},
                      {'reasons': [], 'windows': []}, {'reasons': [], 'windows': [{'remaining_pct': float('nan')}]}):
            with patch.object(quota, 'check', return_value=check):
                self.assertEqual(self.coordinator.readiness(guard=quota)['status'], 'unknown')
        self.assertEqual(self.coordinator.readiness(guard=Quota(fresh=False))['status'], 'unknown')

    def test_readiness_checks_the_current_lead_after_handoff(self):
        prepared = self.prepare()
        quota = Quota()
        self.assertEqual(self.coordinator.readiness(guard=quota)['status'], 'handoff_pending')
        self.claim(prepared)
        with patch.object(quota, 'check', return_value={'reasons': [], 'windows': [{'remaining_pct': 5}]}):
            self.assertEqual(self.coordinator.readiness(guard=quota)['status'], 'handoff_due')
        self.assertEqual(quota.calls, [('refresh', 'claude')])

    def test_invalid_threshold_is_rejected(self):
        for threshold in (-1, 11, True, float('nan')):
            with self.assertRaises(ValueError):
                self.coordinator.readiness(threshold, Quota())


    def test_claim_whitespace_retry_is_idempotent(self):
        prepared = self.prepare()
        first = self.claim(prepared, session=' receiving-session ')
        self.assertEqual(self.claim(prepared, session=' receiving-session '), first)

    def test_current_lead_can_yield_without_manual_stop_attestation(self):
        self.checkpoint_once()
        with self.assertRaises(ValueError):
            self.coordinator.prepare('fable', 'near-zero', 2, owner='astra', session='wrong', yield_lead=True)
        result = self.coordinator.prepare('fable', 'near-zero', 2, owner='astra', session=self.session, yield_lead=True)
        self.assertTrue(result['handoff']['yielded_by_lead'])
        self.assertEqual(result['handoff']['from_session'], self.session)

    def test_outgoing_session_becomes_worker_only_after_successful_claim(self):
        self.checkpoint_once()
        prepared = self.coordinator.prepare('fable', 'near-zero', 2, owner='astra', session=self.session, yield_lead=True)
        self.assertFalse(prepared['handoff']['outgoing_session_stopped'])
        pending = self.coordinator.role('astra', self.session)
        self.assertEqual(pending['role'], 'worker')
        self.assertFalse(pending['can_coordinate'])
        self.assertFalse(pending['may_continue_assigned_work'])
        with self.assertRaises(ValueError):
            self.claim(prepared, quota=Quota(allowed=False))
        self.assertEqual(self.coordinator.role('astra', self.session), pending)
        self.claim(prepared)
        worker = self.coordinator.role('astra', self.session)
        self.assertEqual(worker['role'], 'worker')
        self.assertTrue(worker['may_continue_assigned_work'])
        self.assertTrue(worker['requires_current_lead_assignment'])
        self.assertFalse(worker['can_coordinate'])
        self.assertFalse(worker['quota_checked'])
        self.assertEqual(worker['lead_session'], 'fable-session')
        self.assertEqual(self.coordinator.read()['checkpoint']['open_jobs'], self.checkpoint['open_jobs'])
        self.assertTrue(self.coordinator.role('fable', 'fable-session')['can_coordinate'])

    def test_worker_identity_is_exact_and_cannot_checkpoint_as_lead(self):
        prepared = self.prepare()
        self.claim(prepared)
        for owner, session in [('astra', 'wrong'), ('fable', self.session)]:
            role = self.coordinator.role(owner, session)
            self.assertEqual(role['role'], 'unassigned')
            self.assertFalse(role['can_coordinate'])
            self.assertFalse(role['may_continue_assigned_work'])
        with self.assertRaises(ValueError):
            self.coordinator.checkpoint(self.checkpoint, 'astra', self.session, 4)

    def test_workers_survive_later_handoffs_and_exact_receiver_is_promoted(self):
        first = self.prepare()
        self.claim(first)
        second = self.coordinator.prepare('astra', 'near-zero', 4, owner='fable', session='fable-session', yield_lead=True)
        self.assertFalse(self.coordinator.role('astra', self.session)['may_continue_assigned_work'])
        self.coordinator.claim(second['handoff']['id'], 'new-astra', 5, Quota())
        self.assertTrue(self.coordinator.role('astra', self.session)['may_continue_assigned_work'])
        self.assertTrue(self.coordinator.role('fable', 'fable-session')['may_continue_assigned_work'])
        self.assertEqual(len(self.coordinator.read()['worker_sessions']), 2)
        third = self.coordinator.prepare('fable', 'manual', 6, owner='astra', session='new-astra', yield_lead=True)
        self.coordinator.claim(third['handoff']['id'], 'fable-session', 7, Quota())
        self.assertEqual(len(self.coordinator.read()['worker_sessions']), 2)
        self.assertTrue(self.coordinator.role('fable', 'fable-session')['can_coordinate'])
        self.assertTrue(self.coordinator.role('astra', 'new-astra')['may_continue_assigned_work'])

    def test_legacy_state_remains_readable_and_invalid_worker_identity_fails_closed(self):
        state = self.coordinator.read()
        del state['worker_sessions']
        write_json(self.coordinator.path, state)
        self.assertTrue(self.coordinator.role('astra', self.session)['can_coordinate'])
        state['worker_sessions'] = {'fake-key': {'owner': 'astra', 'session': self.session, 'role': 'worker'}}
        write_json(self.coordinator.path, state)
        with self.assertRaises(ValueError):
            self.coordinator.role('astra', self.session)

    def test_role_lookup_is_read_only_and_reset_does_not_promote_worker(self):
        prepared = self.prepare()
        self.claim(prepared)
        before = self.coordinator.path.read_bytes()
        with patch('coordinator_handoff.Guard', side_effect=AssertionError('Role lookup does not read quota')):
            result = self.coordinator.role('astra', self.session)
        self.assertEqual(result['role'], 'worker')
        self.assertFalse(result['can_coordinate'])
        self.assertEqual(before, self.coordinator.path.read_bytes())

    def test_claim_atomically_persists_lead_and_worker_and_retry_preserves_both(self):
        prepared = self.prepare()
        before = self.coordinator.path.read_bytes()
        with patch('task_store.os.replace', side_effect=OSError('claim persistence failure')):
            with self.assertRaises(OSError):
                self.claim(prepared)
        self.assertEqual(before, self.coordinator.path.read_bytes())
        claimed = self.claim(prepared)
        self.assertEqual(self.claim(prepared), claimed)
        workers = list(claimed['worker_sessions'].values())
        self.assertEqual(len(workers), 1)
        self.assertEqual(workers[0]['session'], self.session)
        self.assertEqual(workers[0]['demoted_at_generation'], claimed['generation'])
        self.assertTrue(workers[0]['session_stopped_at_handoff'])
        self.assertFalse(self.coordinator.role('astra', self.session)['session_liveness_checked'])

    def test_role_cli_reports_worker_without_exporting_checkpoint(self):
        prepared = self.prepare()
        self.claim(prepared)
        result = subprocess.run([sys.executable, str(Path(__file__).resolve().parents[1] / 'orchestrator.py'),
                                 'lead', 'role', '--run', str(self.run), '--owner', 'astra', '--session', self.session],
                                capture_output=True, text=True, timeout=10)
        self.assertEqual(result.returncode, 0, result.stderr)
        role = json.loads(result.stdout)
        self.assertEqual(role['role'], 'worker')
        self.assertTrue(role['may_continue_assigned_work'])
        self.assertNotIn('Accepted parser', result.stdout)

    def test_large_history_is_archived_without_losing_entries(self):
        state = self.coordinator.read()
        state['history'] = [{'id': str(index), 'from': 'astra', 'to': 'fable'} for index in range(51)]
        self.coordinator.save(state)
        current = self.coordinator.read()
        archives = list((self.run / 'coordinator-history').glob('*.json'))
        self.assertEqual(len(current['history']), 50)
        self.assertEqual(len(archives), 1)
        self.assertEqual(json.loads(archives[0].read_text())['handoffs'], [{'id': '0', 'from': 'astra', 'to': 'fable'}])

    def test_pretty_serialized_state_limit_preserves_readable_old_record(self):
        checkpoint = deepcopy(self.checkpoint)
        checkpoint['completed'] = [0] * 65000
        with self.assertRaises(ValueError):
            self.coordinator.checkpoint(checkpoint, 'astra', self.session, 1)
        self.assertEqual(self.coordinator.read(), self.initial)

    def test_provider_refresh_does_not_hold_run_lock(self):
        from usage_guard import file_lock
        prepared = self.prepare()
        quota = Quota()
        def refresh(worker):
            with file_lock(self.coordinator.lock, timeout=0.1):
                self.assertEqual(self.coordinator.read()['status'], 'handoff_ready')
            return {worker: {'ok': True}}
        with patch.object(quota, 'refresh', side_effect=refresh):
            self.claim(prepared, quota=quota)

if __name__ == '__main__':
    unittest.main()
