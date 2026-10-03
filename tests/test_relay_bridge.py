"""Synthetic Relay bridge checks: temporary runs and mailbox only, no model or provider calls."""
from datetime import datetime, timedelta, timezone
import io
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

APP = Path(__file__).resolve().parents[1] / 'app'
sys.path.insert(0, str(APP))
import relay_bridge
import relay_mcp

NOW = datetime(2026, 10, 2, 3, 0, tzinfo=timezone.utc)


def make_run(workspace, run_id, *, status='in_progress', checkpoint_at=NOW, project='agent-orchestrator', **checkpoint):
    run = Path(workspace) / '.orchestration' / run_id
    (run / 'deliverables').mkdir(parents=True)
    state = {'run_id': run_id, 'owner': 'fable', 'session': run_id, 'generation': 3, 'status': 'active',
             'checkpoint_at': checkpoint_at.isoformat(),
             'checkpoint': {'objective': f'Objective for {run_id}\nsecond line', 'completed': [], 'next_steps': [],
                            'decisions': [], 'constraints': [], 'open_jobs': [], **checkpoint}}
    (run / 'coordinator.json').write_text(json.dumps(state), encoding='utf-8')
    (run / 'run.json').write_text(json.dumps({'run_id': run_id, 'status': status, 'project_id': project}), encoding='utf-8')
    (run / 'brief.md').write_text('# Brief\nDo the thing.', encoding='utf-8')
    (run / 'deliverables' / 'report.md').write_text('done', encoding='utf-8')
    return run


class BridgeTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.base = Path(temp.name)
        self.workspace = self.base / 'ws'
        self.mail = self.base / 'relay'
        self.tasks = self.base / 'tasks'
        patches = [patch.object(relay_bridge, 'relay_dir', return_value=self.mail),
                   patch.object(relay_bridge, 'TASKS', self.tasks),
                   patch.object(relay_bridge, 'default_workspaces', return_value=[self.workspace])]
        for item in patches:
            item.start()
            self.addCleanup(item.stop)

    def test_overview_groups_runs_and_hides_experiments_and_archived(self):
        make_run(self.workspace, 'live-run', checkpoint_at=NOW - timedelta(hours=1))
        make_run(self.workspace, 'old-run', checkpoint_at=NOW - timedelta(days=30))
        make_run(self.workspace, 'trial-run', project='experiment-abc')
        with patch.object(relay_bridge, '_modified', return_value=None):
            view = relay_bridge.overview([self.workspace], now=NOW)
            everything = relay_bridge.overview([self.workspace], include_archived=True, now=NOW)
        self.assertEqual([r['id'] for r in view['runs']], ['live-run'])
        self.assertEqual(view['runs'][0]['group'], 'current')
        self.assertEqual(view['runs'][0]['title'], 'Objective for live-run')
        self.assertEqual({r['id'] for r in everything['runs']}, {'live-run', 'old-run'})
        self.assertEqual(view['model_calls'], 0)

    def test_default_peek_and_exact_ack_keep_unreceived_messages_unread(self):
        first = relay_bridge.post('lead', 'First', 'one', run_id='run-a')
        second = relay_bridge.post('lead', 'Second', 'two', run_id='run-b')
        self.assertEqual(relay_bridge.inbox('relay')['count'], 2)
        self.assertEqual(relay_bridge.inbox('relay')['count'], 2, 'lost transport response must not consume messages')
        self.assertFalse((self.mail / 'cursors.json').exists())
        relay_bridge.acknowledge('relay', [second['id']])
        self.assertEqual([m['id'] for m in relay_bridge.inbox('relay')['messages']], [first['id']])
        relay_bridge.acknowledge('relay', [second['id']])
        relay_bridge.acknowledge('relay', [first['id']])
        self.assertEqual(relay_bridge.inbox('relay')['count'], 0)
        self.assertEqual(len(relay_bridge.thread()['messages']), 2)

    def test_ack_rejects_unknown_or_other_recipient_ids_atomically(self):
        mine = relay_bridge.post('lead', 'To Relay', 'body')
        other = relay_bridge.post('relay', 'To lead', 'body')
        for ids in ([mine['id'], other['id']], [mine['id'], 'missing0001'], [], 'wrong', [5]):
            with self.subTest(ids=ids), self.assertRaises(ValueError):
                relay_bridge.acknowledge('relay', ids)
        self.assertEqual(relay_bridge.inbox('relay')['count'], 1)
        with patch.object(relay_bridge, 'write_json', side_effect=OSError('disk full')):
            with self.assertRaises(OSError):
                relay_bridge.acknowledge('relay', [mine['id']])
        self.assertEqual(relay_bridge.inbox('relay')['count'], 1)

    def test_same_client_ref_requires_the_same_semantic_message(self):
        original = dict(sender='relay', subject='Question', body='Which one?', kind='question',
                        to='user', reply_to='request0001', run_id='run-a', client_ref='operation-0001')
        first = relay_bridge.post(**original)
        self.assertEqual(relay_bridge.post(**original)['id'], first['id'])
        for change in ({'subject': 'Changed'}, {'body': 'Other'}, {'kind': 'reply'}, {'to': 'lead'},
                       {'reply_to': 'request0002'}, {'run_id': 'run-b'}):
            with self.subTest(change=change), self.assertRaisesRegex(ValueError, 'different message'):
                relay_bridge.post(**{**original, **change})
        self.assertEqual(len(relay_bridge.thread()['messages']), 1)
        second = relay_bridge.post(**{**original, 'client_ref': 'operation-0002'})
        self.assertNotEqual(second['id'], first['id'], 'identical content can be a deliberate new operation')

    def test_cli_peeks_by_default_then_acks_exact_ids(self):
        message = relay_bridge.post('relay', 'Test', 'Body')
        with patch('sys.stdout', new=io.StringIO()):
            relay_bridge.main(['inbox', '--for', 'lead'])
        self.assertEqual(relay_bridge.inbox('lead')['count'], 1)
        with patch('sys.stdout', new=io.StringIO()):
            relay_bridge.main(['ack', '--for', 'lead', '--id', message['id']])
        self.assertEqual(relay_bridge.inbox('lead')['count'], 0)

    def test_run_status_includes_checkpoint_tail_tasks_and_deliverables(self):
        make_run(self.workspace, 'live-run', completed=[f'step {i}' for i in range(12)], next_steps=['ship'])
        record = self.tasks / 'job1' / 'record.json'
        record.parent.mkdir(parents=True)
        record.write_text(json.dumps({'job_id': 'job1', 'run_id': 'live-run', 'worker': 'claude',
                                      'status': 'accepted', 'created_at': '2026-10-02'}), encoding='utf-8')
        other = self.tasks / 'job2' / 'record.json'
        other.parent.mkdir(parents=True)
        other.write_text(json.dumps({'job_id': 'job2', 'run_id': 'another'}), encoding='utf-8')
        detail = relay_bridge.run_status('live-run', [self.workspace], now=NOW)
        self.assertEqual(detail['checkpoint']['completed'], [f'step {i}' for i in range(4, 12)])
        self.assertEqual(detail['checkpoint']['next_steps'], ['ship'])
        self.assertEqual([t['job_id'] for t in detail['tasks']], ['job1'])
        self.assertEqual(detail['deliverables'], ['report.md'])
        self.assertIn('Do the thing', detail['brief_excerpt'])

    def test_run_status_rejects_path_traversal_and_unknown_runs(self):
        for bad in ('../secrets', 'a/b', '', None, '..'):
            with self.assertRaises(ValueError):
                relay_bridge.run_status(bad, [self.workspace])
        with self.assertRaises(ValueError):
            relay_bridge.run_status('missing-run', [self.workspace])

    def test_mailbox_round_trip_with_read_cursors(self):
        first = relay_bridge.post('relay', 'Need status', 'What is left on live-run?', kind='question', run_id='live-run')
        self.assertEqual((first['from'], first['to'], first['seq']), ('relay', 'lead', 1))
        reply = relay_bridge.post('lead', 'Status', 'Tests remain.', kind='reply', lead='claude', reply_to=first['id'])
        self.assertEqual((reply['to'], reply['lead'], reply['seq']), ('relay', 'claude', 2))
        self.assertEqual(relay_bridge.inbox('lead', mark_read=False)['count'], 1)
        self.assertEqual(relay_bridge.inbox('lead', mark_read=True)['messages'][0]['subject'], 'Need status')
        self.assertEqual(relay_bridge.inbox('lead')['count'], 0)
        self.assertEqual(relay_bridge.inbox('lead', unread_only=False)['count'], 1)
        self.assertEqual(relay_bridge.inbox('relay')['messages'][0]['body'], 'Tests remain.')
        self.assertEqual([m['seq'] for m in relay_bridge.thread()['messages']], [1, 2])

    def test_run_filtered_reads_are_marked_and_cursor_compacts(self):
        a = relay_bridge.post('lead', 'a', 'for run x', run_id='run-x')
        relay_bridge.post('lead', 'b', 'general')
        c = relay_bridge.post('lead', 'c', 'for run x again', run_id='run-x')
        self.assertEqual([m['id'] for m in relay_bridge.inbox('relay', run_id='run-x', mark_read=True)['messages']], [a['id'], c['id']])
        self.assertEqual(relay_bridge.inbox('relay', run_id='run-x')['count'], 0)
        self.assertEqual(json.loads((self.mail / 'cursors.json').read_text())['relay'], {'cursor': 1, 'read': [3]})
        self.assertEqual([m['subject'] for m in relay_bridge.inbox('relay', mark_read=True)['messages']], ['b'])
        self.assertEqual(json.loads((self.mail / 'cursors.json').read_text())['relay'], {'cursor': 3, 'read': []})

    def test_mailbox_skips_malformed_lines_and_keeps_sequence_unique(self):
        relay_bridge.post('relay', 'one', 'first')
        with (self.mail / 'mailbox.jsonl').open('a', encoding='utf-8') as stream:
            stream.write('null\n{"to": "lead"}\nnot json\n')
        second = relay_bridge.post('relay', 'two', 'second')
        self.assertEqual(second['seq'], 2)
        self.assertEqual([m['subject'] for m in relay_bridge.inbox('lead')['messages']], ['one', 'two'])

    def test_unlocked_appends_with_repeated_seq_are_renumbered_in_file_order(self):
        relay_bridge.post('relay', 'one', 'first')
        row = lambda ident, seq: json.dumps({'id': ident, 'seq': seq, 'from': 'relay', 'to': 'lead',
                                             'kind': 'note', 'subject': ident, 'body': 'b'})
        with (self.mail / 'mailbox.jsonl').open('a', encoding='utf-8') as stream:
            stream.write('\n'.join([row('dup', 1), '{"seq": 2}', row('jump', 9), row('back', 4)]) + '\n')
        third = relay_bridge.post('relay', 'after', 'appended by the bridge')
        seqs = [(m['subject'], m['seq']) for m in relay_bridge.thread(root=self.mail)['messages']]
        self.assertEqual(seqs, [('one', 1), ('dup', 2), ('jump', 9), ('back', 10), ('after', 11)])
        self.assertEqual(third['seq'], 11)
        self.assertEqual([m['subject'] for m in relay_bridge.inbox('lead', mark_read=True)['messages']], ['one', 'dup', 'jump', 'back', 'after'])
        self.assertEqual(relay_bridge.inbox('lead')['count'], 0)

    def test_run_folder_link_outside_orchestration_is_refused(self):
        outside = make_run(self.base / 'elsewhere', 'secret-run')
        link = self.workspace / '.orchestration' / 'linked-run'
        link.parent.mkdir(parents=True, exist_ok=True)
        try:
            import _winapi
            _winapi.CreateJunction(str(outside), str(link))
        except (ImportError, OSError):
            try:
                link.symlink_to(outside, target_is_directory=True)
            except OSError:
                self.skipTest('Cannot create a directory link here')
        with self.assertRaisesRegex(ValueError, 'outside'):
            relay_bridge.run_status('linked-run', [self.workspace])
        view = relay_bridge.overview([self.workspace], include_archived=True, now=NOW)
        self.assertEqual(view['runs'], [])
        self.assertEqual([e['id'] for e in view['errors']], ['linked-run'])

    def test_post_validates_sender_kind_size_and_lead_identity(self):
        cases = [dict(sender='bot'), dict(kind='dispatch'), dict(subject=' '), dict(body='x' * (relay_bridge.MAX_BODY + 1)),
                 dict(sender='relay', lead='claude'), dict(sender='lead', lead='grok'), dict(run_id='../x')]
        for case in cases:
            values = {'sender': 'relay', 'subject': 's', 'body': 'b', 'kind': 'note', **case}
            with self.subTest(case=case), self.assertRaises(ValueError):
                relay_bridge.post(values.pop('sender'), values.pop('subject'), values.pop('body'), **values)

    def test_handoff_lists_current_runs_and_lead_messages(self):
        make_run(self.workspace, 'live-run', completed=['built bridge'], next_steps=['review'], decisions=['mailbox only'])
        relay_bridge.post('lead', 'Plan', 'Please review the MCP tools.', kind='request', lead='claude', run_id='live-run')
        with patch.object(relay_bridge, '_modified', return_value=None):
            written = relay_bridge.write_handoff([self.workspace], now=NOW + timedelta(minutes=5))
        text = Path(written['path']).read_text(encoding='utf-8')
        for expected in ('# Relay handoff', '### live-run  (current)', 'built bridge', 'review', 'mailbox only',
                         'Please review the MCP tools.', 'relay_post_to_lead'):
            self.assertIn(expected, text)


class McpTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.base = Path(temp.name)
        self.workspace = self.base / 'ws'
        make_run(self.workspace, 'live-run')
        for item in (patch.object(relay_bridge, 'relay_dir', return_value=self.base / 'relay'),
                     patch.object(relay_bridge, 'TASKS', self.base / 'tasks')):
            item.start()
            self.addCleanup(item.stop)

    def exchange(self, *messages):
        stdin = io.BytesIO(b''.join((json.dumps(m) + '\n').encode() if not isinstance(m, bytes) else m for m in messages))
        stdout = io.BytesIO()
        relay_mcp.serve(stdin, stdout, [self.workspace])
        return [json.loads(line) for line in stdout.getvalue().decode().splitlines()]

    def test_initialize_negotiates_version_and_ignores_notifications(self):
        replies = self.exchange({'jsonrpc': '2.0', 'id': 1, 'method': 'initialize',
                                 'params': {'protocolVersion': '2025-03-26', 'capabilities': {}, 'clientInfo': {'name': 't'}}},
                                {'jsonrpc': '2.0', 'method': 'notifications/initialized'},
                                {'jsonrpc': '2.0', 'id': 2, 'method': 'initialize', 'params': {'protocolVersion': '1999-01-01'}},
                                {'jsonrpc': '2.0', 'id': 3, 'method': 'ping'})
        self.assertEqual([r['id'] for r in replies], [1, 2, 3])
        self.assertEqual(replies[0]['result']['protocolVersion'], '2025-03-26')
        self.assertEqual(replies[1]['result']['protocolVersion'], relay_mcp.PROTOCOLS[0])
        self.assertIn('tools', replies[0]['result']['capabilities'])

    def test_retried_writes_keep_one_question_and_reject_conflicting_reuse(self):
        arguments = {'subject': 'Question', 'body': 'Cobalt?', 'kind': 'question',
                     'reply_to': 'request0001', 'client_ref': 'stable-mcp-question-001'}
        def call(ident, args):
            return {'jsonrpc': '2.0', 'id': ident, 'method': 'tools/call',
                    'params': {'name': 'relay_reply_to_user', 'arguments': args}}
        # Two independent stdio exchanges model a lost response and a client/server reconnect.
        first = self.exchange(call(1, arguments))[0]['result']['structuredContent']
        second = self.exchange(call(2, arguments))[0]['result']['structuredContent']
        self.assertEqual(first['id'], second['id'])
        self.assertTrue(second['duplicate'])
        conflict = self.exchange(call(3, {**arguments, 'body': 'Changed'}))[0]['result']
        self.assertTrue(conflict['isError'])
        missing = self.exchange(call(4, {key: value for key, value in arguments.items() if key != 'client_ref'}))[0]['result']
        self.assertTrue(missing['isError'])
        self.assertEqual(len(relay_bridge.thread()['messages']), 1)

    def test_dropped_mcp_read_response_is_redelivered_until_explicit_ack(self):
        message = relay_bridge.post('lead', 'For Relay', 'Body')
        def call(ident, name, arguments):
            return {'jsonrpc': '2.0', 'id': ident, 'method': 'tools/call',
                    'params': {'name': name, 'arguments': arguments}}
        self.exchange(call(1, 'relay_read_messages', {}))
        again = self.exchange(call(2, 'relay_read_messages', {}))[0]['result']['structuredContent']
        self.assertEqual([m['id'] for m in again['messages']], [message['id']])
        ack = call(3, 'relay_ack_messages', {'message_ids': [message['id']]})
        self.assertFalse(self.exchange(ack)[0]['result']['isError'])
        self.assertFalse(self.exchange(ack)[0]['result']['isError'])
        self.assertEqual(self.exchange(call(4, 'relay_read_messages', {}))[0]['result']['structuredContent']['count'], 0)

    def test_stdio_write_failure_after_operation_is_safe_to_retry(self):
        class BrokenOutput:
            def write(self, _data):
                raise BrokenPipeError('connection closed before receipt')

        incoming = relay_bridge.post('lead', 'Read me', 'Body')
        request = {'jsonrpc': '2.0', 'id': 1, 'method': 'tools/call',
                   'params': {'name': 'relay_read_messages', 'arguments': {}}}
        with self.assertRaises(BrokenPipeError):
            relay_mcp.serve(io.BytesIO((json.dumps(request) + '\n').encode()), BrokenOutput(), [self.workspace])
        self.assertEqual(relay_bridge.inbox('relay')['messages'][0]['id'], incoming['id'])
        request['params'] = {'name': 'relay_post_to_lead', 'arguments': {
            'subject': 'Done', 'body': 'Result', 'client_ref': 'lost-write-receipt-001'}}
        with self.assertRaises(BrokenPipeError):
            relay_mcp.serve(io.BytesIO((json.dumps(request) + '\n').encode()), BrokenOutput(), [self.workspace])
        retry = self.exchange(request)[0]['result']['structuredContent']
        self.assertTrue(retry['duplicate'])
        self.assertEqual(relay_bridge.inbox('lead')['count'], 1)

    def test_tools_list_and_post_then_read_round_trip(self):
        replies = self.exchange(
            {'jsonrpc': '2.0', 'id': 1, 'method': 'tools/list'},
            {'jsonrpc': '2.0', 'id': 2, 'method': 'tools/call',
             'params': {'name': 'relay_post_to_lead', 'arguments': {'subject': 'Hi', 'body': 'Relay here', 'kind': 'handoff', 'client_ref': 'handoff-test-001'}}},
            {'jsonrpc': '2.0', 'id': 3, 'method': 'tools/call',
             'params': {'name': 'relay_run_status', 'arguments': {'run_id': 'live-run'}}})
        names = {tool['name'] for tool in replies[0]['result']['tools']}
        self.assertEqual(names, {'relay_overview', 'relay_run_status', 'relay_handoff', 'relay_read_messages',
                                 'relay_thread', 'relay_post_to_lead', 'relay_reply_to_user', 'relay_ack_messages'})
        posted = replies[1]['result']
        self.assertFalse(posted['isError'])
        self.assertEqual(posted['structuredContent']['to'], 'lead')
        self.assertEqual(replies[2]['result']['structuredContent']['id'], 'live-run')
        self.assertEqual(relay_bridge.inbox('lead')['messages'][0]['body'], 'Relay here')

    def test_reply_to_user_requires_a_reply_target_and_reaches_only_the_user(self):
        request = relay_bridge.post('user', 'Voice', 'What is next?')
        replies = self.exchange(
            {'jsonrpc': '2.0', 'id': 1, 'method': 'tools/call',
             'params': {'name': 'relay_reply_to_user', 'arguments': {'subject': 'Next', 'body': 'Tests.'}}},
            {'jsonrpc': '2.0', 'id': 2, 'method': 'tools/call',
             'params': {'name': 'relay_reply_to_user', 'arguments': {'subject': 'Next', 'body': 'Tests.',
                                                                     'reply_to': request['id'], 'kind': 'request'}}},
            {'jsonrpc': '2.0', 'id': 3, 'method': 'tools/call',
             'params': {'name': 'relay_reply_to_user', 'arguments': {'subject': 'Next', 'body': 'Tests.',
                                                                     'reply_to': request['id'], 'client_ref': 'reply-test-001'}}})
        self.assertEqual([r['result']['isError'] for r in replies], [True, True, False])
        sent = replies[2]['result']['structuredContent']
        self.assertEqual((sent['from'], sent['to'], sent['kind'], sent['reply_to']), ('relay', 'user', 'reply', request['id']))
        self.assertEqual(relay_bridge.inbox('lead')['count'], 0)
        self.assertEqual(relay_bridge.inbox('user')['messages'][0]['body'], 'Tests.')

    def test_relay_cannot_post_as_lead_or_pass_unknown_arguments(self):
        replies = self.exchange({'jsonrpc': '2.0', 'id': 1, 'method': 'tools/call',
                                 'params': {'name': 'relay_post_to_lead',
                                            'arguments': {'subject': 's', 'body': 'b', 'sender': 'lead'}}},
                                {'jsonrpc': '2.0', 'id': 2, 'method': 'tools/call',
                                 'params': {'name': 'relay_run_status', 'arguments': {'run_id': '../../etc'}}},
                                {'jsonrpc': '2.0', 'id': 3, 'method': 'tools/call', 'params': {'name': 'dispatch'}})
        self.assertTrue(all(r['result']['isError'] for r in replies))
        self.assertEqual(relay_bridge.thread()['messages'], [])

    def test_arguments_must_match_schema_types(self):
        relay_bridge.post('lead', 'keep', 'unread')
        bad = [('relay_read_messages', {'mark_read': 'false'}), ('relay_overview', {'limit': True}),
               ('relay_overview', {'limit': 0}), ('relay_post_to_lead', {'subject': 's'}),
               ('relay_post_to_lead', {'subject': 's', 'body': 'b', 'kind': 'dispatch'}),
               ('relay_run_status', {'run_id': 5}), ('relay_overview', ['not', 'an', 'object'])]
        replies = self.exchange(*[{'jsonrpc': '2.0', 'id': i, 'method': 'tools/call',
                                   'params': {'name': name, 'arguments': args}} for i, (name, args) in enumerate(bad)])
        self.assertTrue(all(r['result']['isError'] for r in replies))
        self.assertEqual(relay_bridge.inbox('relay', mark_read=False)['count'], 1)

    def test_protocol_errors_keep_the_server_running(self):
        replies = self.exchange(b'not json\n', {'jsonrpc': '2.0', 'id': 7, 'method': 'resources/list'},
                                {'id': 8, 'method': 'ping'}, b'null\n', b'[]\n',
                                b'{"x": "' + b'a' * (relay_mcp.MAX_LINE + 10) + b'"}\n',
                                [{'jsonrpc': '2.0', 'id': 10, 'method': 'ping'}, {'jsonrpc': '2.0', 'method': 'n'}],
                                {'jsonrpc': '2.0', 'id': 9, 'method': 'ping'})
        codes = [r.get('error', {}).get('code') if isinstance(r, dict) else 'batch' for r in replies]
        self.assertEqual(codes, [-32700, -32601, -32600, -32600, -32600, -32600, 'batch', None])
        self.assertEqual(replies[6], [{'jsonrpc': '2.0', 'id': 10, 'result': {}}])

    def test_real_process_answers_over_stdio(self):
        payload = json.dumps({'jsonrpc': '2.0', 'id': 1, 'method': 'tools/list'}) + '\n'
        result = subprocess.run([sys.executable, str(APP / 'relay_mcp.py'), '--workspace', str(self.workspace)],
                                input=payload.encode(), capture_output=True, timeout=30, check=True)
        self.assertNotIn(b'\r\n', result.stdout)
        self.assertEqual(len(json.loads(result.stdout)['result']['tools']), 8)


if __name__ == '__main__':
    unittest.main()
