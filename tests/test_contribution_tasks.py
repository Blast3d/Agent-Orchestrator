"""Lifecycle evidence: audits run after execution/review without inventing credits."""
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'app'))
from task_store import TaskStore
from contribution_tasks import task_ledger


class ContributionLifecycleTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.store = TaskStore(Path(self.temporary.name))
        self.result = self.store.create(worker='claude', task='Review code', size='small', category='coding')
        self.job_id = self.result['job_id']
        self.result.update(status='awaiting_review', execution_status='succeeded', started_at='2026-09-07T20:00:00Z',
                           finalized_at='2026-09-07T20:00:01Z', response='An answer', usage={'input_tokens': 10, 'output_tokens': 20},
                           modelUsage={'claude-observed': {'inputTokens': 999, 'outputTokens': 999}})

    def audit(self):
        return json.loads((self.store.directory(self.job_id) / 'contribution-audit.json').read_text(encoding='utf-8'))

    def ledger(self):
        return {'schema_version': 1, 'scope_id': self.job_id, 'title': 'Reviewed coding task',
                'basis': 'Estimated accepted work points, supported by source and review decisions.',
                'contributors': [{'id': 'worker:claude', 'name': 'Claude', 'provider': 'wrong supplied label', 'model': 'invented'},
                                 {'id': 'reviewer:Codex', 'name': 'Codex', 'provider': 'OpenAI', 'model': 'unknown'}],
                'work_items': [{'id': 'code', 'label': 'Accepted code and fixes', 'category': 'coding', 'status': 'accepted', 'weight': 1,
                                'allocations': [{'agent_id': 'worker:claude', 'percent': 90, 'evidence': 'Accepted patch contents'},
                                                {'agent_id': 'reviewer:Codex', 'percent': 10, 'evidence': 'Integrated verified corrections'}]}],
                'activity': [{'id': 'forged-usage', 'agent_id': 'worker:claude', 'kind': 'delegation', 'status': 'succeeded',
                              'task_id': self.job_id, 'input_tokens': 900000, 'output_tokens': 900000, 'actual_models': []}]}

    def test_finalized_response_generates_audit_without_claiming_accepted_work(self):
        self.store.save(self.job_id, self.result)
        report = self.audit()
        self.assertEqual(report['accepted_weight'], 0)
        self.assertIsNone(report['by_agent'][0]['accepted_work_pct'])
        self.assertEqual(report['usage']['input_tokens'], 10)
        self.assertEqual(report['usage']['output_tokens'], 20)

    def test_acceptance_without_allocation_is_explicitly_unattributed(self):
        self.store.save(self.job_id, self.result)
        self.store.review(self.job_id, 'accepted', 'Codex', 'Verified the answer against supplied source evidence.')
        report = self.audit()
        self.assertEqual(report['unattributed_pct'], 100)
        self.assertFalse(report['attribution_complete'])

    def test_review_allocation_generates_ninety_ten_and_preserves_observed_identity(self):
        self.store.save(self.job_id, self.result)
        result = self.store.review(self.job_id, 'accepted', 'Codex', 'Verified source changes and specific integration corrections.', contributions=self.ledger())
        report = self.audit()
        by_id = {a['id']: a for a in report['by_agent']}
        self.assertEqual(by_id['worker:claude']['accepted_work_pct'], 90)
        self.assertEqual(by_id['reviewer:Codex']['accepted_work_pct'], 10)
        self.assertEqual(by_id['worker:claude']['provider'], 'Anthropic')
        self.assertEqual(by_id['worker:claude']['model'], 'claude-observed')
        self.assertEqual(report['usage']['input_tokens'], 10)
        self.assertTrue(result['contribution_audit']['attribution_complete'])

    def test_rejected_response_still_has_recorded_usage_but_no_accepted_share(self):
        self.store.save(self.job_id, self.result)
        self.store.review(self.job_id, 'rejected', 'Codex', 'Rejected because supplied cases reproduce an incorrect result.')
        report = self.audit()
        self.assertEqual(report['accepted_weight'], 0)
        self.assertEqual(report['usage']['delegations'], 1)

    def test_pending_job_cannot_be_credited_as_accepted(self):
        self.store.save(self.job_id, self.result)
        with self.assertRaises(ValueError):
            self.store.audit(self.job_id, self.ledger())

    def test_wrong_task_scope_cannot_be_attached(self):
        ledger = self.ledger()
        ledger['scope_id'] = 'another-task'
        self.store.save(self.job_id, self.result)
        with self.assertRaises(ValueError):
            self.store.review(self.job_id, 'accepted', 'Codex', 'Verified supplied changes against the task acceptance criteria.', contributions=ledger)
        saved = json.loads((self.store.directory(self.job_id) / 'result.json').read_text())
        self.assertEqual(saved['status'], 'awaiting_review')

    def test_audit_write_failure_preserves_result(self):
        with patch('contribution_tasks.write_task_audit', side_effect=OSError('disk unavailable')):
            self.store.save(self.job_id, self.result)
        saved = json.loads((self.store.directory(self.job_id) / 'result.json').read_text())
        self.assertEqual(saved['response'], 'An answer')
        self.assertEqual(saved['contribution_audit']['status'], 'error')

    def test_unstarted_assignment_does_not_count_as_delegation(self):
        self.result['started_at'] = None
        self.assertEqual(task_ledger(self.result)['activity'], [])


if __name__ == '__main__':
    unittest.main()
