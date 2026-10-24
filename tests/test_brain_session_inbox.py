"""Native session inbox: parsing, skip rules, redaction, review flow and the hook."""
import io
import json
import os
from pathlib import Path
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'app'))
from brain_store import BrainStore
import brain_session_inbox as inbox_module
from brain_session_inbox import Inbox, build_candidate, parse_claude, parse_codex, resolve_project, skip_reason


def claude_entry(kind, content, *, at, **extra):
    return dict({'type': kind, 'sessionId': 'session-1', 'cwd': extra.pop('cwd', 'C:/work/alpha'),
                 'timestamp': at, 'entrypoint': extra.pop('entrypoint', 'claude-vscode'),
                 'message': {'role': kind, 'content': content}}, **extra)


class InboxTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.workspace = self.root / 'alpha-workspace'
        (self.workspace / '.orchestration').mkdir(parents=True)
        (self.workspace / '.orchestration' / 'project.json').write_text(
            json.dumps({'schema_version': 1, 'project_id': 'alpha'}), encoding='utf-8')

    def transcript(self, entries, name='session.jsonl'):
        path = self.root / name
        path.write_text('\n'.join(json.dumps(entry) for entry in entries) + '\n', encoding='utf-8')
        return path

    def claude_session(self, *, entrypoint='claude-vscode', final='Fixed the retry loop; tests pass.'):
        cwd = str(self.workspace)
        return self.transcript([
            claude_entry('user', '<command-name>/model</command-name>', at='2026-10-23T10:00:00Z', cwd=cwd, entrypoint=entrypoint),
            claude_entry('user', [{'type': 'text', 'text': 'Fix the retry loop <system-reminder>ignore me</system-reminder>'}],
                         at='2026-10-23T10:00:05Z', cwd=cwd, entrypoint=entrypoint),
            claude_entry('user', 'Caveat text', at='2026-10-23T10:00:06Z', cwd=cwd, entrypoint=entrypoint, isMeta=True),
            claude_entry('assistant', [{'type': 'tool_use', 'name': 'Edit', 'input': {'file_path': 'app/retry.py'}},
                                       {'type': 'tool_use', 'name': 'Bash', 'input': {'command': 'python -m pytest\nmore'}}],
                         at='2026-10-23T10:03:00Z', cwd=cwd, entrypoint=entrypoint),
            claude_entry('user', [{'type': 'tool_result', 'is_error': True, 'content': 'AssertionError: boom\ntrace'}],
                         at='2026-10-23T10:04:00Z', cwd=cwd, entrypoint=entrypoint),
            claude_entry('assistant', [{'type': 'text', 'text': 'Working on it.'}], at='2026-10-23T10:05:00Z',
                         cwd=cwd, entrypoint=entrypoint, isSidechain=True),
            claude_entry('user', [{'type': 'text', 'text': 'Also update the docs'}], at='2026-10-23T10:06:00Z',
                         cwd=cwd, entrypoint=entrypoint),
            claude_entry('assistant', [{'type': 'text', 'text': final}], at='2026-10-23T10:09:00Z',
                         cwd=cwd, entrypoint=entrypoint),
        ])

    def test_claude_parser_keeps_requests_tools_errors_and_final_reply(self):
        facts = parse_claude(self.claude_session())
        self.assertEqual(facts['prompts'], ['Fix the retry loop', 'Also update the docs'])
        self.assertEqual(facts['files'], ['app/retry.py'])
        self.assertEqual(facts['commands'], ['python -m pytest'])
        self.assertEqual(facts['errors'], ['AssertionError: boom'])
        self.assertEqual(facts['final'], 'Fixed the retry loop; tests pass.')
        self.assertEqual((facts['tool_calls'], facts['session_id'], facts['interactive']), (2, 'session-1', True))
        self.assertIsNone(skip_reason(facts))

    def test_sdk_sessions_are_left_to_task_review(self):
        facts = parse_claude(self.claude_session(entrypoint='sdk-cli'))
        self.assertEqual(skip_reason(facts), 'non_interactive')

    def test_codex_parser_reads_patches_shell_calls_and_filters_injected_context(self):
        rows = [
            {'type': 'session_meta', 'timestamp': '2026-10-23T09:00:00Z',
             'payload': {'id': 'codex-1', 'cwd': str(self.workspace), 'source': 'vscode', 'originator': 'codex_vscode'}},
            {'type': 'response_item', 'timestamp': '2026-10-23T09:00:01Z', 'payload': {'type': 'message', 'role': 'user',
             'content': [{'type': 'input_text', 'text': '<environment_context>cwd</environment_context>'}]}},
            {'type': 'response_item', 'timestamp': '2026-10-23T09:00:02Z', 'payload': {'type': 'message', 'role': 'user',
             'content': [{'type': 'input_text', 'text': 'Add quota handoff'}]}},
            {'type': 'response_item', 'timestamp': '2026-10-23T09:01:00Z', 'payload': {'type': 'custom_tool_call',
             'name': 'apply_patch', 'input': '*** Begin Patch\n*** Update File: app/quota.py\n*** Add File: tests/test_quota.py\n'}},
            {'type': 'response_item', 'timestamp': '2026-10-23T09:02:00Z', 'payload': {'type': 'function_call',
             'name': 'shell', 'arguments': json.dumps({'command': ['powershell', '-Command', 'pytest']})}},
            {'type': 'response_item', 'timestamp': '2026-10-23T09:05:00Z', 'payload': {'type': 'message',
             'role': 'assistant', 'content': [{'type': 'output_text', 'text': 'Quota handoff added.'}]}},
        ]
        facts = parse_codex(self.transcript(rows, 'rollout-test.jsonl'))
        self.assertEqual(facts['prompts'], ['Add quota handoff'])
        self.assertEqual(facts['files'], ['app/quota.py', 'tests/test_quota.py'])
        self.assertEqual(facts['commands'], ['pytest'])  # the shell wrapper is stripped
        self.assertEqual((facts['session_id'], facts['final'], facts['interactive']), ('codex-1', 'Quota handoff added.', True))
        rows[0]['payload']['source'] = 'exec'
        self.assertEqual(skip_reason(parse_codex(self.transcript(rows, 'rollout-exec.jsonl'))), 'non_interactive')

    def facts(self, **updates):
        base = {'agent': 'claude-code', 'session_id': 's', 'cwd': str(self.workspace), 'started_at': '2026-10-23T10:00:00Z',
                'ended_at': '2026-10-23T10:10:00Z', 'prompts': ['Investigate the build'], 'files': [], 'commands': [],
                'errors': [], 'final': 'Done.', 'tool_calls': 3, 'truncated': False, 'interactive': True}
        base.update(updates)
        return base

    def test_skip_rules(self):
        cases = {'no_requests': self.facts(prompts=[]),
                 'no_activity': self.facts(tool_calls=0),
                 'too_short': self.facts(ended_at='2026-10-23T10:00:10Z'),
                 'smoke_test': self.facts(commands=['echo hello', 'git status']),
                 'setup_failure': self.facts(errors=['bash: rg: command not found', 'Permission denied'])}
        for reason, facts in cases.items():
            with self.subTest(reason=reason):
                self.assertEqual(skip_reason(facts), reason)
        self.assertIsNone(skip_reason(self.facts(files=['a.py'], ended_at='2026-10-23T10:00:10Z')))

    def test_candidate_redacts_secrets_drops_broken_claims_and_fits_the_brain(self):
        facts = self.facts(prompts=['Use key sk-or-v1-abcdefghijklmnopqrstuvwxyz and password=hunter22 to test'],
                           files=['app/a.py'], final='The upload now retries. The old CLI is broken. Tests pass.')
        candidate, evidence, warnings = build_candidate(facts, 'alpha')
        dumped = json.dumps([candidate, evidence])
        self.assertNotIn('abcdefghijklmnop', dumped)
        self.assertNotIn('hunter22', dumped)
        self.assertIn('password=[redacted]', candidate['episode']['problem'])
        self.assertNotIn('is broken', candidate['episode']['outcome'])
        self.assertIn('Tests pass.', candidate['episode']['outcome'])
        self.assertEqual(len(warnings), 1)
        BrainStore(self.root).candidate(dict(candidate, source={'type': 'user', 'note': 'Synthetic reviewed session'}))

    def test_project_resolution_uses_marker_then_known_git_root(self):
        self.assertEqual(resolve_project(str(self.workspace / 'app'), set()), 'alpha')
        repo = self.root / 'OpenWhispr'
        (repo / '.git').mkdir(parents=True)
        (repo / 'src').mkdir()
        self.assertEqual(resolve_project(str(repo / 'src'), {'openwhispr'}), 'openwhispr')
        self.assertIsNone(resolve_project(str(repo / 'src'), {'other'}))
        self.assertIsNone(resolve_project(None, {'other'}))

    def test_capture_review_and_rejection_flow(self):
        box = Inbox(self.root)
        first = box.capture(parse_claude(self.claude_session()), known=set())
        self.assertEqual((first['status'], first['project_id']), ('captured', 'alpha'))
        again = box.capture(parse_claude(self.claude_session()), known=set())
        self.assertEqual(again['status'], 'refreshed')
        listed = box.listing()
        self.assertEqual([row['id'] for row in listed], [first['candidate_id']])
        self.assertTrue(listed[0]['title'].startswith('Session: Fix the retry loop'))
        with self.assertRaises(ValueError):
            box.accept(first['candidate_id'], 'Tester', 'too short')
        accepted = box.accept(first['candidate_id'], 'Tester', 'Checked the retry fix against the diff and tests.')
        brain = BrainStore(self.root)
        memory = brain.get(accepted['memory_id'])
        self.assertEqual((memory['status'], memory['kind'], memory['source']['type']), ('active', 'episode', 'user'))
        self.assertIn('Reviewed native claude-code session session-1', memory['source']['note'])
        self.assertEqual(brain.search('retry loop', 'alpha', record_trace=False, jev=False)['results'][0]['id'],
                         accepted['memory_id'])
        self.assertEqual(box.capture(parse_claude(self.claude_session()), known=set())['reason'], 'already_reviewed')

    def test_rejected_sessions_and_fingerprints_are_not_offered_again(self):
        box = Inbox(self.root)
        captured = box.capture(parse_claude(self.claude_session()), known=set())
        box.reject(captured['candidate_id'], 'Tester', 'Not durable knowledge.')
        self.assertEqual(box.listing(), [])
        self.assertEqual(box.capture(parse_claude(self.claude_session()), known=set())['reason'], 'rejected_before')
        recent = json.loads((box.home / 'recent-captures.json').read_text(encoding='utf-8'))
        self.assertEqual(recent[-1]['reason'], 'rejected_before')

    def test_full_inbox_and_unknown_projects_are_skipped(self):
        box = Inbox(self.root)
        with patch.object(inbox_module, 'MAX_PENDING', 0):
            self.assertEqual(box.capture(parse_claude(self.claude_session()), known=set())['reason'], 'inbox_full')
        stray = self.facts(cwd=str(self.root / 'random'), files=['x.py'])
        self.assertEqual(box.capture(stray, known=set())['reason'], 'unknown_project')

    def test_hook_reads_stdin_and_never_raises(self):
        path = self.claude_session()
        payload = io.StringIO(json.dumps({'session_id': 'session-1', 'transcript_path': str(path),
                                          'cwd': str(self.workspace), 'hook_event_name': 'SessionEnd'}))
        self.assertEqual(inbox_module.hook(self.root, payload)['status'], 'captured')
        self.assertEqual(inbox_module.hook(self.root, io.StringIO('not json'))['status'], 'error')
        self.assertEqual(inbox_module.hook(self.root, io.StringIO('{}'))['reason'], 'no_transcript')
        with patch('sys.stdin', io.StringIO('{}')):
            self.assertEqual(inbox_module.main(['--root', str(self.root), 'hook']), 0)

    def test_codex_scan_captures_only_idle_recent_rollouts(self):
        home = self.root / 'codex-home'
        folder = home / 'sessions' / '2026' / '10' / '23'
        folder.mkdir(parents=True)
        rows = [{'type': 'session_meta', 'timestamp': '2026-10-23T09:00:00Z',
                 'payload': {'id': 'codex-2', 'cwd': str(self.workspace), 'source': 'vscode'}},
                {'type': 'response_item', 'timestamp': '2026-10-23T09:00:02Z', 'payload': {'type': 'message',
                 'role': 'user', 'content': [{'type': 'input_text', 'text': 'Refactor the parser'}]}},
                {'type': 'response_item', 'timestamp': '2026-10-23T09:03:00Z', 'payload': {'type': 'custom_tool_call',
                 'name': 'apply_patch', 'input': '*** Update File: app/parser.py\n'}},
                {'type': 'response_item', 'timestamp': '2026-10-23T09:04:00Z', 'payload': {'type': 'message',
                 'role': 'assistant', 'content': [{'type': 'output_text', 'text': 'Parser refactored.'}]}}]
        idle, active = folder / 'rollout-idle.jsonl', folder / 'rollout-active.jsonl'
        for path in (idle, active):
            path.write_text('\n'.join(json.dumps(row) for row in rows), encoding='utf-8')
        old = time.time() - 3600
        os.utime(idle, (old, old))
        result = inbox_module.scan_codex(self.root, home, hours=24, idle_minutes=10)
        self.assertEqual(result['scanned'], 1)
        self.assertEqual(result['results'][0]['status'], 'captured')


if __name__ == '__main__':
    unittest.main()
