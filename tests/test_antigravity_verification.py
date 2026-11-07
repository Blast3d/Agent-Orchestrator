import hashlib
import json
import sys
import tempfile
import unittest
from copy import deepcopy
from datetime import datetime, timedelta
from pathlib import Path

APP = Path(__file__).resolve().parents[1] / 'app'
if str(APP) not in sys.path:
    sys.path.insert(0, str(APP))

from antigravity_boundary import MODEL  # noqa: E402
from antigravity_verification import (  # noqa: E402
    CANARY_TEXT,
    DENIED,
    HOOK_FAILED,
    USAGE_COUNTERS,
    evaluate,
    verify_and_publish,
)


HASHES = {
    'exe': 'e' * 64,
    'handler': 'h' * 64,
    'python': 'p' * 64,
}
CONFIG_HASHES = {
    'antigravity-cli/settings.json': '1' * 64,
    'config/config.json': '2' * 64,
    'config/mcp_config.json': '3' * 64,
}
MCP_BYTES = b'{"mcpServers": {}}'
MCP_HASH = hashlib.sha256(MCP_BYTES).hexdigest()
REG_TOOLS = [
    'view_file', 'list_dir', 'grep_search', 'find_by_name', 'write_to_file',
    'replace_file_content', 'run_command', 'read_url_content', 'search_web',
    'open_browser_url', 'invoke_subagent', 'send_message', 'schedule',
    'generate_image', 'call_mcp_tool',
]
CAP_TOOLS = [
    'read_file', 'write_file', 'edit_file', 'search_web',
    'invoke_subagent', 'send_message', 'schedule', 'run_command',
]


def _preflight(mode):
    command = 'python /handler'
    timeout_seconds = 5
    if mode == 'timeout':
        command += ' --probe-timeout'
        timeout_seconds = 1
    return {
        'credit_overage': False,
        'local_zero_usage': True,
        'executable_sha256': HASHES['exe'],
        'handler_sha256': HASHES['handler'],
        'python_sha256': HASHES['python'],
        'configuration_hashes': dict(CONFIG_HASHES),
        'inspections': {
            'hooks': {'command': {'data': {'hooks': [{
                'actions': [{'command': command, 'timeout_seconds': timeout_seconds}],
            }]}}},
            'config': {'effective': {'useG1Credits': False}},
        },
    }


def _record(mode, preflight):
    return {
        'job_id': 'job-' + mode,
        'execution_status': 'succeeded',
        'exit_code': 0,
        'model': MODEL,
        'marker_exists': False,
        'mcp_counter_exists': False,
        'settings_unchanged': True,
        'executable_unchanged': True,
        'count_as_build_worker': False,
        'synthetic_input': True,
        'preflight': preflight,
        'started_at': '2026-11-07T19:00:00',
        'ended_at': '2026-11-07T21:00:00',
        'response': 'denied as expected',
    }


def _step(index, name, state, message, duration=0.2, parameters=None):
    info = {'error': {'message': message}}
    if parameters is not None:
        info['parameters'] = parameters
    return {
        'step_index': index,
        'tool_name': name,
        'state': state,
        'duration_seconds': duration,
        'tool_info': info,
    }


def _events(steps):
    events = [{'event': 'init', 'init': {'model': MODEL}}]
    for step in steps:
        events.append({'event': 'step_update', 'step_update': step})
    events.append({'event': 'result', 'result': {
        'status': 'SUCCESS', 'usage': {}, 'response': 'denied as expected',
    }})
    return events


def _pairs(tools, start='2026-11-07T20:00:00'):
    current = datetime.fromisoformat(start)
    rows = []
    for tool in tools:
        rows.append({'at': current.isoformat(), 'event': 'entered', 'tool': tool})
        current += timedelta(seconds=4)
        rows.append({'at': current.isoformat(), 'event': 'denied', 'tool': tool})
        current += timedelta(seconds=4)
    return rows


def _probe(mode, steps, handler_rows, extra_step_fields=None):
    preflight = _preflight(mode)
    steps = [dict(step) for step in steps]
    if extra_step_fields:
        for step, fields in zip(steps, extra_step_fields):
            step.update(fields)
    return {
        'record': _record(mode, preflight),
        'events': _events(steps),
        'preflight': preflight,
        'handler_rows': handler_rows,
        'canary_text': CANARY_TEXT if mode in ('regression', 'capabilities') else None,
        'marker_exists_now': False,
    }


def passing_bundle():
    regression_steps = [
        _step(index, name, 'ERROR', 'unknown tool: "' + name + '"')
        for index, name in enumerate(REG_TOOLS)
    ]
    cap_steps = []
    for index, name in enumerate(CAP_TOOLS):
        message = '' if name == 'invoke_subagent' else DENIED
        cap_steps.append(_step(index, name, 'ERROR', message))
    timeout_steps = [_step(0, 'run_command', 'ERROR', HOOK_FAILED + ' deadline', duration=1.5)]
    mcp_steps = [_step(
        0, 'call_mcp_tool', 'ERROR', DENIED, parameters={
            'ServerName': 'orchestrator-boundary-probe', 'ToolName': 'probe_counter',
        })]
    return {
        'version': '1.5.3',
        'installed_version': '1.5.3',
        'executable_sha256': HASHES['exe'],
        'handler_sha256': HASHES['handler'],
        'python_sha256': HASHES['python'],
        'current_hashes': {
            'executable_sha256': HASHES['exe'],
            'handler_sha256': HASHES['handler'],
            'python_sha256': HASHES['python'],
            'configuration_hashes': dict(CONFIG_HASHES),
            'mcp_config_sha256': MCP_HASH,
        },
        'probes': {
            'regression': _probe('regression', regression_steps, []),
            'capabilities': _probe('capabilities', cap_steps, _pairs(CAP_TOOLS)),
            'timeout': _probe('timeout', timeout_steps, [
                {'at': '2026-11-07T20:00:00', 'event': 'entered', 'tool': 'run_command'},
                {'at': '2026-11-07T20:00:04', 'event': 'denied', 'tool': 'run_command'},
            ]),
            'mcp': _probe('mcp', mcp_steps, _pairs(['call_mcp_tool'], '2026-11-07T20:10:00')),
        },
        'mcp_restoration': {
            'restoration_status': 'restored',
            'original_sha256': MCP_HASH,
            'restored_sha256': MCP_HASH,
        },
        'mcp_positive_control': {'counter_written': True, 'counter_reset': True},
    }


def _kinds(result):
    return [item['kind'] for item in result['failures']]


class Clock:
    def __init__(self, start='2026-11-07T20:00:00'):
        self.current = datetime.fromisoformat(start)

    def __call__(self):
        value = self.current.isoformat()
        self.current += timedelta(seconds=4)
        return value


class FakeStore:
    def __init__(self):
        self.records = {}
        self.n = 0

    def create(self, **kwargs):
        self.n += 1
        record = dict(kwargs)
        record['job_id'] = 'job-%d' % self.n
        record.setdefault('status', 'queued')
        self.records[record['job_id']] = record
        return record

    def save(self, job_id, record):
        self.records[job_id] = record


class FakeGuard:
    def check(self, *args, **kwargs):
        return {'allowed': True, 'reservation_id': 'res-1'}

    def finish(self, *args, **kwargs):
        return None


class FakeLauncher:
    def __init__(self, root, clock, done_step=False, raise_on=None):
        self.root = Path(root)
        self.clock = clock
        self.done_step = done_step
        self.raise_on = raise_on
        self.runs = []

    def version(self, exe, env):
        return '1.5.3'

    def inspect(self, mode, name, command, cwd, env):
        work = Path(cwd)
        hook_path = work / '.agents' / 'hooks.json'
        hook = json.loads(hook_path.read_text(encoding='utf-8'))
        item = hook['orchestrator-no-tools']['PreToolUse'][0]['hooks'][0]
        usage = {key: 0 for key in USAGE_COUNTERS}
        if name == 'hooks':
            return {
                'status': 'SUCCESS', 'num_turns': 0, 'conversation_id': '',
                'duration_seconds': 0, 'usage': usage,
                'command': {'name': 'hooks', 'data': {'hooks': [{
                    'name': 'orchestrator-no-tools', 'enabled': True,
                    'source': str(hook_path),
                    'actions': [{
                        'event': 'PreToolUse', 'matcher': '*', 'type': 'command',
                        'command': item['command'],
                        'timeout_seconds': item.get('timeout', 5),
                    }],
                }]}},
            }
        return {
            'status': 'SUCCESS', 'num_turns': 0, 'conversation_id': '',
            'duration_seconds': 0, 'usage': usage,
            'command': {'name': 'config', 'data': {'config': {
                'useG1Credits': False, 'toolPermission': 'request-review',
                'customModelsConfig': None, 'modelProvider': None,
                'allowNonWorkspaceAccess': False,
            }}},
        }

    def _log(self, event, tool):
        path = self.root / 'runtime' / 'antigravity-denials.ndjson'
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open('a', encoding='utf-8') as handle:
            handle.write(json.dumps({
                'at': self.clock(), 'event': event, 'tool': tool,
            }) + '\n')

    def _stdout(self, mode):
        if mode == 'regression':
            steps = [
                _step(index, name, 'ERROR', 'unknown tool: "' + name + '"')
                for index, name in enumerate(REG_TOOLS)
            ]
        elif mode == 'capabilities':
            state = 'DONE' if self.done_step else 'ERROR'
            steps = []
            for index, name in enumerate(CAP_TOOLS):
                message = '' if name == 'invoke_subagent' else DENIED
                steps.append(_step(index, name, state if index == 0 else 'ERROR', message))
            if state != 'DONE':
                for name in CAP_TOOLS:
                    self._log('entered', name)
                    self._log('denied', name)
        elif mode == 'timeout':
            steps = [_step(0, 'run_command', 'ERROR', HOOK_FAILED + ' deadline', duration=1.5)]
            self._log('entered', 'run_command')
            self._log('denied', 'run_command')
        else:
            steps = [_step(0, 'call_mcp_tool', 'ERROR', DENIED, parameters={
                'ServerName': 'orchestrator-boundary-probe', 'ToolName': 'probe_counter',
            })]
            self._log('entered', 'call_mcp_tool')
            self._log('denied', 'call_mcp_tool')
        return ''.join(json.dumps(event) + '\n' for event in _events(steps))

    def run(self, mode, command, cwd, env, timeout):
        self.runs.append(mode)
        if self.raise_on == mode:
            raise RuntimeError('probe boom')
        return type('ProbeRun', (), {
            'stdout': self._stdout(mode), 'stderr': '', 'returncode': 0,
            'pid': 1, 'uncertain': False, 'terminated': True,
        })()

    def positive_control(self, counter, allow_dir):
        return {'at': self.clock(), 'returncode': 0, 'counter_written': True,
                'counter_reset': True, 'reply': []}


class EvaluateTests(unittest.TestCase):
    def test_passing_bundle(self):
        result = evaluate(passing_bundle())
        self.assertTrue(result['passed'], result['failures'])
        self.assertEqual(result['failures'], [])
        self.assertTrue(all(result['checks'].values()), result['checks'])
        self.assertIn('hook_denied', result['tool_outcomes']['read_file'])
        self.assertIn('rejected_before_execution', result['tool_outcomes']['view_file'])

    def test_done_step(self):
        bundle = passing_bundle()
        step = bundle['probes']['capabilities']['events'][1]['step_update']
        step['state'] = 'DONE'
        result = evaluate(bundle)
        self.assertFalse(result['passed'])
        self.assertIn('done_step', _kinds(result))

    def test_unresolved_call(self):
        bundle = passing_bundle()
        step = bundle['probes']['capabilities']['events'][1]['step_update']
        step['state'] = 'RUNNING'
        result = evaluate(bundle)
        self.assertFalse(result['passed'])
        self.assertIn('unresolved_call', _kinds(result))

    def test_unknown_tool_with_handler_row(self):
        bundle = passing_bundle()
        bundle['probes']['regression']['handler_rows'] = _pairs(['view_file'])
        result = evaluate(bundle)
        self.assertFalse(result['passed'])
        self.assertIn('handler_on_rejected_tool', _kinds(result))

    def test_handler_order_mismatch(self):
        bundle = passing_bundle()
        rows = bundle['probes']['capabilities']['handler_rows']
        rows[0], rows[2] = rows[2], rows[0]
        rows[1], rows[3] = rows[3], rows[1]
        result = evaluate(bundle)
        self.assertFalse(result['passed'])
        self.assertIn('handler_order_mismatch', _kinds(result))

    def test_missing_required_hook_denied_tool(self):
        bundle = passing_bundle()
        events = bundle['probes']['capabilities']['events']
        events[:] = [event for event in events if event.get('step_update', {}).get('tool_name') != 'read_file']
        bundle['probes']['capabilities']['handler_rows'] = _pairs(
            [name for name in CAP_TOOLS if name != 'read_file'])
        result = evaluate(bundle)
        self.assertFalse(result['passed'])
        self.assertIn('missing_hook_denied', _kinds(result))

    def test_wrong_timeout_message(self):
        bundle = passing_bundle()
        step = bundle['probes']['timeout']['events'][1]['step_update']
        step['tool_info']['error']['message'] = DENIED
        result = evaluate(bundle)
        self.assertFalse(result['passed'])
        self.assertIn('timeout_outcome', _kinds(result))

    def test_short_handler_sleep(self):
        bundle = passing_bundle()
        bundle['probes']['timeout']['handler_rows'][1]['at'] = '2026-11-07T20:00:01'
        result = evaluate(bundle)
        self.assertFalse(result['passed'])
        self.assertIn('timeout_handler_sleep', _kinds(result))

    def test_mcp_positive_control_not_written(self):
        bundle = passing_bundle()
        bundle['mcp_positive_control'] = {'counter_written': False, 'counter_reset': True}
        result = evaluate(bundle)
        self.assertFalse(result['passed'])
        self.assertIn('mcp_positive_control', _kinds(result))
        self.assertFalse(result['checks']['mcp_denied'])

    def test_canary_changed(self):
        bundle = passing_bundle()
        bundle['probes']['capabilities']['canary_text'] = 'CHANGED'
        result = evaluate(bundle)
        self.assertFalse(result['passed'])
        self.assertIn('canary_changed', _kinds(result))

    def test_marker_present(self):
        bundle = passing_bundle()
        bundle['probes']['regression']['record']['marker_exists'] = True
        result = evaluate(bundle)
        self.assertFalse(result['passed'])
        self.assertIn('marker_present', _kinds(result))

    def test_config_not_restored(self):
        bundle = passing_bundle()
        bundle['mcp_restoration']['restoration_status'] = 'held_concurrent_config_change'
        result = evaluate(bundle)
        self.assertFalse(result['passed'])
        self.assertIn('config_not_restored', _kinds(result))

    def test_wrong_version(self):
        bundle = passing_bundle()
        bundle['installed_version'] = '9.9.9'
        result = evaluate(bundle)
        self.assertFalse(result['passed'])
        self.assertIn('wrong_version', _kinds(result))


class PublishTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.exe = self.root / 'agy.exe'
        self.exe.write_bytes(b'agy')
        self.handler = self.root / 'antigravity_deny_tools.py'
        self.handler.write_text('# handler\n', encoding='utf-8')
        self.validation = self.root / 'config' / 'antigravity-boundary.json'
        self.validation.parent.mkdir(parents=True)
        self.old_proof = b'{"schema_version": 1, "status": "stale-proof"}\n'
        self.validation.write_bytes(self.old_proof)
        self.mcp = self.root / 'mcp_config.json'
        self.mcp.write_bytes(MCP_BYTES)
        self.guide = self.root / 'guide.md'
        self.guide.write_text('GUIDE', encoding='utf-8')
        (self.root / 'runtime').mkdir()
        self.clock = Clock()
        self.store = FakeStore()
        self.guard = FakeGuard()

    def tearDown(self):
        self.temp.cleanup()

    def _digest(self, path):
        path = Path(path)
        if path == self.exe:
            return HASHES['exe']
        if path == self.handler:
            return HASHES['handler']
        if path == Path(sys.executable):
            return HASHES['python']
        if path.exists():
            return hashlib.sha256(path.read_bytes()).hexdigest()
        return hashlib.sha256(str(path).encode('utf-8')).hexdigest()

    def _kwargs(self, launcher):
        return dict(
            root=self.root, force=True, launcher=launcher, mcp_config_path=self.mcp,
            clock=self.clock, validation_path=self.validation, exe=self.exe,
            handler=self.handler, store=self.store, guard=self.guard,
            boundary_error_fn=lambda: 'revalidation required',
            configuration_hashes_fn=lambda: dict(CONFIG_HASHES),
            digest_fn=self._digest, operating_guide_path=self.guide, env={'AGY_CLI_DISABLE_AUTO_UPDATE': 'true'},
        )

    def test_proof_written_with_backup_on_pass(self):
        launcher = FakeLauncher(self.root, self.clock)
        result = verify_and_publish(**self._kwargs(launcher))
        self.assertEqual(result['status'], 'verified', result)
        self.assertTrue(self.validation.exists())
        proof = json.loads(self.validation.read_text(encoding='utf-8'))
        self.assertEqual(proof['status'], 'verified')
        self.assertEqual(proof['cli_version'], '1.5.3')
        self.assertIn('published_at', proof)
        self.assertIn('verification_id', proof)
        self.assertNotIn('run_id', proof)
        backup = Path(result['evidence_dir']) / 'agy-boundary-before.json'
        self.assertEqual(backup.read_bytes(), self.old_proof)
        self.assertTrue((Path(result['evidence_dir']) / 'agy-boundary-validated.json').exists())
        self.assertFalse(any(
            record.get('run_id') for record in self.store.records.values()))
        self.assertTrue(all(record.get('synthetic_input') is True for record in self.store.records.values()))
        self.assertTrue(all(record.get('count_as_build_worker') is False for record in self.store.records.values()))

    def test_proof_untouched_on_failure(self):
        launcher = FakeLauncher(self.root, self.clock, done_step=True)
        result = verify_and_publish(**self._kwargs(launcher))
        self.assertEqual(result['status'], 'failed', result)
        self.assertEqual(self.validation.read_bytes(), self.old_proof)
        self.assertIn('done_step', [item['kind'] for item in result['failures']])

    def test_attempt_dedup_by_hash(self):
        kwargs = self._kwargs(FakeLauncher(self.root, self.clock, done_step=True))
        first = verify_and_publish(**kwargs)
        self.assertEqual(first['status'], 'failed')
        launcher = FakeLauncher(self.root, Clock())
        kwargs['launcher'] = launcher
        kwargs['force'] = False
        second = verify_and_publish(**kwargs)
        self.assertEqual(second['status'], 'already_attempted')
        self.assertEqual(launcher.runs, [])
        self.assertEqual(self.validation.read_bytes(), self.old_proof)

    def test_mcp_config_restored_when_probe_raises(self):
        launcher = FakeLauncher(self.root, self.clock, raise_on='mcp')
        original = self.mcp.read_bytes()
        result = verify_and_publish(**self._kwargs(launcher))
        self.assertEqual(result['status'], 'error')  # a crash is not a verdict; retried later
        self.assertEqual(self.mcp.read_bytes(), original)
        self.assertEqual(self.validation.read_bytes(), self.old_proof)
        self.assertIn('mcp', launcher.runs)



class AttemptRetryTests(unittest.TestCase):
    def test_probe_errors_retry_after_an_hour_but_verdicts_do_not(self):
        import antigravity_verification as av
        self.assertGreaterEqual(av._age_seconds('2026-11-07T20:00:00+00:00', '2026-11-07T21:00:01+00:00'),
                                av.ERROR_RETRY_SECONDS)
        self.assertLess(av._age_seconds('2026-11-07T20:30:00+00:00', '2026-11-07T21:00:00+00:00'),
                        av.ERROR_RETRY_SECONDS)
        self.assertEqual(av._age_seconds(None, '2026-11-07T21:00:00+00:00'), float('inf'))


if __name__ == '__main__':
    unittest.main()
