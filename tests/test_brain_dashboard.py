"""Exercise the actual loopback HTTP boundary without touching user data."""
from copy import deepcopy
from http.client import HTTPConnection
import io
import json
import os
from pathlib import Path
import re
import sys
import tempfile
from threading import Thread
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'app'))
from brain_dashboard import MAX_REQUEST_BYTES, PAGE, main, make_server


class FakeStore:
    def __init__(self):
        self.items = {}
        self.calls = []
        self.failure = None
        self.head = 0

    def status(self):
        if self.failure:
            raise self.failure
        return {'backend': 'sqlite', 'projects': ['alpha'], 'counts': {'active': 0},
                'storage': {'brain_bytes': 4096, 'brain_limit_bytes': 1073741824,
                            'total_bytes': 8192, 'total_limit_bytes': 2147483648, 'status': 'ready'}}

    def snapshot(self, project_id=None, user_id='local'):
        self.calls.append(('snapshot', project_id, user_id))
        return {'status': self.status(), 'memories': [deepcopy(m) for m in self.items.values()
                if m['project_id'] == project_id and m['user_id'] == user_id], 'relations': [], 'traces': []}

    def list_memories(self, project_id=None, user_id='local', status=None, limit=100):
        self.calls.append(('list_memories', project_id, user_id, status, limit))
        return [deepcopy(m) for m in self.items.values() if m['project_id'] == project_id
                and m['user_id'] == user_id and (status is None or m['status'] == status)][:limit]

    def get(self, memory_id):
        if memory_id not in self.items:
            raise ValueError('Memory was not found.')
        return deepcopy(self.items[memory_id])

    def propose(self, payload):
        memory_id = 'memory-' + str(len(self.items) + 1)
        item = dict(payload, id=memory_id, status='pending')
        self.items[memory_id] = item
        self.calls.append(('propose', deepcopy(payload)))
        return deepcopy(item)

    def approve(self, memory_id, reviewer, note):
        if len(note) < 20:
            raise ValueError('A meaningful review note is required.')
        self.items[memory_id].update(status='active', reviewer=reviewer, review_note=note)
        self.calls.append(('approve', memory_id, reviewer, note))
        return self.get(memory_id)

    def search(self, **kwargs):
        self.calls.append(('search', kwargs))
        matches = [deepcopy(m) for m in self.items.values() if m['status'] == 'active'
                   and m['project_id'] == kwargs['project_id'] and m['user_id'] == kwargs['user_id']]
        return {'results': matches[:kwargs['limit']], 'elapsed_ms': 1.5, 'trace_id': 'trace-1'}

    def forget(self, memory_id, actor, reason):
        self.items[memory_id] = {key: value for key, value in self.items[memory_id].items()
                                 if key in ('id', 'project_id', 'user_id')}
        self.items[memory_id]['status'] = 'deleted'
        self.calls.append(('forget', memory_id, actor, reason))
        return self.get(memory_id)

    def supersede(self, old_id, new_id, actor, reason):
        self.calls.append(('supersede', old_id, new_id, actor, reason))
        self.items[old_id].update(status='superseded', superseded_by=new_id)
        return self.get(old_id)

    def relate(self, source_id, target_id, relation, actor, **kwargs):
        self.calls.append(('relate', source_id, target_id, relation, actor, kwargs))
        return {'id': 'edge-1', 'source_id': source_id, 'target_id': target_id, 'relation': relation}

    def changes(self, project_id, user_id='local', after=0, limit=100):
        self.calls.append(('changes', project_id, user_id, after, limit))
        return {'changes': [{'seq': seq} for seq in range(after + 1, self.head + 1)][:limit],
                'cursor': self.head, 'head': self.head, 'resync_required': after > self.head}


class DashboardTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.store = FakeStore()
        self.server = make_server(self.root, store_factory=lambda root: self.store)
        self.thread = Thread(target=self.server.serve_forever, kwargs={'poll_interval': .02}, daemon=True)
        self.thread.start()
        self.addCleanup(self.stop)

    def stop(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=2)

    def request(self, method='GET', path='/', data=None, headers=None, authorized=True, origin=True, raw=None):
        final_headers = {}
        if authorized:
            final_headers['X-Brain-Token'] = self.server.brain_token
        if origin and method == 'POST':
            final_headers['Origin'] = self.server.origin
        body = raw
        if data is not None:
            body = json.dumps(data).encode('utf-8')
            final_headers['Content-Type'] = 'application/json'
        final_headers.update(headers or {})
        connection = HTTPConnection(*self.server.server_address, timeout=3)
        try:
            connection.request(method, path, body=body, headers=final_headers)
            response = connection.getresponse()
            payload = response.read().decode('utf-8')
            content = json.loads(payload) if response.getheader('Content-Type', '').startswith('application/json') else payload
            return response.status, dict(response.getheaders()), content
        finally:
            connection.close()

    def propose(self, project='alpha', **extra):
        payload = {'project_id': project, 'kind': 'fact', 'title': 'Approved source', 'content': 'A concise useful memory.',
                   'source': {'type': 'user', 'note': 'Explicitly supplied by the local user.'}, **extra}
        status, _, memory = self.request('POST', '/api/memories', data=payload)
        self.assertEqual(status, 201, memory)
        return memory

    def test_oversized_body_rejection_closes_connection_before_reuse(self):
        # HTTP/1.0 closes rejected connections even for a keep-alive 1.1 client.
        connection=HTTPConnection(*self.server.server_address,timeout=3)
        try:
            connection.putrequest('POST','/api/search')
            connection.putheader('Origin',self.server.origin)
            connection.putheader('X-Brain-Token',self.server.brain_token)
            connection.putheader('Content-Type','application/json')
            connection.putheader('Content-Length',str(MAX_REQUEST_BYTES+1))
            connection.putheader('Connection','keep-alive');connection.endheaders()
            response=connection.getresponse()
            self.assertEqual(response.status,413);self.assertTrue(response.will_close);response.read()
            connection.request('GET','/api/status',headers={'X-Brain-Token':self.server.brain_token})
            response=connection.getresponse();self.assertEqual(response.status,200);response.read()
        finally:connection.close()

    def test_page_is_loopback_only_and_has_nonce_policy_without_external_dependencies(self):
        self.assertEqual(self.server.server_address[0], '127.0.0.1')
        status, headers, body = self.request(authorized=False)
        self.assertEqual(status, 200)
        self.assertIn(self.server.brain_token, body)
        self.assertIn("script-src 'nonce-" + self.server.nonce, headers['Content-Security-Policy'])
        self.assertIn("frame-ancestors 'none'", headers['Content-Security-Policy'])
        self.assertIn('nonce="' + self.server.nonce + '"', body)
        self.assertEqual(headers['Cache-Control'], 'no-store')
        self.assertEqual(headers['Cross-Origin-Resource-Policy'], 'same-origin')
        self.assertNotIn('Access-Control-Allow-Origin', headers)
        self.assertNotRegex(body, r'<(?:script|link)[^>]+(?:src|href)=')
        self.assertNotIn('__TOKEN__', body)
        self.assertNotIn('innerHTML', body)
        self.assertNotIn('document.write', body)
        self.assertNotIn('insertAdjacentHTML', body)

    def test_host_origin_and_fetch_site_gate_page_and_api(self):
        for path in ('/', '/health', '/api/status'):
            for headers in ({'Host': 'attacker.example'}, {'Origin': 'https://attacker.example'},
                            {'Sec-Fetch-Site': 'cross-site'}, {'Origin': 'null'}):
                with self.subTest(path=path, headers=headers):
                    self.assertEqual(self.request(path=path, headers=headers)[0], 403)

    def test_viewer_port_can_navigate_to_memory_without_reading_its_api(self):
        navigation = {'Sec-Fetch-Site': 'same-site', 'Sec-Fetch-Mode': 'navigate',
                      'Sec-Fetch-Dest': 'document'}
        # The sibling uses Referrer-Policy:no-referrer and an async launcher;
        # neither Referer nor Sec-Fetch-User is guaranteed on the document GET.
        status, headers, body = self.request(headers=navigation, authorized=False)
        self.assertEqual(status, 200)
        self.assertIn('Memory workspace', body)
        self.assertEqual(headers['X-Frame-Options'], 'DENY')
        self.assertEqual(self.request(path='/api/status', headers=navigation)[0], 403)
        self.assertEqual(self.request('POST', '/api/open', data={'target': 'viewer'}, headers=navigation)[0], 403)
        for invalid in ({'Sec-Fetch-Mode': 'cors'}, {'Sec-Fetch-Dest': 'iframe'},
                        {'Sec-Fetch-Site': 'cross-site', 'Sec-Fetch-Dest': 'iframe'}, {'Host': 'foreign.example'},
                        {'Origin': 'http://127.0.0.1:6161'}):
            self.assertEqual(self.request(headers={**navigation, **invalid}, authorized=False)[0], 403, invalid)

    def test_api_requires_token_in_header_and_rejects_absolute_target(self):
        self.assertEqual(self.request(path='/api/status', authorized=False)[0], 403)
        self.assertEqual(self.request(path='/api/status', headers={'X-Brain-Token': 'bad'})[0], 403)
        self.assertEqual(self.request(path='/api/status', headers={'X-Brain-Token': '\u00e9'})[0], 403)
        self.assertEqual(self.request(path=self.server.origin + '/api/status', authorized=False)[0], 400)
        self.assertEqual(self.request(path='//127.0.0.1/api/status', authorized=False)[0], 404)
        self.assertEqual(self.request(path='/api/status?token=' + self.server.brain_token, authorized=False)[0], 403)
        self.assertEqual(self.request(path='/api/status')[0], 200)

    def test_mutations_require_explicit_same_origin_even_with_valid_token(self):
        payload = {'project_id': 'alpha', 'query': 'fact'}
        self.assertEqual(self.request('POST', '/api/search', data=payload, origin=False)[0], 403)
        self.assertEqual(self.request('POST', '/api/search', data=payload, headers={'Origin': 'http://localhost'})[0], 403)
        self.assertEqual(self.request('POST', '/api/search', data=payload)[0], 200)

    def test_body_limits_content_type_and_malformed_payloads(self):
        status, _, _ = self.request('POST', '/api/search', raw=b'', headers={
            'Content-Type': 'application/json', 'Content-Length': str(MAX_REQUEST_BYTES + 1)})
        self.assertEqual(status, 413)
        self.assertEqual(self.request('POST', '/api/search', raw=b'', headers={'Content-Type': 'text/plain'})[0], 415)
        for raw in (b'[1,2]', b'{', b'{"project_id":"alpha","importance":NaN}', b'\xff'):
            with self.subTest(raw=raw):
                self.assertEqual(self.request('POST', '/api/search', raw=raw, headers={'Content-Type': 'application/json'})[0], 400)
        self.assertEqual(self.request('POST', '/api/search', raw=b'', headers={'Transfer-Encoding': 'chunked'})[0], 400)
        self.assertEqual(self.request(path='/' + 'a' * 2050)[0], 414)

    def test_no_file_serving_or_unsupported_methods(self):
        (self.root / 'secret.txt').write_text('secret-do-not-serve', encoding='utf-8')
        for path in ('/secret.txt', '/../secret.txt', '/%2e%2e/secret.txt', '/api/file?path=secret.txt'):
            status, _, body = self.request(path=path)
            self.assertEqual(status, 404)
            self.assertNotIn('secret-do-not-serve', str(body))
        for method in ('PUT', 'PATCH', 'DELETE', 'OPTIONS', 'TRACE'):
            self.assertEqual(self.request(method, '/api/status')[0], 405)

    def test_project_is_explicit_and_local_scope_is_pinned(self):
        item = self.propose()
        self.assertEqual(self.request(path='/api/snapshot')[0], 400)
        self.assertEqual(self.request(path='/api/snapshot?project_id=alpha&project_id=beta')[0], 400)
        self.assertEqual(self.request(path='/api/snapshot?project_id=alpha&user_id=other')[0], 400)
        self.assertEqual(self.request('POST', '/api/search', data={'project_id': 'alpha', 'query': 'x', 'user_id': 'other'})[0], 400)
        status, _, snapshot = self.request(path='/api/snapshot?project_id=alpha')
        self.assertEqual(status, 200)
        self.assertEqual(snapshot['memories'][0]['id'], item['id'])
        self.assertEqual(self.store.calls[-1], ('snapshot', 'alpha', 'local'))

    def test_review_queue_is_scoped_and_requested_independently_of_recent_snapshot(self):
        pending = self.propose()
        self.propose(project='beta')
        status, _, rows = self.request(path='/api/memories?project_id=alpha&status=pending')
        self.assertEqual(status, 200)
        self.assertEqual([item['id'] for item in rows], [pending['id']])
        self.assertEqual(self.store.calls[-1], ('list_memories', 'alpha', 'local', 'pending', 200))

    def test_propose_approve_search_forget_flow_is_explicit_and_bounded(self):
        item = self.propose(status='active', reviewer='Injected reviewer')
        self.assertEqual(item['status'], 'pending')
        self.assertNotIn('reviewer', item)
        status, _, found = self.request('POST', '/api/search', data={'project_id': 'alpha', 'query': 'concise', 'limit': 1000})
        self.assertEqual((status, found['results']), (200, []))
        status, _, approved = self.request('POST', '/api/approve', data={'project_id': 'alpha', 'memory_id': item['id'],
            'reviewer': 'Local user', 'note': 'Checked against the supplied source and confirmed correct.'})
        self.assertEqual((status, approved['status']), (200, 'active'))
        _, _, found = self.request('POST', '/api/search', data={'project_id': 'alpha', 'query': 'concise', 'limit': 999, 'hops': 99})
        self.assertEqual(found['results'][0]['id'], item['id'])
        args = self.store.calls[-1][1]
        self.assertEqual((args['limit'], args['max_chars'], args['hops'], args['user_id']), (6, 8000, 1, 'local'))
        status, _, forgotten = self.request('POST', '/api/forget', data={'project_id': 'alpha', 'memory_id': item['id'],
            'actor': 'Local user', 'reason': 'No longer applicable.'})
        self.assertEqual((status, forgotten['status']), (200, 'deleted'))
        self.assertNotIn('content', forgotten)

    def test_cross_project_and_cross_user_ids_cannot_be_read_or_mutated(self):
        other = self.propose(project='beta')
        for action in ('approve', 'forget', 'supersede', 'relate'):
            before = deepcopy(self.store.calls)
            status, _, _ = self.request('POST', '/api/' + action, data={'project_id': 'alpha', 'memory_id': other['id']})
            self.assertEqual(status, 400)
            self.assertEqual(before, self.store.calls)
        self.assertEqual(self.request(path='/api/memories/' + other['id'] + '?project_id=alpha')[0], 400)
        self.store.items[other['id']]['user_id'] = 'different-user'
        self.assertEqual(self.request(path='/api/memories/' + other['id'] + '?project_id=beta')[0], 400)

    def test_relationship_targets_must_share_scope(self):
        first = self.propose()
        second = self.propose()
        other = self.propose(project='beta')
        common = {'project_id': 'alpha', 'memory_id': first['id'], 'actor': 'Local user', 'reason': 'Newer evidence.'}
        self.assertEqual(self.request('POST', '/api/relate', data=dict(common, target_id=other['id'], relation='supports'))[0], 400)
        self.assertEqual(self.request('POST', '/api/supersede', data=dict(common, new_id=other['id']))[0], 400)
        self.assertEqual(self.request('POST', '/api/relate', data=dict(common, target_id=second['id'], relation='supports'))[0], 200)
        status, _, result = self.request('POST', '/api/supersede', data=dict(common, new_id=second['id']))
        self.assertEqual((status, result['superseded_by']), (200, second['id']))

    def test_untrusted_content_remains_json_data_and_is_not_in_page(self):
        attack = '</script><img src=x onerror="window.pwned=true">'
        memory = self.propose(title=attack, content=attack)
        status, headers, result = self.request(path='/api/memories/' + memory['id'] + '?project_id=alpha')
        self.assertEqual(status, 200)
        self.assertEqual(result['content'], attack)
        self.assertTrue(headers['Content-Type'].startswith('application/json'))
        self.assertEqual(headers['X-Content-Type-Options'], 'nosniff')
        self.assertNotIn(attack, self.request()[2])
        self.assertIn('node.textContent=String(text)', PAGE)

    def test_storage_errors_are_actionable_internal_errors_are_sanitized(self):
        self.store.failure = RuntimeError('secret-token-at-private-path')
        status, _, body = self.request(path='/api/status')
        self.assertEqual(status, 500)
        self.assertNotIn('secret-token', str(body))
        StorageLimitError = type('StorageLimitError', (RuntimeError,), {})
        self.store.failure = StorageLimitError('private storage path')
        status, _, body = self.request(path='/api/status')
        self.assertEqual(status, 507)
        self.assertIn('Storage is at its limit', body['error'])

    def test_health_is_minimal_and_process_tokens_change(self):
        status, _, body = self.request(path='/health', authorized=False)
        self.assertEqual((status, body), (200, {'service': 'orchestrator-brain-dashboard', 'version': 1,
                                               'instance_id': self.server.instance_id}))
        with make_server(self.root, store_factory=lambda root: self.store) as other:
            self.assertNotEqual(self.server.brain_token, other.brain_token)

    def test_concurrent_request_capacity_is_bounded(self):
        for _ in range(8):
            self.assertTrue(self.server.request_slots.acquire(blocking=False))
        try:
            status, _, body = self.request(path='/health', authorized=False)
            self.assertEqual(status,503)
            self.assertIn('busy',body['error'])
        finally:
            for _ in range(8):
                self.server.request_slots.release()
        self.assertEqual(self.request(path='/health', authorized=False)[0], 200)

    def test_launcher_state_is_atomic_has_no_token_and_is_removed_on_close(self):
        state_file = self.root / 'runtime' / 'dashboard.json'
        server = make_server(self.root, store_factory=lambda root: self.store)
        observed = []

        def during_service(**kwargs):
            observed.append(json.loads(state_file.read_text(encoding='utf-8')))
            self.assertEqual(list(state_file.parent.glob('*.tmp')), [])

        with patch('brain_dashboard.make_server', return_value=server), patch.object(server, 'serve_forever', side_effect=during_service), patch('sys.stdout', new_callable=io.StringIO):
            self.assertEqual(main(['--root', str(self.root), '--state-file', str(state_file)]), 0)
        self.assertEqual(observed, [{'pid': os.getpid(), 'origin': server.origin,
                                   'service': 'orchestrator-brain-dashboard', 'version': 1,
                                   'instance_id': server.instance_id}])
        self.assertNotIn(server.brain_token, str(observed))
        self.assertFalse(state_file.exists())

    def test_launcher_does_not_remove_a_replacement_process_state(self):
        state_file = self.root / 'dashboard.json'
        server = make_server(self.root, store_factory=lambda root: self.store)

        def during_service(**kwargs):
            state_file.write_text(json.dumps({'pid': os.getpid() + 100, 'origin': 'replacement'}), encoding='utf-8')

        with patch('brain_dashboard.make_server', return_value=server), patch.object(server, 'serve_forever', side_effect=during_service), patch('sys.stdout', new_callable=io.StringIO):
            main(['--root', str(self.root), '--state-file', str(state_file)])
        self.assertEqual(json.loads(state_file.read_text(encoding='utf-8'))['origin'], 'replacement')

    def test_real_store_roundtrip_through_http(self):
        from brain_store import BrainStore
        self.server.store_factory = lambda root: BrainStore(root=root)
        first = self.propose(title='Review bounded evidence', content='Use concise approved evidence during a handoff.')
        second = self.propose(title='Updated review process', content='Follow the updated process for memory review.')
        for item in (first, second):
            status, _, body = self.request('POST', '/api/approve', data={'project_id': 'alpha', 'memory_id': item['id'],
                'reviewer': 'Browser test reviewer', 'note': 'Verified the synthetic user source supplied for this test.'})
            self.assertEqual(status, 200, body)
        status, _, edge = self.request('POST', '/api/relate', data={'project_id': 'alpha', 'memory_id': second['id'],
            'target_id': first['id'], 'relation': 'supports', 'actor': 'Browser test reviewer'})
        self.assertEqual(status, 200, edge)
        status, _, found = self.request('POST', '/api/search', data={'project_id': 'alpha', 'query': 'handoff'})
        self.assertEqual(status, 200, found)
        self.assertTrue(any(item['id'] == first['id'] for item in found['results']))
        status, _, snapshot = self.request(path='/api/snapshot?project_id=alpha')
        self.assertEqual(status, 200, snapshot)
        self.assertEqual(len(snapshot['relations']), 1)
        status, _, body = self.request('POST', '/api/supersede', data={'project_id': 'alpha', 'memory_id': first['id'],
            'new_id': second['id'], 'actor': 'Browser test reviewer', 'reason': 'The updated source replaces the earlier guidance.'})
        self.assertEqual(status, 200, body)
        status, _, body = self.request('POST', '/api/forget', data={'project_id': 'alpha', 'memory_id': first['id'],
            'actor': 'Browser test reviewer', 'reason': 'Remove the temporary test source and derived references.'})
        self.assertEqual(status, 200, body)
        self.assertEqual(body['status'], 'deleted')

    def test_change_feed_is_scoped_bounded_and_reports_head(self):
        self.assertEqual(self.request(path='/api/changes')[0], 400)
        status, _, feed = self.request(path='/api/changes?project_id=alpha')
        self.assertEqual((status, feed['head'], feed['changes']), (200, 0, []))
        self.assertEqual(self.store.calls[-1], ('changes', 'alpha', 'local', 0, 50))
        self.store.head = 7
        status, _, feed = self.request(path='/api/changes?project_id=alpha&after=5')
        self.assertEqual((status, feed['head'], feed['changes']), (200, 7, [{'seq': 6}, {'seq': 7}]))
        self.assertEqual(self.store.calls[-1], ('changes', 'alpha', 'local', 5, 50))
        for query in ('after=-1', 'after=x', 'after=1&after=2', 'after=' + '9' * 13, 'limit=5', 'user_id=other'):
            self.assertEqual(self.request(path='/api/changes?project_id=alpha&' + query)[0], 400, query)
        self.assertEqual(self.request('POST', '/api/changes', data={'project_id': 'alpha'})[0], 404)

    def test_page_polls_the_change_feed_and_applies_quietly_only_when_safe(self):
        self.assertIn("api('/api/changes'+projectQuery()+'&after='+", PAGE)
        self.assertIn('setInterval(poll,10000)', PAGE)
        self.assertIn("if(document.hidden||$('refresh').disabled||state.polling)return;", PAGE)
        self.assertIn("!$('detail').open&&!$('editor').open&&!$('action').open&&!$('feedback').open&&!state.searchResults&&state.view!=='graph'", PAGE)
        self.assertIn("state.cursor=0;state.pending=0;state.recallRevision=null;state.services=[];saveScope();renderCrumbs();$('detail').close();refresh();", PAGE)

    def test_real_store_change_feed_advances_after_writes_without_content(self):
        from brain_store import BrainStore
        self.server.store_factory = lambda root: BrainStore(root=root)
        status, _, feed = self.request(path='/api/changes?project_id=alpha')
        self.assertEqual((status, feed['head'], feed['changes']), (200, 0, []))
        item = self.propose()
        status, _, feed = self.request(path='/api/changes?project_id=alpha&after=0')
        self.assertEqual(status, 200, feed)
        self.assertEqual(feed['head'], feed['changes'][-1]['seq'])
        self.assertEqual(feed['changes'][-1]['memory_id'], item['id'])
        self.assertNotIn('A concise useful memory.', json.dumps(feed))
        status, _, again = self.request(path='/api/changes?project_id=alpha&after=' + str(feed['head']))
        self.assertEqual((status, again['changes'], again['head'], again['resync_required']), (200, [], feed['head'], False))

    def test_services_and_open_link_the_pages_through_the_registry(self):
        status, _, body = self.request(path='/api/services')
        self.assertEqual(status, 200, body)
        self.assertEqual(body['current'], 'brain')
        self.assertEqual([(r['id'], r['current']) for r in body['services']], [('brain', True), ('viewer', False)])
        self.assertNotIn('run', body['services'][1])
        (self.root / '.orchestration/alpha').mkdir(parents=True)
        (self.root / '.orchestration/alpha/coordinator.json').write_text('{}', encoding='utf-8')
        self.assertEqual(self.request(path='/api/services?project_id=alpha')[2]['services'][1]['run'], 'alpha')
        self.assertEqual(self.request(path='/api/services?project_id=alpha&after=1')[0], 400)
        self.assertEqual(self.request(path='/api/services', authorized=False)[0], 403)
        self.assertEqual(self.request(path='/api/open')[0], 404)
        self.assertEqual(self.request('POST', '/api/open', data={'target': 'viewer'}, origin=False)[0], 403)
        opened = {'origin': 'http://127.0.0.1:5151', 'reused': True, 'pid': 7}
        with patch('start_brain_dashboard.open_dashboard', return_value=opened) as launcher:
            status, _, body = self.request('POST', '/api/open', data={'target': 'viewer'})
        self.assertEqual((status, body['origin'], body['id']), (200, 'http://127.0.0.1:5151', 'viewer'), body)
        self.assertEqual(launcher.call_args.kwargs['script'], 'coordinator_viewer.py')
        self.assertEqual(self.request('POST', '/api/open', data={'target': 'nope'})[0], 400)
        self.assertEqual(self.request('POST', '/api/open', data={})[0], 400)
        self.assertEqual(self.store.calls, [])  # neither route touches the memory store

    def test_page_has_orchestrator_crumb_and_run_deep_links(self):
        self.assertIn('id="crumb-viewer"', PAGE)
        self.assertIn("api('/api/services'+projectQuery()+(run?", PAGE)
        self.assertIn("api('/api/open',{target:'viewer'})", PAGE)
        self.assertIn("new URLSearchParams(location.hash.slice(1)).get('project')", PAGE)
        self.assertIn("query.set('run',viewer.run)", PAGE)
        self.assertIn("new URLSearchParams({project:state.project})", PAGE)


if __name__ == '__main__':
    unittest.main()
