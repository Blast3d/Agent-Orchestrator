"""Quota collection cannot gate advisory work; execution uncertainty still can."""
import unittest
import json
from datetime import timedelta
from unittest.mock import Mock, patch
from tests import test_dispatch_worker as fixtures
from worker_execution import WorkerInterrupted
from task_handoff import dispatch_with_handoff, apply_automatic_fallbacks
from usage_guard import Guard, now, stamp, validate_policy


class AdvisoryDispatchTests(unittest.TestCase):
    run_task = fixtures.DispatchTests.run_task

    def setUp(self):
        fixtures.DispatchTests.setUp(self)
        self.guard.policy = {'quota_admission_mode': 'advisory'}
        self.guard.refresh.side_effect = TimeoutError('collector must not be awaited')
        self.guard.request_refresh.return_value = {'status': 'queued'}
        self.guard.check.return_value.update(admission_mode='advisory', reading_status='cached',
                                             warnings=['latest quota refresh failed'])

    def test_output_is_claimed_before_refresh(self):
        def request(provider):
            self.assertTrue(self.args.output.exists())
            return {'status': 'queued'}
        self.guard.request_refresh.side_effect = request
        result = self.run_task()
        self.assertEqual(result['status'], 'awaiting_review')
        self.guard.refresh.assert_not_called()

    def test_background_launch_failure_is_a_warning_and_preserves_answer(self):
        self.guard.request_refresh.side_effect = OSError('launch failed')
        result = self.run_task()
        self.assertEqual(result['status'], 'awaiting_review')
        self.assertEqual(result['quota_before']['reading_status'], 'cached')
        self.assertEqual(result['background_quota_refresh']['before']['error'], 'OSError')
        self.assertEqual(result['background_quota_refresh']['after']['error'], 'OSError')
        self.guard.finish.assert_called_once()
        self.guard.refresh.assert_not_called()
        self.assertEqual(result['cleanup_errors'], [])

    def test_confirmed_hold_does_not_start_primary_but_still_collects(self):
        self.guard.check.return_value = {'allowed': False, 'admission_mode': 'advisory',
                                         'threshold_pct': 20, 'status': 'held'}
        result = self.run_task()
        self.assertEqual(result['status'], 'held')
        self.invoke.assert_not_called()
        self.guard.request_refresh.assert_called_once_with('claude')
        self.guard.refresh.assert_not_called()

    def test_uncertain_provider_execution_still_retains_reservation(self):
        self.invoke.side_effect = WorkerInterrupted('timeout', True, 1234, {})
        result = self.run_task()
        self.assertEqual(result['execution_status'], 'uncertain')
        self.assertEqual(result['reservation_state'], 'held_for_reconciliation')
        self.guard.finish.assert_not_called()
        self.guard.refresh.assert_not_called()


class AdvisoryRoutingTests(unittest.TestCase):
    def setUp(self):
        fixtures.DispatchTests.setUp(self)
        signin = patch('codex_worker.plan_signin', return_value=True)
        signin.start()
        self.addCleanup(signin.stop)
        self.real_guard = Guard(self.root / 'quota')
        self.real_guard.policy.update(quota_admission_mode='advisory', worker_start_threshold_pct=20,
            automatic_fallbacks={'codex': ['claude', 'grok']},
            worker_pools={w: [w + '-weekly'] for w in ('codex', 'claude', 'grok')})
        self.real_guard.request_refresh = Mock(return_value={'status': 'queued'})
        self.args.project = 'quota-integration'
        self.args.assignment_id = 'bounded-review'
        self.args.no_memory = True
        self.args.claude_model = 'opus'
        self.cache('codex', 16)
        self.cache('claude', 70)
        self.cache('grok', 70)

    def cache(self, worker, remaining, *, stale=False):
        if stale:
            with self.real_guard.state() as data:
                data['windows'].pop(worker + '-weekly', None)
        self.real_guard.observe([{'id': worker + '-weekly', 'remaining_pct': remaining,
            'observed_at': stamp(now() - timedelta(hours=2) if stale else now()),
            'reset_at': stamp(now() + timedelta(days=2)), 'max_age_seconds': 600,
            'source': 'synthetic regression fixture'}], worker, memberships={worker: [worker + '-weekly']})

    def dispatch(self, args, *, store):
        return fixtures.dispatcher.dispatch(args, guard_factory=lambda: self.real_guard,
            store=store, workspaces=self.root / 'workspaces')

    def run_plan(self):
        return dispatch_with_handoff(self.args, dispatch_fn=self.dispatch, store=self.store)

    def read_task(self, job_id):
        return json.loads((self.store.directory(job_id) / 'result.json').read_text(encoding='utf-8'))

    def test_direct_low_positive_task_runs_once_and_repeat_reuses_answer(self):
        self.cache('claude', 16)
        first = self.run_plan()
        self.assertEqual(first['execution_status'], 'succeeded')
        self.assertTrue(first['quota_before']['prefer_alternate'])
        repeated = self.run_plan()
        self.assertEqual(repeated['job_id'], first['job_id'])
        self.assertTrue(repeated['assignment_reused'])
        self.invoke.assert_called_once()

    def test_codex_policy_accepts_hosted_routes_and_rejects_self_duplicate_or_local(self):
        validate_policy(self.real_guard.policy)
        for routes in (['codex'], ['claude', 'claude'], ['local-chat'], ['paid-api']):
            with self.subTest(routes=routes):
                policy = dict(self.real_guard.policy, automatic_fallbacks={'codex': routes})
                with self.assertRaises(ValueError):
                    validate_policy(policy)

    def test_automatic_codex_alternate_runs_once_without_primary_reservation(self):
        self.args.worker = 'codex'
        apply_automatic_fallbacks(self.args, self.real_guard.policy)
        result = self.run_plan()
        self.assertEqual(result['execution_status'], 'succeeded')
        self.assertEqual(result['worker'], 'claude')
        self.assertEqual([step['worker'] for step in result['handoff_chain']], ['codex', 'claude'])
        primary = self.read_task(result['handoff_chain'][0]['job_id'])
        self.assertTrue(primary['quota_before']['route_preference'])
        self.assertIsNone(primary.get('reservation_id'))
        self.assertIsNone(primary.get('started_at'))
        repeated = self.run_plan()
        self.assertEqual(repeated['job_id'], result['job_id'])
        self.invoke.assert_called_once()
        with self.real_guard.state() as data:
            self.assertEqual([r['worker'] for r in data['reservations'].values()], ['claude'])

    def test_all_low_positive_routes_continue_with_terminal_authorized_worker(self):
        for worker in ('codex', 'claude', 'grok'):
            self.cache(worker, 16, stale=True)
        self.args.worker = 'codex'
        self.args.fallback_worker = ['claude', 'grok']
        result = self.run_plan()
        self.assertEqual(result['execution_status'], 'succeeded')
        self.assertEqual(result['worker'], 'grok')
        self.assertEqual(len(result['handoff_chain']), 3)
        self.assertTrue(result['quota_before']['prefer_alternate'])
        self.invoke.assert_called_once()

    def test_reviewed_preflight_and_accepted_alternate_replay_without_inference(self):
        self.args.worker = 'codex'
        self.args.fallback_worker = ['claude']
        first = self.run_plan()
        primary = first['handoff_chain'][0]['job_id']
        self.store.review(primary, 'rejected', 'Sol',
                          'Quota preference deferred before inference; no answer to retain.')
        with patch('automatic_memory.record_accepted_outcome', return_value={'status': 'remembered'}):
            self.store.review(first['job_id'], 'accepted', 'Sol',
                              'Reviewed the supplied answer and retained its useful findings.')
        repeated = self.run_plan()
        self.assertEqual(repeated['job_id'], first['job_id'])
        self.assertEqual(repeated['status'], 'accepted')
        self.invoke.assert_called_once()

    def test_zero_and_confirmed_rejection_route_without_primary_execution(self):
        for state in ('zero', 'rejection'):
            with self.subTest(state=state):
                self.args.worker = 'codex'
                self.args.assignment_id = 'held-' + state
                self.args.output = self.root / (state + '.json')
                self.args.fallback_worker = ['claude']
                if state == 'zero':
                    self.cache('codex', 0)
                else:
                    self.cache('codex', 80)
                    self.real_guard.block('codex')
                result = self.run_plan()
                self.assertEqual(result['execution_status'], 'succeeded')
                self.assertEqual(result['worker'], 'claude')
                primary = self.read_task(result['handoff_chain'][0]['job_id'])
                self.assertIsNone(primary.get('started_at'))
                self.assertIsNone(primary.get('reservation_id'))
        self.assertEqual(self.invoke.call_count, 2)

    def test_uncertain_low_execution_never_launches_alternate_or_repeats(self):
        self.args.fallback_worker = ['grok']
        self.invoke.side_effect = WorkerInterrupted('timeout', True, 1234, {})
        first = self.run_plan()
        self.assertEqual(first['execution_status'], 'uncertain')
        self.assertEqual(first['reservation_state'], 'held_for_reconciliation')
        self.assertEqual(len(first['handoff_chain']), 1)
        repeated = self.run_plan()
        self.assertEqual(repeated['job_id'], first['job_id'])
        self.invoke.assert_called_once()
        with self.real_guard.state() as data:
            self.assertEqual(len(data['reservations']), 1)
            self.assertIsNone(next(iter(data['reservations'].values()))['finished_at'])


if __name__ == '__main__':
    import unittest
    unittest.main()
