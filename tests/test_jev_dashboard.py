"""Actual loopback HTTP Jev routes; temporary stores and offline decisions only."""
from http.client import HTTPConnection
import json
from pathlib import Path
import sys
import tempfile
from threading import Barrier, Event, Thread
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'app'))
from brain_dashboard import MAX_REQUEST_BYTES, make_server
from brain_store import BrainStore


class JevDashboardTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.server = make_server(self.root)
        self.thread = Thread(target=self.server.serve_forever, kwargs={'poll_interval': .01}, daemon=True)
        self.thread.start()
        self.addCleanup(self.stop)
        self.config = {'status': 'ready', 'key_present': True, 'authorized_projects': ['alpha'],
                       'purposes': ['memory_support'], 'min_confidence': .8, 'cache_enabled': True}
        config_patch = patch('jev_openrouter.load_config', return_value=self.config)
        config_patch.start()
        self.addCleanup(config_patch.stop)
        post_patch = patch('jev_openrouter._post', side_effect=AssertionError('Network is forbidden in this test'))
        post_patch.start()
        self.addCleanup(post_patch.stop)

    def stop(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=2)

    def request(self, method='GET', path='/jev', data=None, *, authorized=True, origin=True, headers=None, raw=None):
        final_headers = {}
        if authorized:
            final_headers['X-Brain-Token'] = self.server.brain_token
        if method == 'POST' and origin:
            final_headers['Origin'] = self.server.origin
        if data is not None:
            raw = json.dumps(data).encode('utf-8')
            final_headers['Content-Type'] = 'application/json'
        final_headers.update(headers or {})
        connection = HTTPConnection(*self.server.server_address, timeout=5)
        try:
            connection.request(method, path, body=raw, headers=final_headers)
            response = connection.getresponse()
            body = response.read().decode('utf-8')
            if response.getheader('Content-Type', '').startswith('application/json'):
                body = json.loads(body)
            return response.status, dict(response.getheaders()), body
        finally:
            connection.close()

    def payload(self, **updates):
        data = {'project_id': 'alpha', 'workflow': 'memory_support', 'payload': {'claim': 'Reviewed claim.'}}
        data.update(updates)
        return data

    def memory(self, project='alpha', user='local'):
        brain = BrainStore(self.root)
        proposed = brain.propose({'project_id': project, 'user_id': user, 'kind': 'fact',
            'title': 'Retry policy', 'content': 'Retry policy uses bounded backoff.',
            'source': {'type': 'user', 'note': 'Synthetic dashboard evidence.'}})
        return brain.approve(proposed['id'], 'Tester', 'Checked the synthetic HTTP fixture evidence.')

    def test_page_navigation_has_nonce_token_and_same_origin_policy(self):
        self.assertEqual(self.server.server_address[0], '127.0.0.1')
        status, headers, page = self.request(authorized=False)
        self.assertEqual(status, 200)
        self.assertIn(self.server.brain_token, page)
        self.assertIn('nonce="' + self.server.nonce + '"', page)
        self.assertNotIn('__TOKEN__', page)
        self.assertNotIn('__NONCE__', page)
        self.assertIn("connect-src 'self'", headers['Content-Security-Policy'])
        self.assertIn("frame-ancestors 'none'", headers['Content-Security-Policy'])
        self.assertEqual(headers['Cache-Control'], 'no-store')
        for site in ('same-site', 'cross-site'):
            self.assertEqual(self.request(authorized=False, headers={
                'Sec-Fetch-Site': site, 'Sec-Fetch-Mode': 'navigate', 'Sec-Fetch-Dest': 'document'})[0], 200)
        self.assertEqual(self.request(headers={'Host': 'attacker.example'})[0], 403)
        self.assertEqual(self.request(headers={'Sec-Fetch-Site': 'cross-site', 'Sec-Fetch-Mode': 'cors'})[0], 403)

    def test_catalogue_is_token_scoped_and_omits_credentials(self):
        path = '/api/jev/workflows?project_id=alpha'
        self.assertEqual(self.request(path=path, authorized=False)[0], 403)
        status, _, result = self.request(path=path)
        self.assertEqual(status, 200)
        self.assertEqual(len(result['workflows']), 14)
        self.assertEqual(result['configuration'], {'enabled': True, 'purposes': ['memory_support'], 'cache_enabled': True})
        self.assertNotIn('key_present', json.dumps(result))
        self.assertNotIn('api_key', json.dumps(result))
        status, _, result = self.request(path='/api/jev/workflows?project_id=beta')
        self.assertEqual(status, 200)
        self.assertFalse(result['configuration']['enabled'])
        self.assertEqual(result['configuration']['purposes'], [])
        for query in ('', '?project_id=', '?project_id=alpha&project_id=beta', '?project_id=alpha&user_id=other', '?project_id=alpha&extra=1'):
            with self.subTest(query=query):
                self.assertEqual(self.request(path='/api/jev/workflows' + query)[0], 400)

    def test_post_requires_token_matching_origin_and_allowed_envelope(self):
        with patch('jev_workflows.run_workflow') as service:
            self.assertEqual(self.request('POST', '/api/jev/workflow', self.payload(), authorized=False)[0], 403)
            self.assertEqual(self.request('POST', '/api/jev/workflow', self.payload(), origin=False)[0], 403)
            self.assertEqual(self.request('POST', '/api/jev/workflow', self.payload(), headers={'Origin': 'http://attacker.example'})[0], 403)
            self.assertEqual(self.request('POST', '/api/jev/workflow', self.payload(), headers={'Sec-Fetch-Site': 'cross-site'})[0], 403)
            for data in (self.payload(user_id='other'), self.payload(extra=True), self.payload(project_id=''),
                         self.payload(workflow=''), {'workflow': 'memory_support', 'payload': {}}, []):
                with self.subTest(data=data):
                    self.assertEqual(self.request('POST', '/api/jev/workflow', data)[0], 400)
            service.assert_not_called()

    def test_post_rejects_oversized_non_json_and_nonfinite_bodies_before_service(self):
        with patch('jev_workflows.run_workflow') as service:
            self.assertEqual(self.request('POST', '/api/jev/workflow', raw=b'{}', headers={'Content-Type': 'text/plain'})[0], 415)
            self.assertEqual(self.request('POST', '/api/jev/workflow', raw=b'x' * (MAX_REQUEST_BYTES + 1), headers={'Content-Type': 'application/json'})[0], 413)
            for body in (b'not json', b'[]', b'{"project_id":"alpha","payload":NaN}', b'\xff'):
                with self.subTest(body=body):
                    self.assertEqual(self.request('POST', '/api/jev/workflow', raw=body, headers={'Content-Type': 'application/json'})[0], 400)
            service.assert_not_called()

    def test_real_workflow_response_is_advisory_and_does_not_write_memory(self):
        memory = self.memory()
        brain = BrainStore(self.root)
        before = brain.get(memory['id'])

        def provider(root, project, purpose, state, questions, **kwargs):
            self.assertEqual((project, purpose), ('alpha', 'memory_support'))
            return {'status': 'ok', 'provider_calls': 1, 'answers': {
                key: {'type': 'choice', 'choice': 'supported', 'confidence': .95}
                for key in questions}}

        payload = {'memory_ids': [memory['id']], 'claim': 'Retry uses bounded backoff.'}
        with patch('jev_openrouter.evaluate', side_effect=provider) as evaluate:
            status, _, result = self.request('POST', '/api/jev/workflow', self.payload(payload=payload))
        self.assertEqual(status, 200, result)
        self.assertEqual(result['status'], 'ok')
        self.assertTrue(result['advisory_only'])
        self.assertTrue(result['requires_review'])
        self.assertEqual(result['source_ids'], [memory['id']])
        evaluate.assert_called_once()
        self.assertEqual(brain.get(memory['id']), before)

    def test_service_authorization_and_memory_scope_precede_inference(self):
        foreign = self.memory(project='beta')
        other_user = self.memory(user='other')
        with patch('jev_openrouter.evaluate') as evaluate:
            for data, expected in ((self.payload(project_id='beta'), 'unavailable'),
                                   (self.payload(workflow='instruction_scan', payload={}), 'unavailable'),
                                   (self.payload(payload={'claim': 'X', 'memory_ids': [foreign['id']]}), 'invalid_input'),
                                   (self.payload(payload={'claim': 'X', 'memory_ids': [other_user['id']]}), 'invalid_input')):
                status, _, result = self.request('POST', '/api/jev/workflow', data)
                self.assertEqual(status, 200)
                self.assertEqual(result['status'], expected)
                self.assertEqual(result['source_ids'], [])
            evaluate.assert_not_called()

    def test_only_two_decisions_run_and_admission_recovers_after_completion(self):
        arrivals = Barrier(3)
        release = Event()
        responses = []

        def service(*args, **kwargs):
            arrivals.wait(timeout=3)
            if not release.wait(timeout=3):
                raise RuntimeError('Test did not release request')
            return {'status': 'ok', 'advisory_only': True}

        def client():
            responses.append(self.request('POST', '/api/jev/workflow', self.payload())[0])

        workers = [Thread(target=client, daemon=True) for _ in range(2)]
        with patch('jev_workflows.run_workflow', side_effect=service) as call:
            try:
                for worker in workers:
                    worker.start()
                arrivals.wait(timeout=3)
                status, _, result = self.request('POST', '/api/jev/workflow', self.payload())
                self.assertEqual(status, 503)
                self.assertIn('Two Jev decisions', result['error'])
                self.assertEqual(call.call_count, 2)
                # The read-only catalogue remains available while both slots run.
                self.assertEqual(self.request(path='/api/jev/workflows?project_id=alpha')[0], 200)
            finally:
                release.set()
                for worker in workers:
                    worker.join(timeout=3)
        self.assertEqual(responses, [200, 200])
        with patch('jev_workflows.run_workflow', return_value={'status': 'ok'}):
            self.assertEqual(self.request('POST', '/api/jev/workflow', self.payload())[0], 200)

    def test_semantic_slot_is_released_when_service_raises(self):
        with patch('jev_workflows.run_workflow', side_effect=ValueError('Invalid workflow fixture.')):
            self.assertEqual(self.request('POST', '/api/jev/workflow', self.payload())[0], 400)
        self.assertTrue(self.server.jev_slots.acquire(blocking=False))
        self.assertTrue(self.server.jev_slots.acquire(blocking=False))
        self.assertFalse(self.server.jev_slots.acquire(blocking=False))
        self.server.jev_slots.release()
        self.server.jev_slots.release()


if __name__ == '__main__':
    unittest.main()
