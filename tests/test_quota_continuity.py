"""Regression tests for advisory quota continuity (the 20 percent dead-end hold).

Low but positive fresh allowance must start bounded work while steering the lead
toward another provider. Only fresh zero available allowance and a confirmed
active provider rejection hold. Pure in-memory inputs; no provider calls.
"""
import copy
import unittest
from datetime import datetime, timedelta, timezone

from app.quota_admission import evaluate_advisory


NOW = datetime(2026, 9, 9, 12, 0, tzinfo=timezone.utc)
POOL = 'codex-five-hour'
WEEKLY = 'codex-seven-day'


def _iso(moment):
    return moment.isoformat().replace('+00:00', 'Z')


def _policy(**overrides):
    base = {
        'worker_pools': {
            'codex': [POOL],
            'claude': ['claude-five-hour'],
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
        'observed_at': _iso(observed or (NOW - timedelta(seconds=30))),
        'max_age_seconds': max_age,
        'reset_at': _iso(reset_at) if reset_at else None,
        'reset_display': 'soon' if reset_at else None,
        'source': source,
    }


def _reservation(estimate, pools=None, created=None, finished=None):
    return {
        'pools': list(pools or [POOL]),
        'estimate_pct': estimate,
        'created_at': _iso(created or (NOW - timedelta(minutes=10))),
        'finished_at': _iso(finished) if finished else None,
    }


def _cooldown(until):
    return {'until': _iso(until), 'reason': 'provider rejected work'}


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


def _evaluate(data, worker='codex', size='small', policy=None):
    return evaluate_advisory(policy or _policy(), data, worker, size, NOW)


class _Assertions(unittest.TestCase):
    def assert_shape(self, result):
        self.assertEqual(result['admission_mode'], 'advisory')
        self.assertIsInstance(result['prefer_alternate'], bool)
        self.assertIsInstance(result['warnings'], list)
        for warning in result['warnings']:
            self.assertIsInstance(warning, str)
            self.assertTrue(warning.strip())

    def assert_ready(self, result):
        self.assertTrue(result['allowed'])
        self.assert_shape(result)
        self.assertEqual(result['status'], 'ready')
        self.assertEqual(result['reasons'], [])

    def assert_held(self, result):
        self.assert_shape(result)
        self.assertFalse(result['allowed'])
        self.assertEqual(result['status'], 'held')
        self.assertTrue(result['reasons'])

    def assert_low_allowance_notice(self, result, key=POOL):
        self.assertIs(result['prefer_alternate'], True)
        self.assertTrue(any(key in warning for warning in result['warnings']),
                        f'no warning names {key}: {result["warnings"]}')


class LowFreshAllowanceTests(_Assertions):
    def test_healthy_fresh_reading_has_no_notice(self):
        result = _evaluate(_data(windows={POOL: _window(60)}))
        self.assert_ready(result)
        self.assertIs(result['prefer_alternate'], False)
        self.assertEqual(result['warnings'], [])
        self.assertEqual(result['reading_status'], 'fresh')

    def test_fresh_sixteen_percent_starts_work_and_prefers_alternate(self):
        result = _evaluate(_data(windows={POOL: _window(16)}))
        self.assert_ready(result)
        self.assert_low_allowance_notice(result)
        self.assertEqual(result['reading_status'], 'fresh')
        self.assertEqual(result['windows'][0]['available_pct'], 16)

    def test_fresh_exactly_twenty_percent_starts_work_and_prefers_alternate(self):
        for remaining in (20, 20.0):
            with self.subTest(remaining=remaining):
                result = _evaluate(_data(windows={POOL: _window(remaining)}))
                self.assert_ready(result)
                self.assert_low_allowance_notice(result)
                self.assertEqual(result['threshold_pct'], 20)

    def test_just_above_threshold_does_not_prefer_alternate(self):
        result = _evaluate(_data(windows={POOL: _window(20.5)}))
        self.assert_ready(result)
        self.assertIs(result['prefer_alternate'], False)
        self.assertEqual(result['warnings'], [])

    def test_default_threshold_is_twenty_when_policy_omits_it(self):
        policy = _policy()
        del policy['worker_start_threshold_pct']
        low = _evaluate(_data(windows={POOL: _window(20)}), policy=policy)
        self.assert_ready(low)
        self.assert_low_allowance_notice(low)
        self.assertEqual(low['threshold_pct'], 20)
        above = _evaluate(_data(windows={POOL: _window(21)}), policy=policy)
        self.assert_ready(above)
        self.assertIs(above['prefer_alternate'], False)

    def test_large_task_at_sixteen_percent_still_starts(self):
        result = _evaluate(_data(windows={POOL: _window(16)}), size='large')
        self.assert_ready(result)
        self.assert_low_allowance_notice(result)
        self.assertEqual(result['estimate_pct'], 15)

    def test_low_pool_beside_healthy_pool_starts_and_names_the_low_pool(self):
        policy = _policy(worker_pools={'codex': [POOL, WEEKLY], 'local-chat': []})
        result = _evaluate(_data(windows={POOL: _window(16), WEEKLY: _window(80)}), policy=policy)
        self.assert_ready(result)
        self.assert_low_allowance_notice(result, POOL)
        self.assertFalse(any(WEEKLY in warning for warning in result['warnings']))

    def test_low_pool_beside_unknown_pool_starts(self):
        policy = _policy(worker_pools={'codex': [POOL, WEEKLY], 'local-chat': []})
        result = _evaluate(_data(windows={POOL: _window(16)}), policy=policy)
        self.assert_ready(result)
        self.assert_low_allowance_notice(result, POOL)
        self.assertEqual(result['reading_status'], 'unknown')


class UncertainReadingTests(_Assertions):
    def test_missing_reading_does_not_block(self):
        result = _evaluate(_data())
        self.assert_ready(result)
        self.assertEqual(result['reading_status'], 'unknown')
        self.assertEqual(result['windows'], [])
        self.assertTrue(any(POOL in warning for warning in result['warnings']))

    def test_stale_low_reading_does_not_block(self):
        window = _window(8, observed=NOW - timedelta(hours=2), reset_at=NOW + timedelta(hours=1))
        result = _evaluate(_data(windows={POOL: window}))
        self.assert_ready(result)
        self.assertEqual(result['reading_status'], 'cached')
        self.assertEqual(result['windows'][0]['freshness'], 'cached')
        self.assertEqual(result['windows'][0]['reading_age_seconds'], 7200)
        self.assertTrue(any(POOL in warning for warning in result['warnings']))

    def test_failed_refresh_after_low_reading_does_not_block_and_stays_visible(self):
        failure = {'at': _iso(NOW - timedelta(seconds=10)), 'error': 'collection timed out'}
        result = _evaluate(_data(windows={POOL: _window(8)}, refresh_errors={'codex': failure}))
        self.assert_ready(result)
        self.assertEqual(result['reading_status'], 'cached')
        self.assertEqual(result['provider_refresh_errors'], {'codex': failure})
        self.assertEqual(result['windows'][0]['remaining_pct'], 8)
        self.assertEqual(result['windows'][0]['observed_at'], _iso(NOW - timedelta(seconds=30)))
        self.assertTrue(any(POOL in warning for warning in result['warnings']))

    def test_failed_refresh_without_any_reading_does_not_block(self):
        failure = {'at': _iso(NOW - timedelta(seconds=10)), 'error': 'collection timed out'}
        result = _evaluate(_data(refresh_errors={'codex': failure}))
        self.assert_ready(result)
        self.assertEqual(result['reading_status'], 'unknown')
        self.assertIn('codex', result['provider_refresh_errors'])

    def test_refresh_failure_older_than_reading_keeps_reading_fresh(self):
        failure = {'at': _iso(NOW - timedelta(minutes=5)), 'error': 'collection timed out'}
        result = _evaluate(_data(windows={POOL: _window(16)}, refresh_errors={'codex': failure}))
        self.assert_ready(result)
        self.assertEqual(result['reading_status'], 'fresh')
        self.assert_low_allowance_notice(result)

    def test_other_provider_refresh_failure_is_not_reported_for_this_worker(self):
        failure = {'at': _iso(NOW - timedelta(seconds=10)), 'error': 'collection timed out'}
        result = _evaluate(_data(windows={POOL: _window(60)}, refresh_errors={'claude': failure}))
        self.assert_ready(result)
        self.assertEqual(result['provider_refresh_errors'], {})
        self.assertEqual(result['reading_status'], 'fresh')


class FreshExhaustionTests(_Assertions):
    def test_failed_refresh_does_not_release_zero_before_known_reset(self):
        failure = {'at': _iso(NOW - timedelta(seconds=10)), 'error': 'collection timed out'}
        window = _window(0, reset_at=NOW + timedelta(hours=2))
        result = _evaluate(_data(windows={POOL: window}, refresh_errors={'codex': failure}))
        self.assert_held(result)
        self.assertEqual(result['reading_status'], 'cached')

    def test_cached_zero_without_known_period_is_unknown_and_nonblocking(self):
        window = _window(0, observed=NOW - timedelta(hours=2))
        result = _evaluate(_data(windows={POOL: window}))
        self.assert_ready(result)
        self.assertEqual(result['reading_status'], 'cached')

    def test_fresh_zero_holds(self):
        result = _evaluate(_data(windows={POOL: _window(0, reset_at=NOW + timedelta(hours=2))}))
        self.assert_held(result)
        self.assertTrue(any(POOL in reason for reason in result['reasons']))
        self.assertEqual(result['windows'][0]['available_pct'], 0)

    def test_fresh_zero_in_one_pool_holds_despite_healthy_sibling(self):
        policy = _policy(worker_pools={'codex': [POOL, WEEKLY], 'local-chat': []})
        result = _evaluate(_data(windows={POOL: _window(0), WEEKLY: _window(80)}), policy=policy)
        self.assert_held(result)
        self.assertTrue(any(POOL in reason for reason in result['reasons']))
        self.assertFalse(any(WEEKLY in reason for reason in result['reasons']))

    def test_pending_reservations_consuming_all_allowance_hold(self):
        for remaining, estimate in ((30, 30), (10, 15)):
            with self.subTest(remaining=remaining, estimate=estimate):
                data = _data(windows={POOL: _window(remaining)},
                             reservations={'r1': _reservation(estimate)})
                result = _evaluate(data)
                self.assert_held(result)
                window = result['windows'][0]
                self.assertEqual(window['pending_pct'], estimate)
                self.assertEqual(window['available_pct'], 0)

    def test_pending_reservation_leaving_sixteen_percent_starts(self):
        data = _data(windows={POOL: _window(30)}, reservations={'r1': _reservation(14)})
        result = _evaluate(data)
        self.assert_ready(result)
        self.assert_low_allowance_notice(result)
        self.assertEqual(result['windows'][0]['available_pct'], 16)

    def test_pending_reservation_leaving_exactly_twenty_percent_starts(self):
        data = _data(windows={POOL: _window(35)}, reservations={'r1': _reservation(15)})
        result = _evaluate(data)
        self.assert_ready(result)
        self.assert_low_allowance_notice(result)
        self.assertEqual(result['windows'][0]['available_pct'], 20)

    def test_reservation_for_another_pool_is_not_counted(self):
        data = _data(windows={POOL: _window(30)},
                     reservations={'r1': _reservation(30, pools=['claude-five-hour'])})
        result = _evaluate(data)
        self.assert_ready(result)
        self.assertIs(result['prefer_alternate'], False)
        self.assertEqual(result['windows'][0]['available_pct'], 30)


class ResetPassedTests(_Assertions):
    def test_old_zero_after_reset_is_unknown_and_does_not_block(self):
        window = _window(0, observed=NOW - timedelta(minutes=5), reset_at=NOW - timedelta(minutes=1))
        result = _evaluate(_data(windows={POOL: window}))
        self.assert_ready(result)
        self.assertEqual(result['reading_status'], 'unknown')
        self.assertTrue(result['windows'][0]['reset_passed'])
        self.assertTrue(any(POOL in warning for warning in result['warnings']))

    def test_reset_exactly_now_counts_as_passed(self):
        window = _window(0, observed=NOW - timedelta(minutes=5), reset_at=NOW)
        result = _evaluate(_data(windows={POOL: window}))
        self.assert_ready(result)
        self.assertTrue(result['windows'][0]['reset_passed'])

    def test_reservation_from_before_reset_does_not_hold_new_period(self):
        window = _window(0, observed=NOW - timedelta(minutes=5), reset_at=NOW - timedelta(minutes=1))
        data = _data(windows={POOL: window},
                     reservations={'r1': _reservation(50, created=NOW - timedelta(minutes=30))})
        result = _evaluate(data)
        self.assert_ready(result)
        self.assertEqual(result['windows'][0]['pending_pct'], 0)

    def test_zero_with_future_reset_still_holds(self):
        window = _window(0, observed=NOW - timedelta(minutes=5), reset_at=NOW + timedelta(minutes=1))
        result = _evaluate(_data(windows={POOL: window}))
        self.assert_held(result)
        self.assertFalse(result['windows'][0]['reset_passed'])


class CooldownScopeTests(_Assertions):
    def test_active_cooldown_on_own_pool_holds(self):
        until = NOW + timedelta(minutes=15)
        result = _evaluate(_data(windows={POOL: _window(60)}, cooldowns={POOL: _cooldown(until)}))
        self.assert_held(result)
        self.assertEqual(result['cooldown_active_pools'], {POOL: _iso(until)})
        self.assertTrue(any(POOL in reason for reason in result['reasons']))

    def test_active_cooldown_holds_even_with_low_positive_allowance(self):
        until = NOW + timedelta(minutes=15)
        result = _evaluate(_data(windows={POOL: _window(16)}, cooldowns={POOL: _cooldown(until)}))
        self.assert_held(result)
        self.assertIn(POOL, result['cooldown_active_pools'])

    def test_active_cooldown_without_reading_holds(self):
        until = NOW + timedelta(minutes=15)
        result = _evaluate(_data(cooldowns={POOL: _cooldown(until)}))
        self.assert_held(result)
        self.assertEqual(result['reading_status'], 'unknown')

    def test_active_cooldown_on_same_owner_dynamic_pool_holds(self):
        until = NOW + timedelta(minutes=15)
        result = _evaluate(_data(windows={POOL: _window(60)}, cooldowns={WEEKLY: _cooldown(until)}))
        self.assert_held(result)
        self.assertEqual(list(result['cooldown_active_pools']), [WEEKLY])

    def test_cooldown_from_another_provider_does_not_hold(self):
        until = NOW + timedelta(minutes=15)
        data = _data(windows={POOL: _window(16)}, cooldowns={'claude-five-hour': _cooldown(until)})
        result = _evaluate(data)
        self.assert_ready(result)
        self.assert_low_allowance_notice(result)
        self.assertEqual(result['cooldown_active_pools'], {})

    def test_expired_cooldown_does_not_hold(self):
        for until in (NOW - timedelta(seconds=1), NOW):
            with self.subTest(until=until):
                data = _data(windows={POOL: _window(16)}, cooldowns={POOL: _cooldown(until)})
                result = _evaluate(data)
                self.assert_ready(result)
                self.assertEqual(result['cooldown_active_pools'], {})

    def test_local_chat_ignores_provider_cooldowns_and_low_readings(self):
        until = NOW + timedelta(minutes=15)
        data = _data(windows={POOL: _window(0)}, cooldowns={POOL: _cooldown(until)})
        result = _evaluate(data, worker='local-chat')
        self.assert_shape(result)
        self.assertTrue(result['allowed'])
        self.assertEqual(result['status'], 'local')
        self.assertEqual(result['reasons'], [])
        self.assertEqual(result['cooldown_active_pools'], {})


class InputPreservationTests(_Assertions):
    def _scenarios(self):
        soon = NOW + timedelta(minutes=15)
        failure = {'at': _iso(NOW - timedelta(seconds=10)), 'error': 'collection timed out'}
        return {
            'low fresh': _data(windows={POOL: _window(16)}),
            'fresh zero': _data(windows={POOL: _window(0)}),
            'pending': _data(windows={POOL: _window(30)}, reservations={'r1': _reservation(30)}),
            'failed refresh': _data(windows={POOL: _window(8)}, refresh_errors={'codex': failure}),
            'cooldown': _data(windows={POOL: _window(60)}, cooldowns={POOL: _cooldown(soon)}),
            'reset passed': _data(windows={POOL: _window(0, observed=NOW - timedelta(minutes=5),
                                                         reset_at=NOW - timedelta(minutes=1))}),
            'declared pools': _data(windows={POOL: _window(16)}, worker_pools={'codex': [POOL]}),
            'unknown': _data(),
        }

    def test_policy_and_data_are_not_mutated(self):
        for name, data in self._scenarios().items():
            with self.subTest(scenario=name):
                policy = _policy()
                policy_before = copy.deepcopy(policy)
                data_before = copy.deepcopy(data)
                evaluate_advisory(policy, data, 'codex', 'small', NOW)
                self.assertEqual(policy, policy_before)
                self.assertEqual(data, data_before)

    def test_repeated_evaluation_is_deterministic(self):
        for name, data in self._scenarios().items():
            with self.subTest(scenario=name):
                policy = _policy()
                first = evaluate_advisory(policy, data, 'codex', 'small', NOW)
                second = evaluate_advisory(policy, data, 'codex', 'small', NOW)
                self.assertEqual(first, second)

    def test_mutating_a_result_does_not_change_inputs_or_later_results(self):
        failure = {'at': _iso(NOW - timedelta(seconds=10)), 'error': 'collection timed out'}
        policy = _policy()
        data = _data(windows={POOL: _window(16)}, refresh_errors={'codex': failure})
        data_before = copy.deepcopy(data)
        first = evaluate_advisory(policy, data, 'codex', 'small', NOW)
        expected = copy.deepcopy(first)
        first['warnings'].append('edited by caller')
        first['windows'][0]['remaining_pct'] = 99
        first['provider_refresh_errors']['codex']['error'] = 'edited by caller'
        self.assertEqual(data, data_before)
        self.assertEqual(evaluate_advisory(policy, data, 'codex', 'small', NOW), expected)


if __name__ == '__main__':
    unittest.main()
