"""Viewer team-mix API and slider script: real local server, temporary root, no models."""
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
import team_mix
from coordinator_viewer import ViewerServer, ViewerStore
from coordinator_viewer_page import PAGE
from task_store import write_json

VECTORS = Path(__file__).with_name('team_mix_vectors.json')
RUN_ID = 'mix-run-20261107T000000Z-00000000'


class TeamMixApiTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        root_patch = patch.object(team_mix, 'ROOT', self.root)
        root_patch.start()
        self.addCleanup(root_patch.stop)
        self.run = self.root / '.orchestration' / RUN_ID
        self.run.mkdir(parents=True)
        write_json(self.run / 'coordinator.json', {'owner': 'fable', 'session': RUN_ID, 'generation': 1})
        write_json(self.run / 'run.json', {'run_id': RUN_ID, 'status': 'in_progress'})
        team_mix.init_run_mix(self.run, team_mix.default_mix())
        store = ViewerStore(self.root, home=self.root, async_listing=False, collector=lambda: [])
        self.server = ViewerServer(self.root, store=store)
        self.thread = Thread(target=self.server.serve_forever, kwargs={'poll_interval': .02}, daemon=True)
        self.thread.start()
        self.addCleanup(self.stop)

    def stop(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=2)

    def request(self, path, payload=None):
        headers = {'X-Viewer-Token': self.server.viewer_token}
        body = None
        if payload is not None:
            headers.update({'Origin': self.server.origin, 'Content-Type': 'application/json'})
            body = json.dumps(payload).encode('utf-8')
        connection = HTTPConnection(*self.server.server_address, timeout=5)
        try:
            connection.request('POST' if payload is not None else 'GET', path, body=body, headers=headers)
            response = connection.getresponse()
            return response.status, json.loads(response.read().decode('utf-8'))
        finally:
            connection.close()

    def test_reads_the_default_and_a_run(self):
        status, value = self.request('/api/team-mix')
        self.assertEqual(status, 200)
        self.assertEqual((value['scope'], value['mix']['preset']), ('global', 'balanced'))
        self.assertEqual(set(value['tier_models']), set(team_mix.FAMILIES))
        status, value = self.request('/api/team-mix?run=' + RUN_ID)
        self.assertEqual(status, 200)
        self.assertEqual((value['scope'], value['run_id'], value['lead_family']), ('run', RUN_ID, 'Anthropic'))
        self.assertEqual(value['history_count'], 1)

    def test_saves_the_default_for_new_runs(self):
        shares = {'OpenAI': 30, 'Anthropic': 40, 'xAI': 20, 'Google': 10}
        status, value = self.request('/api/team-mix', {'scope': 'global', 'shares': shares, 'preset': None,
                                                       'tolerance': 15,
                                                       'tiers': dict.fromkeys(team_mix.FAMILIES, 'economy')})
        self.assertEqual(status, 200, value)
        saved = team_mix.read_global()
        self.assertEqual((saved['shares'], saved['preset'], saved['tolerance'], saved['source']),
                         (shares, None, 15, 'viewer'))
        self.assertEqual(saved['tiers']['Anthropic'], 'economy')
        self.assertEqual(team_mix.read_run_mix(self.run)['current']['shares'], team_mix.PRESETS['balanced'])

    def test_retunes_one_run_and_logs_history(self):
        status, value = self.request('/api/team-mix', {'scope': 'run', 'run': RUN_ID, 'preset': 'code',
                                                       'shares': team_mix.PRESETS['code']})
        self.assertEqual(status, 200, value)
        self.assertEqual((value['mix']['preset'], value['history_count']), ('code', 2))
        state = team_mix.read_run_mix(self.run)
        self.assertEqual([row['source'] for row in state['history']], ['startup', 'viewer'])
        self.assertFalse(team_mix.global_path().exists())

    def test_rejects_malformed_changes_without_writing(self):
        for body in ({'scope': 'global', 'surprise': 1},
                     {'scope': 'run'},
                     {'scope': 'global', 'run': RUN_ID},
                     {'scope': 'global', 'shares': {'OpenAI': 100}},
                     {'scope': 'global', 'shares': {'OpenAI': 40, 'Anthropic': 40, 'xAI': 15, 'Google': 15}},
                     {'scope': 'global', 'tolerance': True},
                     {'scope': 'global', 'tiers': dict.fromkeys(team_mix.FAMILIES, 'cheapest')},
                     {'scope': 'run', 'run': '../escape'},
                     ['not', 'an', 'object']):
            with self.subTest(body=body):
                status, value = self.request('/api/team-mix', body)
                self.assertGreaterEqual(status, 400)
                self.assertIn('error', value)
        self.assertFalse(team_mix.global_path().exists())
        self.assertEqual(len(team_mix.read_run_mix(self.run)['history']), 1)

    def test_completed_runs_refuse_retunes(self):
        write_json(self.run / 'run.json', {'run_id': RUN_ID, 'status': 'completed'})
        status, value = self.request('/api/team-mix', {'scope': 'run', 'run': RUN_ID, 'preset': 'visual'})
        self.assertGreaterEqual(status, 400)
        self.assertIn('Completed runs', value['error'])


@unittest.skipUnless(shutil.which('node'), 'Node is needed for the slider script')
class TeamMixScriptTests(unittest.TestCase):
    def test_page_rebalance_matches_the_python_contract(self):
        start = PAGE.index('function teamMixRebalance')
        source = PAGE[PAGE.rindex('const MIX_FAMILIES', 0, start):PAGE.index('function mixEditable', start)]
        script = source + r'''
const vectors = JSON.parse(require('fs').readFileSync(process.argv[process.argv.length - 1], 'utf8'));
for (const v of vectors) {
  const got = JSON.stringify(teamMixRebalance(v.shares, v.family, v.value));
  if (got !== JSON.stringify(v.expected)) { console.error('mismatch', JSON.stringify(v), got); process.exitCode = 1; }
}
'''
        result = subprocess.run([shutil.which('node'), '-', str(VECTORS)], input=script, text=True,
                                encoding='utf-8', capture_output=True, timeout=10)
        self.assertEqual(result.returncode, 0, result.stderr)
        for vector in json.loads(VECTORS.read_text(encoding='utf-8')):
            self.assertEqual(team_mix.rebalance(vector['shares'], vector['family'], vector['value']),
                             vector['expected'])


if __name__ == '__main__':
    unittest.main()
