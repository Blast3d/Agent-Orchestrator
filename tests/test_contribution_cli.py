"""Final-delivery admission uses actual attribution completeness."""
from contextlib import redirect_stdout
import io
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'app'))
import contribution_cli
from init_run import create_run


class ContributionCompletionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.run, _ = create_run(Path(self.temp.name), 'test-contributions', 'Verify final contribution audit')
        self.path = self.run / 'contributions-ledger.json'
        self.ledger = json.loads(self.path.read_text())
        self.visual_patch = patch('project_visuals.refresh_views')
        self.visual_refresh = self.visual_patch.start()
        self.addCleanup(self.visual_patch.stop)

    def execute(self):
        self.path.write_text(json.dumps(self.ledger), encoding='utf-8')
        with patch.object(sys, 'argv', ['contributions', '--ledger', str(self.path), '--output-dir', str(self.run), '--require-complete']), redirect_stdout(io.StringIO()):
            return contribution_cli.main()

    def add_accepted_item(self, allocate):
        self.ledger['contributors'] = [{'id': 'lead', 'name': 'Codex', 'provider': 'OpenAI', 'model': 'unknown'}]
        self.ledger['work_items'] = [{'id': 'implementation', 'label': 'Verified implementation', 'category': 'coding', 'status': 'accepted',
            'weight': 1, 'allocations': [{'agent_id': 'lead', 'percent': 100, 'evidence': 'Accepted implementation diff and regression checks'}] if allocate else []}]

    def test_new_run_requires_an_audit_and_empty_ledger_does_not_pass_finalization(self):
        self.assertTrue(json.loads((self.run / 'run.json').read_text())['contribution_audit']['required'])
        self.assertEqual(self.execute(), 2)
        self.assertTrue((self.run / 'contribution-audit.md').is_file())

    def test_unattributed_accepted_work_holds_delivery_and_updates_manifest(self):
        self.add_accepted_item(False)
        self.assertEqual(self.execute(), 2)
        saved = json.loads((self.run / 'run.json').read_text())
        self.assertEqual(saved['contribution_audit']['status'], 'needs_attribution')

    def test_complete_audit_allows_delivery_without_changing_overall_run_status(self):
        self.add_accepted_item(True)
        self.assertEqual(self.execute(), 0)
        saved = json.loads((self.run / 'run.json').read_text())
        self.assertEqual(saved['contribution_audit']['status'], 'complete')
        self.assertEqual(saved['status'], 'planning')
        self.visual_refresh.assert_called_once_with(self.run / 'contribution-audit.json')


if __name__ == '__main__':
    unittest.main()
