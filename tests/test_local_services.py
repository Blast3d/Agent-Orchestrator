"""Sibling-page discovery from state files only; no server is started and no browser opens."""
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'app'))
from local_services import ensure, links, memory_projects


class LinkTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        (self.root / 'runtime').mkdir()

    def test_links_report_siblings_without_origins_when_nothing_runs(self):
        rows = links(self.root, 'brain')
        self.assertEqual([(r['id'], r['title'], r['current'], r['origin']) for r in rows],
                         [('brain', 'Memory', True, None), ('viewer', 'Orchestrator', False, None)])
        self.assertTrue(links(self.root, 'viewer')[1]['current'])

    def test_stale_state_file_without_a_live_server_gives_no_link(self):
        (self.root / 'runtime/coordinator-viewer.json').write_text(json.dumps({
            'service': 'orchestrator-session-viewer', 'version': 1, 'pid': 1,
            'origin': 'http://127.0.0.1:9', 'instance_id': 'a' * 32}), encoding='utf-8')
        self.assertIsNone(links(self.root, 'brain')[1]['origin'])

    def test_live_sibling_origin_is_linked_and_the_current_page_never_probes_itself(self):
        with patch('start_brain_dashboard.running_state', return_value={'origin': 'http://127.0.0.1:4242'}) as probe:
            rows = links(self.root, 'brain')
        self.assertEqual(rows[1]['origin'], 'http://127.0.0.1:4242')
        self.assertEqual(probe.call_count, 1)

    def test_run_deep_link_needs_a_real_run_and_a_plain_name(self):
        (self.root / '.orchestration/alpha').mkdir(parents=True)
        (self.root / '.orchestration/alpha/coordinator.json').write_text('{}', encoding='utf-8')
        self.assertEqual(links(self.root, 'brain', 'alpha')[1].get('run'), 'alpha')
        for project in ('beta', '../alpha', 'alpha/..', '.hidden', ''):
            self.assertNotIn('run', links(self.root, 'brain', project)[1], project)
        self.assertNotIn('run', links(self.root, 'viewer', 'alpha')[1])

    def test_ensure_starts_only_known_pages_and_reports_launch_errors_plainly(self):
        with self.assertRaises(ValueError):
            ensure(self.root, 'nope')
        launched = {'origin': 'http://127.0.0.1:5151', 'reused': False, 'pid': 7}
        with patch('start_brain_dashboard.open_dashboard', return_value=launched) as launcher:
            result = ensure(self.root, 'viewer')
        self.assertEqual(result, {'id': 'viewer', 'title': 'Orchestrator', 'origin': 'http://127.0.0.1:5151', 'reused': False})
        self.assertEqual(launcher.call_args.args, (self.root, False))
        self.assertEqual(launcher.call_args.kwargs, {'service': 'orchestrator-session-viewer',
                                                     'script': 'coordinator_viewer.py', 'state_name': 'coordinator-viewer'})
        with patch('start_brain_dashboard.open_dashboard', side_effect=RuntimeError('Dashboard did not start.')):
            with self.assertRaisesRegex(ValueError, 'did not start'):
                ensure(self.root, 'brain')

    def test_sibling_probe_is_cached_until_state_revision_changes(self):
        cache = {}
        with patch('start_brain_dashboard.running_state', return_value={'origin': 'http://127.0.0.1:4242'}) as probe:
            for _ in range(5):
                self.assertEqual(links(self.root, 'brain', cache=cache)[1]['origin'], 'http://127.0.0.1:4242')
            self.assertEqual(probe.call_count, 1)
            (self.root / 'runtime/coordinator-viewer.json').write_text('{}', encoding='utf-8')
            links(self.root, 'brain', cache=cache)
            self.assertEqual(probe.call_count, 2)

    def test_exact_run_and_shared_memory_scope_round_trip(self):
        run = self.root / '.orchestration/run-one'
        run.mkdir(parents=True)
        (run / 'coordinator.json').write_text('{}', encoding='utf-8')
        manifest = {'run_id': run.name, 'memory_project_id': 'shared-library'}
        (run / 'run.json').write_text(json.dumps(manifest), encoding='utf-8')
        row = links(self.root, 'brain', 'shared-library', run.name)[1]
        self.assertEqual((row['project'], row['run']), ('shared-library', run.name))
        self.assertNotIn('run', links(self.root, 'brain', 'unrelated-project', run.name)[1])
        self.assertNotIn('run', links(self.root, 'brain', 'shared-library', 'missing-run')[1])

    def test_scope_comes_from_exact_canonical_task_records(self):
        job = 'a' * 32
        task = self.root / 'runs/tasks' / job
        task.mkdir(parents=True)
        record = {'job_id': job, 'status': 'preparing', 'assignment_project_id': 'canonical-memory', 'prompt': 'PRIVATE_PROMPT'}
        (task / 'record.json').write_text(json.dumps(record), encoding='utf-8')
        manifest = {'tasks': [{'job_id': job}, {'job_id': '../elsewhere'}]}
        self.assertEqual(memory_projects(self.root, 'run-one', manifest), ['canonical-memory'])
        record['job_id'] = 'b' * 32
        (task / 'record.json').write_text(json.dumps(record), encoding='utf-8')
        self.assertEqual(memory_projects(self.root, 'run-one', manifest), ['run-one'])

    def test_existing_canonical_result_overrides_a_conflicting_scope_index(self):
        job = 'a' * 32
        task = self.root / 'runs/tasks' / job
        task.mkdir(parents=True)
        for filename, project in [('record.json', 'wrong-project'), ('result.json', 'verified-project')]:
            (task / filename).write_text(json.dumps({'job_id': job, 'status': 'accepted',
                'assignment_project_id': project}), encoding='utf-8')
        self.assertEqual(memory_projects(self.root, 'run-one', {'tasks': [{'job_id': job}]}), ['verified-project'])
        (task / 'result.json').write_text('{broken', encoding='utf-8')
        self.assertEqual(memory_projects(self.root, 'run-one', {'tasks': [{'job_id': job}]}), ['run-one'])


if __name__ == '__main__':
    unittest.main()
