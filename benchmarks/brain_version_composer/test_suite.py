"""Offline controller gates; synthetic receipts only, no contestant calls."""
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch


HERE = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location('composer_suite_under_test', HERE / 'suite.py')
suite = importlib.util.module_from_spec(spec)
spec.loader.exec_module(suite)


class ControllerTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.logs = self.root / 'logs'
        self.logs.mkdir()
        self.lead_id = '11111111-1111-1111-1111-111111111111'
        self.sol_id = '22222222-2222-2222-2222-222222222222'
        self.terra_id = '33333333-3333-3333-3333-333333333333'
        self.prep_id = '44444444-4444-4444-4444-444444444444'
        self.frozen = self.root / 'frozen'
        for relative in suite.OWNED.values():
            path = self.frozen / 'fixture' / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text('# Synthetic test placeholder\n', encoding='utf-8')
        (self.frozen / 'fixture/README.md').write_text('Public test documentation\n', encoding='utf-8')
        (self.frozen / 'worker-guide.md').write_text('Synthetic bounded test guide\n', encoding='utf-8')
        for mode in ('old', 'new'):
            suite.save(self.frozen / (mode + '-recall.json'), {
                'searches': [{'max_chars': 3000, 'context': 'Frozen synthetic memory packet\n'}]})
        self.manifest = {
            'created_at': '2026-10-26T00:00:00+00:00', 'preparation_session': self.prep_id,
            'models': suite.MODELS, 'workers': suite.WORKERS, 'reasoning_effort': 'high',
            'order': list(suite.ORDER), 'public_feedback_limit': 2, 'context_chars_limit': 3000,
            'frozen_hashes': suite.tree_hashes(self.frozen), 'harness_sha256': suite.sha(suite.__file__),
        }
        suite.save(self.root / 'manifest.json', self.manifest)
        (self.root / 'manifest.sha256').write_text(suite.sha(self.root / 'manifest.json'), encoding='utf-8')
        for condition in suite.ORDER:
            (self.root / 'trials' / condition).mkdir(parents=True)
        self.lead = self.receipt(self.lead_id, suite.MODELS['astra'],
                                 created='2026-10-27T00:00:00+00:00')
        self.receipt(self.sol_id, suite.MODELS['sol'])
        self.receipt(self.terra_id, suite.MODELS['terra'])
        self.current = patch.object(suite, 'current_evidence', side_effect=lambda: self.lead)
        self.current.start()
        self.addCleanup(self.current.stop)
        log_patch = patch.object(suite, 'session_log', side_effect=lambda session: self.logs / (session + '.jsonl'))
        log_patch.start()
        self.addCleanup(log_patch.stop)
        clock = patch.object(suite, 'now', return_value='2026-10-27T00:01:00+00:00')
        clock.start()
        self.addCleanup(clock.stop)

    def receipt(self, session, model, *, created='2026-10-27T00:02:00+00:00',
                effort='high', fork=None, parent=None, meta_id=None, usage=None):
        parent = self.lead_id if parent is None else parent
        source = 'vscode' if session == self.lead_id else {
            'subagent': {'thread_spawn': {'parent_thread_id': parent, 'depth': 1}}}
        rows = [
            {'type': 'session_meta', 'payload': {
                'id': meta_id or session, 'timestamp': created, 'forked_from_id': fork, 'source': source}},
            {'type': 'turn_context', 'timestamp': created, 'payload': {
                'turn_id': session + '-turn-1', 'model': model, 'effort': effort}},
        ]
        if usage is not None:
            rows.append({'type': 'event_msg', 'payload': {
                'type': 'token_count', 'info': {'total_token_usage': usage}}})
        path = self.logs / (session + '.jsonl')
        path.write_text(''.join(json.dumps(row) + '\n' for row in rows), encoding='utf-8')
        return suite.session_evidence(path)

    def begin_trial(self):
        suite.begin(self.root, 'astra-old')
        return self.root / 'trials/astra-old'

    def finish_trial(self):
        directory = self.root / 'trials/astra-old'
        (directory / 'plan.md').write_text('Synthetic plan\n', encoding='utf-8')
        suite.save(directory / 'review.json', {
            'sol_session': self.sol_id, 'terra_session': self.terra_id,
            'limitations': ['Synthetic test; no model execution'],
        })
        with patch.object(suite, 'grade', return_value={'passed': 0, 'total': 0}):
            return suite.finish(self.root, 'astra-old', self.sol_id, self.terra_id)

    def test_wrong_lead_version_is_refused(self):
        self.lead['turns'][0]['model'] = 'gpt-6-sol'
        with self.assertRaises(ValueError):
            self.begin_trial()

    def test_valid_trial_records_fresh_workers_and_unknown_usage(self):
        self.begin_trial()
        result = self.finish_trial()
        self.assertEqual(result['condition'], 'astra-old')
        saved = suite.read(self.root / 'trials/astra-old/result.json')
        self.assertEqual([worker['session_id'] for worker in saved['workers']],
                         [self.sol_id, self.terra_id])
        self.assertIsNone(saved['lead_reported_usage_delta'])

    def test_subagent_cannot_impersonate_native_lead(self):
        self.lead['source'] = {'subagent': {'thread_spawn': {'parent_thread_id': self.prep_id}}}
        with self.assertRaises(ValueError):
            self.begin_trial()

    def test_wrong_lead_effort_is_refused(self):
        self.lead['turns'][0]['effort'] = 'medium'
        with self.assertRaises(ValueError):
            self.begin_trial()

    def test_midtrial_switch_and_switch_back_is_refused(self):
        self.begin_trial()
        self.lead['turns'].extend([
            {'turn_id': 'wrong-turn', 'model': 'gpt-6-sol', 'effort': 'high'},
            {'turn_id': 'restored-turn', 'model': suite.MODELS['astra'], 'effort': 'high'},
        ])
        with self.assertRaises(ValueError):
            suite.active(self.root, 'astra-old')

    def test_preparation_session_is_refused(self):
        self.lead['session_id'] = self.prep_id
        with self.assertRaises(ValueError):
            self.begin_trial()

    def test_forked_session_is_refused(self):
        self.lead['forked_from_id'] = self.prep_id
        with self.assertRaises(ValueError):
            self.begin_trial()

    def test_reused_lead_session_is_refused(self):
        previous = self.root / 'trials/astra-old'
        suite.save(previous / 'begin.json', {'session': self.lead})
        suite.save(previous / 'result.json', {'synthetic': True})
        with self.assertRaises(ValueError):
            suite.begin(self.root, 'astra-new')

    def test_stale_session_is_refused(self):
        self.lead['created_at'] = '2026-10-25T00:00:00+00:00'
        with self.assertRaises(ValueError):
            self.begin_trial()

    def test_post_freeze_session_with_prior_conversation_is_not_fresh(self):
        self.lead['turns'].append({
            'turn_id': 'second-user-turn', 'model': suite.MODELS['astra'], 'effort': 'high',
            'timestamp': '2026-10-27T00:00:30+00:00'})
        with self.assertRaises(ValueError):
            self.begin_trial()

    def test_frozen_input_drift_is_refused(self):
        (self.frozen / 'fixture/README.md').write_text('Changed frozen input', encoding='utf-8')
        with self.assertRaises(ValueError):
            suite.verified(self.root)

    def test_manifest_drift_is_refused(self):
        path = self.root / 'manifest.json'
        path.write_text(path.read_text(encoding='utf-8') + ' ', encoding='utf-8')
        with self.assertRaises(ValueError):
            suite.verified(self.root)

    def test_unowned_module_edit_is_refused(self):
        directory = self.begin_trial()
        (directory / 'workspace/README.md').write_text('Changed candidate documentation', encoding='utf-8')
        with self.assertRaises(ValueError):
            self.finish_trial()

    def test_extra_workspace_file_is_refused(self):
        directory = self.begin_trial()
        (directory / 'workspace/extra.py').write_text('# Extra candidate file\n', encoding='utf-8')
        with self.assertRaises(ValueError):
            self.finish_trial()

    def test_wrong_worker_version_is_refused(self):
        self.begin_trial()
        self.receipt(self.sol_id, 'gpt-6-sol')
        with self.assertRaises(ValueError):
            self.finish_trial()

    def test_worker_switch_is_refused(self):
        self.begin_trial()
        path = self.logs / (self.sol_id + '.jsonl')
        with path.open('a', encoding='utf-8') as handle:
            handle.write(json.dumps({'type': 'turn_context', 'payload': {
                'turn_id': 'switched-worker-turn', 'model': 'gpt-6-sol', 'effort': 'high'}}) + '\n')
        with self.assertRaises(ValueError):
            self.finish_trial()

    def test_missing_usage_remains_unknown(self):
        self.assertIsNone(self.lead['usage'])
        self.assertIsNone(suite.usage_delta(None, {'input_tokens': 5}))
        self.assertIsNone(suite.usage_delta({'input_tokens': 5}, None))
        self.assertIsNone(suite.usage_delta({}, {}))

    def test_counter_reset_is_unknown(self):
        self.assertIsNone(suite.usage_delta({'input_tokens': 10}, {'input_tokens': 9}))

    def test_partial_usage_does_not_invent_missing_fields(self):
        self.assertEqual(suite.usage_delta({'input_tokens': 10, 'output_tokens': None},
                                          {'input_tokens': 12, 'output_tokens': 7}),
                         {'input_tokens': 2})

    def test_unrelated_worker_parent_is_refused(self):
        self.begin_trial()
        self.receipt(self.sol_id, suite.MODELS['sol'], parent=self.prep_id)
        with self.assertRaises(ValueError):
            self.finish_trial()

    def test_worker_receipt_id_must_match_requested_id(self):
        self.begin_trial()
        self.receipt(self.sol_id, suite.MODELS['sol'], meta_id=self.prep_id)
        with self.assertRaises(ValueError):
            self.finish_trial()

    def test_native_worker_parent_receipt_is_preserved(self):
        evidence = suite.session_evidence(self.logs / (self.sol_id + '.jsonl'))
        self.assertEqual(suite.worker_parent(evidence), self.lead_id)

    def test_worker_from_finished_condition_cannot_be_reused(self):
        self.begin_trial()
        suite.save(self.root / 'trials/terra-new/result.json', {
            'workers': [{'session_id': self.sol_id}, {'session_id': self.terra_id}]})
        with self.assertRaises(ValueError):
            self.finish_trial()

    def test_changed_memory_delivery_is_refused(self):
        directory = self.begin_trial()
        (directory / 'memory-context.md').write_text('Altered treatment packet', encoding='utf-8')
        with self.assertRaises(ValueError):
            self.finish_trial()

    def test_worker_older_in_actual_time_with_offset_is_refused(self):
        self.begin_trial()
        self.receipt(self.sol_id, suite.MODELS['sol'], created='2026-10-27T01:00:00+02:00')
        with self.assertRaises(ValueError):
            self.finish_trial()

    def test_boolean_usage_is_unknown(self):
        self.assertIsNone(suite.usage_delta({'input_tokens': False}, {'input_tokens': True}))


if __name__ == '__main__':
    unittest.main()
