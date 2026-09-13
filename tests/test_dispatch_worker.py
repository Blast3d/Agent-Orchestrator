"""Dispatcher regressions: no live quota, provider, GPU, or billing operations."""
import argparse
from concurrent.futures import ThreadPoolExecutor
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
import urllib.error
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'app'))
import dispatch_worker as dispatcher
from task_store import OutputClaim, TaskStore


class DispatchTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.prompt = self.root / 'prompt.txt'
        self.prompt.write_text('Find concrete defects in the supplied code.', encoding='utf-8')
        self.store = TaskStore(self.root / 'tasks')
        self.guard = Mock()
        self.guard.refresh.side_effect = lambda provider: {provider: {'ok': True}}
        self.guard.check.return_value = {'allowed': True, 'reservation_id': 'reservation-test'}
        self.factory = Mock(return_value=self.guard)
        self.args = argparse.Namespace(worker='claude', prompt_file=self.prompt,
            output=self.root / 'answer.json', task='Audit dispatcher', size='small')
        self.payload = {'result': 'Specific answer for Codex to independently check.',
            'modelUsage': {'claude-sonnet-test': {'inputTokens': 15, 'outputTokens': 23}},
            'usage': {'input_tokens': 15, 'output_tokens': 23}, 'total_cost_usd': 0.012}
        self.invoke = Mock(return_value=subprocess.CompletedProcess(['native-worker'], 0,
                                                                  json.dumps(self.payload), ''))
        for item in (
            patch.object(dispatcher, 'ensure_directories'),
            patch.object(dispatcher, 'invoke_cloud', self.invoke),
            patch.object(dispatcher, 'cloud_command', return_value=([sys.executable], 'brief')),
        ):
            item.start()
            self.addCleanup(item.stop)

    def run_task(self):
        return dispatcher.dispatch(self.args, guard_factory=self.factory, store=self.store,
                                   workspaces=self.root / 'workspaces')

    def canonical(self, result):
        return json.loads(Path(result['canonical_result']).read_text(encoding='utf-8'))

    def test_no_memory_still_supplies_operating_guide_in_actual_request(self):
        import hashlib
        self.args.no_memory = True
        result = self.run_task()
        sent_prompt = dispatcher.cloud_command.call_args.args[1]
        self.assertTrue(sent_prompt.startswith('# Orchestration Operating Guide (v1)'))
        self.assertIn(self.prompt.read_text(), sent_prompt)
        self.assertNotIn('memory_context', result)
        context = self.canonical(result)['orchestration_context']
        self.assertTrue(context['execution_requested'])
        self.assertEqual(context['request_sha256'], hashlib.sha256((dispatcher.PREFIX + sent_prompt).encode()).hexdigest())
        index = json.loads((self.store.root / result['job_id'] / 'record.json').read_text(encoding='utf-8'))
        self.assertNotIn('context', index['orchestration_context'])
        self.assertEqual(index['orchestration_context']['sha256'], context['sha256'])

    def test_missing_operating_guide_holds_before_quota_or_provider(self):
        with patch.object(dispatcher, 'load_operating_context', side_effect=dispatcher.OperatingContextError('Guide missing')):
            result = self.run_task()
        self.assertEqual(result['execution_status'], 'held')
        self.factory.assert_not_called()
        self.invoke.assert_not_called()
        self.assertIn('Guide missing', result['reason'])

    def test_guide_change_during_quota_preparation_prevents_execution(self):
        import orchestration_context
        original = orchestration_context.load_operating_context()
        changed = dict(original, sha256='0' * 64)
        with patch.object(orchestration_context, 'load_operating_context', return_value=changed):
            result = self.run_task()
        self.assertEqual(result['execution_status'], 'held')
        self.assertFalse(result['orchestration_context']['execution_requested'])
        self.invoke.assert_not_called()
        self.guard.finish.assert_called_once()

    def test_assignment_reuse_keeps_original_context_when_guide_is_unavailable(self):
        self.args.project = 'context-test'
        self.args.assignment_id = 'context-replay'
        self.args.no_memory = True
        first = self.run_task()
        self.assertEqual(first['execution_status'], 'succeeded')
        self.invoke.reset_mock()
        self.factory.reset_mock()
        with patch.object(dispatcher, 'load_operating_context', side_effect=dispatcher.OperatingContextError('Guide missing')):
            repeated = self.run_task()
        self.assertTrue(repeated['assignment_reused'])
        self.assertEqual(repeated['orchestration_context'], first['orchestration_context'])
        self.invoke.assert_not_called()
        self.factory.assert_not_called()

    def test_existing_output_is_preserved_before_any_quota_work(self):
        self.args.output.write_text('existing user work', encoding='utf-8')
        result = self.run_task()
        self.assertEqual(result['status'], 'failed')
        self.assertEqual(self.args.output.read_text(), 'existing user work')
        self.factory.assert_not_called()
        self.invoke.assert_not_called()
        self.assertEqual(self.canonical(result)['execution_status'], 'failed')

    def test_missing_parent_fails_before_reservation(self):
        self.args.output = self.root / 'missing' / 'answer.json'
        self.assertEqual(self.run_task()['status'], 'failed')
        self.factory.assert_not_called()
        self.invoke.assert_not_called()

    def test_output_is_claimed_before_refresh(self):
        def refresh(provider):
            claimed = json.loads(self.args.output.read_text(encoding='utf-8'))
            self.assertEqual(claimed['status'], 'reserved_output')
            return {provider: {'ok': True}}
        self.guard.refresh.side_effect = refresh
        self.assertEqual(self.run_task()['status'], 'awaiting_review')

    def test_export_failure_preserves_canonical_answer_and_finishes_reservation(self):
        with patch.object(OutputClaim, 'write', side_effect=OSError('export disconnected')):
            result = self.run_task()
        self.assertEqual(result['status'], 'awaiting_review')
        self.assertEqual(result['export_status'], 'failed')
        self.assertEqual(self.canonical(result)['response'], self.payload['result'])
        self.assertEqual(self.canonical(result)['export_status'], 'failed')
        self.guard.finish.assert_called_once()

    def test_parse_failure_finishes_reservation(self):
        self.invoke.return_value.stdout = '{malformed'
        result = self.run_task()
        self.assertEqual(result['execution_status'], 'failed')
        self.guard.finish.assert_called_once()

    def test_task_size_controls_execution_deadline(self):
        self.args.size = 'medium'
        result = self.run_task()
        self.assertEqual(result['timeout_seconds'], 900)
        self.assertEqual(self.invoke.call_args.kwargs['timeout_seconds'], 900)
        self.assertTrue(result['execution_progress_path'].endswith('execution-progress.json'))

    def test_stream_disconnect_keeps_reservation_even_after_quota_retry_message(self):
        self.invoke.return_value.returncode = 1
        self.invoke.return_value.stdout = '{}'
        self.invoke.return_value.stderr = 'Earlier retry: HTTP 429 rate limit exceeded'
        self.invoke.return_value.progress = {'terminal_received': False, 'retry_count': 1}
        result = self.run_task()
        self.assertEqual(result['execution_status'], 'uncertain')
        self.assertEqual(result['error'], 'missing_terminal_result')
        self.assertEqual(result['reservation_state'], 'held_for_reconciliation')
        self.guard.finish.assert_not_called()
        self.guard.block.assert_not_called()

    def test_structured_terminal_quota_error_with_zero_exit_allows_handoff(self):
        self.invoke.return_value.stdout = json.dumps({'type': 'result', 'is_error': True,
                                                    'errors': ['quota_exhausted']})
        self.invoke.return_value.progress = {'terminal_received': True}
        result = self.run_task()
        self.assertEqual(result['failure_kind'], 'quota_exhausted')
        self.assertEqual(result['execution_status'], 'failed')
        self.guard.block.assert_called_once_with('claude')
        self.guard.finish.assert_called_once()

    def test_successful_answer_quoting_quota_error_is_not_reassigned(self):
        self.invoke.return_value.stdout = json.dumps({'result': 'Example: quota_exhausted'})
        result = self.run_task()
        self.assertEqual(result['status'], 'awaiting_review')
        self.guard.block.assert_not_called()

    def test_terminal_auth_failure_is_not_overridden_by_old_quota_retry(self):
        self.invoke.return_value.returncode = 1
        self.invoke.return_value.stdout = json.dumps({'type': 'result', 'is_error': True,
                                                    'errors': ['Authentication failed']})
        self.invoke.return_value.stderr = 'Earlier retry: HTTP 429 rate limit exceeded'
        self.invoke.return_value.progress = {'terminal_received': True}
        result = self.run_task()
        self.assertEqual(result['execution_status'], 'failed')
        self.assertNotIn('failure_kind', result)
        self.guard.block.assert_not_called()

    def test_launch_failure_finishes_reservation(self):
        self.invoke.side_effect = FileNotFoundError('worker disappeared')
        result = self.run_task()
        self.assertEqual(result['status'], 'failed')
        self.guard.finish.assert_called_once()

    def test_quota_rejection_stops_without_inference(self):
        self.guard.check.return_value = {'allowed': False, 'reasons': ['reserve exceeds allowance']}
        self.assertEqual(self.run_task()['status'], 'held')
        self.invoke.assert_not_called()
        self.guard.finish.assert_not_called()

    def test_failed_quota_refresh_stops_without_reservation(self):
        self.guard.refresh.return_value = {'claude': {'ok': False}}
        self.guard.refresh.side_effect = None
        self.assertEqual(self.run_task()['status'], 'held')
        self.guard.check.assert_not_called()
        self.invoke.assert_not_called()

    def test_provider_quota_error_blocks_and_finishes_without_retry(self):
        self.invoke.return_value = subprocess.CompletedProcess(['worker'], 1, '', '429 rate limit')
        self.assertEqual(self.run_task()['status'], 'failed')
        self.guard.block.assert_called_once_with('claude')
        self.guard.finish.assert_called_once()
        self.invoke.assert_called_once()

    def test_timeout_retains_reservation_even_when_local_process_is_terminated(self):
        self.invoke.side_effect = dispatcher.WorkerInterrupted('timeout', True, 4321)
        result = self.run_task()
        self.assertEqual(result['status'], 'recovery_required')
        self.assertEqual(result['reservation_state'], 'held_for_reconciliation')
        self.assertEqual(result['process_pid'], 4321)
        self.assertTrue(result['local_process_terminated'])
        self.guard.finish.assert_not_called()
        self.assertEqual(self.guard.refresh.call_count, 1)

    def test_interruption_with_unknown_termination_retains_reservation(self):
        self.invoke.side_effect = dispatcher.WorkerInterrupted('interrupted', False, 4322)
        result = self.run_task()
        self.assertEqual(self.canonical(result)['execution_status'], 'uncertain')
        self.guard.finish.assert_not_called()

    def test_local_timeout_may_continue_in_ollama_and_retains_reservation(self):
        self.args.worker = 'local-chat'
        with patch.object(dispatcher, 'local_request', side_effect=TimeoutError()):
            result = self.run_task()
        self.assertEqual(result['status'], 'recovery_required')
        self.guard.finish.assert_not_called()
        self.guard.refresh.assert_not_called()

    def test_success_is_unreviewed_and_actual_model_metadata_is_preserved(self):
        result = self.run_task()
        self.assertEqual(result['status'], 'awaiting_review')
        self.assertEqual(result['execution_status'], 'succeeded')
        self.assertEqual(result['review_status'], 'pending')
        self.assertEqual(result['modelUsage'], self.payload['modelUsage'])
        self.assertEqual(result['provider_result']['total_cost_usd'], 0.012)
        self.assertEqual(len(result['prompt_sha256']), 64)
        self.assertLessEqual(result['started_at'], result['ended_at'])
        self.assertIn(result['job_id'], self.guard.check.call_args.kwargs['task'])
        self.assertEqual(self.canonical(result)['status'], 'awaiting_review')

    def test_substantive_review_is_explicit_and_cannot_be_repeated(self):
        result = self.run_task()
        with self.assertRaises(ValueError):
            self.store.review(result['job_id'], 'accepted', 'Codex', 'ok')
        reviewed = self.store.review(result['job_id'], 'accepted', 'Codex',
            'Reproduced each reported defect and checked the source evidence.')
        self.assertEqual(reviewed['status'], 'accepted')
        self.assertEqual(reviewed['execution_status'], 'succeeded')
        with self.assertRaises(ValueError):
            self.store.review(result['job_id'], 'rejected', 'Codex', 'This later review must not overwrite an earlier review.')

    def test_review_cannot_accept_running_cleanup_or_failed_job(self):
        result = self.run_task()
        result.pop('finalized_at')
        self.store.save(result['job_id'], result)
        with self.assertRaises(ValueError):
            self.store.review(result['job_id'], 'accepted', 'Codex', 'I checked this answer against all task requirements.')
        result.update(finalized_at=result['ended_at'], status='failed', execution_status='failed')
        self.store.save(result['job_id'], result)
        with self.assertRaises(ValueError):
            self.store.review(result['job_id'], 'accepted', 'Codex', 'I checked this answer against all task requirements.')

    def test_antigravity_is_held_before_any_provider_activity(self):
        self.args.worker = 'gemini'
        with patch.object(dispatcher, 'boundary_error', return_value='Antigravity verification is missing'):
            result = self.run_task()
        self.assertEqual(result['status'], 'held')
        self.assertIn('verification is missing', result['reason'])
        self.factory.assert_not_called()
        self.invoke.assert_not_called()

    def test_verified_antigravity_uses_dedicated_parser_and_reviews_answer(self):
        self.args.worker = 'gemini'
        self.invoke.return_value.stdout = json.dumps({'response': 'Reviewed later', 'status': 'SUCCESS',
            'model': 'gemini-3.8-flash-medium', 'usage': {'input_tokens': 1, 'output_tokens': 2}})
        with patch.object(dispatcher, 'boundary_error', return_value=None), patch('antigravity_boundary.verify_prepared') as verify:
            result = self.run_task()
        self.assertEqual(result['status'], 'awaiting_review')
        self.assertEqual(self.invoke.call_args.kwargs['protocol'], 'antigravity')
        self.assertEqual(self.invoke.call_args.kwargs['expected_model'], 'gemini-3.8-flash-medium')
        verify.assert_called_once()
        self.guard.finish.assert_called_once()

    def test_antigravity_config_drift_after_reservation_never_starts_inference(self):
        self.args.worker = 'gemini'
        with patch.object(dispatcher, 'boundary_error', return_value=None), patch('antigravity_boundary.verify_prepared', side_effect=dispatcher.BoundaryHeld('settings changed')):
            result = self.run_task()
        self.assertEqual(result['status'], 'held')
        self.invoke.assert_not_called()
        self.guard.finish.assert_called_once()
        self.assertIsNone(result.get('started_at'))

    def test_antigravity_protocol_violation_preserves_uncertain_reservation(self):
        self.args.worker = 'gemini'
        self.invoke.side_effect = dispatcher.WorkerInterrupted('antigravity_tool_event', True, 123)
        with patch.object(dispatcher, 'boundary_error', return_value=None), patch('antigravity_boundary.verify_prepared'):
            result = self.run_task()
        self.assertEqual(result['status'], 'recovery_required')
        self.assertEqual(result['reservation_state'], 'held_for_reconciliation')
        self.guard.finish.assert_not_called()

    def test_cleanup_failure_does_not_discard_the_answer(self):
        self.guard.finish.side_effect = OSError('state unavailable')
        result = self.run_task()
        self.assertEqual(result['status'], 'awaiting_review')
        self.assertEqual(result['reservation_state'], 'cleanup_failed')
        self.assertEqual(self.canonical(result)['response'], self.payload['result'])
        self.guard.dashboard.assert_called_once()


class BoundaryTests(unittest.TestCase):
    def test_concurrent_export_claims_have_one_owner(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / 'result.json'
            def claim(index):
                try:
                    return OutputClaim(output, str(index))
                except FileExistsError:
                    return None
            with ThreadPoolExecutor(max_workers=8) as pool:
                claims = list(pool.map(claim, range(8)))
            winners = [claim for claim in claims if claim is not None]
            try:
                self.assertEqual(len(winners), 1)
            finally:
                for claim in winners:
                    claim.close()

    def test_replaced_export_is_detected_and_not_overwritten(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / 'result.json'
            claim = OutputClaim(output, 'job')
            try:
                replacement = Path(directory) / 'other.json'
                replacement.write_text('other writer', encoding='utf-8')
                # Windows prevents replacing a normally held open file. Close the
                # descriptor to reproduce a replacement before the identity check.
                claim.close()
                os.replace(replacement, output)
                with self.assertRaises(OSError):
                    claim.write({'answer': 'mine'})
                self.assertEqual(output.read_text(), 'other writer')
            finally:
                claim.close()

    def test_claude_uses_native_executable_and_tool_boundary_flags(self):
        with tempfile.TemporaryDirectory() as directory:
            command, stdin = dispatcher.cloud_command('claude', 'Brief', Path(directory), 'sonnet')
        self.assertTrue(command[0].endswith('claude.exe'))
        for flag in ('--safe-mode', '--no-chrome', '--strict-mcp-config', '--tools='):
            self.assertIn(flag, command)
        self.assertNotIn('--bare', command)
        self.assertEqual(command[command.index('--permission-mode') + 1], 'dontAsk')
        self.assertEqual(command[command.index('--model') + 1], 'sonnet')
        self.assertEqual(command[command.index('--effort') + 1], 'medium')
        self.assertIn('"remoteControlAtStartup":false', command[-1])
        self.assertTrue(stdin.endswith('Brief'))

    def test_grok_preserves_long_brief_and_removes_read_and_mcp_capabilities(self):
        brief = 'Supplied text ' * 2500
        with tempfile.TemporaryDirectory() as directory:
            work = Path(directory)
            command, stdin = dispatcher.cloud_command('grok', brief, work)
            self.assertEqual((work / 'task.txt').read_text(encoding='utf-8'), dispatcher.PREFIX + brief)
        self.assertIsNone(stdin)
        for flag in ('--verbatim', '--no-subagents', '--disable-web-search', '--no-memory'):
            self.assertIn(flag, command)
        allowed = set(command[command.index('--tools') + 1].split(','))
        removed = set(command[command.index('--disallowed-tools') + 1].split(','))
        self.assertFalse(allowed - removed)
        self.assertIn('Agent', removed)
        denied = {command[i + 1] for i, arg in enumerate(command) if arg == '--deny'}
        self.assertTrue({'Read', 'Grep', 'Bash', 'Edit', 'Write', 'WebFetch', 'MCPTool'} <= denied)

    def test_child_environment_disallows_api_fallback_without_parent_mutation(self):
        variables = {'ANTHROPIC_API_KEY': 'sentinel', 'ANTHROPIC_AUTH_TOKEN': 'sentinel',
            'ANTHROPIC_BASE_URL': 'sentinel', 'CLAUDE_CODE_USE_BEDROCK': '1',
            'CLAUDE_CODE_USE_VERTEX': '1', 'CLAUDE_CODE_USE_FOUNDRY': '1',
            'GOOGLE_GENAI_USE_VERTEXAI': '1', 'GEMINI_API_KEY': 'sentinel',
            'XAI_API_KEY': 'sentinel'}
        with patch.dict(os.environ, variables):
            env = dispatcher.worker_environment()
            for variable in variables:
                self.assertNotIn(variable, env)
                self.assertIn(variable, os.environ)
            self.assertEqual(env['DISABLE_AUTOUPDATER'], '1')

    def test_timeout_kills_and_reaps_direct_child_but_reports_uncertain_provider(self):
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaises(dispatcher.WorkerInterrupted) as raised:
                dispatcher.invoke_cloud([sys.executable, '-c', 'import time; time.sleep(10)'],
                                        None, directory, os.environ.copy(), timeout_seconds=0.2)
        self.assertTrue(raised.exception.process_terminated)
        self.assertIsInstance(raised.exception.process_pid, int)
        self.assertEqual(raised.exception.cause, 'timeout')
        self.assertEqual(raised.exception.progress['process_status'], 'timeout')

    def test_local_wrapped_transport_timeout_is_uncertain(self):
        local = Mock()
        local.request.side_effect = urllib.error.URLError(TimeoutError())
        with patch.dict(sys.modules, {'manage_local': local}):
            with self.assertRaises(dispatcher.WorkerInterrupted) as raised:
                dispatcher.local_request('Brief')
        self.assertEqual(raised.exception.cause, 'local_transport_error')

    def test_local_explicit_http_rejection_is_an_ordinary_failure(self):
        local = Mock()
        local.request.side_effect = urllib.error.HTTPError('loopback', 400, 'Rejected', {}, None)
        with patch.dict(sys.modules, {'manage_local': local}):
            with self.assertRaisesRegex(RuntimeError, 'HTTP 400'):
                dispatcher.local_request('Brief')


if __name__ == '__main__':
    unittest.main()
