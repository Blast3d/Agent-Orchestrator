from copy import deepcopy
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'app'))

import team_planner as planner
from usage_guard import DEFAULT_POLICY, Guard


def quota_fixture(remaining=80, shared=False):
    now = datetime.now(timezone.utc)
    policy = deepcopy(DEFAULT_POLICY)
    policy['worker_pools'] = {'claude': ['claude-main'], 'grok': ['claude-main' if shared else 'grok-main']}
    windows = {key: {'id': key, 'remaining_pct': remaining, 'observed_at': now.isoformat(),
                     'max_age_seconds': 600, 'source': 'synthetic quota observation',
                     'reset_at': (now + timedelta(hours=1)).isoformat()}
               for keys in policy['worker_pools'].values() for key in keys}
    return policy, {'windows': windows, 'reservations': {}, 'cooldowns': {}, 'refresh_errors': {}}


def record(**updates):
    result = {'worker': 'claude', 'execution_status': 'succeeded',
              'created_at': '2026-09-09T01:00:00+00:00', 'started_at': '2026-09-09T01:00:04+00:00',
              'ended_at': '2026-09-09T01:00:14+00:00', 'finalized_at': '2026-09-09T01:00:17+00:00',
              'execution_progress': {'first_event_s': 1.0, 'first_answer_s': 5.0}}
    result.update(updates)
    return result


class TeamPlanningTests(unittest.TestCase):
    def test_simple_keeps_lead_without_launch_or_quota_requirement(self):
        plan = planner.recommend('simple', 9, 4)
        self.assertEqual(plan['status'], 'solo')
        self.assertEqual(plan['total_roles_including_lead'], 1)
        self.assertEqual(plan['first_wave_workers'], 0)

    def test_large_audit_uses_waves_and_counts_reviewers_separately(self):
        plan = planner.recommend('audit', 6, 4, {'capacity': 4, 'status': 'ready'})
        self.assertEqual(plan['producer_assignments'], 6)
        self.assertEqual(plan['reviewer_assignments'], 2)
        self.assertEqual(plan['total_worker_assignments'], 8)
        self.assertEqual(plan['total_roles_including_lead'], 9)
        self.assertEqual(plan['planned_peak_simultaneous_workers'], 4)
        self.assertEqual(plan['planned_waves'], 3)

    def test_dependency_count_limits_makers_and_cap_limits_all_workers(self):
        plan = planner.recommend('implementation', 1, 1, {'capacity': 1})
        self.assertEqual(plan['producer_assignments'], 1)
        self.assertEqual(plan['first_wave_workers'], 1)
        self.assertEqual(plan['planned_peak_simultaneous_workers'], 1)
        self.assertEqual(plan['planned_waves'], 2)

    def test_partial_and_unknown_quota_are_not_silent_permission(self):
        plan = planner.recommend('research', 4, 4, {'capacity': 2, 'status': 'ready'})
        self.assertEqual(plan['status'], 'limited')
        self.assertEqual(plan['quota_limited_peak_workers'], 2)
        held = planner.recommend('research', 4, 4)
        self.assertEqual(held['status'], 'held')
        self.assertEqual(held['quota_state'], 'unknown')

    def test_invalid_inputs_fail(self):
        for kind, streams, cap in [('missing', 1, 4), ('audit', 0, 4), ('audit', True, 4),
                                   ('audit', 1, 11), ('audit', 1, False)]:
            with self.assertRaises(ValueError):
                planner.recommend(kind, streams, cap)

    def test_quota_simulation_respects_shared_pools_and_does_not_mutate(self):
        policy, data = quota_fixture(25, shared=True)
        before = deepcopy(data)
        capacity = planner.quota_capacity(policy, data, ('claude', 'grok'), 'small', 8)
        # 25 -> 22 -> 19; low quota is held for planning even though one more
        # small task could fit the guard's 10-percent floor.
        self.assertEqual(capacity['capacity'], 2)
        self.assertEqual(data, before)
        self.assertEqual(capacity['allocations'], ['claude', 'grok'])

    def test_active_reservations_reduce_capacity(self):
        policy, data = quota_fixture(30)
        data['reservations']['existing'] = {'pools': ['claude-main'], 'estimate_pct': 10, 'finished_at': None}
        result = planner.quota_capacity(policy, data, ('claude',), 'small', 4)
        self.assertEqual(result['capacity'], 0)
        self.assertEqual(result['status'], 'held')

    def test_missing_stale_and_failed_quota_are_unknown_not_zero_allowance(self):
        policy, data = quota_fixture()
        missing = deepcopy(data)
        missing['windows'] = {}
        stale = deepcopy(data)
        stale['windows']['claude-main']['observed_at'] = '2020-01-01T00:00:00+00:00'
        failed = deepcopy(data)
        failed['refresh_errors']['claude'] = {'at': (datetime.now(timezone.utc) + timedelta(seconds=1)).isoformat()}
        for sample in (missing, stale, failed):
            result = planner.quota_capacity(policy, sample, ('claude',), 'small', 4)
            self.assertEqual(result['capacity'], 0)
            self.assertEqual(result['status'], 'unknown')

    def test_known_exhaustion_is_held(self):
        policy, data = quota_fixture(0)
        result = planner.quota_capacity(policy, data, ('claude',), 'small', 4)
        self.assertEqual(result['capacity'], 0)
        self.assertEqual(result['status'], 'held')

    def test_local_models_and_unknown_workers_are_never_planned(self):
        for workers in (('local-chat',), ('anything',), ()):
            with self.assertRaises(ValueError):
                planner.quota_capacity(*quota_fixture(), workers)


class TimingTests(unittest.TestCase):
    def test_timing_separates_stages_and_does_not_invent_queue(self):
        safe = planner.sanitize_record(record())
        self.assertEqual(safe['metrics']['preparation_seconds'], 4)
        self.assertEqual(safe['metrics']['execution_seconds'], 10)
        self.assertEqual(safe['metrics']['postprocessing_seconds'], 3)
        self.assertEqual(safe['metrics']['total_seconds'], 17)
        self.assertIsNone(safe['metrics']['queue_wait_seconds'])

    def test_imported_completed_artifact_is_not_zero_second_inference(self):
        safe = planner.sanitize_record(record(imported_completed_artifact=True, started_at=None))
        self.assertFalse(safe['timed_success'])
        self.assertTrue(all(value is None for value in safe['metrics'].values()))

    def test_reconciliation_excludes_delayed_finalization(self):
        safe = planner.sanitize_record(record(reconciled_at='2026-09-10T01:00:00+00:00'))
        self.assertEqual(safe['metrics']['execution_seconds'], 10)
        self.assertIsNone(safe['metrics']['postprocessing_seconds'])
        self.assertIsNone(safe['metrics']['total_seconds'])

    def test_bad_negative_naive_and_nonfinite_timings_stay_unknown(self):
        safe = planner.sanitize_record(record(ended_at='2026-09-09T00:00:00+00:00',
            finalized_at='2026-09-09T01:00:17', execution_progress={'first_answer_s': float('nan'), 'first_event_s': True}))
        self.assertIsNone(safe['metrics']['execution_seconds'])
        self.assertIsNone(safe['metrics']['total_seconds'])
        self.assertIsNone(safe['metrics']['first_event_seconds'])
        self.assertIsNone(safe['metrics']['first_answer_seconds'])

    def test_private_content_is_discarded_before_aggregation(self):
        safe = planner.sanitize_record(record(task='PRIVATE_TASK', prompt='PRIVATE_PROMPT',
            response='PRIVATE_RESPONSE', memory_context={'context': 'PRIVATE_MEMORY'},
            execution_progress={'first_event_s': 2, 'partial_response': 'PRIVATE_PARTIAL'}))
        self.assertNotIn('PRIVATE_', json.dumps(safe))

    def test_history_uses_only_indexes_and_null_for_unknown_provider_timing(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            for index, value in enumerate((record(), record(worker='codex', imported_completed_artifact=True, started_at=None))):
                folder = root / str(index)
                folder.mkdir()
                (folder / 'record.json').write_text(json.dumps(value), encoding='utf-8')
                (folder / 'result.json').write_text('THIS MUST NOT BE PARSED', encoding='utf-8')
            result = planner.timing_history(root)
            self.assertEqual(result['records_read'], 2)
            self.assertEqual(result['by_provider']['claude']['latency']['execution_seconds']['median'], 10)
            self.assertIsNone(result['by_provider']['codex']['latency']['execution_seconds']['median'])

    def test_malformed_and_oversized_indexes_have_a_total_read_budget(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            for index in range(4):
                folder = root / str(index)
                folder.mkdir()
                (folder / 'record.json').write_text('x' * 100, encoding='utf-8')
            with patch.object(planner, 'MAX_RECORD_BYTES', 20), patch.object(planner, 'MAX_HISTORY_BYTES', 30):
                result = planner.timing_history(root)
            self.assertLessEqual(result['bytes_read'], 30)
            self.assertEqual(result['records_read'], 0)
            self.assertTrue(result['history_truncated'])

    def test_history_limit_and_no_history_are_explicit(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            for index in range(2):
                folder = root / str(index)
                folder.mkdir()
                (folder / 'record.json').write_text(json.dumps(record()), encoding='utf-8')
            result = planner.timing_history(root, 1)
            self.assertTrue(result['history_truncated'])
            self.assertEqual(result['records_read'], 1)
            self.assertEqual(planner.timing_history(root, 0)['records_read'], 0)

    def test_planning_report_writes_nothing_and_never_calls_providers(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            with patch.object(Guard, '__init__', side_effect=AssertionError('no constructor')), \
                 patch.object(Guard, 'check', side_effect=AssertionError('no reservation')), \
                 patch.object(Guard, 'refresh', side_effect=AssertionError('no network')):
                result = planner.build_report(root, task_type='audit', independent_workstreams=6)
            self.assertEqual(result['plan']['status'], 'held')
            self.assertEqual(result['quota']['status'], 'unknown')
            self.assertEqual(list(root.iterdir()), [])
            self.assertIn('unknown is not a measured zero allowance', planner.render(result))


if __name__ == '__main__':
    unittest.main()
