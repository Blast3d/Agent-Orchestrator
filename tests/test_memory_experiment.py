"""Verify experiment isolation and accounting controls without invoking models."""
import importlib.util
from pathlib import Path
import tempfile
import unittest

SOURCE=Path(__file__).resolve().parents[1]/'benchmarks/memory_control/workspace.py'
spec=importlib.util.spec_from_file_location('memory_workspace',SOURCE)
workspace=importlib.util.module_from_spec(spec)
spec.loader.exec_module(workspace)


class WorkspaceTests(unittest.TestCase):
    def test_external_and_parent_paths_are_rejected(self):
        for path in ('../private','a/../private','C:/private','/private','a\\private','a/./b'):
            with self.subTest(path=path),self.assertRaises(ValueError):workspace.relative(path)

    def test_search_reads_only_fixture_and_reports_missing_files(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder)/'fixture';root.mkdir()
            (Path(folder)/'secret.txt').write_text('needle external',encoding='utf-8')
            (root/'a.py').write_text('needle local',encoding='utf-8')
            view=workspace.Workspace(root)
            results=view.actions([{'op':'search','value':'needle'},{'op':'read','value':'missing.py'}])
            self.assertEqual(results[0]['total_matches'],1)
            self.assertEqual(results[0]['matches'][0]['path'],'a.py')
            self.assertFalse(results[1]['ok'])

    def test_submission_requires_current_read_and_exact_ownership(self):
        files=[{'path':'a.py','content':'answer=1'}]
        with self.assertRaises(ValueError):workspace.check_submission(files,{'a.py'},set())
        with self.assertRaises(ValueError):workspace.check_submission(files,{'b.py'},{'a.py'})
        self.assertEqual(workspace.check_submission(files,{'a.py'},{'a.py'}),{'a.py':'answer=1'})

    def test_search_does_not_smuggle_complete_large_files(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder)
            (root/'a.md').write_text(('needle '+('x'*500)+'\n')*45,encoding='utf-8')
            result=workspace.Workspace(root).actions([{'op':'search','value':'needle'}])[0]
            self.assertEqual(len(result['matches']),40)
            self.assertTrue(result['truncated'])
            self.assertTrue(all(len(hit['text'])<=240 for hit in result['matches']))

    def test_documented_divmod_builtin_is_available_to_candidate(self):
        path=SOURCE.parent/'grader.py'
        spec=importlib.util.spec_from_file_location('tern_grader',path)
        grader=importlib.util.module_from_spec(spec);spec.loader.exec_module(grader)
        function=grader.load_candidate('def allocate_slots(requests, slots):\n    quotient, remainder = divmod(slots, 2)\n    return quotient, remainder\n','allocation')
        self.assertEqual(function([],7),(3,1))


if __name__=='__main__':unittest.main()
