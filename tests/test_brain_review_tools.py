"""Current-source checks and context selection use only temporary synthetic stores."""
import json
from pathlib import Path
import sys
import tempfile
import unittest
from http.client import HTTPConnection
from threading import Thread
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'app'))
from brain_store import BrainStore
from brain_dashboard import make_server


class ReviewToolsTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.store = BrainStore(self.root)
        self.job = 'a' * 32
        self.task = self.root / 'runs/tasks' / self.job / 'result.json'
        self.task.parent.mkdir(parents=True)
        self.canonical = dict(job_id=self.job, assignment_project_id='alpha', task='Synthetic task',
            response='Synthetic canonical evidence', execution_status='succeeded', finalized_at='2026-09-01T00:00:00Z',
            status='accepted', review_status='accepted', review={'note':'Synthetic review'})
        self.save_task()

    def save_task(self):
        self.task.write_text(json.dumps(self.canonical), encoding='utf-8')

    def memory(self, title='Synthetic needle', task=True, active=False, **extra):
        value = self.store.propose(dict(project_id='alpha', kind='fact', title=title,
            content='Needle useful synthetic evidence',
            source={'type':'task','job_id':self.job} if task else {'type':'user','note':'Synthetic source'}, **extra))
        return self.store.approve(value['id'], 'Tester', 'Reviewed against independent synthetic evidence.') if active else value

    def test_source_drift_read_only_and_acceptance_separate(self):
        from brain_review_tools import source_check
        value = self.memory()
        self.assertEqual(source_check(self.store, 'alpha', value['id'])['status'], 'current')
        self.canonical.update(status='awaiting_review', review_status='pending')
        self.save_task()
        check = source_check(self.store, 'alpha', value['id'])
        self.assertEqual(check['status'], 'current')
        self.assertFalse(check['approval_ready'])
        self.canonical['response'] = 'Changed synthetic evidence'
        self.save_task()
        self.assertEqual(source_check(self.store, 'alpha', value['id'])['status'], 'changed')
        self.assertEqual(self.store.get(value['id'])['status'], 'pending')
        self.task.unlink()
        self.assertEqual(source_check(self.store, 'alpha', value['id'])['status'], 'unavailable')
        with self.assertRaises(ValueError): source_check(self.store, 'beta', value['id'])

    def test_source_badge_rechecks_hash_when_acceptance_check_races(self):
        from brain_review_tools import source_check
        value = self.memory()
        original = self.store._source
        def changing_source(*args, **kwargs):
            result = original(*args, **kwargs)
            if kwargs.get('approved') is False:
                self.canonical['response'] = 'Changed between source and acceptance checks'
                self.save_task()
            return result
        with patch.object(self.store, '_source', side_effect=changing_source):
            checked = source_check(self.store, 'alpha', value['id'])
        self.assertEqual(checked['status'], 'changed')
        self.assertFalse(checked['approval_ready'])

    def test_selected_context_rechecks_copy_and_keeps_healthy_sources(self):
        from brain_review_tools import selected_context
        backed = self.memory(active=True)
        healthy = self.memory('Needle independent', task=False, active=True)
        found = self.store.search('needle', 'alpha', strategy='keyword')
        ids = [backed['id'], healthy['id']]
        first = selected_context(self.store, 'alpha', found['trace_id'], ids)
        self.assertEqual(set(first['memory_ids']), set(ids))
        self.canonical['response'] = 'Drifted after preview'
        self.save_task()
        second = selected_context(self.store, 'alpha', found['trace_id'], ids)
        self.assertEqual(second['memory_ids'], [healthy['id']])
        self.assertNotIn(backed['title'], second['context'])
        self.assertEqual(len(second['excluded']), 1)
        self.assertEqual(second['excluded'][0]['reason'], 'Canonical task evidence changed; review the memory separately')
        with self.assertRaises(ValueError): selected_context(self.store, 'beta', found['trace_id'], ids)
        with self.assertRaises(ValueError): selected_context(self.store, 'alpha', found['trace_id'], [healthy['id']]*25)
        self.store.forget(healthy['id'], 'Tester', 'Erase the selected synthetic source')
        with self.assertRaises(ValueError): selected_context(self.store, 'alpha', found['trace_id'], ids)

    def test_context_bound_excludes_whole_records_without_extra_recall(self):
        from brain_review_tools import selected_context
        values = []
        for index in range(5):
            proposed = self.store.propose(dict(project_id='alpha', kind='fact', title='Needle large '+str(index),
                content='Needle '+('x'*2900), source={'type':'user','note':'Synthetic long source '+str(index)}))
            values.append(self.store.approve(proposed['id'], 'Tester', 'Reviewed this independent long synthetic record.'))
        found = self.store.search('needle', 'alpha', strategy='keyword', max_chars=20000)
        result = selected_context(self.store, 'alpha', found['trace_id'], [row['id'] for row in values])
        self.assertLessEqual(len(result['context']), 12000)
        self.assertEqual(len(result['memory_ids'])+len(result['excluded']), 5)
        self.assertGreater(len(result['excluded']), 0)

    def test_context_checks_dates_selection_membership_and_bound(self):
        from brain_review_tools import selected_context
        value = self.memory(task=False, active=True)
        found = self.store.search('needle', 'alpha', strategy='keyword')
        other = self.memory('Unselected evidence', task=False, active=True)
        with self.assertRaises(ValueError): selected_context(self.store, 'alpha', found['trace_id'], [other['id']])
        with self.store._connection() as con:
            con.execute("UPDATE memories SET valid_to='2026-01-01T00:00:00Z' WHERE id=?", (value['id'],))
            con.commit()
        result = selected_context(self.store, 'alpha', found['trace_id'], [value['id']])
        self.assertEqual(result['memory_ids'], [])
        self.assertLessEqual(len(result['context']), 12000)

    def test_task_picker_uses_finalized_scoped_canonical_evidence(self):
        from brain_review_tools import source_tasks
        self.assertEqual(source_tasks(self.store, 'alpha')['tasks'][0]['job_id'], self.job)
        self.assertEqual(source_tasks(self.store, 'beta')['tasks'], [])
        self.canonical.update(status='awaiting_review', review_status='pending')
        self.save_task()
        self.assertFalse(source_tasks(self.store, 'alpha')['tasks'][0]['accepted'])
        proposed = self.memory()
        with self.assertRaises(ValueError): self.store.approve(proposed['id'], 'Tester', 'Review has not yet been accepted.')
        self.canonical.update(execution_status='running', finalized_at=None)
        self.save_task()
        self.assertEqual(source_tasks(self.store, 'alpha')['tasks'], [])
        with self.assertRaises(ValueError): self.memory('Running source')
        self.task.write_text('null', encoding='utf-8')
        self.assertEqual(source_tasks(self.store, 'alpha')['tasks'], [])


class ReviewToolsHttpTests(ReviewToolsTests):
    def setUp(self):
        super().setUp()
        self.server = make_server(self.root, store_factory=lambda root: self.store)
        self.thread = Thread(target=self.server.serve_forever, kwargs={'poll_interval':.02}, daemon=True)
        self.thread.start()
        self.addCleanup(self.stop)

    def stop(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=2)

    def request(self, method, path, payload=None, authorized=True):
        headers = {'X-Brain-Token':self.server.brain_token} if authorized else {}
        if method == 'POST': headers.update(Origin=self.server.origin, **{'Content-Type':'application/json'})
        connection = HTTPConnection(*self.server.server_address, timeout=3)
        try:
            connection.request(method, path, json.dumps(payload) if payload is not None else None, headers)
            response = connection.getresponse()
            return response.status, json.loads(response.read())
        finally:
            connection.close()

    def test_http_source_check_scope_and_fresh_context(self):
        pending = self.memory()
        path = '/api/source-check?project_id=alpha&memory_id='+pending['id']
        self.assertEqual(self.request('GET', path)[1]['status'], 'current')
        self.assertEqual(self.request('GET', path, authorized=False)[0], 403)
        self.assertEqual(self.request('GET', path.replace('alpha','beta'))[0], 400)
        active = self.memory('Needle HTTP context', task=False, active=True)
        found = self.store.search('needle', 'alpha', strategy='keyword')
        payload = dict(project_id='alpha', trace_id=found['trace_id'], memory_ids=[active['id']])
        code, result = self.request('POST', '/api/context', payload)
        self.assertEqual(code, 200)
        self.assertIn(active['title'], result['context'])
        self.assertEqual(self.request('POST', '/api/context', dict(payload, memory_ids=[]))[0], 400)
        self.assertEqual(self.request('POST', '/api/context', dict(payload, unexpected=True))[0], 400)

    def test_http_context_copy_revalidates_after_preview(self):
        backed = self.memory(active=True)
        healthy = self.memory('Needle HTTP independent', task=False, active=True)
        found = self.store.search('needle', 'alpha', strategy='keyword')
        payload = dict(project_id='alpha', trace_id=found['trace_id'], memory_ids=[backed['id'],healthy['id']])
        self.assertEqual(len(self.request('POST','/api/context',payload)[1]['memory_ids']), 2)
        self.canonical['response'] = 'Changed after HTTP preview'
        self.save_task()
        code, copied = self.request('POST','/api/context',payload)
        self.assertEqual(code, 200)
        self.assertEqual(copied['memory_ids'], [healthy['id']])
        self.assertNotIn(backed['title'], copied['context'])

    def test_http_task_source_wizard_and_gated_approval(self):
        self.canonical.update(status='awaiting_review', review_status='pending')
        self.save_task()
        code, listing = self.request('GET', '/api/source-tasks?project_id=alpha')
        self.assertEqual(code, 200)
        self.assertEqual(listing['tasks'][0]['job_id'], self.job)
        payload = dict(project_id='alpha', kind='fact', title='HTTP proposal', content='Synthetic source proposal',
                       source={'type':'task','job_id':self.job})
        code, proposed = self.request('POST', '/api/memories', payload)
        self.assertEqual(code, 201)
        self.assertEqual(self.request('POST', '/api/approve', dict(project_id='alpha', memory_id=proposed['id'],
            reviewer='Tester', note='Task review must first be accepted.'))[0], 400)


if __name__ == '__main__': unittest.main()
