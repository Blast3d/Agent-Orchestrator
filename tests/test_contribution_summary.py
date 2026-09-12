"""Who-did-what summaries from synthetic audits and task records; no model calls."""
from contextlib import redirect_stdout
import io
import json
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'app'))
from contribution_summary import by_worker, main, overview, render_markdown, run_summary

JOB_A, JOB_B, JOB_C = 'a' * 32, 'b' * 32, 'c' * 32


def agent(identifier, name, share, delegations=0, tokens=(None, None)):
    return {'id': identifier, 'name': name, 'provider': 'Synthetic', 'model': 'unknown', 'accepted_work_pct': share,
            'delegations': delegations, 'delegation_pct': 0.0, 'input_tokens': tokens[0], 'output_tokens': tokens[1],
            'known_tokens': None, 'known_token_share_pct': None, 'token_coverage': {'status': 'partial'}, 'actual_models': []}


class SummaryTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.run = self.root / '.orchestration/run-a'
        self.run.mkdir(parents=True)
        self.write(self.run / 'run.json', {'run_id': 'run-a', 'display_name': 'Run A', 'status': 'completed',
                                            'tasks': [{'job_id': JOB_A, 'role': 'maker'}, {'agent': 'native', 'status': 'completed'}]})
        self.write(self.run / 'contribution-audit.json', {
            'scope_id': 'run-a', 'title': 'Run A audit', 'status': 'complete', 'attribution_complete': True, 'unattributed_pct': 0.0,
            'basis': 'PRIVATE_BASIS', 'work_items': [{'label': 'PRIVATE_ITEM'}],
            'by_agent': [agent('astra', 'ASTRA', 60.0), agent('claude', 'Claude maker', 40.0, 1, (100, 50))],
            'by_category': [{'category': 'coding', 'unattributed_pct': 0.0, 'by_agent': [{'id': 'claude', 'accepted_work_pct': 100.0}]}],
            'usage': {'delegations': 1, 'input_tokens': 100, 'output_tokens': 50, 'known_tokens': 150, 'token_coverage': {'status': 'partial'}},
            'activity': [{'id': 'd1', 'agent_id': 'claude', 'kind': 'delegation', 'status': 'accepted', 'task_id': JOB_B, 'evidence': 'PRIVATE_EVIDENCE'}]})
        self.record(JOB_A, 'claude', 'accepted', usage={'input_tokens': 100, 'output_tokens': 50},
                    modelUsage={'claude-sonnet-4-6': {'costUSD': 0.0123, 'inputTokens': 100}})
        self.record(JOB_B, 'grok', 'rejected', usage={'input_tokens': 7, 'output_tokens': 3}, model='grok-4.6')
        self.record(JOB_C, 'gemini', 'held', assignment_project_id='run-a')
        self.record('d' * 32, 'claude', 'accepted', assignment_project_id='elsewhere')
        (self.root / '.orchestration/run-b').mkdir()
        self.write(self.root / '.orchestration/run-b/run.json', {'run_id': 'run-b', 'status': 'implementing', 'tasks': []})

    def write(self, path, value):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(value), encoding='utf-8')

    def record(self, job, worker, status, **extra):
        self.write(self.root / 'runs/tasks' / job / 'record.json',
                   {'job_id': job, 'worker': worker, 'status': status, 'task': 'review', 'created_at': '2026-09-09T00:00:00Z',
                    'prompt': 'PRIVATE_PROMPT', 'response': 'PRIVATE_RESPONSE', **extra})
        (self.root / 'runs/tasks' / job / 'contribution-audit.md').write_text('# audit', encoding='utf-8')

    def test_run_summary_reports_agents_usage_and_every_linked_task_once(self):
        summary = run_summary(self.root, 'run-a')
        self.assertEqual(summary['title'], 'Run A')
        self.assertEqual([(a['name'], a['accepted_work_pct'], a['input_tokens']) for a in summary['agents']],
                         [('ASTRA', 60.0, None), ('Claude maker', 40.0, 100)])
        self.assertEqual(summary['audit']['usage'], {'delegations': 1, 'input_tokens': 100, 'output_tokens': 50, 'known_tokens': 150})
        self.assertEqual(summary['categories'][0]['by_agent'], [{'id': 'claude', 'accepted_work_pct': 100.0}])
        tasks = {task['job_id']: task for task in summary['tasks']}
        self.assertEqual(set(tasks), {JOB_A, JOB_B, JOB_C})  # manifest id, activity id, assignment scope; never 'elsewhere'
        self.assertEqual((tasks[JOB_A]['model'], tasks[JOB_B]['model'], tasks[JOB_C]['model']), ('claude-sonnet-4-6', 'grok-4.6', 'unknown'))
        self.assertEqual((tasks[JOB_C]['input_tokens'], tasks[JOB_C]['output_tokens'], tasks[JOB_C]['cost_usd']), (None, None, None))
        self.assertEqual((tasks[JOB_A]['cost_usd'], tasks[JOB_B]['cost_usd']), (0.0123, None))
        self.assertEqual(summary['task_usage'], {'tasks': 3, 'tasks_with_tokens': 2, 'input_tokens': 107, 'output_tokens': 53, 'cost_usd': 0.0123})
        self.assertTrue(all(task['audit'] for task in summary['tasks']))

    def test_missing_audit_is_reported_not_raised(self):
        summary = run_summary(self.root, 'run-b')
        self.assertIsNone(summary['audit'])
        self.assertEqual((summary['agents'], summary['tasks'], summary['title']), ([], [], 'run-b'))
        runs = {run['run_id']: run for run in overview(self.root)['runs']}
        self.assertEqual(runs['run-b']['audit_status'], 'missing')
        self.assertEqual(runs['run-a']['audit_status'], 'complete')
        self.assertEqual(runs['run-a']['tasks'], 3)

    def test_run_ids_cannot_leave_the_workspace(self):
        for run in ('../run-a', 'run-a/..', '', '.hidden', 'missing-run', None):
            with self.assertRaises(ValueError, msg=run):
                run_summary(self.root, run)

    def test_by_worker_totals_keep_unknown_tokens_unknown(self):
        totals = {total['worker']: total for total in by_worker(self.root)}
        self.assertEqual(totals['claude']['tasks'], 2)
        self.assertEqual(totals['claude']['by_status'], {'accepted': 2})
        self.assertEqual((totals['claude']['input_tokens'], totals['claude']['output_tokens'], totals['claude']['tasks_with_tokens']), (100, 50, 1))
        self.assertEqual((totals['gemini']['input_tokens'], totals['gemini']['tasks_with_tokens'], totals['gemini']['cost_usd']), (None, 0, None))
        self.assertEqual(totals['claude']['cost_usd'], 0.0123)
        self.assertEqual(totals['grok']['models'], {'grok-4.6': 1})
        self.assertEqual([total['worker'] for total in by_worker(self.root)], ['claude', 'gemini', 'grok'])

    def test_output_never_includes_prompts_responses_or_evidence_text(self):
        text = json.dumps(overview(self.root)) + json.dumps(run_summary(self.root, 'run-a')) + render_markdown(run_summary(self.root, 'run-a'))
        for private in ('PRIVATE_PROMPT', 'PRIVATE_RESPONSE', 'PRIVATE_EVIDENCE', 'PRIVATE_BASIS', 'PRIVATE_ITEM'):
            self.assertNotIn(private, text)

    def test_markdown_shows_shares_and_unknowns(self):
        page = render_markdown(overview(self.root))
        self.assertIn('| Run A | completed | complete | ASTRA 60.0%; Claude maker 40.0% | 1 | 100 / 50 | $0.0123 |', page)
        self.assertIn('| run-b | implementing | missing | no audit | Unknown | Unknown / Unknown | Unknown |', page)
        self.assertIn('| gemini | 1 | held 1 | 0 | Unknown / Unknown | Unknown | unknown x1 |', page)
        self.assertIn('| claude | 2 | accepted 2 | 1 | 100 / 50 | $0.0123 |', page)
        detail = render_markdown(run_summary(self.root, 'run-a'))
        self.assertIn('| ASTRA | Synthetic | 60.0% | 0 | Unknown / Unknown | pending |', detail)
        self.assertIn("coordinator's own conversation", detail)

    def test_cli_prints_json_or_markdown_and_reports_bad_runs(self):
        out = io.StringIO()
        with redirect_stdout(out):
            self.assertEqual(main(['--root', str(self.root), '--run', 'run-a']), 0)
        self.assertEqual(json.loads(out.getvalue())['run_id'], 'run-a')
        out = io.StringIO()
        with redirect_stdout(out):
            self.assertEqual(main(['--root', str(self.root), '--markdown']), 0)
        self.assertTrue(out.getvalue().startswith('# Who did what, and how much'))
        out = io.StringIO()
        with redirect_stdout(out):
            self.assertEqual(main(['--root', str(self.root), '--run', '../run-a']), 2)
        self.assertEqual(json.loads(out.getvalue())['ok'], False)


if __name__ == '__main__':
    unittest.main()
