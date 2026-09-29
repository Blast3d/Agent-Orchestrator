"""Served pages link through loopback routes, never file:// (synthetic tasks and reports only)."""
from http.client import HTTPConnection
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
from threading import Thread
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'app'))
from brain_dashboard import DashboardServer
from coordinator_viewer import ViewerServer
import task_panel
from workspace_navigation import navigation_script

JOB = 'a' * 32


def save_task(root, **changes):
    folder = root / 'runs/tasks' / JOB
    folder.mkdir(parents=True)
    data = {'job_id': JOB, 'task': 'Synthetic review', 'worker': 'codex', 'status': 'held', 'execution_status': 'held',
            'review_status': 'pending', 'reason': 'Quota admission refused', 'created_at': '2026-09-26T18:16:00+00:00',
            'ended_at': '2026-09-26T18:17:00+00:00', 'finalized_at': '2026-09-26T18:17:01+00:00', **changes}
    (folder / 'record.json').write_text(json.dumps(data), encoding='utf-8')
    (folder / 'result.json').write_text(json.dumps(data), encoding='utf-8')


class TaskPanelTests(unittest.TestCase):
    def test_panel_uses_routes_with_task_fragments_and_pinned_report_runs(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            save_task(root)
            report = root / '.orchestration/run-one/contribution-audit.json'
            report.parent.mkdir(parents=True)
            report.write_text(json.dumps({'title': 'Run one audit', 'scope_id': 'run-one', 'attribution_complete': True,
                                          'by_agent': [{'name': 'Lead', 'accepted_work_pct': 100}]}), encoding='utf-8')
            html = task_panel.render(root, root / 'runs/tasks')
        self.assertNotIn('file:', html)
        self.assertIn('href="/tasks#task=' + JOB + '" data-workspace-page="tasks" data-workspace-hash="task=' + JOB + '"', html)
        self.assertIn('href="/contributions?run=run-one" data-workspace-page="contributions" data-workspace-run="run-one"', html)
        self.assertNotIn('Codex accepts', html)


@unittest.skipUnless(shutil.which('node'), 'Node is needed for the navigation script check')
class ScriptTargetTests(unittest.TestCase):
    def targets(self, url):
        with tempfile.TemporaryDirectory() as directory, \
                patch('local_services.links', return_value=[{'id': 'viewer', 'origin': 'http://127.0.0.1:4242'},
                                                            {'id': 'brain', 'origin': None}]):
            script = navigation_script(Path(directory))
        harness = r'''
const vm=require('node:vm');const payload=JSON.parse(require('node:fs').readFileSync(0,'utf8'));
const specs=[['tasks',{'data-workspace-hash':'task=''' + JOB + r''''}],['contributions',{'data-workspace-run':'run-one'}],
  ['experiments',{}],['system-map',{}],['brain',{}],['viewer',{'data-workspace-hash':'lead'}]];
const links=specs.map(([page,attrs])=>({dataset:{workspacePage:page},attrs:Object.assign({},attrs),listeners:{},
  setAttribute(k,v){this.attrs[k]=String(v)},removeAttribute(k){delete this.attrs[k]},getAttribute(k){return this.attrs[k]??null},
  addEventListener(k,fn){this.listeners[k]=fn},closest(){return null}}));
const sandbox={URLSearchParams,location:new URL(payload.url),window:{addEventListener(){}},
  document:{readyState:'complete',querySelectorAll(selector){return selector.startsWith('a[')?links:[]}}};
vm.runInNewContext(payload.script,sandbox);
process.stdout.write(JSON.stringify(links.map(link=>link.attrs.href??null)));
'''
        result = subprocess.run([shutil.which('node'), '-e', harness], input=json.dumps({'script': script, 'url': url}),
                                capture_output=True, text=True, encoding='utf-8', check=True, timeout=10)
        return json.loads(result.stdout)

    def test_http_pages_use_routes_fragments_and_pinned_runs(self):
        tasks, contributions, experiments, system_map, brain, lead = self.targets(
            'http://127.0.0.1:4242/usage?project=alpha&run=current-run')
        self.assertEqual(tasks, '/tasks?project=alpha&run=current-run#task=' + JOB)
        self.assertEqual(contributions, '/contributions?run=run-one')
        self.assertEqual(experiments, 'http://127.0.0.1:4242/experiments')
        self.assertEqual(system_map, '/system-map')
        self.assertIsNone(brain)
        self.assertEqual(lead, 'http://127.0.0.1:4242/?project=alpha&run=current-run#lead')

    def test_saved_file_pages_use_siblings_and_prefer_a_live_system_map(self):
        tasks, contributions, _, system_map, _, _ = self.targets('file:///C:/workspace/runtime/usage-dashboard.html')
        self.assertEqual(tasks, 'task-inbox.html#task=' + JOB)
        self.assertEqual(contributions, 'project-map.html?run=run-one')
        self.assertEqual(system_map, 'http://127.0.0.1:4242/system-map')


class LiveTasksRouteTests(unittest.TestCase):
    def test_both_servers_render_tasks_live_with_navigation_and_a_strict_policy(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            save_task(root)
            servers = [DashboardServer(root), ViewerServer(root)]
            threads = [Thread(target=server.serve_forever, kwargs={'poll_interval': .02}, daemon=True) for server in servers]
            for thread in threads:
                thread.start()
            try:
                for server in servers:
                    with self.subTest(page=server.page_id):
                        connection = HTTPConnection(*server.server_address, timeout=10)
                        try:
                            connection.request('GET', '/tasks?run=run-one')
                            response = connection.getresponse()
                            page = response.read().decode('utf-8')
                        finally:
                            connection.close()
                        self.assertEqual(response.status, 200, page[:300])
                        policy = response.getheader('Content-Security-Policy')
                        self.assertIn("script-src 'self' 'sha256-", policy)
                        self.assertIn("connect-src 'none'", policy)
                        self.assertIn('data-workspace-current="tasks"', page)
                        self.assertIn('"live": true', page)
                        self.assertIn('<script src="/workspace-navigation.js" defer></script>', page)
                        self.assertNotIn(server.brain_token, page)
                        self.assertNotIn('file:', page)
                        self.assertEqual(response.getheader('X-Frame-Options'), 'DENY')
                        connection = HTTPConnection(*server.server_address, timeout=10)
                        try:
                            connection.request('GET', '/tasks?path=secret')
                            self.assertEqual(connection.getresponse().status, 400)
                        finally:
                            connection.close()
            finally:
                for server in servers:
                    server.shutdown()
                    server.server_close()
                for thread in threads:
                    thread.join(timeout=2)


if __name__ == '__main__':
    unittest.main()
