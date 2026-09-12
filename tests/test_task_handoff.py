"""Quota handoffs reuse receipts and never turn uncertain execution into a retry."""
from argparse import Namespace
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import hashlib
import json
from pathlib import Path
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'app'))
from assignment_receipts import AssignmentReceipts
from task_handoff import apply_automatic_fallbacks, dispatch_with_handoff, quota_handoff_reason
from task_store import OutputClaim, TaskStore, timestamp
import claude_models


class RecordedDispatcher:
    """The real receipt store surrounding a fake worker, with no provider access."""
    def __init__(self, outcomes=None, before=None):
        self.outcomes = outcomes or {'claude': 'low', 'grok': 'success', 'local-chat': 'success'}
        self.before = before
        self.started = []
        self.calls = []

    def __call__(self, args, *, store):
        self.calls.append(args.worker)
        raw = args.prompt_file.read_bytes()
        metadata = {'worker': args.worker, 'task': args.task, 'size': args.size, 'category': args.category,
                    'prompt_sha256': hashlib.sha256(raw).hexdigest(), 'prompt_bytes': len(raw),
                    'requested_output': str(args.output.absolute()),
                    'handoff_from_job_id': getattr(args, 'handoff_from_job_id', None),
                    'handoff_reason': getattr(args, 'handoff_reason', None)}
        contract = {key: metadata[key] for key in ('worker', 'prompt_sha256', 'size', 'category')}
        contract.update(claude_model=args.claude_model if args.worker == 'claude' else None,
                        claude_effort=args.claude_effort if args.worker == 'claude' else None,
                        require_brief_check=args.require_brief_check)
        record, reused = AssignmentReceipts(store).claim(args.project, args.assignment_id, contract, metadata,
                                                       getattr(args, 'revision_of', None))
        if reused:
            return dict(record, assignment_reused=True)
        self.started.append({'worker': args.worker, 'brief': raw, 'job_id': record['job_id'],
                             'assignment_id': args.assignment_id, 'output': args.output,
                             'revision_of': getattr(args, 'revision_of', None)})
        if self.before:
            self.before(args, record, store)
        outcome = self.outcomes[args.worker]
        result = dict(record, finalized_at=timestamp(), cleanup_errors=[], export_status='written',
                      reservation_id=None, started_at=None)
        if outcome in ('low', 'blocked', 'unknown', 'stale', 'mixed', 'auth', 'reset'):
            reason = {'low': 'task plus safety buffer exceeds available quota',
                      'blocked': 'provider rejected work; cooldown active',
                      'unknown': 'usage unknown; refresh or supply a current account reading',
                      'stale': 'reading is stale', 'auth': 'latest quota refresh failed',
                      'reset': 'reset boundary passed; fetch a fresh reading',
                      'mixed': 'task plus safety buffer exceeds available quota'}[outcome]
            reasons = ['worker-window: ' + reason]
            if outcome == 'mixed':
                reasons.append('worker-window: reading is stale')
            result.update(status='held', execution_status='held', reason='Quota admission refused',
                          quota_before={'allowed': False, 'reasons': reasons,
                                        'windows': [{'id': 'worker-window', 'remaining_pct': 5, 'available_pct': 5}]})
        elif outcome == 'quota_failure':
            result.update(status='failed', execution_status='failed', failure_kind='quota_exhausted',
                          started_at=timestamp(), reservation_id='saved-allowance',
                          reservation_state='finished_pending_fresh_quota', error='Worker exited with a rate limit')
        elif outcome == 'uncertain':
            result.update(status='recovery_required', execution_status='uncertain', error='timeout',
                          started_at=timestamp(), reservation_id='still-reserved', reservation_state='held_for_reconciliation')
        elif outcome == 'parse_failure':
            result.update(status='failed', execution_status='failed', error='Invalid JSON returned')
        elif outcome in ('brief', 'permissions'):
            result.update(status='held', execution_status='held', reason=outcome + ' needs correction')
        else:
            result.update(status='awaiting_review', execution_status='succeeded', review_status='pending',
                          response='An answer from ' + args.worker)
        store.save(record['job_id'], result)
        output = OutputClaim(args.output, record['job_id'])
        output.write(result)
        output.close()
        return result


class HandoffTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.store = TaskStore(self.root / 'tasks')
        self.brief = self.root / 'brief.txt'
        self.brief.write_text('The exact same approved task for each suitable worker.', encoding='utf-8')
        self.args = Namespace(worker='claude', fallback_worker=['grok'], project='demo', assignment_id='review',
                              prompt_file=self.brief, output=self.root / 'answer.json', size='small',
                              task='Read a supplied brief', category='review', claude_model='sonnet',
                              claude_effort='medium', require_brief_check=False, revision_of=None)
        self.policy = self.root / 'config/workers.json'
        self.policy.parent.mkdir()
        self.policy.write_text(json.dumps({'policy': {'paused_claude_model_families': ['fable']},
            'workers': [{'id': 'claude', 'requested_model': 'opus'}]}), encoding='utf-8')
        patched = patch.object(claude_models, 'ROOT', self.root)
        patched.start()
        self.addCleanup(patched.stop)

    def run_plan(self, dispatcher, args=None):
        return dispatch_with_handoff(args or self.args, dispatch_fn=dispatcher, store=self.store)

    def test_configured_model_is_frozen_before_first_worker_and_forwarded_to_fallback(self):
        self.args.worker = 'grok'
        self.args.fallback_worker = ['claude']
        self.args.claude_model = None
        observed = []
        def before(args, record, store):
            observed.append((args.worker, args.claude_model))
            # A live preference change must not alter a previously frozen plan.
            self.policy.write_text(json.dumps({'workers': [{'id': 'claude', 'requested_model': 'haiku'}]}),
                                   encoding='utf-8')
        dispatcher = RecordedDispatcher({'grok': 'low', 'claude': 'success'}, before=before)
        result = self.run_plan(dispatcher)
        self.assertEqual(result['status'], 'awaiting_review')
        self.assertEqual(observed, [('grok', 'opus'), ('claude', 'opus')])
        plan = json.loads(next((self.root / 'handoffs').glob('*.json')).read_text())
        self.assertEqual(plan['contract']['claude_model'], 'opus')

    def test_paused_explicit_fable_holds_the_plan_before_either_worker(self):
        self.args.worker = 'grok'
        self.args.fallback_worker = ['claude']
        self.args.claude_model = 'claude-fable-5'
        dispatcher = RecordedDispatcher()
        result = self.run_plan(dispatcher)
        self.assertEqual(result['status'], 'held')
        self.assertIn('paused by the user', result['reason'])
        self.assertEqual(dispatcher.calls, [])
        self.assertFalse(self.args.output.exists())

    def test_explicit_deadline_is_frozen_and_forwarded_to_each_worker(self):
        self.args.timeout_seconds=600;observed=[]
        dispatcher=RecordedDispatcher(before=lambda args,record,store:observed.append(args.timeout_seconds))
        self.assertEqual(self.run_plan(dispatcher)['status'],'awaiting_review')
        self.assertEqual(observed,[600,600])
        self.args.timeout_seconds=900
        result=self.run_plan(dispatcher)
        self.assertEqual(result['status'],'held');self.assertEqual(len(dispatcher.started),2)

    def test_default_memory_query_and_opt_out_are_frozen_in_parent_plan(self):
        dispatcher = RecordedDispatcher()
        self.assertEqual(self.run_plan(dispatcher)['status'], 'awaiting_review')
        plan = json.loads(next((self.root / 'handoffs').glob('*.json')).read_text())
        self.assertEqual(plan['contract']['memory_policy'], 'task_label')
        self.assertEqual(plan['contract']['memory_query'], self.args.task)
        for field, value in (('task', 'Changed task label'), ('no_memory', True),
                             ('memory_query', 'Explicit replacement query')):
            with self.subTest(field=field):
                changed = deepcopy(self.args)
                setattr(changed, field, value)
                self.assertTrue(self.run_plan(dispatcher, changed)['handoff_blocked'])
        self.assertEqual(len(dispatcher.started), 2)

    def test_conflicting_recall_flags_hold_before_any_dispatch(self):
        dispatcher = RecordedDispatcher()
        self.args.no_memory = True
        self.args.memory_query = 'Recovery'
        self.assertTrue(self.run_plan(dispatcher)['handoff_blocked'])
        self.assertEqual(dispatcher.calls, [])

    def test_known_low_allowance_hands_off_once_and_keeps_original_export(self):
        dispatcher = RecordedDispatcher()
        result = self.run_plan(dispatcher)
        self.assertEqual([row['worker'] for row in dispatcher.started], ['claude', 'grok'])
        self.assertEqual(result['status'], 'awaiting_review')
        self.assertEqual(result['review_status'], 'pending')
        self.assertEqual(len(result['handoff_chain']), 2)
        first, second = dispatcher.started
        self.assertEqual(second['revision_of'], first['job_id'])
        self.assertLess(len(second['assignment_id']), 160)
        self.assertEqual(second['output'].name, 'answer.handoff-1-grok.json')
        self.assertEqual(first['brief'], second['brief'])
        original = json.loads(self.args.output.read_text())
        self.assertEqual(original['worker'], 'claude')
        self.assertEqual(original['status'], 'held')
        self.assertEqual(result['handoff_from_job_id'], first['job_id'])
        self.assertIn('allowance', result['handoff_reason'])

    def test_repeated_call_reuses_same_chain_without_new_worker_starts(self):
        dispatcher = RecordedDispatcher()
        first = self.run_plan(dispatcher)
        before = self.args.output.read_bytes()
        second = self.run_plan(dispatcher)
        self.assertEqual(len(dispatcher.started), 2)
        self.assertEqual(first['job_id'], second['job_id'])
        self.assertEqual([step['job_id'] for step in first['handoff_chain']],
                         [step['job_id'] for step in second['handoff_chain']])
        self.assertTrue(second['assignment_reused'])
        self.assertEqual(before, self.args.output.read_bytes())

    def test_blocked_provider_can_handoff_through_guarded_next_dispatch(self):
        dispatcher = RecordedDispatcher({'claude': 'blocked', 'grok': 'success'})
        self.assertEqual(self.run_plan(dispatcher)['worker'], 'grok')
        self.assertEqual(dispatcher.calls, ['claude', 'grok'])

    def test_exited_quota_rejection_hands_off_after_reservation_cleanup(self):
        dispatcher = RecordedDispatcher({'claude': 'quota_failure', 'grok': 'success'})
        result = self.run_plan(dispatcher)
        self.assertEqual(result['worker'], 'grok')
        previous = json.loads(self.args.output.read_text())
        self.assertEqual(previous['reservation_state'], 'finished_pending_fresh_quota')

    def test_unknown_stale_auth_reset_and_mixed_quota_never_handoff(self):
        for outcome in ('unknown', 'stale', 'auth', 'reset', 'mixed'):
            with self.subTest(outcome=outcome):
                args = deepcopy(self.args)
                args.assignment_id = outcome
                args.output = self.root / (outcome + '.json')
                dispatcher = RecordedDispatcher({'claude': outcome, 'grok': 'success'})
                result = self.run_plan(dispatcher, args)
                self.assertEqual(len(dispatcher.started), 1)
                self.assertEqual(result['status'], 'held')

    def test_timeout_permissions_brief_parse_failure_and_success_never_handoff(self):
        for outcome in ('uncertain', 'permissions', 'brief', 'parse_failure', 'success'):
            with self.subTest(outcome=outcome):
                args = deepcopy(self.args)
                args.assignment_id = outcome
                args.output = self.root / (outcome + '.json')
                dispatcher = RecordedDispatcher({'claude': outcome, 'grok': 'success'})
                result = self.run_plan(dispatcher, args)
                self.assertEqual(len(dispatcher.started), 1)
                self.assertEqual(len(result['handoff_chain']), 1)
                if outcome == 'uncertain':
                    self.assertEqual(result['reservation_state'], 'held_for_reconciliation')
                    self.run_plan(dispatcher, args)
                    self.assertEqual(len(dispatcher.started), 1)

    def test_every_worker_distinct_bounded_and_local_explicit(self):
        self.args.fallback_worker = ['grok', 'local-chat']
        self.args.category = 'classification'
        dispatcher = RecordedDispatcher({'claude': 'low', 'grok': 'low', 'local-chat': 'success'})
        result = self.run_plan(dispatcher)
        self.assertEqual([row['worker'] for row in dispatcher.started], ['claude', 'grok', 'local-chat'])
        self.assertEqual(result['worker'], 'local-chat')
        self.assertEqual(len(result['handoff_chain']), 3)
        self.assertEqual(dispatcher.started[2]['revision_of'], dispatcher.started[1]['job_id'])

    def test_local_handoff_blocks_large_or_nonbasic_work_before_any_dispatch(self):
        for size, category in (('medium', 'chat'), ('large', 'general'), ('small', 'coding'),
                               ('tiny', 'code-review'), ('small', 'review')):
            with self.subTest(size=size, category=category):
                args = deepcopy(self.args)
                args.fallback_worker = ['local-chat']
                args.size, args.category = size, category
                dispatcher = RecordedDispatcher()
                result = self.run_plan(dispatcher, args)
                self.assertTrue(result['handoff_blocked'])
                self.assertIn('tiny or small basic tasks', result['reason'])
                self.assertEqual(dispatcher.calls, [])

    def test_small_basic_handoff_allows_explicit_local_worker(self):
        self.args.fallback_worker = ['local-chat']
        self.args.category = 'summarization'
        dispatcher = RecordedDispatcher()
        result = self.run_plan(dispatcher)
        self.assertEqual(result['worker'], 'local-chat')
        self.assertEqual([step['worker'] for step in dispatcher.started], ['claude', 'local-chat'])

    def test_duplicate_invalid_or_too_many_workers_are_held_before_dispatch(self):
        for order in (['claude'], ['grok', 'grok'], ['grok', 'local-chat', 'claude'], ['gemini'], ['paid-api']):
            with self.subTest(order=order):
                args = deepcopy(self.args)
                args.fallback_worker = order
                dispatcher = RecordedDispatcher()
                result = self.run_plan(dispatcher, args)
                self.assertTrue(result['handoff_blocked'])
                self.assertEqual(dispatcher.started, [])

    def test_handoff_requires_both_stable_identity_fields(self):
        for key in ('project', 'assignment_id'):
            args = deepcopy(self.args)
            setattr(args, key, None)
            dispatcher = RecordedDispatcher()
            self.assertTrue(self.run_plan(dispatcher, args)['handoff_blocked'])
            self.assertEqual(dispatcher.started, [])

    def test_changed_plan_or_prompt_held_without_launching_new_branch(self):
        self.args.category = 'chat'
        dispatcher = RecordedDispatcher()
        self.run_plan(dispatcher)
        changed = deepcopy(self.args)
        changed.fallback_worker = ['local-chat']
        result = self.run_plan(dispatcher, changed)
        self.assertTrue(result['handoff_blocked'])
        self.assertEqual(len(dispatcher.started), 2)
        self.brief.write_text('Different task', encoding='utf-8')
        self.assertTrue(self.run_plan(dispatcher)['handoff_blocked'])
        self.assertEqual(len(dispatcher.started), 2)

    def test_concurrent_call_cannot_start_second_branch(self):
        self.args.category = 'chat'
        entered, release = threading.Event(), threading.Event()
        def before(args, record, store):
            if args.worker == 'claude':
                entered.set()
                if not release.wait(5):
                    raise AssertionError('Test did not release worker')
        dispatcher = RecordedDispatcher(before=before)
        with ThreadPoolExecutor(max_workers=2) as executor:
            future = executor.submit(self.run_plan, dispatcher)
            self.assertTrue(entered.wait(3))
            try:
                changed = deepcopy(self.args)
                changed.fallback_worker = ['local-chat']
                competing = self.run_plan(dispatcher, changed)
                self.assertTrue(competing['handoff_blocked'])
                self.assertIn('already being handled', competing['reason'])
                self.assertEqual(len(dispatcher.started), 1)
            finally:
                release.set()
            self.assertEqual(future.result(timeout=3)['worker'], 'grok')
        self.assertEqual(len(dispatcher.started), 2)

    def test_source_edit_between_steps_cannot_change_the_frozen_brief(self):
        original = self.brief.read_bytes()
        def before(args, record, store):
            if args.worker == 'claude':
                self.brief.write_text('A concurrent edit of the original file', encoding='utf-8')
        dispatcher = RecordedDispatcher(before=before)
        self.assertEqual(self.run_plan(dispatcher)['worker'], 'grok')
        self.assertTrue(all(step['brief'] == original for step in dispatcher.started))

    def test_changed_snapshot_stops_before_the_next_worker(self):
        def before(args, record, store):
            if args.worker == 'claude':
                args.prompt_file.write_text('Unexpected change to frozen evidence', encoding='utf-8')
        dispatcher = RecordedDispatcher(before=before)
        result = self.run_plan(dispatcher)
        self.assertTrue(result['handoff_blocked'])
        self.assertIn('brief changed', result['reason'])
        self.assertEqual(len(dispatcher.started), 1)
        self.assertEqual(len(result['handoff_chain']), 1)

    def test_missing_frozen_brief_holds_repeated_plan_without_dispatch(self):
        dispatcher = RecordedDispatcher()
        self.run_plan(dispatcher)
        next((self.root / 'handoffs').glob('*.brief.txt')).unlink()
        prior_calls = len(dispatcher.calls)
        self.assertTrue(self.run_plan(dispatcher)['handoff_blocked'])
        self.assertEqual(len(dispatcher.calls), prior_calls)

    def test_crash_after_receipt_never_creates_duplicate_or_uncertain_child(self):
        crashed = False
        def before(args, record, store):
            nonlocal crashed
            if not crashed:
                crashed = True
                store.save(record['job_id'], dict(record, status='running', execution_status='running',
                                                 reservation_id='keep-me', started_at=timestamp()))
                raise RuntimeError('Simulated interruption')
        dispatcher = RecordedDispatcher(before=before)
        self.assertTrue(self.run_plan(dispatcher)['handoff_blocked'])
        result = self.run_plan(dispatcher)
        self.assertEqual(result['status'], 'running')
        self.assertEqual(result['reservation_id'], 'keep-me')
        self.assertEqual(len(dispatcher.started), 1)

    def test_missing_plan_with_existing_children_is_not_reconstructed(self):
        dispatcher = RecordedDispatcher()
        self.run_plan(dispatcher)
        next((self.root / 'handoffs').glob('*.json')).unlink()
        result = self.run_plan(dispatcher)
        self.assertTrue(result['handoff_blocked'])
        self.assertIn('plan is missing', result['reason'])
        self.assertEqual(len(dispatcher.started), 2)

    def test_malformed_plan_is_held_and_not_replaced(self):
        dispatcher = RecordedDispatcher()
        self.run_plan(dispatcher)
        path = next((self.root / 'handoffs').glob('*.json'))
        path.write_text('{bad json')
        self.assertTrue(self.run_plan(dispatcher)['handoff_blocked'])
        self.assertEqual(path.read_text(), '{bad json')
        self.assertEqual(len(dispatcher.started), 2)

    def test_unresolved_reservations_and_missing_known_readings_never_qualify(self):
        base = {'job_id': 'a'*32, 'finalized_at': timestamp(), 'status': 'failed', 'execution_status': 'failed',
                'failure_kind': 'quota_exhausted', 'cleanup_errors': []}
        for changes in ({'reservation_id': 'active'}, {'reservation_state': 'cleanup_failed'},
                        {'reservation_state': 'held_for_reconciliation'}, {'cleanup_errors': [{'step': 'finish'}]},
                        {'finalized_at': None}, {'execution_status': 'uncertain'}):
            with self.subTest(changes=changes):
                self.assertIsNone(quota_handoff_reason(dict(base, **changes)))
        held = dict(base, status='held', execution_status='held', quota_before={
            'allowed': False, 'reasons': ['worker-window: task plus safety buffer exceeds available quota'], 'windows': []})
        self.assertIsNone(quota_handoff_reason(held))

    def test_without_fallback_list_dispatch_is_unchanged(self):
        self.args.fallback_worker = []
        self.args.project = self.args.assignment_id = None
        calls = []
        expected = {'status': 'the normal result'}
        def dispatch(args, *, store):
            calls.append(args)
            return expected
        self.assertIs(self.run_plan(dispatch), expected)
        self.assertIs(calls[0], self.args)
        self.assertFalse((self.root / 'handoffs').exists())

    def automatic_policy(self):
        return {'quota_admission_mode': 'advisory', 'worker_start_threshold_pct': 20,
                'automatic_fallbacks': {'claude': ['grok'], 'grok': ['claude']}}

    def advisory_receipt(self):
        return {'job_id': 'a' * 32, 'finalized_at': timestamp(), 'status': 'held',
                'execution_status': 'held', 'cleanup_errors': [], 'started_at': None,
                'quota_before': {'admission_mode': 'advisory', 'threshold_pct': 20,
                    'allowed': False,
                    'reasons': ['worker-window: available allowance is at or below the worker start threshold'],
                    'warnings': ['worker-window: reading is stale', 'Claude usage panel timed out'],
                    'windows': [{'id': 'worker-window', 'remaining_pct': 20, 'available_pct': 20,
                        'observed_at': (datetime.now(timezone.utc) - timedelta(days=2)).isoformat(),
                        'reset_passed': False, 'freshness': 'stale'}]}}

    def test_automatic_config_keeps_explicit_plan_and_strict_default(self):
        self.args.fallback_worker = ['local-chat']
        self.assertIs(apply_automatic_fallbacks(self.args, self.automatic_policy()), self.args)
        self.assertEqual(self.args.fallback_worker, ['local-chat'])
        for policy in ({}, {'automatic_fallbacks': {'claude': ['grok']}},
                       dict(self.automatic_policy(), quota_admission_mode='strict')):
            with self.subTest(policy=policy):
                args = deepcopy(self.args)
                args.fallback_worker = []
                self.assertIs(apply_automatic_fallbacks(args, policy), args)
                self.assertEqual(args.fallback_worker, [])

    def test_automatic_opt_out_preserves_original_provider_and_identity(self):
        self.args.fallback_worker = []
        self.args.project = self.args.assignment_id = None
        self.args.no_auto_fallback = True
        apply_automatic_fallbacks(self.args, self.automatic_policy(), project_default='workspace-project')
        self.assertEqual(self.args.worker, 'claude')
        self.assertEqual(self.args.fallback_worker, [])
        self.assertIsNone(self.args.project)
        self.assertIsNone(self.args.assignment_id)

    def test_automatic_routes_only_distinct_claude_or_grok_without_model_escalation(self):
        self.args.fallback_worker = []
        policy = self.automatic_policy()
        policy['automatic_fallbacks']['claude'] = ['local-chat', 'paid-api', 'grok', 'grok', 'claude']
        apply_automatic_fallbacks(self.args, policy)
        self.assertEqual(self.args.fallback_worker, ['grok'])
        self.assertEqual(self.args.claude_model, 'sonnet')
        args = deepcopy(self.args)
        args.worker, args.fallback_worker = 'grok', []
        apply_automatic_fallbacks(args, policy)
        self.assertEqual(args.fallback_worker, ['claude'])
        for worker in ('local-chat', 'paid-api', 'codex', 'gemini'):
            args = deepcopy(self.args)
            args.worker, args.fallback_worker = worker, []
            apply_automatic_fallbacks(args, policy)
            self.assertEqual(args.fallback_worker, [])

    def test_automatic_unscoped_calls_have_unique_receipts_and_no_inferred_memory_scope(self):
        self.args.project = self.args.assignment_id = None
        self.args.fallback_worker = []
        other = deepcopy(self.args)
        policy = self.automatic_policy()
        apply_automatic_fallbacks(self.args, policy)
        identity = (self.args.project, self.args.assignment_id)
        apply_automatic_fallbacks(self.args, policy)
        apply_automatic_fallbacks(other, policy)
        self.assertEqual((self.args.project, self.args.assignment_id), identity)
        self.assertNotEqual(self.args.assignment_id, other.assignment_id)
        self.assertEqual(self.args.project, 'unscoped-worker-dispatch')
        self.assertTrue(self.args.no_memory)
        self.assertTrue(other.no_memory)
        dispatcher = RecordedDispatcher({'claude': 'quota_failure', 'grok': 'success'})
        first = self.run_plan(dispatcher)
        second = self.run_plan(dispatcher)
        self.assertEqual(first['status'], 'awaiting_review')
        self.assertEqual(first['job_id'], second['job_id'])
        self.assertEqual([row['worker'] for row in dispatcher.started], ['claude', 'grok'])
        plan = json.loads(next((self.root / 'handoffs').glob('*.json')).read_text())
        self.assertEqual(plan['project'], 'unscoped-worker-dispatch')
        self.assertEqual(plan['contract']['memory_policy'], 'disabled')

    def test_automatic_project_default_is_explicit_and_keeps_project_scoped_recall(self):
        self.args.project = self.args.assignment_id = None
        self.args.fallback_worker = []
        apply_automatic_fallbacks(self.args, self.automatic_policy(), project_default='workspace-project')
        self.assertEqual(self.args.project, 'workspace-project')
        self.assertTrue(self.args.assignment_id.startswith('auto-'))
        self.assertFalse(getattr(self.args, 'no_memory', False))
        dispatcher = RecordedDispatcher()
        self.assertEqual(self.run_plan(dispatcher)['status'], 'awaiting_review')
        plan = json.loads(next((self.root / 'handoffs').glob('*.json')).read_text())
        self.assertEqual(plan['contract']['memory_policy'], 'task_label')

    def test_automatic_helper_does_not_infer_invalid_missing_identity_or_query_scope(self):
        for fields in ({'project': None}, {'project': None, 'assignment_id': None, 'memory_query': 'private'},
                       {'assignment_id': None, 'revision_of': 'a' * 32},
                       {'project': None, 'assignment_id': None, 'no_memory': 'invalid'}):
            with self.subTest(fields=fields):
                args = deepcopy(self.args)
                args.fallback_worker = []
                for key, value in fields.items():
                    setattr(args, key, value)
                apply_automatic_fallbacks(args, self.automatic_policy())
                self.assertEqual(args.fallback_worker, [])
                for key, value in fields.items():
                    self.assertEqual(getattr(args, key), value)

    def test_automatic_actual_exhaustion_hands_off_once_and_keeps_frozen_receipts(self):
        self.args.fallback_worker = []
        apply_automatic_fallbacks(self.args, self.automatic_policy())
        dispatcher = RecordedDispatcher({'claude': 'quota_failure', 'grok': 'success'})
        first = self.run_plan(dispatcher)
        second = self.run_plan(dispatcher)
        self.assertEqual(first['job_id'], second['job_id'])
        self.assertEqual([row['worker'] for row in dispatcher.started], ['claude', 'grok'])
        self.assertEqual(first['handoff_chain'][0]['status'], 'failed')
        self.assertEqual(dispatcher.started[0]['brief'], dispatcher.started[1]['brief'])

    def test_cached_twenty_percent_hands_off_once_despite_refresh_timeout_warnings(self):
        self.args.fallback_worker = []
        apply_automatic_fallbacks(self.args, self.automatic_policy())
        recorded = RecordedDispatcher()
        def dispatch(args, *, store):
            result = recorded(args, store=store)
            if args.worker == 'claude' and not result.get('assignment_reused'):
                result['quota_before'] = self.advisory_receipt()['quota_before']
                store.save(result['job_id'], result)
            return result
        first = self.run_plan(dispatch)
        second = self.run_plan(dispatch)
        self.assertEqual(first['worker'], 'grok')
        self.assertEqual(first['job_id'], second['job_id'])
        self.assertEqual([row['worker'] for row in recorded.started], ['claude', 'grok'])

    def test_advisory_unknown_other_pool_does_not_cancel_a_confirmed_low_reading(self):
        result = self.advisory_receipt()
        result['quota_before']['windows'].append({'id': 'other-window', 'remaining_pct': None,
                                                  'available_pct': None, 'freshness': 'unknown'})
        self.assertIsNotNone(quota_handoff_reason(result))
        result['quota_before']['windows'] = result['quota_before']['windows'][1:]
        self.assertIsNone(quota_handoff_reason(result))

    def test_advisory_low_requires_real_nonreset_reading_at_or_below_threshold(self):
        for fields in ({'remaining_pct': None}, {'available_pct': None}, {'available_pct': 20.01},
                       {'available_pct': True}, {'available_pct': float('nan')}, {'observed_at': None},
                       {'observed_at': '2026-09-09T20:00:00'}, {'reset_passed': True}, {'reset_passed': None}):
            with self.subTest(fields=fields):
                result = self.advisory_receipt()
                result['quota_before']['windows'][0].update(fields)
                self.assertIsNone(quota_handoff_reason(result))
        result = self.advisory_receipt()
        result['quota_before']['threshold_pct'] = True
        self.assertIsNone(quota_handoff_reason(result))
        result = self.advisory_receipt()
        result['quota_before'].pop('admission_mode')
        self.assertIsNone(quota_handoff_reason(result))

    def test_unknown_allowed_check_and_nonquota_errors_are_not_exhaustion(self):
        for fields in ({'allowed': True, 'reasons': []},
                       {'allowed': False, 'reasons': ['worker-window: usage unknown']},
                       {'allowed': False, 'reasons': ['worker-window: latest quota refresh failed']}):
            result = self.advisory_receipt()
            result['quota_before'].update(fields)
            self.assertIsNone(quota_handoff_reason(result))
        for changes in ({'status': 'recovery_required', 'execution_status': 'uncertain'},
                        {'reservation_id': 'still-running', 'reservation_state': 'held_for_reconciliation'},
                        {'cleanup_errors': [{'step': 'canonical_save'}]},
                        {'cleanup_errors': [{'step': 'export_close'}]}, {'started_at': timestamp()},
                        {'status': 'failed', 'execution_status': 'failed', 'failure_kind': 'authentication'}):
            result = self.advisory_receipt()
            result.update(changes)
            self.assertIsNone(quota_handoff_reason(result))

    def test_cold_window_cooldown_requires_confirmed_unexpired_evidence(self):
        result = self.advisory_receipt()
        quota = result['quota_before']
        quota['windows'] = []
        quota['reasons'] = ['worker-window: provider rejected work; cooldown active']
        self.assertIsNone(quota_handoff_reason(result))
        quota['cooldown_active_pools'] = {'worker-window': (datetime.now(timezone.utc) + timedelta(minutes=5)).isoformat()}
        self.assertIsNotNone(quota_handoff_reason(result))
        quota['cooldown_active_pools']['worker-window'] = (datetime.now(timezone.utc) - timedelta(seconds=1)).isoformat()
        self.assertIsNone(quota_handoff_reason(result))
        quota['windows'] = self.advisory_receipt()['quota_before']['windows']
        self.assertIsNone(quota_handoff_reason(result))

    def test_automatic_handoff_never_replaces_an_uncertain_worker_or_releases_reservation(self):
        self.args.fallback_worker = []
        apply_automatic_fallbacks(self.args, self.automatic_policy())
        dispatcher = RecordedDispatcher({'claude': 'uncertain', 'grok': 'success'})
        for _ in range(2):
            result = self.run_plan(dispatcher)
            self.assertEqual(result['reservation_state'], 'held_for_reconciliation')
            self.assertEqual(result['reservation_id'], 'still-reserved')
        self.assertEqual([row['worker'] for row in dispatcher.started], ['claude'])

    def test_automatic_chain_stops_after_each_cloud_provider_is_exhausted(self):
        self.args.fallback_worker = []
        apply_automatic_fallbacks(self.args, self.automatic_policy())
        dispatcher = RecordedDispatcher({'claude': 'quota_failure', 'grok': 'quota_failure'})
        result = self.run_plan(dispatcher)
        self.assertEqual(result['status'], 'failed')
        self.assertEqual([row['worker'] for row in dispatcher.started], ['claude', 'grok'])
        self.assertEqual(len(result['handoff_chain']), 2)


if __name__ == '__main__':
    unittest.main()
