"""Hand-labelled recall test: extraction, scoring, A/B comparison and the billed-call guard."""
import json
import os
from pathlib import Path
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'app'))
from brain_store import BrainStore
import brain_eval


class BrainEvalTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.brain = BrainStore(self.root)
        self.retry = self.active('Retry policy', 'Retry three times with backoff after a timeout.')
        self.quota = self.active('Quota handoff', 'Hand the task to the runner-up when quota runs out.')

    def active(self, title, content):
        proposed = self.brain.propose({'project_id': 'alpha', 'kind': 'fact', 'title': title, 'content': content,
                                       'source': {'type': 'user', 'note': 'Synthetic reviewed source'}})
        return self.brain.approve(proposed['id'], 'Tester', 'Checked against the synthetic source.')['id']

    def task(self, job, query, ids, project='alpha'):
        path = self.root / 'runs/tasks' / job / 'result.json'
        path.parent.mkdir(parents=True)
        path.write_text(json.dumps({'job_id': job, 'assignment_project_id': project, 'finalized_at': '2026-10-01T00:00:00Z',
                                    'memory_context': {'query': query, 'ids': ids, 'profile': 'review',
                                                       'budget': {'depth': 'compact'}}}), encoding='utf-8')

    def cases_file(self, cases):
        path = self.root / 'cases.json'
        path.write_text(json.dumps({'schema_version': 1, 'project_id': 'alpha', 'cases': cases}), encoding='utf-8')
        return path

    def test_extract_drafts_unlabelled_cases_from_tasks_and_startup_packets(self):
        self.task('a' * 32, 'retry after timeout', [self.retry])
        self.task('b' * 32, 'Retry  after TIMEOUT', [self.retry])  # same query, deduplicated
        newer = time.time() + 60  # the unrated copy is scanned first; the rated copy must still win
        os.utime(self.root / 'runs/tasks' / ('b' * 32), (newer, newer))
        self.task('c' * 32, 'other project query', [], project='beta')
        packet = self.root / '.orchestration' / 'run-one' / 'startup-context.json'
        packet.parent.mkdir(parents=True)
        packet.write_text(json.dumps({'project_id': 'alpha', 'run_id': 'run-one', 'project_memory': {
            'query': 'quota handoff', 'profile': 'general', 'results': [{'id': self.quota}]}}), encoding='utf-8')
        with patch('brain_eval._rating', side_effect=lambda result: 'helped' if result['job_id'] == 'a' * 32 else None):
            document = brain_eval.extract(self.root, 'alpha')
        self.assertEqual([case['query'] for case in document['cases']], ['retry after timeout', 'quota handoff'])
        first = document['cases'][0]
        self.assertEqual((first['rating'], first['profile'], first['expected_ids']), ('helped', 'review', []))
        self.assertTrue(any(c['id'] == self.retry and c['supplied'] for c in first['candidates']))
        self.assertEqual(document['cases'][1]['supplied_ids'], [self.quota])
        self.assertEqual(document['scanned_startup_packets'], 1)

    def test_run_scores_labelled_cases_and_reports_flips(self):
        path = self.cases_file([
            {'id': 'retry', 'query': 'timeout retry', 'expected_ids': [self.retry]},
            {'id': 'quota', 'query': 'quota runner-up', 'expected_ids': [self.quota]},
            {'id': 'skipped', 'query': 'unlabelled', 'expected_ids': []}])
        cases = brain_eval.load_cases(path)
        self.assertEqual([case['id'] for case in cases], ['retry', 'quota'])
        report = brain_eval.run(self.root, cases, brain_eval.parse_config(['strategy=keyword']),
                                brain_eval.parse_config(['strategy=keyword', 'depth=deep']))
        summary = report['runs']['baseline']['summary']
        self.assertEqual((summary['cases'], summary['hit_at_1'], summary['mrr']), (2, 1.0, 1.0))
        self.assertEqual(report['comparison']['hit_at_1_gained'], [])
        self.assertEqual(report['comparison']['delta']['hit_at_1'], 0.0)
        self.assertIn('| hit_at_1 | 1.0 | 1.0 | 0.0 |', brain_eval.markdown(report))

    def test_flip_detection(self):
        rows = lambda hits: [{'case_id': str(i), 'hit_at_1': hit, 'rank': 1 if hit else 2} for i, hit in enumerate(hits)]
        result = brain_eval.compare(rows([True, False]), rows([False, True]), {'hit_at_1': .5}, {'hit_at_1': .5})
        self.assertEqual([item['case_id'] for item in result['hit_at_1_gained']], ['1'])
        self.assertEqual([item['case_id'] for item in result['hit_at_1_lost']], ['0'])

    def test_searches_are_read_only_and_record_no_traces(self):
        cases = brain_eval.load_cases(self.cases_file([{'id': 'retry', 'query': 'timeout retry',
                                                        'expected_ids': [self.retry]}]))
        with self.brain._connection() as con:
            before = con.execute('SELECT count(*) FROM traces').fetchone()[0]
        brain_eval.run(self.root, cases, brain_eval.parse_config([]))
        with self.brain._connection() as con:
            self.assertEqual(con.execute('SELECT count(*) FROM traces').fetchone()[0], before)

    def test_jev_configurations_need_explicit_permission(self):
        cases = brain_eval.load_cases(self.cases_file([{'id': 'retry', 'query': 'timeout retry',
                                                        'expected_ids': [self.retry]}]))
        with patch('jev_openrouter.evaluate', side_effect=AssertionError('billed call')):
            with self.assertRaises(ValueError):
                brain_eval.run(self.root, cases, brain_eval.parse_config([]),
                               brain_eval.parse_config(['jev=on', 'shape=choice']))

    def test_invalid_configs_and_labels_are_rejected(self):
        for pairs in (['strategy=fast'], ['shape'], ['colour=red']):
            with self.subTest(pairs=pairs), self.assertRaises(ValueError):
                brain_eval.parse_config(pairs)
        with self.assertRaises(ValueError):
            brain_eval.load_cases(self.cases_file([{'id': 'x', 'query': 'q', 'expected_ids': ['not-an-id']}]))
        with self.assertRaises(ValueError):
            brain_eval.run(self.root, [], brain_eval.parse_config([]))

    def test_cli_routes_through_brain_command(self):
        import brain_cli
        self.task('a' * 32, 'retry after timeout', [self.retry])
        output = self.root / 'drafted.json'
        with patch('builtins.print') as printed:
            code = brain_cli.main(['--root', str(self.root), 'eval', 'extract', '--project', 'alpha',
                                   '--output', str(output)])
        self.assertEqual(code, 0, printed.call_args)
        self.assertEqual(len(json.loads(output.read_text(encoding='utf-8'))['cases']), 1)


if __name__ == '__main__':
    unittest.main()
