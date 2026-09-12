"""Offline AGY permission-boundary regressions; never invoke a CLI or account."""
import copy
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'app'))
import antigravity_boundary as boundary

ZERO_USAGE = {key: 0 for key in ('input_tokens', 'output_tokens', 'thinking_tokens',
                               'cache_read_tokens', 'total_tokens')}


class AntigravityBoundaryTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.work = self.root / 'job'
        self.work.mkdir()
        self.home = self.root / 'home'
        self.home.mkdir()
        self.exe, self.handler, self.python = [self.root / name for name in
                                               ('agy.exe', 'deny_tools.py', 'python.exe')]
        for path in (self.exe, self.handler, self.python):
            path.write_bytes(('synthetic ' + path.name).encode())
        self.validation = self.root / 'validation.json'
        for item in (patch.object(boundary, 'EXE', self.exe),
                     patch.object(boundary, 'HANDLER', self.handler),
                     patch.object(boundary, 'VALIDATION', self.validation),
                     patch.object(boundary.sys, 'executable', str(self.python)),
                     patch.object(boundary.Path, 'home', return_value=self.home)):
            item.start()
            self.addCleanup(item.stop)
        self.proof = {'schema_version': 1, 'status': 'verified', 'model': boundary.MODEL,
                      'checks': {key: True for key in boundary.REQUIRED_CHECKS},
                      'executable_sha256': boundary.digest(self.exe),
                      'handler_sha256': boundary.digest(self.handler),
                      'python_sha256': boundary.digest(self.python)}
        self.write_proof(self.proof)
        runner_patch = patch.object(boundary.subprocess, 'run', side_effect=self.fake_cli)
        self.runner = runner_patch.start()
        self.addCleanup(runner_patch.stop)

    def write_proof(self, proof):
        self.validation.write_text(json.dumps(proof), encoding='utf-8')

    def payload(self, name):
        payload = {'status': 'SUCCESS', 'num_turns': 0, 'conversation_id': '',
                   'duration_seconds': 0, 'usage': dict(ZERO_USAGE),
                   'command': {'name': name, 'data': {}}}
        if name == 'hooks':
            command = self.python.as_posix() + ' ' + self.handler.as_posix()
            payload['command']['data']['hooks'] = [{
                'name': 'orchestrator-no-tools', 'enabled': True,
                'source': str(self.work / '.agents/hooks.json'),
                'actions': [{'event': 'PreToolUse', 'matcher': '*', 'type': 'command',
                             'command': command, 'timeout_seconds': 5}]}]
        elif name == 'config':
            payload['command']['data']['config'] = {
                'useG1Credits': False, 'toolPermission': 'request-review',
                'customModelsConfig': {}, 'modelProvider': None}
        else:
            raise AssertionError('Unexpected CLI command; a model must never be invoked')
        return payload

    def fake_cli(self, command, **kwargs):
        name = command[command.index('-p') + 1]
        self.assertIn(name, ('/hooks', '/config'))
        return subprocess.CompletedProcess(command, 0, json.dumps(self.payload(name[1:])), '')

    def rewrite_payload(self, mutate):
        def run(command, **kwargs):
            name = command[command.index('-p') + 1][1:]
            payload = self.payload(name)
            mutate(name, payload)
            return subprocess.CompletedProcess(command, 0, json.dumps(payload), '')
        self.runner.side_effect = run

    def test_complete_matching_proof_passes_offline_without_process_calls(self):
        self.assertIsNone(boundary.boundary_error())
        self.runner.assert_not_called()

    def test_missing_or_malformed_validation_holds_without_process_calls(self):
        for contents in (None, '', '{invalid', '[]', 'null'):
            with self.subTest(contents=contents):
                if contents is None:
                    self.validation.unlink(missing_ok=True)
                else:
                    self.validation.write_text(contents, encoding='utf-8')
                self.assertIsNotNone(boundary.boundary_error())
        self.runner.assert_not_called()

    def test_every_required_check_must_be_explicitly_true(self):
        for key in boundary.REQUIRED_CHECKS:
            for value in (None, False, 1, 'true'):
                with self.subTest(key=key, value=value):
                    proof = copy.deepcopy(self.proof)
                    proof['checks'][key] = value
                    self.write_proof(proof)
                    self.assertIsNotNone(boundary.boundary_error())
        self.runner.assert_not_called()

    def test_wrong_status_model_schema_or_missing_checks_hold(self):
        for field, value in (('status', 'candidate'), ('model', 'other-model'),
                             ('schema_version', 2), ('checks', {})):
            proof = copy.deepcopy(self.proof)
            proof[field] = value
            self.write_proof(proof)
            self.assertIsNotNone(boundary.boundary_error())

    def test_executable_handler_and_python_drift_each_hold(self):
        for path in (self.exe, self.handler, self.python):
            with self.subTest(path=path.name):
                original = path.read_bytes()
                path.write_bytes(original + b' changed')
                self.assertIn('changed', boundary.boundary_error())
                path.write_bytes(original)
        self.runner.assert_not_called()

    def test_missing_pinned_file_holds(self):
        self.handler.unlink()
        self.assertIsNotNone(boundary.boundary_error())
        with self.assertRaises(boundary.BoundaryHeld):
            boundary.prepare(self.work, {})
        self.runner.assert_not_called()

    def test_unvalidated_handler_path_with_spaces_holds(self):
        spaced = self.root / 'deny tools.py'
        spaced.write_bytes(self.handler.read_bytes())
        with patch.object(boundary, 'HANDLER', spaced):
            self.assertIn('spaces', boundary.boundary_error())
        self.runner.assert_not_called()

    def test_exact_hook_discovery_passes_without_a_process_call(self):
        boundary.validate_discovery(self.payload('hooks'), self.work / '.agents/hooks.json')
        self.runner.assert_not_called()

    def test_hook_discovery_rejects_each_contract_change(self):
        baseline = self.payload('hooks')
        for field, value in (('name', 'other'), ('enabled', False), ('enabled', 1),
                             ('source', str(self.root / 'other.json')), ('actions', [])):
            payload = copy.deepcopy(baseline)
            payload['command']['data']['hooks'][0][field] = value
            with self.assertRaises(boundary.BoundaryHeld):
                boundary.validate_discovery(payload, self.work / '.agents/hooks.json')
        for field, value in (('event', 'PostToolUse'), ('matcher', 'view_file'),
                             ('type', 'prompt'), ('command', 'different-handler'),
                             ('timeout_seconds', 50)):
            payload = copy.deepcopy(baseline)
            payload['command']['data']['hooks'][0]['actions'][0][field] = value
            with self.assertRaises(boundary.BoundaryHeld):
                boundary.validate_discovery(payload, self.work / '.agents/hooks.json')

    def test_missing_or_extra_discovered_hooks_hold(self):
        for hooks in ([], None, [self.payload('hooks')['command']['data']['hooks'][0]] * 2):
            payload = self.payload('hooks')
            payload['command']['data']['hooks'] = hooks
            with self.assertRaises(boundary.BoundaryHeld):
                boundary.validate_discovery(payload, self.work / '.agents/hooks.json')

    def test_successful_prepare_runs_only_zero_turn_inspections(self):
        parent_env = {'GOOGLE_GEMINI_BASE_URL': 'synthetic-route', 'GOOGLE_CLOUD_PROJECT': 'synthetic-project',
                      'KEEP_VALUE': 'retained'}
        original_env = dict(parent_env)
        command = boundary.prepare(self.work, parent_env)
        self.assertEqual(self.runner.call_count, 2)
        self.assertEqual([call.args[0][call.args[0].index('-p') + 1]
                          for call in self.runner.call_args_list], ['/hooks', '/config'])
        self.assertEqual(command, [str(self.exe), '--add-dir', str(self.work), '--mode', 'plan', '--sandbox'])
        for call in self.runner.call_args_list:
            self.assertEqual(call.kwargs['cwd'], self.work)
            self.assertEqual(call.kwargs['env']['AGY_CLI_DISABLE_AUTO_UPDATE'], 'true')
            self.assertNotIn('GOOGLE_GEMINI_BASE_URL', call.kwargs['env'])
            self.assertNotIn('GOOGLE_CLOUD_PROJECT', call.kwargs['env'])
            self.assertEqual(call.kwargs['env']['KEEP_VALUE'], 'retained')
            self.assertLessEqual(call.kwargs['timeout'], 60)
        self.assertEqual(parent_env, original_env)
        receipt = json.loads((self.work / 'antigravity-preflight.json').read_text(encoding='utf-8'))
        self.assertTrue(receipt['verified'])
        self.assertFalse(receipt['credit_overage'])
        self.assertEqual(receipt['hook_sha256'], boundary.digest(self.work / '.agents/hooks.json'))

    def test_existing_agents_directory_is_not_overwritten(self):
        settings = self.work / '.agents'
        settings.mkdir()
        marker = settings / 'hooks.json'
        marker.write_text('existing work', encoding='utf-8')
        with self.assertRaises(boundary.BoundaryHeld):
            boundary.prepare(self.work, {})
        self.assertEqual(marker.read_text(), 'existing work')
        self.runner.assert_not_called()

    def test_global_mcp_servers_prevent_even_inspection_calls(self):
        config = self.home / '.gemini/config/mcp_config.json'
        config.parent.mkdir(parents=True)
        config.write_text(json.dumps({'mcpServers': {'synthetic': {'command': 'do-not-start'}}}), encoding='utf-8')
        with self.assertRaises(boundary.BoundaryHeld):
            boundary.prepare(self.work, {})
        self.runner.assert_not_called()

    def test_malformed_global_mcp_config_holds(self):
        config = self.home / '.gemini/config/mcp_config.json'
        config.parent.mkdir(parents=True)
        config.write_text('{invalid', encoding='utf-8')
        with self.assertRaises(boundary.BoundaryHeld):
            boundary.prepare(self.work, {})
        self.runner.assert_not_called()

    def test_credit_overage_and_alternate_permission_or_provider_settings_hold(self):
        for field, value in (('useG1Credits', True), ('useG1Credits', None), ('useG1Credits', 0),
                             ('toolPermission', 'always-proceed'), ('toolPermission', None),
                             ('customModelsConfig', {'custom': {}}), ('modelProvider', 'api-route')):
            with self.subTest(field=field, value=value):
                self.work = self.root / ('setting-' + str(len(list(self.root.iterdir()))))
                self.work.mkdir()
                self.rewrite_payload(lambda name, payload: payload['command']['data']['config'].__setitem__(field, value)
                                     if name == 'config' else None)
                with self.assertRaises(boundary.BoundaryHeld):
                    boundary.prepare(self.work, {})
                self.assertFalse((self.work / 'antigravity-preflight.json').exists())

    def test_cli_nonzero_exit_or_invalid_json_never_produces_a_receipt(self):
        for response in (subprocess.CompletedProcess([], 1, json.dumps(self.payload('hooks')), ''),
                         subprocess.CompletedProcess([], 0, '{bad', '')):
            self.work = self.root / ('failure-' + str(len(list(self.root.iterdir()))))
            self.work.mkdir()
            self.runner.side_effect = None
            self.runner.return_value = response
            with self.assertRaises(boundary.BoundaryHeld):
                boundary.prepare(self.work, {})
            self.assertFalse((self.work / 'antigravity-preflight.json').exists())

    def test_changed_hook_during_inspection_cannot_pass(self):
        def mutate(name, payload):
            if name == 'config':
                (self.work / '.agents/hooks.json').write_text('{"changed":true}', encoding='utf-8')
        self.rewrite_payload(mutate)
        with self.assertRaises(boundary.BoundaryHeld):
            boundary.prepare(self.work, {})
        self.assertFalse((self.work / 'antigravity-preflight.json').exists())

    def test_handler_change_during_inspection_cannot_pass(self):
        def mutate(name, payload):
            if name == 'config':
                self.handler.write_bytes(b'changed during preflight')
        self.rewrite_payload(mutate)
        with self.assertRaises(boundary.BoundaryHeld):
            boundary.prepare(self.work, {})
        self.assertFalse((self.work / 'antigravity-preflight.json').exists())

    def test_nonzero_reported_usage_cannot_be_called_quota_free(self):
        for target in ('hooks', 'config'):
            with self.subTest(command=target):
                self.work = self.root / ('usage-' + target)
                self.work.mkdir()
                self.rewrite_payload(lambda name, payload: payload['usage'].__setitem__('input_tokens', 1)
                                     if name == target else None)
                with self.assertRaises(boundary.BoundaryHeld):
                    boundary.prepare(self.work, {})

    def test_boolean_turn_count_cannot_prove_zero_turns(self):
        payload = self.payload('hooks')
        payload['num_turns'] = False
        with self.assertRaises(boundary.BoundaryHeld):
            boundary.validate_discovery(payload, self.work / '.agents/hooks.json')

    def test_final_recheck_passes_without_new_cli_calls(self):
        boundary.prepare(self.work, {})
        self.runner.reset_mock()
        boundary.verify_prepared(self.work)
        self.runner.assert_not_called()

    def test_final_recheck_rejects_hook_tampering_even_with_updated_receipt_hash(self):
        boundary.prepare(self.work, {})
        hook = self.work / '.agents/hooks.json'
        hook.write_text('{"disabled":true}', encoding='utf-8')
        receipt_path = self.work / 'antigravity-preflight.json'
        receipt = json.loads(receipt_path.read_text(encoding='utf-8'))
        receipt['hook_sha256'] = boundary.digest(hook)
        receipt_path.write_text(json.dumps(receipt), encoding='utf-8')
        with self.assertRaises(boundary.BoundaryHeld):
            boundary.verify_prepared(self.work)

    def test_final_recheck_rejects_each_pinned_binary_or_handler_drift(self):
        boundary.prepare(self.work, {})
        for path in (self.exe, self.handler, self.python):
            with self.subTest(path=path.name):
                original = path.read_bytes()
                path.write_bytes(original + b'changed after quota')
                with self.assertRaises(boundary.BoundaryHeld):
                    boundary.verify_prepared(self.work)
                path.write_bytes(original)

    def test_final_recheck_rejects_each_global_configuration_change(self):
        boundary.prepare(self.work, {})
        for name in ('antigravity-cli/settings.json', 'config/config.json', 'config/mcp_config.json'):
            with self.subTest(name=name):
                path = self.home / '.gemini' / name
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text('{"synthetic_change":true}', encoding='utf-8')
                with self.assertRaises(boundary.BoundaryHeld):
                    boundary.verify_prepared(self.work)
                path.unlink()

    def test_final_recheck_rejects_missing_or_malformed_receipt(self):
        boundary.prepare(self.work, {})
        path = self.work / 'antigravity-preflight.json'
        for value in (None, '', '[]'):
            if value is None:
                path.unlink(missing_ok=True)
            else:
                path.write_text(value, encoding='utf-8')
            with self.assertRaises(boundary.BoundaryHeld):
                boundary.verify_prepared(self.work)

    def test_malformed_discovery_shapes_raise_a_boundary_hold(self):
        for value in (None, [], {}, {'command': None}):
            with self.assertRaises(boundary.BoundaryHeld):
                boundary.validate_discovery(value, self.work / '.agents/hooks.json')
        for value in (None, False, []):
            payload = self.payload('hooks')
            payload['command']['data']['hooks'] = [value]
            with self.assertRaises(boundary.BoundaryHeld):
                boundary.validate_discovery(payload, self.work / '.agents/hooks.json')

    def test_zero_usage_inspections_need_complete_unambiguous_metadata(self):
        for field, value in (('conversation_id', 'synthetic-active-conversation'), ('duration_seconds', False),
                             ('duration_seconds', 0.1), ('usage', {}),
                             ('usage', dict(ZERO_USAGE, total_tokens=True)),
                             ('usage', dict(ZERO_USAGE, extra_counter=1))):
            payload = self.payload('hooks')
            payload[field] = value
            with self.assertRaises(boundary.BoundaryHeld):
                boundary.validate_discovery(payload, self.work / '.agents/hooks.json')

    def test_command_construction_never_runs_the_supplied_prompt(self):
        prompt = 'SYNTHETIC_BRIEF_DO_NOT_EXECUTE'
        command = boundary.command_for(prompt, self.work, {})
        self.assertIn(prompt, command)
        self.assertIn('--disable-slash-commands', command)
        self.assertIn('--sandbox', command)
        self.assertEqual(command[command.index('--model') + 1], boundary.MODEL)
        self.assertNotIn('--effort', command)
        self.assertNotIn('--dangerously-skip-permissions', command)
        for call in self.runner.call_args_list:
            self.assertNotIn(prompt, call.args[0])

    def test_utf8_brief_limit_holds_before_any_inspection(self):
        with self.assertRaises(boundary.BoundaryHeld):
            boundary.command_for(chr(0xE9) * 6001, self.work, {})
        self.runner.assert_not_called()
        self.assertFalse((self.work / '.agents').exists())


if __name__ == '__main__':
    unittest.main()
