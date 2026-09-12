"""Actual loopback HTTP checks for the report links; no providers or user data."""
import base64
import hashlib
from http.client import HTTPConnection
from pathlib import Path
import sys
import tempfile
from threading import Thread
import unittest
from unittest.mock import MagicMock, Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'app'))
from brain_dashboard import DashboardServer
from coordinator_viewer import ViewerServer
import brain_dashboard
import coordinator_viewer


class WorkspaceNavigationHTTPTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.runtime = self.root / 'runtime'
        self.runtime.mkdir()
        self.report_script = 'document.documentElement.dataset.ready = "yes";'
        self.report_style = 'body{color:#123456}'
        self.report = ('<!doctype html><title>Fixture report</title><style>' +
                       self.report_style + '</style><h1>Fixture report</h1>' +
                       '<script>' + self.report_script + '</script>' +
                       '<script src="workspace-navigation.js"></script>')
        for filename in ('usage-dashboard.html', 'project-map.html'):
            (self.runtime / filename).write_text(self.report, encoding='utf-8')
        self.store = Mock()
        self.store.status.return_value = {'projects': []}
        self.store.runs.return_value = {'runs': [], 'model_calls': 0}
        self.servers = [DashboardServer(self.root, store_factory=lambda root: self.store),
                        ViewerServer(self.root, store=self.store)]
        self.threads = []
        for server in self.servers:
            thread = Thread(target=server.serve_forever,
                            kwargs={'poll_interval': .01}, daemon=True)
            thread.start()
            self.threads.append(thread)
        self.addCleanup(self.stop)

    def stop(self):
        for server in self.servers:
            server.shutdown()
            server.server_close()
        for thread in self.threads:
            thread.join(timeout=2)

    def request(self, server, path='/', *, method='GET', headers=None, authorized=False):
        final_headers = dict(headers or {})
        if authorized:
            final_headers['X-' + ('Brain' if server.page_id == 'brain' else 'Viewer') + '-Token'] = server.brain_token
        body = None
        if method == 'POST':
            final_headers.setdefault('Origin', server.origin)
            final_headers['Content-Type'] = 'application/json'
            # These requests must fail the header gate before body parsing.
            # No unread body means Windows can deliver the 403 before closing.
            body = b''
        connection = HTTPConnection(*server.server_address, timeout=3)
        try:
            connection.request(method, path, body=body, headers=final_headers)
            response = connection.getresponse()
            return response.status, dict(response.getheaders()), response.read().decode('utf-8')
        finally:
            connection.close()

    def test_both_dashboard_pages_expose_both_report_destinations(self):
        for server in self.servers:
            with self.subTest(page=server.page_id):
                status, _, page = self.request(server)
                self.assertEqual(status, 200, page)
                self.assertIn('aria-label="Workspace pages"', page)
                for label in ('Orchestrator', 'Memory', 'Provider usage', 'Contribution maps'):
                    self.assertIn(label, page)
                self.assertIn('href="/usage"', page)
                self.assertIn('href="/contributions"', page)

    def test_fixed_reports_accept_scope_and_keep_the_snapshot_inert_to_query_text(self):
        for server in self.servers:
            for route in ('/usage', '/contributions'):
                with self.subTest(page=server.page_id, route=route):
                    status, headers, page = self.request(server, route + '?project=shared%20library&run=fixture-run')
                    self.assertEqual(status, 200, page)
                    self.assertEqual(page, self.report)
                    self.assertEqual(headers['Cache-Control'], 'no-store')
                    self.assertEqual(headers['X-Frame-Options'], 'DENY')
                    self.assertEqual(headers['Referrer-Policy'], 'no-referrer')
                    self.assertNotIn('Access-Control-Allow-Origin', headers)
                    self.assertNotIn(server.brain_token, page)
        self.assertEqual(self.store.mock_calls, [])

    def test_report_csp_hashes_match_exact_script_and_style_while_network_calls_stay_disabled(self):
        for server in self.servers:
            _, headers, _ = self.request(server, '/usage')
            policy = headers['Content-Security-Policy']
            for code in (self.report_script, self.report_style):
                digest = base64.b64encode(hashlib.sha256(code.encode('utf-8')).digest()).decode('ascii')
                self.assertIn("'sha256-" + digest + "'", policy)
            self.assertIn("script-src 'self'", policy)
            self.assertIn("connect-src 'none'", policy)
            self.assertIn("frame-ancestors 'none'", policy)
            self.assertNotIn("script-src 'unsafe-inline'", policy)

    def test_windows_snapshot_line_endings_are_normalized_before_csp_hashing(self):
        script = '\nwindow.fixture = true;\n'
        page = '<!doctype html><script>' + script + '</script>'
        (self.runtime / 'usage-dashboard.html').write_bytes(page.replace('\n', '\r\n').encode('utf-8'))
        digest = base64.b64encode(hashlib.sha256(script.encode('utf-8')).digest()).decode('ascii')
        for server in self.servers:
            status, headers, body = self.request(server, '/usage')
            self.assertEqual(status, 200)
            self.assertEqual(body, page)
            self.assertIn("'sha256-" + digest + "'", headers['Content-Security-Policy'])

    def test_file_and_sibling_links_only_get_a_top_level_page_exception(self):
        for server in self.servers:
            for site in ('same-site', 'cross-site'):
                navigation = {'Sec-Fetch-Site': site, 'Sec-Fetch-Mode': 'navigate',
                              'Sec-Fetch-Dest': 'document'}
                with self.subTest(page=server.page_id, site=site):
                    for route in ('/', '/usage', '/contributions'):
                        self.assertEqual(self.request(server, route, headers=navigation)[0], 200, route)
                    for route in ('/health', '/workspace-navigation.js', '/api/services'):
                        self.assertEqual(self.request(server, route, headers=navigation, authorized=True)[0], 403, route)
                    for change in ({'Sec-Fetch-Dest': 'iframe'}, {'Sec-Fetch-Dest': 'script'},
                                   {'Sec-Fetch-Mode': 'cors'}, {'Sec-Fetch-Mode': 'no-cors'},
                                   {'Origin': 'null'}, {'Origin': 'https://foreign.invalid'},
                                   {'Host': 'foreign.invalid'}):
                        headers = {**navigation, **change}
                        self.assertEqual(self.request(server, '/usage', headers=headers)[0], 403, change)
                    self.assertEqual(self.request(server, '/api/open', method='POST',
                                                   headers=navigation, authorized=True)[0], 403)
        self.assertEqual(self.store.mock_calls, [])

    def test_same_origin_navigation_never_supplies_api_authorization(self):
        for server in self.servers:
            route = '/api/status' if server.page_id == 'brain' else '/api/runs'
            self.assertEqual(self.request(server, route)[0], 403)
            self.assertEqual(self.request(server, route, authorized=True)[0], 200)
            self.assertEqual(self.request(server, '/api/open', method='POST')[0], 403)
            self.assertEqual(self.request(server, '/api/open', method='POST', authorized=True,
                                           headers={'Origin': 'null'})[0], 403)

    def test_report_routes_cannot_be_used_as_an_arbitrary_file_server(self):
        secret = 'DO_NOT_EXPOSE_THIS_UNRELATED_FILE'
        (self.runtime / 'private.txt').write_text(secret, encoding='utf-8')
        for server in self.servers:
            for route in ('/private.txt', '/runtime/private.txt', '/usage/private.txt',
                          '/usage/../private.txt', '/usage%2f..%2fprivate.txt',
                          '/contributions?file=private.txt', '/usage?path=private.txt'):
                with self.subTest(page=server.page_id, route=route):
                    status, _, body = self.request(server, route)
                    self.assertIn(status, (400, 404))
                    self.assertNotIn(secret, body)
                    self.assertNotIn(str(self.root), body)

    def test_ambiguous_and_invalid_report_scopes_are_rejected(self):
        for server in self.servers:
            for query in ('project=alpha&project=beta', 'run=one&run=two',
                          'project=alpha&run=one&other=two', 'run=../foreign',
                          'run=%2Fforeign', 'project=' + 'x' * 161):
                with self.subTest(page=server.page_id, query=query[:60]):
                    self.assertEqual(self.request(server, '/usage?' + query)[0], 400)

    def test_missing_and_oversized_reports_have_a_bounded_response(self):
        (self.runtime / 'usage-dashboard.html').unlink()
        (self.runtime / 'project-map.html').write_bytes(b'Z' * (8 * 1024 * 1024 + 1))
        for server in self.servers:
            status, _, body = self.request(server, '/usage')
            self.assertEqual(status, 404)
            self.assertIn('Report unavailable', body)
            self.assertIn('href="/"', body)
            status, _, body = self.request(server, '/contributions')
            self.assertEqual(status, 400)
            self.assertLess(len(body), 1024)
            self.assertNotIn(str(self.root), body)

    def test_symlinked_report_cannot_read_outside_the_runtime_directory(self):
        secret = self.root / 'outside-report.html'
        secret.write_text('PRIVATE_OUTSIDE_REPORT', encoding='utf-8')
        target = self.runtime / 'usage-dashboard.html'
        target.unlink()
        try:
            target.symlink_to(secret)
        except OSError as error:
            self.skipTest('File symlinks unavailable on this host: ' + str(error.winerror))
        for server in self.servers:
            status, _, body = self.request(server, '/usage')
            self.assertEqual(status, 400)
            self.assertNotIn('PRIVATE_OUTSIDE_REPORT', body)
            self.assertNotIn(str(secret), body)

    def test_navigation_script_discloses_no_tokens_or_user_files_and_does_not_launch_services(self):
        with patch('local_services.ensure', side_effect=AssertionError('Navigation started a service')):
            for server in self.servers:
                with self.subTest(page=server.page_id):
                    status, headers, script = self.request(server, '/workspace-navigation.js')
                    self.assertEqual(status, 200, script)
                    self.assertTrue(headers['Content-Type'].startswith('text/javascript'))
                    self.assertEqual(headers['Cache-Control'], 'no-store')
                    self.assertIn(server.origin, script)
                    self.assertNotIn(server.brain_token, script)
                    self.assertNotIn(server.nonce, script)
                    self.assertNotIn(str(self.root), script)
                    self.assertNotIn('Fixture report', script)
        self.assertEqual(self.store.mock_calls, [])

    def test_unwritable_file_sidecar_does_not_prevent_either_http_dashboard_starting(self):
        for module, factory, page_id in ((brain_dashboard, 'make_server', 'brain'),
                                         (coordinator_viewer, 'ViewerServer', 'viewer')):
            with self.subTest(page=page_id):
                server = MagicMock(root=self.root, page_id=page_id,
                                   origin='http://127.0.0.1:4242', instance_id='a' * 32)
                server.__enter__.return_value = server
                with patch.object(module, factory, return_value=server), \
                        patch('workspace_navigation.write_navigation_script', side_effect=PermissionError('fixture sidecar locked')), \
                        patch('builtins.print'):
                    self.assertEqual(module.main(['--root', str(self.root)]), 0)
                server.serve_forever.assert_called_once()


if __name__ == '__main__':
    unittest.main()
