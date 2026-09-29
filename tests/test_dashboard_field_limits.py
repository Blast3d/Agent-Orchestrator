"""Dashboard name and note limits match the memory store, so API clients get one clear error."""
from http.client import HTTPConnection
import json
from pathlib import Path
import sys
import tempfile
from threading import Thread
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'app'))
from brain_dashboard import NAME_LIMIT, NOTE_LIMIT, make_server


class RecordingStore:
    """Only the calls these handlers make; every write is recorded, none is performed."""

    def __init__(self):
        self.calls = []

    def get(self, memory_id):
        return {'id': memory_id, 'project_id': 'alpha', 'user_id': 'local', 'status': 'active'}

    def approve(self, *args):
        self.calls.append(('approve',) + args)
        return {'ok': True}

    def forget(self, *args):
        self.calls.append(('forget',) + args)
        return {'ok': True}

    def supersede(self, *args):
        self.calls.append(('supersede',) + args)
        return {'ok': True}

    def relate(self, *args, **kwargs):
        self.calls.append(('relate',) + args)
        return {'ok': True}


class FieldLimitTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.store = RecordingStore()
        self.server = make_server(Path(temp.name), store_factory=lambda root: self.store)
        thread = Thread(target=self.server.serve_forever, kwargs={'poll_interval': .02}, daemon=True)
        thread.start()

        def stop():
            self.server.shutdown()
            self.server.server_close()
            thread.join(timeout=2)
        self.addCleanup(stop)

    def post(self, path, data):
        connection = HTTPConnection(*self.server.server_address, timeout=3)
        try:
            connection.request('POST', path, body=json.dumps(dict(data, project_id='alpha', memory_id='m1')),
                               headers={'X-Brain-Token': self.server.brain_token, 'Origin': self.server.origin,
                                        'Content-Type': 'application/json'})
            response = connection.getresponse()
            return response.status, json.loads(response.read())
        finally:
            connection.close()

    def test_limits_equal_the_brain_store_limits(self):
        self.assertEqual((NAME_LIMIT, NOTE_LIMIT), (100, 1000))

    def test_overlong_names_and_notes_are_rejected_before_the_store(self):
        cases = (('/api/approve', {'reviewer': 'r' * 101, 'note': 'Checked the evidence carefully.'}, 'Reviewer'),
                 ('/api/approve', {'reviewer': 'Reviewer', 'note': 'n' * 1001}, 'Review note'),
                 ('/api/forget', {'actor': 'a' * 101, 'reason': 'No longer accurate.'}, 'Name'),
                 ('/api/forget', {'actor': 'Local user', 'reason': 'r' * 1001}, 'Reason'),
                 ('/api/supersede', {'actor': 'a' * 101, 'reason': 'Replaced.', 'new_id': 'm2'}, 'Name'),
                 ('/api/relate', {'actor': 'a' * 101, 'relation': 'supports', 'target_id': 'm2'}, 'Name'))
        for path, data, field in cases:
            with self.subTest(path=path, field=field):
                status, body = self.post(path, data)
                self.assertEqual(status, 400, body)
                self.assertEqual(body['error'], field + ' is required and must fit its character limit.')
        self.assertEqual(self.store.calls, [])

    def test_values_at_the_limit_reach_the_store(self):
        self.assertEqual(self.post('/api/approve', {'reviewer': 'r' * 100, 'note': 'n' * 1000})[0], 200)
        self.assertEqual(self.post('/api/forget', {'actor': 'a' * 100, 'reason': 'r' * 1000})[0], 200)
        self.assertEqual([call[0] for call in self.store.calls], ['approve', 'forget'])


if __name__ == '__main__':
    unittest.main()
