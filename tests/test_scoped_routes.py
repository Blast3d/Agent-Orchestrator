"""Scoped file-tool routes: commands, audits, the AGY gate and dispatcher wiring (no live models)."""
import argparse
import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'app'))
import antigravity_scoped_tools as gate
import dispatch_worker as dispatcher
import scoped_routes as routes
import worker_worktree
from task_store import TaskStore


def step(name, state, kind='tool', **extra):
    body = {'conversation_id': 'c1', 'step_index': 1, 'state': state, 'step_type': kind, 'tool_name': name}
    if kind == 'tool':
        body['tool_info'] = {'name': name, 'parameters': {}}
    body.update(extra)
    return {'event': 'step_update', 'step_update': body}


class CommandTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.work = Path(temporary.name)
        self.tree = self.work / 'tree'

    def test_claude_keeps_only_file_tools_under_restricted_mode(self):
        command = routes.claude_command('opus', 'low')
        self.assertEqual(command[command.index('--tools') + 1], 'Read,Write,Edit,Glob,Grep')
        for flag in ('--restricted', '--safe-mode', '--strict-mcp-config'):
            self.assertIn(flag, command)
        self.assertEqual(command[command.index('--permission-prompts') + 1], 'none')

    def test_codex_replaces_the_empty_server_table_with_ours_only(self):
        with patch('codex_worker.command_for', return_value=[
                'codex', 'exec', '-c', 'mcp_servers={}', '-c', 'web_search="disabled"', '-']):
            command = routes.codex_command('codex', self.work, self.tree, self.work / 'audit', 'gpt', 'low')
        self.assertNotIn('mcp_servers={}', command)
        self.assertEqual(command[-1], '-')
        servers = {item.split('=', 1)[0] for item in command if item.startswith('mcp_servers.')}
        self.assertEqual(servers, {f'mcp_servers.files.{key}' for key in (
            'command', 'args', 'startup_timeout_sec', 'tool_timeout_sec')})
        args = json.loads(next(item for item in command if item.startswith('mcp_servers.files.args=')).split('=', 1)[1])
        self.assertEqual(args[args.index('--root') + 1], str(self.tree))

    def test_grok_config_names_only_our_server_and_refuses_reuse(self):
        command = routes.grok_prepare(self.work, self.tree, self.work / 'audit', 'Edit the file.')
        config = (self.work / '.grok' / 'config.toml').read_text(encoding='utf-8')
        self.assertEqual(config.count('[mcp_servers.'), 1)
        self.assertIn(json.dumps(str(self.tree)), config)
        self.assertIn('MCPTool(files__*)', command)
        self.assertNotIn('Read', command[command.index('--allow'):])
        self.assertTrue((self.work / 'task.txt').read_text(encoding='utf-8').startswith(routes.PREFIX))
        with self.assertRaises(routes.ScopedHeld):
            routes.grok_prepare(self.work, self.tree, self.work / 'audit', 'again')
        self.assertEqual(routes.grok_environment({})['GROK_FOLDER_TRUST'], '0')

    def test_proof_admission_fails_closed(self):
        with patch.object(routes, 'PROOFS', self.work / 'proofs'):
            self.assertIn('missing', routes.proof_error('claude').lower() + 'missing')
            self.assertIn('no scoped', routes.proof_error('local-chat'))
            with patch.object(routes, 'fingerprints', return_value={'executable': 'a'}):
                checks = {check: True for check in routes.REQUIRED_CHECKS}
                routes.write_proof('claude', checks, {})
                self.assertIsNone(routes.proof_error('claude'))
            with patch.object(routes, 'fingerprints', return_value={'executable': 'b'}):
                self.assertIn('changed', routes.proof_error('claude'))
            with self.assertRaises(routes.ScopedHeld):
                routes.write_proof('claude', dict(checks, shell_denied=False), {})


class ParserTests(unittest.TestCase):
    def test_claude_rejects_any_tool_outside_the_file_set(self):
        parser = routes.ScopedClaudeProgress()
        parser.observe({'type': 'assistant', 'message': {'content': [
            {'type': 'tool_use', 'name': 'Edit', 'input': {}}]}}, 1)
        self.assertEqual(parser.tool_calls, ['Edit'])
        for block in ({'type': 'tool_use', 'name': 'Bash'}, {'type': 'server_tool_use', 'name': 'web_search'}):
            with self.assertRaises(routes.ScopedToolViolation):
                parser.observe({'type': 'assistant', 'message': {'content': [block]}}, 2)

    def test_codex_accepts_only_our_server(self):
        parser = routes.ScopedCodexProgress('gpt')
        parser.observe({'type': 'turn.started'}, 1)
        parser.observe({'type': 'item.completed', 'item': {
            'type': 'mcp_tool_call', 'server': 'files', 'tool': 'write_file'}}, 2)
        self.assertEqual(parser.tool_calls, ['write_file'])
        for item in ({'type': 'mcp_tool_call', 'server': 'other', 'tool': 'read_file'},
                     {'type': 'mcp_tool_call', 'server': 'files', 'tool': 'run'}):
            with self.assertRaises(routes.ScopedToolViolation):
                parser.observe({'type': 'item.started', 'item': item}, 3)
        with self.assertRaises(Exception):
            parser.observe({'type': 'item.started', 'item': {'type': 'command_execution'}}, 4)

    def test_antigravity_records_denials_and_stops_a_completed_foreign_tool(self):
        parser = routes.ScopedAntigravityProgress()
        parser.observe({'event': 'init', 'conversation_id': 'c1', 'init': {
            'model': 'm', 'cwd': '.', 'tools': ['view_file'], 'permission_mode': 'request-review'}}, 0)
        for event in (step('view_file', 'ACTIVE'), step('view_file', 'DONE'), step('run_command', 'ACTIVE'),
                      step('run_command', 'ERROR'), step('invoke_subagent', 'ACTIVE'),
                      step('invoke_subagent', 'ERROR', 'subagent', subagent_info={'subagents': []})):
            parser.observe(event, 1)
        self.assertEqual(parser.tool_calls, ['view_file'])
        self.assertEqual(parser.denied_calls, ['run_command', 'invoke_subagent'])
        for event in (step('run_command', 'DONE'),
                      step('invoke_subagent', 'DONE', 'subagent', subagent_info={'subagents': [{}]})):
            with self.assertRaises(routes.ScopedToolViolation):
                routes.ScopedAntigravityProgress().observe(event, 1)

    def test_antigravity_rechecks_paths_of_completed_file_tools(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        root = Path(temporary.name) / 'tree'
        root.mkdir()
        inside = step('view_file', 'DONE', tool_info={
            'name': 'view_file', 'parameters': {'AbsolutePath': str(root / 'a.txt')}})
        routes.ScopedAntigravityProgress(root=root).observe(inside, 1)
        for parameters in ({'AbsolutePath': str(root.parent / 'b.txt')}, None):
            outside = step('view_file', 'DONE', tool_info={'name': 'view_file', 'parameters': parameters})
            with self.subTest(parameters=parameters), self.assertRaises(routes.ScopedToolViolation):
                routes.ScopedAntigravityProgress(root=root).observe(outside, 1)
        written = step('write_to_file', 'DONE', tool_info={
            'name': 'write_to_file', 'parameters': {'TargetFile': str(root / 'AGENTS.md')}})
        with self.assertRaises(routes.ScopedToolViolation):
            routes.ScopedAntigravityProgress(root=root).observe(written, 1)


class GateTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.base = Path(temporary.name)
        self.root = self.base / 'tree'
        self.root.mkdir()

    def decide(self, name, **arguments):
        meta = {'toolAction': 'Viewing', 'toolSummary': 'file'}
        return gate.decide({'toolCall': {'name': name, 'args': dict(meta, **arguments)}}, str(self.root))[0]

    def test_allows_file_tools_inside_the_worktree_only(self):
        self.assertTrue(self.decide('view_file', AbsolutePath=str(self.root / 'a.txt')))
        self.assertTrue(self.decide('write_to_file', TargetFile=str(self.root / 'b.txt'),
                                    CodeContent='x', Overwrite=True, Description='d'))
        self.assertTrue(self.decide('grep_search', SearchPath=str(self.root), Query='x', Includes=['*.py']))
        self.assertTrue(self.decide('find_by_name', SearchDirectory=str(self.root), Pattern=None))
        self.assertTrue(self.decide('view_file', AbsolutePath=str(self.root / 'a.txt'), ContentOffset=4096))
        for name, arguments in (
                ('view_file', {'AbsolutePath': str(self.base / 'outside.txt')}),
                ('write_to_file', {'TargetFile': str(self.root / '.git' / 'config'), 'CodeContent': 'x'}),
                ('write_to_file', {'TargetFile': str(self.root / '.claude' / 'settings.json'), 'CodeContent': 'x'}),
                ('view_file', {'AbsolutePath': str(self.root / 'a.txt'), 'Cwd': 'C:/'}),
                ('view_file', {}),
                ('grep_search', {'SearchPath': str(self.root), 'Query': 'x', 'Includes': ['../**']}),
                ('find_by_name', {'SearchDirectory': str(self.root), 'Pattern': 'C:/Windows/*'}),
                ('run_command', {'CommandLine': 'hostname'}),
                ('sed_file', {'TargetFile': str(self.root / 'a.txt')})):
            with self.subTest(name=name, arguments=arguments):
                self.assertFalse(self.decide(name, **arguments))

    def test_unrecognised_payloads_are_denied(self):
        for payload in ({}, {'toolCall': 'view_file'}, {'toolCall': {'name': 'view_file'}},
                        {'toolCall': {'name': 'view_file', 'args': {}, 'input': {}}}):
            with self.subTest(payload=payload):
                self.assertFalse(gate.decide(payload, str(self.root))[0])


@unittest.skipUnless(shutil.which('git'), 'git is unavailable')
class ScopedDispatchTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        hooks = worker_worktree.EMPTY_HOOKS
        worker_worktree.EMPTY_HOOKS = self.root / 'empty-hooks'
        self.addCleanup(setattr, worker_worktree, 'EMPTY_HOOKS', hooks)
        self.repo = self.root / 'repo'
        self.repo.mkdir()
        for args in (['init', '-q'], ['config', 'user.email', 't@example.com'], ['config', 'user.name', 'T']):
            subprocess.run(['git', *args], cwd=self.repo, check=True, capture_output=True)
        (self.repo / 'app.py').write_text('print(1)\n', encoding='utf-8')
        subprocess.run(['git', 'add', '-A'], cwd=self.repo, check=True, capture_output=True)
        subprocess.run(['git', 'commit', '-qm', 'base'], cwd=self.repo, check=True, capture_output=True)
        prompt = self.root / 'prompt.txt'
        prompt.write_text('Change app.py to print 2.', encoding='utf-8')
        self.store = TaskStore(self.root / 'tasks')
        self.guard = Mock()
        self.guard.refresh.side_effect = lambda provider: {provider: {'ok': True}}
        self.guard.check.return_value = {'allowed': True, 'reservation_id': 'reservation-test'}
        self.factory = Mock(return_value=self.guard)
        self.args = argparse.Namespace(worker='claude', prompt_file=prompt, output=self.root / 'answer.json',
                                       task='Scoped edit', size='small', no_memory=True,
                                       worktree_from=self.repo, worktree_ref='HEAD')
        self.invoke = Mock(side_effect=self.worker_edit)
        for item in (patch.object(dispatcher, 'ensure_directories'),
                     patch.object(dispatcher, 'invoke_cloud', self.invoke),
                     patch.object(dispatcher, 'cloud_command', return_value=([sys.executable], 'brief'))):
            item.start()
            self.addCleanup(item.stop)

    def worker_edit(self, command, stdin, work, env, **options):
        (Path(work) / 'tree' / 'app.py').write_text('print(2)\n', encoding='utf-8')
        payload = {'result': 'Changed app.py.', 'usage': {'input_tokens': 1, 'output_tokens': 1}}
        return subprocess.CompletedProcess(command, 0, json.dumps(payload), '')

    def run_task(self):
        return dispatcher.dispatch(self.args, guard_factory=self.factory, store=self.store,
                                   workspaces=self.root / 'workspaces')

    def test_assignment_identity_accepts_only_a_well_formed_scope(self):
        from assignment_receipts import _contract
        base = {'worker': 'claude', 'prompt_sha256': 'a' * 64, 'size': 'small', 'category': 'coding',
                'claude_model': 'opus', 'claude_effort': 'low', 'require_brief_check': False}
        _contract(dict(base, scoped={'repo': str(self.repo), 'ref': 'HEAD'}))
        for scoped in ({'repo': str(self.repo), 'ref': '--output=x'}, {'repo': str(self.repo)}, 'repo'):
            with self.subTest(scoped=scoped), self.assertRaises(ValueError):
                _contract(dict(base, scoped=scoped))

    def test_scoped_task_is_held_without_a_current_proof(self):
        with patch.object(routes, 'proof_error', return_value='Scoped claude validation is missing.'):
            result = self.run_task()
        self.assertEqual(result['status'], 'held')
        self.assertIn('validation is missing', result['reason'])
        self.factory.assert_not_called()
        self.invoke.assert_not_called()
        self.assertEqual(result['scoped'], {'repo': str(self.repo.resolve()), 'ref': 'HEAD'})
        self.assertFalse((self.root / 'workspaces').exists())

    def test_scoped_task_collects_the_patch_and_removes_the_worktree(self):
        invocation = {'command': [sys.executable], 'stdin': 'brief', 'cwd': None, 'env': {},
                      'parser': None, 'protocol': 'claude'}
        with patch.object(routes, 'proof_error', return_value=None), \
                patch.object(routes, 'prepare', return_value=invocation) as prepare:
            result = self.run_task()
        self.assertEqual(result['status'], 'awaiting_review', result.get('error') or result.get('reason'))
        self.assertTrue(prepare.call_args.args[1].endswith('Change app.py to print 2.'))
        scoped = result['scoped']
        self.assertEqual(scoped['collected']['files'], [{'path': 'app.py', 'status': 'M', 'protected': False}])
        self.assertTrue(scoped['worktree_removed'])
        self.assertFalse(Path(scoped['worktree']['path']).exists())
        self.assertTrue(Path(scoped['collected']['patch_path']).is_relative_to(self.store.root))
        self.assertEqual((self.repo / 'app.py').read_text(encoding='utf-8'), 'print(1)\n')
        self.assertEqual(self.invoke.call_args.kwargs['protocol'], 'claude')


if __name__ == '__main__':
    unittest.main()
