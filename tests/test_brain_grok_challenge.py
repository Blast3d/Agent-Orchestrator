"""Independent B05 Grok challenge; reviewed locally, relink expectation corrected after repair."""
import json, tempfile, unittest
from datetime import datetime, timezone
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'app'))
from brain_store import BrainStore

class ReviewedMemoryChallenge(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name); self.brain = BrainStore(self.root)
    def payload(self, **updates):
        return dict({'project_id': 'alpha', 'kind': 'fact', 'title': 'Timeout fix',
            'content': 'Bounded retries preserve recovery evidence.', 'tags': ['timeout'],
            'source': {'type': 'user', 'note': 'Synthetic user-approved source'}}, **updates)
    def active(self, **updates):
        item = self.brain.propose(self.payload(**updates))
        return self.brain.approve(item['id'], 'Tester', 'Validated against the synthetic source and expected behavior.')

    def test_expired_relationship_accepts_new_validity_period(self):
        a = self.active(title='Zebra incident', content='Zebra failure happened')
        b = self.active(title='Unique repair', content='External material', tags=[])
        stale = self.brain.relate(a['id'], b['id'], 'supports', 'Tester',
                                  '2020-01-01T00:00:00Z', '2020-02-01T00:00:00Z')
        self.assertTrue(stale['valid_to'] and stale['valid_to'] <= datetime.now(timezone.utc).isoformat())
        again = self.brain.relate(a['id'], b['id'], 'supports', 'Tester')
        self.assertNotEqual(again['id'], stale['id'])
        self.assertIsNone(again['valid_to'])
        self.assertEqual(len(self.brain.search('zebra', 'alpha')['results']), 2)

    def test_malformed_provenance_and_bounded_episode_fields(self):
        with self.assertRaises(ValueError):
            self.brain.propose(self.payload(source='not-an-object'))
        with self.assertRaises(ValueError):
            self.brain.propose(self.payload(source={'type': 'task', 'job_id': 'not-a-job-id'}))
        with self.assertRaises(ValueError):
            self.brain.propose(self.payload(kind='episode', episode={'problem': 'x'}))
        with self.assertRaises(ValueError):
            self.brain.propose(self.payload(kind='episode', episode={
                'problem': 'p', 'action': 'a', 'outcome': 'o' * 1001}))
        item = self.active(kind='episode', title='Retry episode',
            content='Procedure shell', tags=[],
            episode={'problem': 'Timeout', 'action': 'Bound retries',
                     'outcome': 'canaryoutcomexyz', 'ignored': 'drop-me'})
        self.assertEqual(item['episode'], {'problem': 'Timeout', 'action': 'Bound retries', 'outcome': 'canaryoutcomexyz'})
        hit = self.brain.search('canaryoutcomexyz', 'alpha')['results']
        self.assertEqual(hit[0]['id'], item['id'])
        self.assertIn('episode', hit[0])

    def test_inverse_links_supersede_history_and_forget_retains_ops(self):
        a = self.active(title='Claim A', content='Retries are required.')
        b = self.active(title='Claim B', content='Retries are forbidden.', tags=['opposite'])
        self.brain.relate(a['id'], b['id'], 'solves', 'Tester')
        self.brain.relate(b['id'], a['id'], 'solves', 'Tester')
        self.brain.relate(a['id'], b['id'], 'depends_on', 'Tester')
        self.assertEqual(len(self.brain.get(a['id'])['relations']), 3)
        c = self.active(title='Claim C', content='Retries are required with a bound.')
        self.brain.supersede(a['id'], c['id'], 'Tester', 'Replace contradictory claim A')
        old = self.brain.get(a['id'])
        self.assertEqual(old['status'], 'superseded')
        self.assertTrue(any(h['operation'] == 'superseded' for h in old['history']))
        ids = [r['id'] for r in self.brain.search('retries', 'alpha')['results']]
        self.assertNotIn(a['id'], ids); self.assertIn(c['id'], ids)
        forgotten = self.brain.forget(c['id'], 'Tester', 'Erase replacement after review')
        self.assertEqual(forgotten['content'], '')
        self.assertEqual(forgotten['episode'], {})
        ops = {h['operation'] for h in self.brain.get(c['id'])['history']}
        self.assertTrue({'proposed', 'approved', 'forgotten'} <= ops)
        self.assertFalse(self.brain.search('bound', 'alpha')['results'])
