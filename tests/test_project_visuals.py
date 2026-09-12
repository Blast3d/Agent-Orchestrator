"""Project views preserve real shares, missing data and private-source boundaries."""
import json
import shutil
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'app'))
from contributions import write_report
from project_visuals import ProjectLibrary, project_from_report


def ledger(scope='first', percent=90, allocated=True):
    return {'schema_version': 1, 'scope_id': scope, 'title': 'My project', 'basis': 'Reviewed estimates with saved evidence.',
            'contributors': [{'id': 'a', 'name': 'Claude', 'provider': 'Anthropic', 'model': 'unknown'},
                             {'id': 'b', 'name': 'Codex', 'provider': 'OpenAI', 'model': 'unknown'}],
            'work_items': [{'id': 'work', 'label': 'Built the feature', 'category': 'coding', 'status': 'accepted', 'weight': 1,
                'allocations': [{'agent_id': 'a', 'percent': percent, 'evidence': 'private/source/path'},
                                {'agent_id': 'b', 'percent': 100 - percent, 'evidence': 'private/source/other'}] if allocated else []}],
            'activity': []}


class ProjectViewTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.library = ProjectLibrary(self.root, self.root / 'state')

    def save(self, name, data):
        folder = self.root / '.orchestration' / name
        return write_report(folder, data), folder / 'contribution-audit.json'

    def test_two_projects_keep_their_own_percentages(self):
        self.save('one', ledger('one', 90))
        self.save('two', ledger('two', 20))
        result = {p['id']: p for p in self.library.collect()['projects']}
        self.assertEqual(result['one']['people'][0]['share_pct'], 90)
        self.assertEqual(result['two']['people'][0]['share_pct'], 20)

    def test_missing_attribution_is_not_normalized(self):
        report, _ = self.save('one', ledger(allocated=False))
        project = project_from_report(report, '2026-09-07')
        self.assertEqual(project['unassigned_pct'], 100)
        self.assertEqual(project['state'], 'incomplete')
        self.assertEqual([p['share_pct'] for p in project['people']], [None, None])

    def test_rejected_work_does_not_create_share_or_kept_work(self):
        data = ledger()
        data['work_items'][0]['status'] = 'rejected'
        report, _ = self.save('one', data)
        project = project_from_report(report, '2026-09-07')
        self.assertEqual(project['state'], 'waiting')
        self.assertTrue(all(p['share_pct'] is None and not p['kept'] for p in project['people']))

    def test_no_report_means_empty_library_not_demo_projects(self):
        self.assertEqual(self.library.collect()['projects'], [])

    def test_private_paths_and_model_usage_are_not_in_view_data(self):
        report, _ = self.save('one', ledger())
        payload = json.dumps(project_from_report(report, '2026-09-07'))
        self.assertNotIn('private/source', payload)
        self.assertNotIn('input_tokens', payload)
        self.assertNotIn('modelUsage', payload)

    def test_tampered_cached_percentages_are_recomputed(self):
        report, path = self.save('one', ledger())
        report['by_agent'][0]['accepted_work_pct'] = 1
        path.write_text(json.dumps(report), encoding='utf-8')
        self.assertEqual(self.library.collect()['projects'][0]['people'][0]['share_pct'], 90)

    def test_external_project_is_registered_and_saved_when_disconnected(self):
        outside = self.root / 'different-project'
        write_report(outside, ledger('external'))
        source = outside / 'contribution-audit.json'
        self.library.register(source)
        self.assertEqual(self.library.collect()['projects'][0]['id'], 'external')
        source.unlink()
        saved = self.library.collect()['projects'][0]
        self.assertIn('Saved view', saved['note'])
        self.assertEqual(saved['people'][0]['share_pct'], 90)

    def test_malformed_new_report_cannot_replace_a_good_saved_view(self):
        _, source = self.save('one', ledger())
        self.library.collect()
        source.write_text('{broken', encoding='utf-8')
        self.assertIn('Saved view', self.library.collect()['projects'][0]['note'])

    def test_embedded_data_cannot_close_script_or_include_source_evidence(self):
        data = ledger()
        data['title'] = '</script><script>alert(1)</script>'
        self.save('one', data)
        template = self.root / 'template.html'
        template.write_text('<script type="application/json">__PROJECT_MAP_DATA__</script>', encoding='utf-8')
        output = self.root / 'view.html'
        self.library.render(output, template)
        text = output.read_text(encoding='utf-8')
        self.assertEqual(text.count('</script>'), 1)
        self.assertIn('\\u003c/script\\u003e', text)
        self.assertNotIn('private/source', text)

    def test_readable_project_name_is_taken_only_from_matching_run(self):
        _, path = self.save('one', ledger('one'))
        (path.parent / 'run.json').write_text(json.dumps({'run_id': 'one', 'display_name': 'A friendly project name'}))
        self.assertEqual(self.library.collect()['projects'][0]['title'], 'A friendly project name')

    def test_available_copy_wins_over_equal_dated_offline_copy(self):
        _, source = self.save('one', ledger())
        duplicate = self.root / 'external-copy.json'
        shutil.copy2(source, duplicate)
        self.library.register(duplicate)
        self.library.collect()
        duplicate.unlink()
        project = self.library.collect()['projects'][0]
        self.assertNotIn('note', project)


if __name__ == '__main__':
    unittest.main()
