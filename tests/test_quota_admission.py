import copy
import math
import unittest
from datetime import datetime, timedelta, timezone

from app.quota_admission import evaluate_advisory


NOW = datetime(2026, 9, 9, 12, 0, tzinfo=timezone.utc)


def _policy(**overrides):
    base = {
        'worker_pools': {
            'coder': ['openai-codex', 'agy:pro'],
            'local-chat': [],
        },
        'estimates_pct': {'small': 5, 'large': 15},
        'floor_pct': 10,
        'warning_pct': 25,
        'worker_start_threshold_pct': 20,
    }
    base.update(overrides)
    return base


def _window(remaining, observed=None, max_age=3600, reset_at=None, source='provider'):
    return {
        'remaining_pct': remaining,
        'observed_at': (observed or (NOW - timedelta(seconds=30))).isoformat().replace('+00:00', 'Z'),
        'max_age_seconds': max_age,
        'reset_at': reset_at,
        'reset_display': 'soon' if reset_at else None,
        'source': source,
    }


def _data(windows=None, reservations=None, cooldowns=None, refresh_errors=None, worker_pools=None):
    payload = {
        'windows': windows or {},
        'reservations': reservations or {},
        'cooldowns': cooldowns or {},
        'refresh_errors': refresh_errors or {},
    }
    if worker_pools is not None:
        payload['worker_pools'] = worker_pools
    return payload


class AdvisoryAdmissionTests(unittest.TestCase):
    def test_boundary_exactly_20_holds_2001_allows(self):
        data = _data({'openai-codex': _window(20.0), 'agy:pro': _window(50)})
        held = evaluate_advisory(_policy(), data, 'coder', 'small', NOW)
        self.assertFalse(held['allowed'])
        self.assertEqual(held['status'], 'held')
        self.assertIn(
            'openai-codex: available allowance is at or below the worker start threshold',
            held['reasons'],
        )
        data_ok = _data({'openai-codex': _window(20.01), 'agy:pro': _window(50)})
        allowed = evaluate_advisory(_policy(), data_ok, 'coder', 'small', NOW)
        self.assertTrue(allowed['allowed'])
        self.assertEqual(allowed['status'], 'ready')
        self.assertEqual(allowed['reasons'], [])
        self.assertEqual(allowed['admission_mode'], 'advisory')
        self.assertEqual(allowed['threshold_pct'], 20)

    def test_fresh_and_stale_failed_refresh_above_threshold_allow(self):
        fresh = _window(70, NOW - timedelta(seconds=10), max_age=3600)
        stale = _window(70, NOW - timedelta(hours=5), max_age=60)
        data = _data(
            {'openai-codex': fresh, 'agy:pro': stale},
            refresh_errors={'antigravity': {'at': NOW.isoformat().replace('+00:00', 'Z'), 'error': 'timeout'}},
        )
        result = evaluate_advisory(_policy(), data, 'coder', 'small', NOW)
        self.assertTrue(result['allowed'])
        self.assertEqual(result['status'], 'ready')
        self.assertEqual(result['reasons'], [])
        self.assertEqual(result['reading_status'], 'cached')
        self.assertTrue(any('stale' in w for w in result['warnings']))
        self.assertTrue(any('refresh failed' in w for w in result['warnings']))
        self.assertIn('antigravity', result['provider_refresh_errors'])
        by_id = {row['id']: row for row in result['windows']}
        self.assertEqual(by_id['openai-codex']['freshness'], 'fresh')
        self.assertEqual(by_id['agy:pro']['freshness'], 'cached')

    def test_mixed_pools_one_below_threshold_holds_but_returns_windows(self):
        data = _data({'openai-codex': _window(70), 'agy:pro': _window(10)})
        result = evaluate_advisory(_policy(), data, 'coder', 'small', NOW)
        self.assertFalse(result['allowed'])
        self.assertEqual(result['status'], 'held')
        self.assertEqual(len(result['windows']), 2)
        self.assertIn(
            'agy:pro: available allowance is at or below the worker start threshold',
            result['reasons'],
        )

    def test_missing_pool_unknown_allows_without_fabricated_percent(self):
        data = _data({'openai-codex': _window(80)})
        result = evaluate_advisory(_policy(), data, 'coder', 'small', NOW)
        self.assertTrue(result['allowed'])
        self.assertEqual(result['status'], 'ready')
        self.assertTrue(any('agy:pro: usage unknown' in w for w in result['warnings']))
        ids = [row['id'] for row in result['windows']]
        self.assertNotIn('agy:pro', ids)
        self.assertTrue(all('available_pct' in row for row in result['windows']))

    def test_unknown_worker_without_pools_is_not_local_except_local_chat(self):
        policy = _policy(worker_pools={'coder': [], 'local-chat': []})
        coder = evaluate_advisory(policy, _data(), 'coder', 'small', NOW)
        self.assertTrue(coder['allowed'])
        self.assertEqual(coder['status'], 'ready')
        self.assertEqual(coder['reading_status'], 'unknown')
        self.assertNotEqual(coder['status'], 'local')
        local = evaluate_advisory(policy, _data(), 'local-chat', 'small', NOW)
        self.assertEqual(local['status'], 'local')
        self.assertEqual(local['estimate_pct'], 0)

    def test_reset_passed_old_zero_does_not_threshold(self):
        reset_at = (NOW - timedelta(minutes=1)).isoformat().replace('+00:00', 'Z')
        data = _data({
            'openai-codex': _window(0, reset_at=reset_at),
            'agy:pro': _window(80),
        })
        result = evaluate_advisory(_policy(), data, 'coder', 'small', NOW)
        self.assertTrue(result['allowed'])
        self.assertEqual(result['status'], 'ready')
        by_id = {row['id']: row for row in result['windows']}
        self.assertTrue(by_id['openai-codex']['reset_passed'])
        self.assertEqual(by_id['openai-codex']['remaining_pct'], 0.0)
        self.assertTrue(any('reset boundary passed' in w for w in result['warnings']))
        self.assertEqual(result['reasons'], [])

    def test_active_cooldown_missing_window_still_holds(self):
        until = (NOW + timedelta(minutes=5)).isoformat().replace('+00:00', 'Z')
        data = _data(
            windows={'openai-codex': _window(90)},
            cooldowns={'agy:pro': {'until': until}},
        )
        result = evaluate_advisory(_policy(), data, 'coder', 'small', NOW)
        self.assertFalse(result['allowed'])
        self.assertEqual(result['status'], 'held')
        self.assertIn('agy:pro: provider rejected work; cooldown active', result['reasons'])
        self.assertEqual(len(result['windows']), 1)

    def test_cooldown_checked_when_worker_pool_list_empty(self):
        until = (NOW + timedelta(minutes=5)).isoformat().replace('+00:00', 'Z')
        policy = _policy(worker_pools={'coder': [], 'local-chat': []})
        data = _data(cooldowns={'openai-codex': {'until': until}})
        result = evaluate_advisory(policy, data, 'coder', 'small', NOW)
        self.assertTrue(result['allowed'])  # An unrelated provider cannot block this worker.
        policy['worker_pools']['openai'] = []
        held = evaluate_advisory(policy, data, 'openai', 'small', NOW)
        self.assertFalse(held['allowed'])
        self.assertIn('openai-codex', held['cooldown_active_pools'])
        self.assertTrue(evaluate_advisory(policy, data, 'local-chat', 'small', NOW)['allowed'])

    def test_reservation_reduces_available_and_can_hold(self):
        data = _data(
            {'openai-codex': _window(30), 'agy:pro': _window(90)},
            reservations={'r1': {'pools': ['openai-codex'], 'estimate_pct': 15}},
        )
        result = evaluate_advisory(_policy(), data, 'coder', 'small', NOW)
        by_id = {row['id']: row for row in result['windows']}
        self.assertEqual(by_id['openai-codex']['reserved_pct'], 15.0)
        self.assertEqual(by_id['openai-codex']['available_pct'], 15.0)
        self.assertFalse(result['allowed'])
        self.assertIn(
            'openai-codex: available allowance is at or below the worker start threshold',
            result['reasons'],
        )

    def test_negative_availability_clamped_to_zero(self):
        data = _data(
            {'openai-codex': _window(5), 'agy:pro': _window(90)},
            reservations={'r1': {'pools': ['openai-codex'], 'estimate_pct': 40}},
        )
        result = evaluate_advisory(_policy(), data, 'coder', 'small', NOW)
        by_id = {row['id']: row for row in result['windows']}
        self.assertEqual(by_id['openai-codex']['available_pct'], 0.0)

    def test_malformed_nan_date_and_cooldown_raise(self):
        with self.assertRaises(ValueError):
            evaluate_advisory(
                _policy(),
                _data({'openai-codex': _window(float('nan')), 'agy:pro': _window(50)}),
                'coder',
                'small',
                NOW,
            )
        bad_date = _window(70)
        bad_date['observed_at'] = 'not-a-date'
        with self.assertRaises(ValueError):
            evaluate_advisory(_policy(), _data({'openai-codex': bad_date, 'agy:pro': _window(50)}), 'coder', 'small', NOW)
        with self.assertRaises(ValueError):
            evaluate_advisory(
                _policy(),
                _data(
                    {'openai-codex': _window(70), 'agy:pro': _window(50)},
                    cooldowns={'openai-codex': {'until': 'bogus'}},
                ),
                'coder',
                'small',
                NOW,
            )
        with self.assertRaises(ValueError):
            evaluate_advisory(
                _policy(),
                _data(
                    {'openai-codex': _window(70), 'agy:pro': _window(50)},
                    reservations={'r1': {'pools': ['openai-codex'], 'estimate_pct': math.inf}},
                ),
                'coder',
                'small',
                NOW,
            )

    def test_does_not_mutate_data(self):
        data = _data(
            {'openai-codex': _window(70), 'agy:pro': _window(40)},
            reservations={'r1': {'pools': ['openai-codex'], 'estimate_pct': 1}},
        )
        snapshot = copy.deepcopy(data)
        evaluate_advisory(_policy(), data, 'coder', 'small', NOW)
        self.assertEqual(data, snapshot)

    def test_does_not_apply_legacy_floor_against_new_task_estimate(self):
        # 21 available, estimate 15, legacy floor 10 would have blocked (21-15 < 10).
        data = _data({'openai-codex': _window(21), 'agy:pro': _window(80)})
        result = evaluate_advisory(_policy(), data, 'coder', 'large', NOW)
        self.assertTrue(result['allowed'])
        self.assertEqual(result['estimate_pct'], 15)
        self.assertEqual(result['reasons'], [])

    def test_unknown_worker_and_size_rejected(self):
        with self.assertRaises(ValueError):
            evaluate_advisory(_policy(), _data(), 'nope', 'small', NOW)
        with self.assertRaises(ValueError):
            evaluate_advisory(_policy(), _data(), 'coder', 'tiny', NOW)

    def test_naive_current_time_rejected(self):
        with self.assertRaises(ValueError):
            evaluate_advisory(_policy(), _data(), 'coder', 'small', datetime(2026, 9, 9, 12, 0))

    def test_malformed_values_are_not_unknown_allowance(self):
        for bad in ({}, None, False, {'remaining_pct': 70}):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                evaluate_advisory(_policy(), _data({'openai-codex': bad}), 'coder', 'small', NOW)
        for remaining in (-1, 101, True, '70'):
            with self.subTest(remaining=remaining), self.assertRaises(ValueError):
                evaluate_advisory(_policy(), _data({'openai-codex': _window(remaining)}), 'coder', 'small', NOW)
        with self.assertRaises(ValueError):
            evaluate_advisory(_policy(), _data(reservations={'bad': {'pools': [], 'estimate_pct': -10}}), 'coder', 'small', NOW)

    def test_missing_or_reset_pool_marks_current_reading_unknown(self):
        data = _data({'openai-codex': _window(70)})
        self.assertEqual(evaluate_advisory(_policy(), data, 'coder', 'small', NOW)['reading_status'], 'unknown')
        data['windows']['agy:pro'] = _window(0, reset_at=(NOW - timedelta(seconds=1)).isoformat())
        result = evaluate_advisory(_policy(), data, 'coder', 'small', NOW)
        self.assertTrue(result['allowed'])
        self.assertEqual(result['reading_status'], 'unknown')

    def test_empty_local_mapping_does_not_inherit_configured_foreign_cooldowns(self):
        policy = _policy(worker_pools={'local-chat':['agy:pro']})
        data = _data(worker_pools={'local-chat':[]}, cooldowns={'agy:pro': {'until': (NOW + timedelta(minutes=2)).isoformat()}})
        result = evaluate_advisory(policy, data, 'local-chat', 'small', NOW)
        self.assertTrue(result['allowed'])
        self.assertEqual(result['status'], 'local')
        self.assertEqual(result['cooldown_active_pools'], {})

    def test_state_timestamps_require_serializable_strings(self):
        with self.assertRaises(ValueError):
            evaluate_advisory(_policy(), _data(cooldowns={'agy:pro':{'until':NOW}}), 'coder', 'small', NOW)

    def test_finished_reservations_only_count_until_a_later_observation(self):
        data = _data({'openai-codex': _window(40), 'agy:pro': _window(80)}, reservations={
            'old': {'pools': ['openai-codex'], 'estimate_pct': 30, 'finished_at': (NOW - timedelta(minutes=2)).isoformat()},
            'pending': {'pools': ['openai-codex'], 'estimate_pct': 3, 'finished_at': NOW.isoformat()}})
        result = evaluate_advisory(_policy(), data, 'coder', 'small', NOW)
        self.assertEqual(result['windows'][0]['available_pct'], 37)


def _ts(moment):
    return moment.isoformat().replace('+00:00', 'Z')


def _period_policy(pools):
    return _policy(worker_pools={'coder': pools, 'local-chat': []})


class ReservationPeriodTests(unittest.TestCase):
    """Estimates must not outlive the window they were charged to or a stale reading."""

    def test_finished_work_after_a_stale_reading_is_reported_but_does_not_hold(self):
        stale = _window(99, NOW - timedelta(hours=40), max_age=600, reset_at=_ts(NOW + timedelta(days=5)))
        finished = {f'r{i}': {'pools': ['codex-weekly'], 'estimate_pct': 3,
                              'created_at': _ts(NOW - timedelta(hours=30)),
                              'finished_at': _ts(NOW - timedelta(hours=29))} for i in range(27)}
        result = evaluate_advisory(_period_policy(['codex-weekly']), _data({'codex-weekly': stale}, finished),
                                   'coder', 'small', NOW)
        self.assertTrue(result['allowed'])
        window = result['windows'][0]
        self.assertEqual(window['reserved_pct'], 0)
        self.assertEqual(window['unsettled_finished_pct'], 81)
        self.assertEqual(window['available_pct'], 99)
        self.assertTrue(any('not counted' in warning for warning in result['warnings']))

    def test_pending_work_still_holds_on_a_stale_reading(self):
        stale = _window(99, NOW - timedelta(hours=40), max_age=600, reset_at=_ts(NOW + timedelta(days=5)))
        pending = {'r1': {'pools': ['codex-weekly'], 'estimate_pct': 81,
                          'created_at': _ts(NOW - timedelta(hours=1)), 'finished_at': None}}
        result = evaluate_advisory(_period_policy(['codex-weekly']), _data({'codex-weekly': stale}, pending),
                                   'coder', 'small', NOW)
        self.assertFalse(result['allowed'])
        self.assertEqual(result['windows'][0]['pending_pct'], 81)

    def test_finished_work_after_a_fresh_reading_still_counts(self):
        fresh = _window(30, NOW - timedelta(seconds=30), max_age=600)
        finished = {'r1': {'pools': ['codex-weekly'], 'estimate_pct': 15,
                           'created_at': _ts(NOW - timedelta(minutes=5)), 'finished_at': _ts(NOW - timedelta(seconds=10))}}
        result = evaluate_advisory(_period_policy(['codex-weekly']), _data({'codex-weekly': fresh}, finished),
                                   'coder', 'small', NOW)
        self.assertFalse(result['allowed'])
        self.assertEqual(result['windows'][0]['reserved_pct'], 15)

    def test_reservations_created_before_the_current_window_do_not_count(self):
        window = _window(60, reset_at=_ts(NOW + timedelta(hours=2)))
        old = {'r1': {'pools': ['claude-five-hour'], 'estimate_pct': 45, 'outcome': 'recovery_hold',
                      'created_at': _ts(NOW - timedelta(days=20)), 'finished_at': None}}
        result = evaluate_advisory(_period_policy(['claude-five-hour']), _data({'claude-five-hour': window}, old),
                                   'coder', 'small', NOW)
        self.assertTrue(result['allowed'])
        self.assertEqual(result['windows'][0]['reserved_pct'], 0)
        current = {'r1': dict(old['r1'], created_at=_ts(NOW - timedelta(hours=1)))}
        held = evaluate_advisory(_period_policy(['claude-five-hour']), _data({'claude-five-hour': window}, current),
                                 'coder', 'small', NOW)
        self.assertFalse(held['allowed'])

    def test_window_period_bounds_reservations_without_a_reset_time(self):
        for key, old_age, recent_age in (('grok-weekly', timedelta(days=8), timedelta(days=2)),
                                         ('agy:gemini-weekly', timedelta(days=8), timedelta(days=2)),
                                         ('notebooklm-chat-daily', timedelta(hours=25), timedelta(hours=2)),
                                         ('codex-provider-block', timedelta(days=10), timedelta(days=6))):
            with self.subTest(key=key):
                data = _data({key: _window(30)}, {'r1': {'pools': [key], 'estimate_pct': 16,
                                                          'created_at': _ts(NOW - old_age), 'finished_at': None}})
                self.assertTrue(evaluate_advisory(_period_policy([key]), data, 'coder', 'small', NOW)['allowed'])
                data['reservations']['r1']['created_at'] = _ts(NOW - recent_age)
                self.assertFalse(evaluate_advisory(_period_policy([key]), data, 'coder', 'small', NOW)['allowed'])

    def test_passed_reset_drops_earlier_reservations_and_keeps_later_ones(self):
        window = _window(10, NOW - timedelta(hours=3), max_age=600, reset_at=_ts(NOW - timedelta(hours=1)))
        reservations = {
            'before': {'pools': ['claude-five-hour'], 'estimate_pct': 50,
                       'created_at': _ts(NOW - timedelta(hours=2)), 'finished_at': None},
            'after': {'pools': ['claude-five-hour'], 'estimate_pct': 5,
                      'created_at': _ts(NOW - timedelta(minutes=30)), 'finished_at': None}}
        result = evaluate_advisory(_period_policy(['claude-five-hour']),
                                   _data({'claude-five-hour': window}, reservations), 'coder', 'small', NOW)
        self.assertTrue(result['allowed'])
        self.assertEqual(result['windows'][0]['pending_pct'], 5)

    def test_invalid_reservation_creation_time_raises(self):
        data = _data({'codex-weekly': _window(90)},
                     {'r1': {'pools': ['codex-weekly'], 'estimate_pct': 3, 'created_at': 'yesterday'}})
        with self.assertRaises(ValueError):
            evaluate_advisory(_period_policy(['codex-weekly']), data, 'coder', 'small', NOW)


if __name__ == '__main__':
    unittest.main()
