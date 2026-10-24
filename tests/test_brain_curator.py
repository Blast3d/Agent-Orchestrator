"""Read-only curator report findings and its CLI route."""
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'app'))
from brain_store import BrainStore
import brain_curator


class CuratorTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.brain = BrainStore(self.root)

    def active(self, title, content, **extra):
        proposed = self.brain.propose(dict({'project_id': 'alpha', 'kind': 'fact', 'title': title, 'content': content,
                                            'source': {'type': 'user', 'note': 'Synthetic reviewed source'}}, **extra))
        return self.brain.approve(proposed['id'], 'Tester', 'Checked against the synthetic source.')['id']

    def age(self, memory_id, created_at):
        with self.brain._connection() as con:
            con.execute('UPDATE memories SET created_at=? WHERE id=?', (created_at, memory_id))
            con.commit()

    def report(self):
        return brain_curator.report(self.root, 'alpha')

    def test_lists_reviewed_contradictions(self):
        a = self.active('Retry limit', 'Three attempts.')
        b = self.active('Retry limit raised', 'Five attempts.')
        self.brain.relate(b, a, 'contradicts', 'Tester')
        found = self.report()['findings']['contradictions']
        self.assertEqual([(item['source_id'], item['target_id']) for item in found], [(b, a)])

    def test_finds_near_duplicates_but_ignores_shared_template_words(self):
        template = 'Accepted review evidence compact canonical inspect answer '
        for index in range(6):
            self.active('Accepted review ' + str(index), template + 'topic' + str(index) + ' unique' + str(index))
        first = self.active('Gemini quota fallback', 'When Gemini quota runs out route review work to Grok instead.')
        second = self.active('Gemini quota fallback rule', 'When Gemini quota runs out route review work to Grok.')
        pairs = self.report()['findings']['near_duplicates']
        self.assertEqual([{pair['a_id'], pair['b_id']} for pair in pairs], [{first, second}])

    def test_flags_old_memories_missing_from_recall_evidence(self):
        old = self.active('Old unrecalled fact', 'Something nobody searched for.')
        recalled = self.active('Old recalled fact', 'Retry with backoff.')
        self.active('New fact', 'Recently added.')
        for identifier in (old, recalled):
            self.age(identifier, '2026-01-01T00:00:00.000000+00:00')
        self.brain.search('backoff', 'alpha', jev=False)  # records a trace for the recalled memory
        report = self.report()
        self.assertEqual([item['id'] for item in report['findings']['not_recently_recalled']], [old])
        self.assertEqual(report['not_recently_recalled_total'], 1)

    def test_flags_open_ended_environment_facts_only(self):
        flagged = self.active('Ripgrep missing', 'rg is not installed on the build agent.')
        self.active('Ripgrep missing until Friday', 'rg is not installed on the build agent.',
                    valid_to='2099-01-01T00:00:00Z')
        self.active('Token usage report', 'Reports count input tokens and cost.')
        found = self.report()['findings']['open_ended_environment']
        self.assertEqual([item['id'] for item in found], [flagged])

    def test_reports_harmful_ratings_and_stale_pending(self):
        memory = self.active('Risky advice', 'Delete the cache.')
        pending = self.brain.propose({'project_id': 'alpha', 'kind': 'fact', 'title': 'Unreviewed', 'content': 'Draft.',
                                      'source': {'type': 'user', 'note': 'Synthetic draft'}})
        self.age(pending['id'], '2026-01-01T00:00:00.000000+00:00')
        task = self.root / 'runs/tasks' / ('a' * 32) / 'result.json'
        task.parent.mkdir(parents=True)
        task.write_text(json.dumps({'job_id': 'a' * 32, 'assignment_project_id': 'alpha',
                                    'memory_context': {'ids': [memory]}}), encoding='utf-8')
        rating = {'feedback': {'status': 'reviewed', 'rating': 'harmful', 'note': 'Misled the worker.'}}
        with patch('memory_usage.summary', return_value=rating):
            report = self.report()
        self.assertEqual(report['findings']['harmful_rated'][0]['current_memory_ids'], [memory])
        self.assertEqual([item['id'] for item in report['findings']['stale_pending']], [pending['id']])

    def test_report_changes_nothing(self):
        self.active('Retry limit', 'Three attempts.')
        before = self.brain.export('alpha')
        self.report()
        after = self.brain.export('alpha')
        self.assertEqual(before['memories'], after['memories'])
        self.assertEqual(before['change_cursor'], after['change_cursor'])

    def test_cli_route(self):
        import brain_cli
        self.active('Retry limit', 'Three attempts.')
        with patch('builtins.print') as printed:
            code = brain_cli.main(['--root', str(self.root), 'curator', '--project', 'alpha'])
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(printed.call_args.args[0])['current_memories'], 1)


if __name__ == '__main__':
    unittest.main()
