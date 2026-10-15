"""Frozen route identity survives preference changes without new provider work."""
from copy import deepcopy
import json
import unittest
from unittest.mock import patch

import test_task_handoff as fixtures


class HandoffResumeTests(unittest.TestCase):
    setUp = fixtures.HandoffTests.setUp
    run_plan = fixtures.HandoffTests.run_plan

    def test_default_claude_model_change_reuses_original_completed_plan(self):
        self.args.claude_model = None
        dispatcher = fixtures.RecordedDispatcher()
        original = self.run_plan(dispatcher)
        self.policy.write_text(json.dumps({'workers': [
            {'id': 'claude', 'requested_model': 'haiku'}]}), encoding='utf-8')
        repeated = self.run_plan(dispatcher)
        self.assertEqual(repeated['status'], 'awaiting_review')
        self.assertEqual(repeated['job_id'], original['job_id'])
        self.assertEqual(len(dispatcher.started), 2)

    def test_default_codex_switch_reuses_original_completed_plan(self):
        self.args.worker = 'codex'
        dispatcher = fixtures.RecordedDispatcher({'codex': 'low', 'grok': 'success'})
        with patch('lead_selection.describe', return_value={'codex_lead': 'astra'}):
            original = self.run_plan(dispatcher)
        with patch('lead_selection.describe', return_value={'codex_lead': 'sol'}):
            repeated = self.run_plan(dispatcher)
        self.assertEqual(repeated['status'], 'awaiting_review')
        self.assertEqual(repeated['job_id'], original['job_id'])
        self.assertEqual(len(dispatcher.started), 2)

    def test_explicit_model_change_still_holds_existing_assignment(self):
        self.args.claude_model = None
        dispatcher = fixtures.RecordedDispatcher()
        self.run_plan(dispatcher)
        changed = deepcopy(self.args)
        changed.claude_model = 'haiku'
        self.assertTrue(self.run_plan(dispatcher, changed)['handoff_blocked'])
        self.assertEqual(len(dispatcher.started), 2)

    def test_new_pause_policy_still_blocks_frozen_model(self):
        self.args.claude_model = None
        dispatcher = fixtures.RecordedDispatcher()
        self.run_plan(dispatcher)
        self.policy.write_text(json.dumps({'policy': {'paused_claude_model_families': ['opus']},
            'workers': [{'id': 'claude', 'requested_model': 'haiku'}]}), encoding='utf-8')
        self.assertTrue(self.run_plan(dispatcher)['handoff_blocked'])
        self.assertEqual(len(dispatcher.started), 2)

    def test_default_change_does_not_retry_uncertain_work_or_advance(self):
        self.args.claude_model = None
        dispatcher = fixtures.RecordedDispatcher({'claude': 'uncertain', 'grok': 'success'})
        original = self.run_plan(dispatcher)
        self.policy.write_text(json.dumps({'workers': [
            {'id': 'claude', 'requested_model': 'haiku'}]}), encoding='utf-8')
        repeated = self.run_plan(dispatcher)
        self.assertEqual(repeated['job_id'], original['job_id'])
        self.assertEqual(repeated['status'], 'recovery_required')
        self.assertEqual(repeated['reservation_state'], 'held_for_reconciliation')
        self.assertEqual(len(dispatcher.started), 1)

    def test_tampered_contract_is_not_used_to_dispatch(self):
        self.args.claude_model = None
        dispatcher = fixtures.RecordedDispatcher()
        self.run_plan(dispatcher)
        path = next((self.root / 'handoffs').glob('*.json'))
        plan = json.loads(path.read_text())
        plan['contract']['claude_model'] = 'haiku'
        path.write_text(json.dumps(plan), encoding='utf-8')
        self.assertTrue(self.run_plan(dispatcher)['handoff_blocked'])
        self.assertEqual(len(dispatcher.started), 2)


if __name__ == '__main__':
    unittest.main()
