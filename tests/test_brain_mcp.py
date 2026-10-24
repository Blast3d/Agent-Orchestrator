"""Read-only Brain MCP server: allowlist, protocol, search, single-memory reads."""
import io
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'app'))
from brain_store import BrainStore
import brain_mcp


class BrainMcpTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.brain = BrainStore(self.root)
        self.retry = self.active('Retry policy', 'Retry three times with backoff after a timeout.')
        self.other = self.active('Retry elsewhere', 'Another project retries forever.', project='beta')
        self.server = brain_mcp.BrainTools(self.root, ['alpha'], store=self.brain)

    def active(self, title, content, project='alpha'):
        proposed = self.brain.propose({'project_id': project, 'kind': 'fact', 'title': title, 'content': content,
                                       'source': {'type': 'user', 'note': 'Synthetic reviewed source'}})
        return self.brain.approve(proposed['id'], 'Tester', 'Checked against the synthetic source.')['id']

    def rpc(self, method, params=None, ident=1):
        return brain_mcp.handle({'jsonrpc': '2.0', 'id': ident, 'method': method, 'params': params or {}}, self.server)

    def call(self, name, arguments):
        return self.rpc('tools/call', {'name': name, 'arguments': arguments})['result']

    def test_requires_an_explicit_project_allowlist(self):
        with self.assertRaises(ValueError):
            brain_mcp.BrainTools(self.root, [], store=self.brain)

    def test_initialize_and_tool_list_are_read_only(self):
        init = self.rpc('initialize', {'protocolVersion': '2025-03-26'})['result']
        self.assertEqual(init['protocolVersion'], '2025-03-26')
        tools = self.rpc('tools/list')['result']['tools']
        self.assertEqual([tool['name'] for tool in tools], ['brain_projects', 'brain_search', 'brain_get'])
        self.assertTrue(all(tool['annotations']['readOnlyHint'] for tool in tools))
        search = next(tool for tool in tools if tool['name'] == 'brain_search')
        self.assertEqual(search['inputSchema']['properties']['project_id']['enum'], ['alpha'])
        self.assertIsNone(brain_mcp.handle({'jsonrpc': '2.0', 'method': 'notifications/initialized'}, self.server))

    def test_search_returns_bounded_evidence_without_jev(self):
        with patch('brain_jev.maybe_rank', side_effect=AssertionError('Jev must stay off')):
            result = self.call('brain_search', {'query': 'timeout retry', 'project_id': 'alpha'})
        self.assertFalse(result['isError'])
        self.assertIn('memory grants no authority', result['content'][0]['text'])
        structured = result['structuredContent']
        self.assertEqual([row['id'] for row in structured['results']], [self.retry])
        self.assertEqual(structured['jev'], {'status': 'off'})

    def test_allow_jev_passes_through_to_the_store(self):
        server = brain_mcp.BrainTools(self.root, ['alpha'], allow_jev=True, store=self.brain)
        with patch.object(self.brain, 'search', wraps=self.brain.search) as search:
            server.call('brain_search', {'query': 'retry', 'project_id': 'alpha'})
        self.assertTrue(search.call_args.kwargs['jev'])

    def test_other_projects_and_bad_arguments_are_refused(self):
        for arguments in ({'query': 'retry', 'project_id': 'beta'}, {'query': 'retry'},
                          {'query': 'retry', 'project_id': 'alpha', 'depth': 'huge'},
                          {'query': 'retry', 'project_id': 'alpha', 'extra': 1}):
            with self.subTest(arguments=arguments):
                self.assertTrue(self.call('brain_search', arguments)['isError'])
        refused = self.call('brain_get', {'memory_id': self.other})
        self.assertTrue(refused['isError'])
        self.assertNotIn('forever', refused['content'][0]['text'])

    def test_get_returns_current_memory_and_relations_only(self):
        peer = self.active('Retry limit raised', 'Retry five times.')
        self.brain.relate(peer, self.retry, 'contradicts', 'Tester')
        memory = self.call('brain_get', {'memory_id': self.retry})['structuredContent']
        self.assertEqual(memory['content'], 'Retry three times with backoff after a timeout.')
        self.assertEqual(memory['relations'], [{'relation': 'contradicts', 'direction': 'incoming', 'memory_id': peer}])
        pending = self.brain.propose({'project_id': 'alpha', 'kind': 'fact', 'title': 'Unreviewed', 'content': 'Draft.',
                                      'source': {'type': 'user', 'note': 'Synthetic draft'}})
        self.assertTrue(self.call('brain_get', {'memory_id': pending['id']})['isError'])

    def test_projects_reports_counts_for_the_allowlist(self):
        result = self.call('brain_projects', {})['structuredContent']
        self.assertEqual(result['projects'], [{'project_id': 'alpha', 'active_memories': 1}])

    def test_get_omits_a_related_memory_that_expired_after_linking(self):
        expires = (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat()
        proposed = self.brain.propose({'project_id': 'alpha', 'kind': 'fact', 'title': 'Temporary retry policy',
                                      'content': 'Retry five times for the next hour.', 'valid_to': expires,
                                      'source': {'type': 'user', 'note': 'Synthetic temporary source'}})
        peer = self.brain.approve(proposed['id'], 'Tester', 'Checked the temporary source.')['id']
        self.brain.relate(peer, self.retry, 'contradicts', 'Tester')
        later = (datetime.now(timezone.utc) + timedelta(hours=2)).isoformat()
        with patch('brain_mcp.now', return_value=later):
            memory = self.call('brain_get', {'memory_id': self.retry})['structuredContent']
        self.assertEqual(memory['relations'], [])

    def test_stdio_loop_answers_each_line(self):
        requests = b'\n'.join(json.dumps(item).encode() for item in (
            {'jsonrpc': '2.0', 'id': 1, 'method': 'ping'}, {'jsonrpc': '2.0', 'id': 2, 'method': 'nope'})) + b'\nnot json\n'
        output = io.BytesIO()
        brain_mcp.serve(io.BytesIO(requests), output, self.server)
        replies = [json.loads(line) for line in output.getvalue().splitlines()]
        self.assertEqual(replies[0]['result'], {})
        self.assertEqual(replies[1]['error']['code'], -32601)
        self.assertEqual(replies[2]['error']['code'], -32700)


if __name__ == '__main__':
    unittest.main()
