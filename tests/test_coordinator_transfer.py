"""Exercise real local quota reservations with fake process launches only."""
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'app'))
from coordinator_handoff import Coordinator
from coordinator_transfer import background_id, launch_command, launch_fable, transfer
from init_run import create_run
from usage_guard import Guard, stamp

LISTING = [
    {'kind': 'interactive', 'name': 'openwhispr-58', 'sessionId': 'aaaa'},
    {'kind': 'background', 'id': 'aaaa1111', 'name': 'Fable coordinator 0ld0ld0l', 'startedAt': 1},
    {'kind': 'background', 'id': '10dea955', 'name': 'Claude Opus coordinator 1a02b462', 'startedAt': 2},
]


class LaunchViewerTests(unittest.TestCase):
    """launch_fable with fake process calls; nothing is executed or attached."""

    def setUp(self):
        self.command = ['fake-claude.exe', '--bg', '--model', 'opus', '--session-id', 'pinned',
                        '--name', 'Claude Opus coordinator 1a02b462', '--permission-mode', 'auto', '{}']
        self.opened = []

    def viewer(self, executable, identifier, workspace, env):
        self.opened.append((executable, identifier, workspace))
        return 'opened'

    def launch(self, returncode=0, stdout='', listing=LISTING, viewer=None):
        run = lambda *args, **kwargs: SimpleNamespace(returncode=returncode, stdout=stdout, stderr='')
        return launch_fable(self.command, Path('workspace'), run=run,
                            agents=lambda executable, workspace, env: listing, viewer=viewer or self.viewer)

    def test_submitted_launch_opens_visible_console_for_the_named_session(self):
        outcome = self.launch()
        self.assertEqual(outcome, {'status': 'submitted', 'exit_code': 0, 'background_id': '10dea955', 'viewer': 'opened'})
        self.assertEqual(self.opened, [('fake-claude.exe', '10dea955', Path('workspace'))])

    def test_failed_submission_never_opens_a_viewer(self):
        self.assertEqual(self.launch(returncode=1), {'status': 'uncertain', 'exit_code': 1})
        self.assertEqual(self.opened, [])

    def test_paused_fable_never_starts_a_process_or_viewer(self):
        self.command[self.command.index('--model') + 1] = 'claude-fable-5'
        with patch('claude_models._configuration', return_value={'policy': {'paused_claude_model_families': ['fable']}}):
            with self.assertRaisesRegex(ValueError, 'paused'):
                launch_fable(self.command, Path('workspace'),
                             run=lambda *args, **kwargs: self.fail('Paused model must never execute'),
                             viewer=self.viewer)
        self.assertEqual(self.opened, [])

    def test_unresolved_id_records_submission_without_viewer(self):
        outcome = self.launch(listing=[], stdout='started in background\n')
        self.assertEqual(outcome['status'], 'submitted')
        self.assertIsNone(outcome['background_id'])
        self.assertEqual(outcome['viewer'], 'unresolved')
        self.assertEqual(self.opened, [])

    def test_viewer_failure_keeps_submission_receipt(self):
        def broken(*args):
            raise OSError('no console')
        outcome = self.launch(viewer=broken)
        self.assertEqual(outcome['status'], 'submitted')
        self.assertEqual(outcome['background_id'], '10dea955')
        self.assertEqual(outcome['viewer'], 'failed:OSError')

    def test_background_id_prefers_listing_then_printed_id(self):
        self.assertEqual(background_id('', LISTING, 'Claude Opus coordinator 1a02b462'), '10dea955')
        self.assertEqual(background_id('Started 10dea955 in background', [], 'Claude Opus coordinator 1a02b462'), '10dea955')
        self.assertIsNone(background_id('no id here', [], 'Claude Opus coordinator 1a02b462'))
        self.assertIsNone(background_id('', ['not a row', None], 'Claude Opus coordinator 1a02b462'))


class LaunchPermissionTests(unittest.TestCase):
    """Inspect launch arguments against temporary projects; never start Claude."""

    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.home = Path(temporary.name) / 'user'
        self.application = self.home / 'Orchestrator'
        self.skill = self.home / '.claude/skills/multi-model-orchestrator'
        self.skill.mkdir(parents=True)
        self.run = self.application / '.orchestration/synthetic-run'
        self.run.mkdir(parents=True)
        self.coordinator = SimpleNamespace(run=self.run, workspace=self.application)
        self.executable = self.application / 'fake-claude.exe'
        self.executable.write_text('fixture; never execute', encoding='utf-8')
        self.addCleanup(patch.stopall)
        patch('coordinator_transfer.ROOT', self.application).start()
        patch('coordinator_transfer.Path.home', return_value=self.home).start()

    def command(self, record=None):
        (self.run / 'run.json').write_text(json.dumps(record or {}), encoding='utf-8')
        return launch_command(self.coordinator, 'pinned-session', 'handoff-id', self.executable)

    def settings(self, record=None):
        command = self.command(record)
        return json.loads(command[command.index('--settings') + 1])

    def test_default_run_uses_auto_and_grants_only_the_external_skill(self):
        command = self.command()
        self.assertEqual(command[command.index('--permission-mode') + 1], 'auto')
        settings = json.loads(command[command.index('--settings') + 1])
        self.assertEqual(settings['permissions'], {'additionalDirectories': [str(self.skill.resolve())]})
        self.assertEqual(settings['worktree']['bgIsolation'], 'none')
        self.assertFalse(settings['remoteControlAtStartup'])
        self.assertNotIn('bypassPermissions', command)
        self.assertNotIn('--dangerously-skip-permissions', command)
        self.assertIn('Honor explicit ask/deny rules', command[-1])
        self.assertEqual(json.loads(command[-1])['receiving_session'], 'pinned-session')

    def test_hosted_run_grants_exact_source_and_worktree_with_spaces(self):
        source = self.home / 'OpenWhispr source'
        worktree = self.home / 'tmp/OpenWhispr working tree'
        source.mkdir()
        worktree.mkdir(parents=True)
        settings = self.settings({'source_workspace': str(source), 'implementation_workspace': str(worktree)})
        self.assertEqual(settings['permissions']['additionalDirectories'],
                         [str(path.resolve()) for path in (self.skill, source, worktree)])

    def test_run_in_another_project_also_grants_the_orchestrator_application(self):
        workspace = self.home / 'Another project'
        self.run = workspace / '.orchestration/synthetic-run'
        self.run.mkdir(parents=True)
        self.coordinator = SimpleNamespace(run=self.run, workspace=workspace)
        settings = self.settings({'source_workspace': str(workspace)})
        self.assertEqual(settings['permissions']['additionalDirectories'],
                         [str(path.resolve()) for path in (self.application, self.skill)])

    def test_optional_invalid_missing_and_broad_directories_are_omitted(self):
        for value in (None, 42, [], {}, '', ' ', 'relative/project', str(self.executable),
                      str(self.home / 'missing/worktree'), str(self.home),
                      str(self.home.parent), self.home.anchor, str(self.home / 'bad\0path')):
            with self.subTest(value=value):
                settings = self.settings({'source_workspace': value, 'implementation_workspace': value})
                self.assertEqual(settings['permissions']['additionalDirectories'], [str(self.skill.resolve())])

    def test_directory_aliases_and_folders_already_covered_by_cwd_are_deduplicated(self):
        source = self.home / 'Source project'
        source.mkdir()
        alias = source / '..' / source.name
        settings = self.settings({'source_workspace': str(source), 'implementation_workspace': str(alias)})
        self.assertEqual(settings['permissions']['additionalDirectories'],
                         [str(self.skill.resolve()), str(source.resolve())])
        if os.name == 'nt':
            settings = self.settings({'source_workspace': str(source), 'implementation_workspace': str(source).upper()})
            self.assertEqual(len(settings['permissions']['additionalDirectories']), 2)
        settings = self.settings({'source_workspace': str(self.application), 'implementation_workspace': str(self.run)})
        self.assertEqual(settings['permissions']['additionalDirectories'], [str(self.skill.resolve())])

    def test_missing_skill_does_not_grant_its_parent(self):
        self.skill.rmdir()
        self.assertEqual(self.settings()['permissions']['additionalDirectories'], [])


class FixtureGuard(Guard):
    def __init__(self, root):
        super().__init__(root)
        self.remaining = {'codex': 5, 'claude': 90}
        self.failed = set()
        self.refreshes = []

    def refresh(self, provider):
        self.refreshes.append(provider)
        if provider in self.failed:
            return {provider: {'ok': False}}
        keys = ['codex-weekly'] if provider == 'codex' else ['claude-five-hour', 'claude-seven-day']
        self.observe([{'id': key, 'remaining_pct': self.remaining[provider], 'observed_at': stamp(),
                       'source': 'synthetic fixture', 'max_age_seconds': 600} for key in keys],
                     provider, complete=True, memberships={provider: keys})
        return {provider: {'ok': True}}


class TransferTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.run, _ = create_run(self.root, 'transfer-test', 'Finish the synthetic task')
        self.coordinator = Coordinator(self.run)
        self.state = self.coordinator.read()
        self.checkpoint = deepcopy(self.state['checkpoint'])
        self.checkpoint['completed'] = ['Accepted source work']
        self.guard = FixtureGuard(self.root / 'quota')
        self.executable = self.root / 'fake-claude.exe'
        self.executable.write_text('fixture; never execute', encoding='utf-8')
        self.started = []

    def launch(self, command, workspace):
        persisted = self.coordinator.read()
        self.assertEqual(persisted['status'], 'handoff_ready')
        self.assertEqual(persisted['handoff']['launch']['status'], 'launching')
        self.started.append((command, workspace))
        return {'status': 'submitted', 'exit_code': 0}

    def transfer(self, **kwargs):
        return transfer(self.coordinator, self.checkpoint, 'astra', self.state['session'], 1,
                        guard=self.guard, launcher=kwargs.pop('launcher', self.launch),
                        executable=self.executable, **kwargs)

    def test_self_yield_persists_checkpoint_and_intent_before_launch(self):
        result = self.transfer()
        self.assertEqual(result['checkpoint'], self.checkpoint)
        self.assertEqual(result['generation'], 2)
        self.assertTrue(result['handoff']['yielded_by_lead'])
        self.assertEqual(result['handoff']['from_session'], self.state['session'])
        self.assertEqual(result['handoff']['outgoing_role'], 'worker')
        self.assertFalse(result['handoff']['outgoing_session_stopped'])
        self.assertEqual(result['handoff']['launch']['status'], 'submitted')
        self.assertEqual(len(self.started), 1)
        command, workspace = self.started[0]
        self.assertEqual(workspace, self.root)
        self.assertIn('--bg', command)
        self.assertEqual(command[command.index('--model') + 1], 'opus')
        self.assertEqual(result['handoff']['launch']['model'], 'opus')
        self.assertEqual(result['handoff']['receiving_model'], 'opus')
        self.assertEqual(command[command.index('--name') + 1], result['handoff']['launch']['name'])
        self.assertTrue(result['handoff']['launch']['name'].startswith('Claude Opus coordinator '))
        self.assertEqual(command[command.index('--session-id') + 1], result['handoff']['launch']['session'])
        self.assertEqual(command[command.index('--permission-mode') + 1], 'auto')
        self.assertNotIn('bypassPermissions', command)
        self.assertNotIn('--dangerously-skip-permissions', command)
        self.assertIn('remains a project worker', command[-1])

    def test_launch_receipt_keeps_viewer_details(self):
        def launch(command, workspace):
            return dict(self.launch(command, workspace), background_id='10dea955', viewer='opened')
        result = self.transfer(launcher=launch)
        self.assertEqual(result['handoff']['launch']['background_id'], '10dea955')
        self.assertEqual(result['handoff']['launch']['viewer'], 'opened')
        self.assertIn('claude attach 10dea955', self.coordinator.render(result))

    def test_high_or_unknown_source_quota_never_yields_or_launches(self):
        for remaining in (20, 10, 6):
            self.guard.remaining['codex'] = remaining
            self.assertTrue(self.transfer()['transfer_held'])
        self.guard.failed.add('codex')
        self.assertTrue(self.transfer()['transfer_held'])
        self.assertEqual(self.coordinator.read(), self.state)
        self.assertEqual(self.started, [])

    def test_receiver_low_or_unknown_keeps_outgoing_lead(self):
        self.guard.remaining['claude'] = 10
        self.assertTrue(self.transfer()['transfer_held'])
        self.guard.failed.add('claude')
        self.assertTrue(self.transfer()['transfer_held'])
        self.assertEqual(self.coordinator.read(), self.state)
        self.assertEqual(self.started, [])

    def test_stale_or_different_session_cannot_self_yield(self):
        with self.assertRaises(ValueError):
            transfer(self.coordinator, self.checkpoint, 'astra', 'wrong-session', 1,
                     guard=self.guard, launcher=self.launch, executable=self.executable)
        self.assertEqual(self.guard.refreshes, [])
        self.assertEqual(self.started, [])

    def test_duplicate_request_never_reserves_or_launches_twice(self):
        first = self.transfer()
        calls = self.guard.refreshes.copy()
        second = self.transfer()
        self.assertTrue(second['transfer_reused'])
        self.assertEqual(first['handoff'], second['handoff'])
        self.assertEqual(self.guard.refreshes, calls)
        self.assertEqual(len(self.started), 1)

    def test_uncertain_timeout_is_durable_and_never_relaunched(self):
        def timeout(command, workspace):
            self.launch(command, workspace)
            raise subprocess.TimeoutExpired('synthetic', 45)
        first = self.transfer(launcher=timeout)
        self.assertEqual(first['handoff']['launch']['status'], 'uncertain')
        self.assertTrue(self.transfer()['transfer_reused'])
        self.assertEqual(len(self.started), 1)
        token = first['handoff']['launch']['reservation_id']
        with self.guard.state() as data:
            self.assertIsNone(data['reservations'][token]['finished_at'])

    def test_crash_after_intent_never_causes_a_second_start(self):
        def interrupted(command, workspace):
            self.launch(command, workspace)
            raise KeyboardInterrupt()
        with self.assertRaises(KeyboardInterrupt):
            self.transfer(launcher=interrupted)
        self.assertEqual(self.coordinator.read()['handoff']['launch']['status'], 'launching')
        self.assertTrue(self.transfer()['transfer_reused'])
        self.assertEqual(len(self.started), 1)

    def test_failure_to_persist_intent_never_launches_and_finishes_only_new_reservation(self):
        self.guard.refresh('claude')
        existing = self.guard.check('claude', 'small', reserve=True, task='Unrelated uncertain job')['reservation_id']
        with patch.object(self.coordinator, 'save', side_effect=OSError('fixture write failure')):
            with self.assertRaises(OSError):
                self.transfer()
        self.assertEqual(self.started, [])
        with self.guard.state() as data:
            self.assertIsNone(data['reservations'][existing]['finished_at'])
            new = [r for token, r in data['reservations'].items() if token != existing]
            self.assertEqual(len(new), 1)
            self.assertEqual(new[0]['outcome'], 'failed')

    def test_concurrent_same_request_launches_once(self):
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(lambda _: self.transfer(), range(2)))
        self.assertEqual(len(self.started), 1)
        self.assertEqual(sum(bool(row.get('transfer_reused')) for row in results), 1)

    def test_pinned_receiver_claims_without_double_counting_its_reservation(self):
        self.guard.remaining['claude'] = 13
        result = self.transfer()
        launch = result['handoff']['launch']
        with self.assertRaises(ValueError):
            self.coordinator.claim(result['handoff']['id'], 'wrong-receiver', 2, self.guard)
        claimed = self.coordinator.claim(result['handoff']['id'], launch['session'], 2, self.guard)
        self.assertEqual(claimed['owner'], 'fable')
        self.assertEqual(claimed['generation'], 3)
        outgoing = self.coordinator.role('astra', self.state['session'])
        self.assertTrue(outgoing['may_continue_assigned_work'])
        self.assertFalse(outgoing['can_coordinate'])
        with self.guard.state() as data:
            self.assertIsNone(data['reservations'][launch['reservation_id']]['finished_at'])
        self.assertTrue(self.transfer()['transfer_reused'])
        self.assertEqual(len(self.started), 1)

    def test_fast_claim_is_not_overwritten_by_launcher_result(self):
        def launch_and_claim(command, workspace):
            outcome = self.launch(command, workspace)
            pending = self.coordinator.read()
            self.coordinator.claim(pending['handoff']['id'], pending['handoff']['launch']['session'], 2, self.guard)
            return outcome
        result = self.transfer(launcher=launch_and_claim)
        self.assertEqual(result['owner'], 'fable')
        self.assertEqual(result['status'], 'active')
        self.assertIn('claim_confirmed_at', result['handoff']['launch'])

    def test_missing_executable_keeps_lead_active(self):
        self.executable.unlink()
        with self.assertRaises(ValueError):
            self.transfer()
        self.assertEqual(self.coordinator.read(), self.state)

    def test_paused_configured_coordinator_keeps_lead_and_never_reserves(self):
        config = {'policy': {'paused_claude_model_families': ['fable']},
                  'coordinator_handoff': {'backup_model': 'claude-fable-5'}}
        with patch('claude_models._configuration', return_value=config):
            with self.assertRaisesRegex(ValueError, 'paused'):
                self.transfer(manual=True)
        self.assertEqual(self.coordinator.read(), self.state)
        self.assertEqual(self.started, [])
        self.assertEqual(self.guard.refreshes, [])
        with self.guard.state() as data:
            self.assertEqual(data['reservations'], {})

    def test_old_fable_receipt_cannot_claim_using_new_opus_default(self):
        pending = self.transfer()
        pending['handoff']['launch']['model'] = 'claude-fable-5'
        self.coordinator.save(pending)
        before = self.coordinator.path.read_bytes()
        refreshes = self.guard.refreshes.copy()
        launch = pending['handoff']['launch']
        with patch('claude_models._configuration', return_value={'policy': {'paused_claude_model_families': ['fable']}}):
            with self.assertRaisesRegex(ValueError, 'paused'):
                self.coordinator.claim(pending['handoff']['id'], launch['session'], 2, self.guard)
            prompt = self.coordinator.render(pending)
        self.assertEqual(self.coordinator.path.read_bytes(), before)
        self.assertEqual(self.guard.refreshes, refreshes)
        self.assertIn('Do not resume or claim this handoff', prompt)
        with self.guard.state() as data:
            self.assertIsNone(data['reservations'][launch['reservation_id']]['finished_at'])

    def test_invalid_run_metadata_keeps_lead_active_without_reserving_or_launching(self):
        (self.run / 'run.json').write_text('{invalid', encoding='utf-8')
        with self.assertRaises(ValueError):
            self.transfer()
        self.assertEqual(self.coordinator.read(), self.state)
        self.assertEqual(self.started, [])
        with self.guard.state() as data:
            self.assertEqual(data['reservations'], {})


if __name__ == '__main__':
    unittest.main()
