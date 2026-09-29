"""Codex worker route: no live Codex, quota, or billing operations."""
import argparse
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'app'))
import codex_worker
import dispatch_worker as dispatcher
from codex_progress import CodexProgress, CodexProtocolError
from task_store import TaskStore
from worker_execution import WorkerInterrupted, invoke_cloud

# Event shapes observed from codex-cli 0.155 `exec --json` on 2026-09-28.
SUCCESS = [
    {'type': 'thread.started', 'thread_id': '01a0ea6b-1c4c-7c00-b2ea-bdabe6c495a3'},
    {'type': 'item.completed', 'item': {'id': 'item_0', 'type': 'error',
                                        'message': 'Code Mode is unavailable because code-mode host is disabled.'}},
    {'type': 'turn.started'},
    {'type': 'item.completed', 'item': {'id': 'item_1', 'type': 'reasoning', 'text': 'private'}},
    {'type': 'item.completed', 'item': {'id': 'item_2', 'type': 'agent_message', 'text': 'Draft note.'}},
    {'type': 'item.completed', 'item': {'id': 'item_3', 'type': 'agent_message', 'text': 'Final reviewed answer.'}},
    {'type': 'turn.completed', 'usage': {'input_tokens': 8301, 'cached_input_tokens': 0,
                                         'output_tokens': 45, 'reasoning_output_tokens': 0}},
]


def feed(progress, events):
    for index, event in enumerate(events):
        progress.observe(event, float(index))
    return progress


class CommandTests(unittest.TestCase):
    def test_command_runs_tool_free_read_only_plan_sign_in_with_brief_on_stdin(self):
        command = codex_worker.command_for('codex.exe', Path('work'), 'gpt-6-sol', 'low')
        self.assertEqual(command[:2], ['codex.exe', 'exec'])
        self.assertEqual(command[-1], '-')
        for flag in ('--json', '--ephemeral', '--ignore-user-config', '--ignore-rules', '--skip-git-repo-check'):
            self.assertIn(flag, command)
        self.assertEqual(command[command.index('--sandbox') + 1], 'read-only')
        self.assertEqual(command[command.index('--cd') + 1], 'work')
        self.assertEqual(command[command.index('--model') + 1], 'gpt-6-sol')
        overrides = [command[i + 1] for i, part in enumerate(command) if part == '-c']
        for expected in ('model_reasoning_effort="low"', 'approval_policy="never"', 'forced_login_method="chatgpt"',
                         'web_search="disabled"', 'mcp_servers={}', 'skills.include_instructions=false'):
            self.assertIn(expected, overrides)
        disabled = {command[i + 1] for i, part in enumerate(command) if part == '--disable'}
        self.assertTrue({'shell_tool', 'unified_exec', 'code_mode_host', 'multi_agent', 'multi_agent_v2',
                         'apps', 'plugins', 'memories', 'hooks'} <= disabled)
        with self.assertRaises(ValueError):
            codex_worker.command_for('codex.exe', Path('work'), 'gpt-6-sol', 'max')

    def test_model_follows_identity_or_the_switch_codex_lead(self):
        self.assertEqual(codex_worker.select_codex_model('sol'), 'gpt-6-sol')
        self.assertEqual(codex_worker.select_codex_model('astra'), 'gpt-6-astra')
        with patch('lead_selection.describe', return_value={'codex_lead': 'sol'}):
            self.assertEqual(codex_worker.select_codex_model(), 'gpt-6-sol')
        with self.assertRaises(ValueError):
            codex_worker.select_codex_model('claude')

    def test_environment_drops_api_billing_credentials(self):
        env = codex_worker.environment({'OPENAI_API_KEY': 'x', 'CODEX_API_KEY': 'y', 'CODEX_HOME': 'home', 'PATH': 'p'})
        self.assertEqual(env, {'CODEX_HOME': 'home', 'PATH': 'p'})

    def test_only_a_chatgpt_plan_sign_in_is_accepted(self):
        def status(text, code=0):
            return subprocess.CompletedProcess([], code, text, '')
        with patch.object(codex_worker.subprocess, 'run', return_value=status('Logged in using ChatGPT\n')):
            self.assertTrue(codex_worker.plan_signin('codex.exe'))
        with patch.object(codex_worker.subprocess, 'run', return_value=status('Logged in using an API key - sk-...')):
            self.assertFalse(codex_worker.plan_signin('codex.exe'))
        with patch.object(codex_worker.subprocess, 'run', return_value=status('Not logged in', 1)):
            self.assertFalse(codex_worker.plan_signin('codex.exe'))
        with patch.object(codex_worker.subprocess, 'run', side_effect=OSError):
            self.assertFalse(codex_worker.plan_signin('codex.exe'))


class ProgressTests(unittest.TestCase):
    def test_last_answer_message_is_the_result_and_reasoning_is_not_kept(self):
        progress = feed(CodexProgress('gpt-6-sol'), SUCCESS)
        result = progress.terminal_result
        self.assertFalse(result['is_error'])
        self.assertEqual(result['result'], 'Final reviewed answer.')
        self.assertIsNone(result['model'])  # Codex does not report the serving model.
        self.assertEqual(result['configured_model'], 'gpt-6-sol')
        self.assertEqual(result['usage']['input_tokens'], 8301)
        self.assertNotIn('private', json.dumps(result) + progress.partial_response)
        snapshot = progress.snapshot()
        self.assertTrue(snapshot['terminal_received'] and snapshot['turn_started'])
        self.assertEqual(snapshot['notice_count'], 1)

    def test_any_tool_item_stops_the_task(self):
        for itype in ('command_execution', 'file_change', 'mcp_tool_call', 'web_search', 'collab_tool_call', 'new_kind'):
            progress = feed(CodexProgress(), SUCCESS[:3])
            with self.assertRaises(CodexProtocolError) as caught:
                progress.observe({'type': 'item.started', 'item': {'id': 'x', 'type': itype}}, 9.0)
            self.assertEqual(caught.exception.cause, 'codex_tool_use')

    def test_protocol_surprises_stop_the_task(self):
        cases = [({'type': 'tool.started'}, 'codex_unknown_event'),
                 (None, 'codex_malformed_event'),
                 ({'type': 'item.completed', 'item': {'id': 'e', 'type': 'error', 'message': 'exec failed'}},
                  'codex_error_item')]
        for event, cause in cases:
            progress = feed(CodexProgress(), SUCCESS[:3])
            with self.assertRaises(CodexProtocolError) as caught:
                progress.observe(event, 9.0)
            self.assertEqual(caught.exception.cause, cause)
        # Before the turn starts a stray line is only counted, and an unknown event still stops it.
        early = CodexProgress()
        early.observe(None, 0.0)
        self.assertEqual(early.snapshot()['malformed_events'], 1)
        with self.assertRaises(CodexProtocolError):
            early.observe({'type': 'session.patched'}, 0.1)
        # The known code-mode notice is tolerated during the turn too.
        late = feed(CodexProgress(), SUCCESS[2:3] + SUCCESS[1:2] + SUCCESS[4:])
        self.assertFalse(late.terminal_result['is_error'])

    def test_usage_limit_failure_is_a_confirmed_quota_rejection(self):
        progress = feed(CodexProgress(), SUCCESS[:3] + [
            {'type': 'error', 'message': "You've hit your usage limit. Try again later."},
            {'type': 'turn.failed', 'error': {'message': "You've hit your usage limit. Try again later."}}])
        self.assertTrue(progress.terminal_result['is_error'])
        completed = subprocess.CompletedProcess([], 1, json.dumps(progress.terminal_result), '')
        self.assertTrue(dispatcher.confirmed_quota_rejection(completed))
        other = feed(CodexProgress(), SUCCESS[:3] + [{'type': 'turn.failed', 'error': {'message': 'model not found'}}])
        self.assertFalse(dispatcher.confirmed_quota_rejection(
            subprocess.CompletedProcess([], 1, json.dumps(other.terminal_result), '')))

    def test_turn_without_an_answer_is_a_failure(self):
        progress = feed(CodexProgress(), SUCCESS[:3] + [SUCCESS[-1]])
        self.assertTrue(progress.terminal_result['is_error'])

    def test_stream_through_the_process_runner(self):
        with tempfile.TemporaryDirectory() as folder:
            script = 'import json,sys\nsys.stdin.read()\n' + ''.join(
                f'print(json.dumps({event!r}), flush=True)\n' for event in SUCCESS)
            completed = invoke_cloud([sys.executable, '-c', script, '--json'], 'brief', folder, None,
                                     timeout_seconds=60, protocol='codex', expected_model='gpt-6-sol')
            self.assertEqual(completed.returncode, 0)
            self.assertEqual(json.loads(completed.stdout)['result'], 'Final reviewed answer.')
            tool = ('import json,sys\nsys.stdin.read()\n'
                    'print(json.dumps({"type":"turn.started"}), flush=True)\n'
                    'print(json.dumps({"type":"item.started","item":{"id":"i","type":"command_execution"}}), flush=True)\n'
                    'import time; time.sleep(20)\n')
            with self.assertRaises(WorkerInterrupted) as caught:
                invoke_cloud([sys.executable, '-c', tool], 'brief', folder, None,
                             timeout_seconds=60, protocol='codex')
            self.assertEqual(caught.exception.cause, 'codex_tool_use')
            self.assertTrue(caught.exception.process_terminated)


class DispatchTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.prompt = self.root / 'prompt.txt'
        self.prompt.write_text('Review the supplied function for defects.', encoding='utf-8')
        self.store = TaskStore(self.root / 'tasks')
        self.guard = Mock()
        self.guard.check.return_value = {'allowed': True, 'reservation_id': 'reservation-test'}
        self.guard.policy = {'quota_admission_mode': 'advisory'}
        self.guard.request_refresh.return_value = {'status': 'queued'}
        self.args = argparse.Namespace(worker='codex', prompt_file=self.prompt, output=self.root / 'answer.json',
                                       task='Codex review', size='small', codex_model='sol', codex_effort='low',
                                       no_memory=True)
        self.terminal = {'type': 'result', 'is_error': False, 'result': 'Codex answer.', 'model': None,
                         'configured_model': 'gpt-6-sol', 'usage': {'input_tokens': 10, 'output_tokens': 5}}
        self.invoke = Mock(return_value=self.completed(0, self.terminal, turn_started=True))
        for item in (patch.object(dispatcher, 'ensure_directories'),
                     patch.object(dispatcher, 'invoke_cloud', self.invoke),
                     patch.object(codex_worker, 'executable', return_value=sys.executable),
                     patch.object(codex_worker, 'plan_signin', return_value=True)):
            item.start()
            self.addCleanup(item.stop)

    @staticmethod
    def completed(code, terminal, *, turn_started):
        done = subprocess.CompletedProcess(['codex'], code, json.dumps(terminal or {}), '')
        done.process_pid = 1
        done.progress = {'terminal_received': terminal is not None, 'turn_started': turn_started}
        return done

    def run_task(self):
        return dispatcher.dispatch(self.args, guard_factory=Mock(return_value=self.guard), store=self.store,
                                   workspaces=self.root / 'workspaces')

    def test_codex_task_runs_isolated_and_reserves_codex_allowance(self):
        result = self.run_task()
        self.assertEqual(result['status'], 'awaiting_review', result.get('error'))
        self.assertEqual(result['response'], 'Codex answer.')
        self.assertEqual(result['requested_model'], 'gpt-6-sol')
        self.assertEqual(result['execution_configuration']['sandbox'], 'read-only')
        self.guard.check.assert_called_once()
        self.assertEqual(self.guard.check.call_args.args[0], 'codex')
        command, stdin = self.invoke.call_args.args[:2]
        self.assertEqual(command[command.index('--model') + 1], 'gpt-6-sol')
        self.assertTrue(stdin.startswith(dispatcher.PREFIX))
        self.assertNotIn(stdin, command)
        self.assertEqual(self.invoke.call_args.kwargs['protocol'], 'codex')

    def test_non_plan_sign_in_holds_before_quota_or_inference(self):
        with patch.object(codex_worker, 'plan_signin', return_value=False):
            result = self.run_task()
        self.assertEqual(result['status'], 'held')
        self.assertIn('ChatGPT plan sign-in', result['reason'])
        self.guard.check.assert_not_called()
        self.invoke.assert_not_called()

    def test_exit_before_the_turn_starts_is_a_plain_failure(self):
        self.invoke.return_value = self.completed(1, None, turn_started=False)
        result = self.run_task()
        self.assertEqual((result['status'], result['execution_status']), ('failed', 'failed'))
        self.assertEqual(result['reservation_state'], 'finished_pending_fresh_quota')

    def test_exit_after_the_turn_started_keeps_the_reservation_for_reconciliation(self):
        self.invoke.return_value = self.completed(1, None, turn_started=True)
        result = self.run_task()
        self.assertEqual((result['status'], result['execution_status']), ('recovery_required', 'uncertain'))
        self.assertEqual(result['reservation_state'], 'held_for_reconciliation')

    def test_assignment_identity_includes_codex_model_and_effort(self):
        self.args.project, self.args.assignment_id = 'agent-orchestrator', 'codex-review-1'
        first = self.run_task()
        self.args.output = self.root / 'second.json'
        reused = self.run_task()
        self.assertTrue(reused['assignment_reused'])
        self.assertEqual(reused['job_id'], first['job_id'])
        self.assertEqual(self.invoke.call_count, 1)
        self.args.output = self.root / 'third.json'
        self.args.codex_model = 'astra'
        again = self.run_task()
        self.assertEqual(again['status'], 'held')
        self.assertFalse(again['assignment_reused'])


class HandoffTests(unittest.TestCase):
    def test_codex_fallback_freezes_its_model_and_other_plans_are_unchanged(self):
        import task_handoff
        with tempfile.TemporaryDirectory() as folder:
            brief = Path(folder) / 'brief.txt'
            brief.write_text('Objective', encoding='utf-8')
            args = argparse.Namespace(worker='grok', fallback_worker=['codex'], size='small', category='review',
                                      project='p', assignment_id='a', prompt_file=brief, no_memory=True,
                                      codex_model='sol', codex_effort='high')
            _, _, contract, _ = task_handoff._configuration(args)
            self.assertEqual((contract['codex_model'], contract['codex_effort']), ('gpt-6-sol', 'high'))
            args.fallback_worker = ['claude']
            _, _, contract, _ = task_handoff._configuration(args)
            self.assertNotIn('codex_model', contract)


if __name__ == '__main__':
    unittest.main()
