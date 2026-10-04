"""Viewer status honesty: lead activity, grouped run picker and bot readiness (synthetic data only)."""
from datetime import datetime, timedelta, timezone
from http.client import HTTPConnection
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
from threading import Thread
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'app'))
from coordinator_handoff import Coordinator
from coordinator_viewer import ViewerServer, ViewerStore, run_grouping
from coordinator_viewer_page import PAGE
from init_run import create_run

NOW = datetime(2026, 9, 28, 20, 0, tzinfo=timezone.utc)
ACTUAL_SESSION = '1234abcd-1234-4567-8910-123456789abc'


class RunGroupingTests(unittest.TestCase):
    def test_experiments_current_recent_and_archived_follow_last_activity(self):
        cases = [({'experiment_parent_run_id': 'parent'}, 'planning', 1, ('experiments', True, False)),
                 ({'project_id': 'experiment-abc-team-warm'}, 'completed', 900, ('experiments', True, False)),
                 ({}, 'planning', 2, ('current', False, False)),
                 ({}, 'in_progress', 72, ('recent', False, True)),
                 ({}, 'completed', 72, ('recent', False, False)),
                 ({}, 'planning', 30 * 24, ('archived', False, True)),
                 ({}, 'complete', 30 * 24, ('archived', False, False))]
        for manifest, status, hours, (group, experiment, idle) in cases:
            with self.subTest(manifest=manifest, status=status, hours=hours):
                result = run_grouping(manifest, status, NOW - timedelta(hours=hours), NOW)
                self.assertEqual(result[:2], (group, experiment))
                self.assertEqual(result[2] is not None, idle)
        self.assertEqual(run_grouping({}, 'planning', None, NOW)[0], 'archived')


class StoreTests(unittest.TestCase):
    def setUp(self):
        policy = patch('coordinator_viewer.require_model_allowed', side_effect=lambda model: model)
        policy.start()
        self.addCleanup(policy.stop)
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name) / 'workspace'
        self.root.mkdir()
        self.home = Path(temp.name) / 'home'
        self.home.mkdir()
        self.run, _ = create_run(self.root, 'viewer-status', 'Keep lead activity honest')
        manifest = json.loads((self.run / 'run.json').read_text(encoding='utf-8'))
        manifest['display_name'] = 'Readable run title'
        (self.run / 'run.json').write_text(json.dumps(manifest), encoding='utf-8')
        self.clock = NOW
        self.store = ViewerStore(self.root, home=self.home, async_listing=False, collector=lambda: [],
                                 clock=lambda: self.clock)

    def checkpoint(self, when, owner='astra'):
        coordinator = Coordinator(self.run)
        state = coordinator.read()
        state.update(owner=owner, checkpoint_at=when.isoformat())
        coordinator.save(state)
        stamp = when.timestamp()
        os.utime(self.run / 'coordinator.json', (stamp, stamp))

    def test_summary_has_title_last_activity_and_group(self):
        self.checkpoint(NOW - timedelta(hours=3))
        row = self.store.runs()['runs'][0]
        self.assertEqual(row['title'], 'Readable run title')
        self.assertEqual(row['last_activity_at'], (NOW - timedelta(hours=3)).isoformat())
        self.assertEqual((row['group'], row['idle_since']), ('current', None))
        self.clock = NOW + timedelta(days=3)
        row = self.store.runs()['runs'][0]
        self.assertEqual(row['group'], 'recent')
        self.assertEqual(row['idle_since'], (NOW - timedelta(hours=3)).isoformat())

    def test_lead_activity_uses_checkpoint_not_the_viewer_poll_time(self):
        self.checkpoint(NOW - timedelta(minutes=20))
        with patch('task_activity.snapshot', return_value={'tasks': []}):
            detail = self.store.detail(self.run.name)
        self.assertEqual(detail['lead_activity'], {'at': (NOW - timedelta(minutes=20)).isoformat(), 'source': 'checkpoint'})
        self.assertNotEqual(detail['lead_activity']['at'], detail['provider']['checked_at'])

    def test_newer_saved_conversation_is_reported_as_the_lead_activity(self):
        self.checkpoint(NOW - timedelta(hours=5), owner='fable')
        project = re.sub('[^a-zA-Z0-9-]', '-', str(self.root))
        transcript = self.home / '.claude/projects' / project / (ACTUAL_SESSION + '.jsonl')
        transcript.parent.mkdir(parents=True)
        transcript.write_text('{}\n', encoding='utf-8')
        newer = (NOW - timedelta(minutes=3)).timestamp()
        os.utime(transcript, (newer, newer))
        state = Coordinator(self.run).read()
        provider = {'format': 'claude', 'session_id': ACTUAL_SESSION}
        with patch.object(ViewerStore, '_claude', return_value=dict(provider, history_note='', checked_at=None)), \
                patch('task_activity.snapshot', return_value={'tasks': []}):
            detail = self.store.detail(self.run.name)
        self.assertEqual(state['owner'], 'fable')
        self.assertEqual(detail['lead_activity'], {'at': (NOW - timedelta(minutes=3)).isoformat(), 'source': 'conversation'})

    def test_readiness_reads_saved_status_only_and_explains_missing_or_damaged_files(self):
        self.assertFalse(self.store.readiness()['available'])
        self.assertIn('No usage reading has been saved yet', self.store.readiness()['reason'])
        runtime = self.root / 'runtime'
        runtime.mkdir()
        (runtime / 'usage-status.json').write_text('[1, 2]', encoding='utf-8')
        self.assertIn('could not be read', self.store.readiness()['reason'])
        report = {'updated_at': NOW.isoformat(), 'admission_mode': 'advisory', 'worker_start_threshold_pct': 20,
                  'active_reservations': 1,
                  'workers': [{'worker': 'codex', 'status': 'held', 'allowed': False, 'reading_status': 'cached',
                               'windows': [{'id': 'codex-weekly', 'remaining_pct': 99, 'reserved_pct': 81, 'pending_pct': 81,
                                            'available_pct': 18, 'observed_at': (NOW - timedelta(hours=32)).isoformat(),
                                            'max_age_seconds': 600, 'source': 'test', 'reset_passed': False}],
                               'reasons': ['codex-weekly: available allowance is at or below the worker start threshold'],
                               'warnings': []}]}
        (runtime / 'usage-status.json').write_text(json.dumps(report), encoding='utf-8')
        before = (runtime / 'usage-status.json').stat().st_mtime_ns
        value = self.store.readiness()
        self.assertTrue(value['available'])
        self.assertEqual(value['counts']['held'], 1)
        self.assertEqual(value['bots'][0]['age_text'], 'reading 32 h old')
        self.assertTrue(value['bots'][0]['sentence'].startswith('Held: 18% is free'))
        self.assertEqual((runtime / 'usage-status.json').stat().st_mtime_ns, before)
        self.assertEqual(sorted(p.name for p in runtime.iterdir()), ['usage-status.json'])


class ReadinessHTTPTests(unittest.TestCase):
    def test_readiness_endpoint_needs_the_viewer_token(self):
        with tempfile.TemporaryDirectory() as directory:
            server = ViewerServer(Path(directory))
            thread = Thread(target=server.serve_forever, kwargs={'poll_interval': .02}, daemon=True)
            thread.start()
            try:
                def get(headers):
                    connection = HTTPConnection(*server.server_address, timeout=3)
                    try:
                        connection.request('GET', '/api/readiness', headers=headers)
                        response = connection.getresponse()
                        return response.status, json.loads(response.read())
                    finally:
                        connection.close()
                self.assertEqual(get({})[0], 403)
                status, body = get({'X-Viewer-Token': server.viewer_token})
                self.assertEqual((status, body['available']), (200, False))
            finally:
                server.shutdown()
                server.server_close()
                thread.join(timeout=2)


@unittest.skipUnless(shutil.which('node'), 'Node is needed for the run picker check')
class RunPickerFlowTests(unittest.TestCase):
    def test_options_are_grouped_titled_and_flag_idle_runs(self):
        begin = PAGE.index('  const seenText = ')
        helpers = PAGE[begin:PAGE.index('  function time(value)', begin)]
        begin = PAGE.index('  // Run picker:')
        picker = PAGE[begin:PAGE.index('  async function refresh()', begin)]
        runs = [{'id': 'exp-1', 'group': 'experiments', 'title': 'OpenAI + Claude + Grok / Brain + JEV',
                 'lead_label': 'ASTRA', 'last_activity_at': (datetime.now(timezone.utc) - timedelta(days=2)).isoformat(),
                 'idle_since': (datetime.now(timezone.utc) - timedelta(days=2)).isoformat()},
                {'id': 'live-1', 'group': 'current', 'title': 'Implement the audit fixes',
                 'lead_label': 'Claude', 'last_activity_at': datetime.now(timezone.utc).isoformat()},
                {'id': 'old-1', 'group': 'unexpected', 'objective': 'x' * 100, 'owner': 'fable'}]
        script = r'''
const assert=require('node:assert/strict');
const document={createElement(tag){return {tag,children:[],append(...items){this.children.push(...items);}};}};
''' + helpers + picker + r'''
const groups=buildRunOptions(''' + json.dumps(runs) + r''');
assert.deepEqual(groups.map(g=>g.label),['Current (1)','Experiments (1)','Archived (1)']);
assert.equal(groups[0].children[0].textContent,'Implement the audit fixes — Claude — last activity under an hour ago');
assert.match(groups[1].children[0].textContent,/^OpenAI \+ Claude \+ Grok \/ Brain \+ JEV — ASTRA — last activity 2 d ago — Idle since /);
assert.equal(groups[2].children[0].value,'old-1');
assert.ok(groups[2].children[0].textContent.startsWith('x'.repeat(71)+'…'));
assert.match(groups[2].children[0].textContent,/— fable — last activity not recorded$/);
'''
        result = subprocess.run([shutil.which('node')], input=script, text=True, encoding='utf-8',
                                capture_output=True, timeout=10)
        self.assertEqual(result.returncode, 0, result.stderr)


class PageCopyTests(unittest.TestCase):
    def test_status_labels_and_switch_copy(self):
        self.assertIn('<span class="label">Status checked</span>', PAGE)
        self.assertIn('<span class="label">Lead last active</span>', PAGE)
        self.assertNotIn('Last observed', PAGE)
        self.assertIn('Background usage checks:', PAGE)
        self.assertNotIn('Off - turn on', PAGE)
        self.assertIn("api('/api/readiness')", PAGE)


if __name__ == '__main__':
    unittest.main()
