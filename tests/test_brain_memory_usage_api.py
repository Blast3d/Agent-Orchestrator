"""Actual loopback API validation for scoped memory measurement and feedback."""
from contextlib import contextmanager
import hashlib
from http.client import HTTPConnection
import json
from pathlib import Path
import sys
import tempfile
from threading import Thread
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'app'))
from brain_dashboard import make_server
from task_store import TaskStore, timestamp
import memory_usage


class BrainMemoryUsageApiTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.store = TaskStore(self.root / 'runs/tasks')
        self.result = self.store.create(worker='claude', task='Recovery procedure review', size='small',
            assignment_project_id='alpha', assignment_id='review-test', memory_lookup_requested=True,
            memory_policy='task_label', memory_user_id='local')
        context = 'PRIVATE_MEMORY_CONTENT'
        self.result.update(status='accepted', review_status='accepted', execution_status='succeeded',
            finalized_at=timestamp(), response='PRIVATE_WORKER_RESPONSE',
            review={'reviewer': 'Reviewer', 'note': 'Checked the source and validated the answer for this task.'},
            memory_context={'ids': ['a' * 32], 'context': context, 'query': 'PRIVATE_QUERY',
                'sha256': hashlib.sha256(context.encode()).hexdigest(), 'project_id': 'alpha',
                'user_id': 'local', 'execution_requested': True})
        self.store.save(self.result['job_id'], self.result)
        self.server = make_server(self.root, store_factory=lambda root: self.fail('Usage endpoints must not open Brain storage'))
        self.thread = Thread(target=self.server.serve_forever, kwargs={'poll_interval': .01}, daemon=True)
        self.thread.start()
        self.addCleanup(self.stop)

    def stop(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=2)

    def saved(self):
        return json.loads((self.store.directory(self.result['job_id']) / 'result.json').read_text())

    def request(self, method='GET', path='/api/usage?project_id=alpha', data=None, *, token=True, origin=True, headers=None):
        request_headers = dict(headers or {})
        if token:
            request_headers['X-Brain-Token'] = self.server.brain_token
        if method == 'POST' and origin:
            request_headers['Origin'] = self.server.origin
        body = json.dumps(data).encode() if data is not None else None
        if body is not None:
            request_headers['Content-Type'] = 'application/json'
        connection = HTTPConnection(*self.server.server_address, timeout=3)
        try:
            connection.request(method, path, body=body, headers=request_headers)
            response = connection.getresponse()
            return response.status, json.loads(response.read())
        finally:
            connection.close()

    def feedback(self, **changes):
        payload = {'project_id': 'alpha', 'job_id': self.result['job_id'], 'rating': 'helped',
            'reviewer': 'Reviewer', 'note': 'The recalled recovery procedure prevented a duplicate execution.'}
        payload.update(changes)
        return payload

    def test_usage_requires_token_and_strict_single_project(self):
        self.assertEqual(self.request(token=False)[0], 403)
        for path in ('/api/usage', '/api/usage?project_id=',
                     '/api/usage?project_id=alpha&project_id=beta',
                     '/api/usage?project_id=alpha&project_id=',
                     '/api/usage?project_id=alpha&user_id=other'):
            with self.subTest(path=path):
                self.assertEqual(self.request(path=path)[0], 400)

    def test_usage_returns_exact_project_and_public_stages(self):
        code, value = self.request()
        self.assertEqual(code, 200)
        self.assertEqual(len(value['tasks']), 1)
        self.assertEqual(value['tasks'][0]['memory']['stage'], 'execution_requested')
        self.assertEqual(value['tasks'][0]['memory']['feedback']['status'], 'not_evaluated')
        self.assertNotIn('PRIVATE_', json.dumps(value))
        code, other = self.request(path='/api/usage?project_id=beta')
        self.assertEqual(code, 200)
        self.assertEqual(other['tasks'], [])

    def test_feedback_requires_origin_token_and_local_user(self):
        for kwargs in ({'token': False}, {'origin': False}):
            with self.subTest(kwargs=kwargs):
                self.assertEqual(self.request('POST', '/api/feedback', self.feedback(), **kwargs)[0], 403)
        self.assertEqual(self.request('POST', '/api/feedback', self.feedback(user_id='other'))[0], 400)
        self.assertNotIn('memory_feedback', self.saved())

    def test_feedback_rejects_foreign_project_and_unsupported_fields_without_write(self):
        self.assertEqual(self.request('POST', '/api/feedback', self.feedback(project_id='beta'))[0], 400)
        self.assertEqual(self.request('POST', '/api/feedback', self.feedback(response='forged'))[0], 400)
        self.assertNotIn('memory_feedback', self.saved())

    def test_feedback_saves_bound_rating_preserves_source_and_invalidates_usage_cache(self):
        _, before = self.request()
        code, value = self.request('POST', '/api/feedback', self.feedback())
        self.assertEqual(code, 200, value)
        self.assertEqual(value['feedback']['rating'], 'helped')
        self.assertNotIn('PRIVATE_', json.dumps(value))
        saved = self.saved()
        self.assertEqual(saved['response'], self.result['response'])
        self.assertEqual(saved['review'], self.result['review'])
        _, after = self.request()
        self.assertNotEqual(before['revision'], after['revision'])
        self.assertEqual(after['tasks'][0]['memory']['feedback']['rating'], 'helped')

    def test_project_is_rechecked_under_write_lock_against_changed_canonical_source(self):
        real_lock = memory_usage.file_lock
        @contextmanager
        def changed_after_request(path):
            with real_lock(path):
                self.result['assignment_project_id'] = 'beta'
                self.result['memory_context']['project_id'] = 'beta'
                self.store.save(self.result['job_id'], self.result)
                yield
        with patch.object(memory_usage, 'file_lock', side_effect=changed_after_request):
            code, _ = self.request('POST', '/api/feedback', self.feedback())
        self.assertEqual(code, 400)
        self.assertNotIn('memory_feedback', self.saved())

    def test_scoped_memory_of_a_different_user_cannot_receive_local_feedback(self):
        self.result['memory_user_id'] = 'other'
        self.result['memory_context']['user_id'] = 'other'
        self.store.save(self.result['job_id'], self.result)
        self.assertEqual(self.request('POST', '/api/feedback', self.feedback())[0], 400)
        self.assertNotIn('memory_feedback', self.saved())

    def test_held_or_unrequested_memory_cannot_be_rated(self):
        self.result['memory_context']['execution_requested'] = False
        self.store.save(self.result['job_id'], self.result)
        self.assertEqual(self.request('POST', '/api/feedback', self.feedback())[0], 400)
        self.assertNotIn('memory_feedback', self.saved())

    def test_stale_feedback_cannot_be_silently_rebound(self):
        self.assertEqual(self.request('POST', '/api/feedback', self.feedback())[0], 200)
        self.result = self.saved()
        original_feedback = self.result['memory_feedback']
        self.result['response'] = 'Changed accepted source'
        self.store.save(self.result['job_id'], self.result)
        self.assertEqual(self.request('POST', '/api/feedback', self.feedback(rating='neutral'))[0], 400)
        self.assertEqual(self.saved()['memory_feedback'], original_feedback)

    def test_oversized_canonical_source_is_rejected_before_any_feedback_write(self):
        with patch.object(memory_usage, 'MAX_CANONICAL_BYTES', 100):
            self.assertEqual(self.request('POST', '/api/feedback', self.feedback())[0], 400)
        self.assertNotIn('memory_feedback', self.saved())

    def evidence_path(self, project='alpha', job_id=None):
        return '/api/use?project_id=' + project + '&job_id=' + (job_id or self.result['job_id'])

    def test_use_evidence_requires_token_and_exact_project_job(self):
        self.assertEqual(self.request(path=self.evidence_path(), token=False)[0], 403)
        self.assertEqual(self.request(path=self.evidence_path('beta'))[0], 400)
        for path in ('/api/use?project_id=alpha', self.evidence_path(job_id='../invalid'),
                     self.evidence_path() + '&job_id=' + self.result['job_id'],
                     self.evidence_path() + '&other=field', self.evidence_path(job_id='f' * 32)):
            with self.subTest(path=path):
                self.assertEqual(self.request(path=path)[0], 400)

    def test_use_evidence_exposes_only_requested_answer_context_and_opaque_bindings(self):
        self.result['prompt'] = 'PRIVATE_BRIEF_MUST_NOT_BE_RETURNED'
        self.result['credentials'] = {'token': 'PRIVATE_CREDENTIAL_MUST_NOT_BE_RETURNED'}
        self.result['requested_output'] = 'C:/Private/Output/result.json'
        self.store.save(self.result['job_id'], self.result)
        code, value = self.request(path=self.evidence_path())
        self.assertEqual(code, 200, value)
        self.assertEqual(value['accepted_answer'], self.result['response'])
        self.assertEqual(value['saved_context'], self.result['memory_context']['context'])
        self.assertFalse(value['accepted_answer_truncated'])
        self.assertFalse(value['saved_context_truncated'])
        self.assertEqual(len(value['source_sha256']), 64)
        self.assertEqual(len(value['context_binding_sha256']), 64)
        for forbidden in ('PRIVATE_BRIEF_', 'PRIVATE_CREDENTIAL_', 'C:/Private', 'PRIVATE_QUERY'):
            self.assertNotIn(forbidden, json.dumps(value))

    def test_use_evidence_reports_explicit_answer_and_context_truncation(self):
        self.result['response'] = 'A' * 14000
        context = 'C' * 9000
        self.result['memory_context'].update(context=context,
            sha256=hashlib.sha256(context.encode()).hexdigest())
        self.store.save(self.result['job_id'], self.result)
        code, value = self.request(path=self.evidence_path())
        self.assertEqual(code, 200, value)
        self.assertEqual(len(value['accepted_answer']), 12000)
        self.assertEqual(len(value['saved_context']), 8000)
        self.assertTrue(value['accepted_answer_truncated'])
        self.assertTrue(value['saved_context_truncated'])
        self.assertEqual(value['accepted_answer_chars'], 14000)
        self.assertEqual(value['saved_context_chars'], 9000)

    def test_use_evidence_rejects_incomplete_unrequested_or_nonlocal_context(self):
        original = json.loads(json.dumps(self.result))
        for changes in ({'execution_requested': False}, {'ids': []},
                        {'user_id': 'other'}, {'sha256': '0' * 64}):
            with self.subTest(changes=changes):
                self.result = json.loads(json.dumps(original))
                self.result['memory_context'].update(changes)
                self.store.save(self.result['job_id'], self.result)
                code, value = self.request(path=self.evidence_path())
                self.assertEqual(code, 400)
                self.assertNotIn('PRIVATE_', json.dumps(value))
        self.result.pop('memory_context')
        self.store.save(self.result['job_id'], self.result)
        self.assertEqual(self.request(path=self.evidence_path())[0], 400)

    def test_use_evidence_rejects_pending_review_and_oversized_canonical(self):
        with patch.object(memory_usage, 'MAX_CANONICAL_BYTES', 100):
            self.assertEqual(self.request(path=self.evidence_path())[0], 400)
        self.result['review_status'] = 'pending'
        self.store.save(self.result['job_id'], self.result)
        self.assertEqual(self.request(path=self.evidence_path())[0], 400)

    def test_feedback_binds_the_actual_displayed_evidence_and_rejects_later_changes(self):
        code, evidence = self.request(path=self.evidence_path())
        self.assertEqual(code, 200)
        payload = self.feedback(expected_source_sha256=evidence['source_sha256'],
            expected_context_binding_sha256=evidence['context_binding_sha256'])
        self.result['response'] = 'Answer changed after the user opened the evidence'
        self.store.save(self.result['job_id'], self.result)
        self.assertEqual(self.request('POST', '/api/feedback', payload)[0], 400)
        self.assertNotIn('memory_feedback', self.saved())
        _, current = self.request(path=self.evidence_path())
        payload.update(expected_source_sha256=current['source_sha256'],
                       expected_context_binding_sha256=current['context_binding_sha256'])
        self.assertEqual(self.request('POST', '/api/feedback', payload)[0], 200)

    def test_feedback_rejects_partial_or_invalid_displayed_fingerprints(self):
        for changes in ({'expected_source_sha256': 'a' * 64},
                        {'expected_source_sha256': 'invalid', 'expected_context_binding_sha256': 'b' * 64}):
            with self.subTest(changes=changes):
                self.assertEqual(self.request('POST', '/api/feedback', self.feedback(**changes))[0], 400)
        self.assertNotIn('memory_feedback', self.saved())


if __name__ == '__main__':
    unittest.main()
