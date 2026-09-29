"""The user's lead-orchestrator switch: Claude, ASTRA or Sol. No model calls."""
from copy import deepcopy
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'app'))
import claude_models
import lead_selection
from coordinator_handoff import Coordinator, OWNERS
from init_run import create_run
from orchestration_lifecycle import start_run

CONFIG = {
    'policy': {'paused_claude_model_families': ['fable']},
    'workers': [{'id': 'claude', 'requested_model': 'opus'}],
    'coordinator_handoff': {'default': 'astra', 'backup': 'fable', 'backup_model': 'claude-opus-5-5'},
    'unrelated': {'kept': True},
}


class LeadSelectionTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.config = self.root / 'config/workers.json'
        self.config.parent.mkdir()
        self.write(CONFIG)
        for module in (lead_selection, claude_models):
            patched = patch.object(module, 'ROOT', self.root)
            patched.start()
            self.addCleanup(patched.stop)

    def write(self, value):
        self.config.write_text(json.dumps(value, indent=2), encoding='utf-8')

    def saved(self):
        return json.loads(self.config.read_text(encoding='utf-8'))

    def test_default_is_astra_and_lists_all_three_leads_with_models(self):
        described = lead_selection.describe()
        self.assertEqual(described['lead'], 'astra')
        self.assertEqual(described['runner_up'], 'claude')
        options = {option['id']: option for option in described['options']}
        self.assertEqual(sorted(options), ['astra', 'claude', 'sol'])
        self.assertEqual(options['astra']['model'], 'gpt-6-astra')
        self.assertEqual(options['sol']['model'], 'gpt-6-sol')
        self.assertEqual(options['claude']['model'], 'claude-opus-5-5')
        self.assertEqual(options['claude']['model_label'], 'Opus 5.5')
        self.assertEqual(options['claude']['owner'], 'fable')
        self.assertEqual(options['sol']['quota_worker'], 'codex')
        self.assertEqual(described['model_calls'], 0)

    def test_selecting_sol_persists_and_preserves_other_settings(self):
        result = lead_selection.select_lead('sol')
        self.assertEqual(result['lead'], 'sol')
        self.assertEqual(result['runner_up'], 'claude')
        saved = self.saved()
        self.assertEqual(saved['coordinator_handoff']['lead'], 'sol')
        self.assertEqual(saved['coordinator_handoff']['codex_lead'], 'sol')
        self.assertIn('lead_selected_at', saved['coordinator_handoff'])
        self.assertEqual(saved['coordinator_handoff']['backup_model'], 'claude-opus-5-5')
        self.assertEqual(saved['unrelated'], {'kept': True})
        self.assertEqual(lead_selection.describe()['lead'], 'sol')

    def test_claude_runner_up_is_the_last_selected_codex_lead(self):
        lead_selection.select_lead('sol')
        self.assertEqual(lead_selection.select_lead('claude')['runner_up'], 'sol')
        lead_selection.select_lead('astra')
        self.assertEqual(lead_selection.select_lead('claude')['runner_up'], 'astra')
        self.assertEqual(self.saved()['coordinator_handoff']['codex_lead'], 'astra')

    def test_invalid_or_paused_choices_leave_the_saved_selection_unchanged(self):
        lead_selection.select_lead('sol')
        before = self.config.read_bytes()
        for choice in ('fable', 'gpt-6-sol', '', None, 'SOL'):
            with self.subTest(choice=choice):
                with self.assertRaises(ValueError):
                    lead_selection.select_lead(choice)
        paused = deepcopy(self.saved())
        paused['coordinator_handoff']['backup_model'] = 'claude-fable-5'
        self.write(paused)
        before = self.config.read_bytes()
        with self.assertRaisesRegex(ValueError, 'paused'):
            lead_selection.select_lead('claude')
        self.assertEqual(self.config.read_bytes(), before)

    def test_unknown_saved_value_falls_back_to_astra_without_rewriting(self):
        broken = deepcopy(CONFIG)
        broken['coordinator_handoff']['lead'] = 'fable'
        self.write(broken)
        self.assertEqual(lead_selection.describe()['lead'], 'astra')
        self.assertEqual(self.saved()['coordinator_handoff']['lead'], 'fable')

    def test_owner_mapping_covers_every_lead(self):
        self.assertEqual(OWNERS['sol'], 'codex')
        for lead, option in lead_selection.LEADS.items():
            self.assertIn(option['owner'], OWNERS)
            self.assertEqual(lead_selection.lead_for_owner(option['owner']), lead)


class LeadSelectionRunTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        (self.root / 'config').mkdir()
        (self.root / 'config/workers.json').write_text(json.dumps(CONFIG), encoding='utf-8')
        for module in (lead_selection, claude_models):
            patched = patch.object(module, 'ROOT', self.root)
            patched.start()
            self.addCleanup(patched.stop)
        self.workspace = self.root / 'project'
        self.workspace.mkdir()

    def test_new_runs_start_with_the_selected_lead(self):
        for choice, owner in (('sol', 'sol'), ('claude', 'fable'), ('astra', 'astra')):
            with self.subTest(choice=choice):
                lead_selection.select_lead(choice)
                run, _ = create_run(self.workspace, 'lead-' + choice, 'Synthetic objective')
                self.assertEqual(Coordinator(run).read()['owner'], owner)

    def test_explicit_lead_overrides_the_switch_for_one_run(self):
        lead_selection.select_lead('claude')
        run, _ = create_run(self.workspace, 'explicit', 'Synthetic objective', lead='sol')
        self.assertEqual(Coordinator(run).read()['owner'], 'sol')
        with self.assertRaises(ValueError):
            create_run(self.workspace, 'bad-lead', 'Synthetic objective', lead='fable')

    def test_start_packet_names_the_run_lead_and_selected_lead(self):
        lead_selection.select_lead('sol')
        packet = start_run(workspace=self.workspace, name='packet', objective='Synthetic objective',
                           lead='claude', no_memory=True, root=self.root)
        self.assertEqual(packet['coordinator']['owner'], 'fable')
        self.assertEqual(packet['lead_selection']['run_lead'], 'claude')
        self.assertEqual(packet['lead_selection']['selected_lead'], 'sol')
        self.assertFalse(packet['lead_selection']['matches_switch'])
        markdown = (Path(packet['run']) / 'startup-context.md').read_text(encoding='utf-8')
        self.assertIn('Lead orchestrator for this run: Claude', markdown)

    def test_sol_can_receive_and_hand_off_leadership(self):
        run, _ = create_run(self.workspace, 'handoff', 'Synthetic objective', lead='astra')
        coordinator = Coordinator(run)
        state = coordinator.read()
        prepared = coordinator.prepare('sol', 'manual', 1, owner='astra', session=state['session'], yield_lead=True)
        self.assertEqual(prepared['handoff']['receiving_model'], 'gpt-6-sol')
        self.assertIn('Receiving Codex model: gpt-6-sol', coordinator.render(prepared))

        class Guard:
            policy = {'quota_admission_mode': 'advisory'}
            def request_refresh(self, worker):
                return {'status': 'queued'}
            def check(self, worker, size):
                self.worker = worker
                return {'allowed': True, 'worker': worker}
        guard = Guard()
        claimed = coordinator.claim(prepared['handoff']['id'], 'sol-session', 2, guard)
        self.assertEqual(guard.worker, 'codex')
        self.assertEqual((claimed['owner'], claimed['session']), ('sol', 'sol-session'))
        self.assertEqual(coordinator.role('astra', state['session'])['role'], 'worker')
        self.assertTrue(coordinator.role('sol', 'sol-session')['can_coordinate'])


if __name__ == '__main__':
    unittest.main()
