"""Scoped recall freshness stays separate from the durable memory change cursor."""
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'app'))
from brain_store import BrainStore
from storage_budget import StorageLimitError


class RecallRevisionTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.brain = BrainStore(Path(temporary.name))

    def active(self, project='alpha', user='local'):
        proposed = self.brain.propose({
            'project_id': project, 'user_id': user, 'kind': 'fact',
            'title': 'Timeout recovery', 'content': 'Keep bounded recovery evidence.',
            'source': {'type': 'user', 'note': 'Synthetic source for a recall test.'},
        })
        return self.brain.approve(proposed['id'], 'Tester',
                                  'Verified the synthetic source for this regression test.')

    def revision(self, project='alpha', user='local'):
        return self.brain.changes(project, user)['recall_revision']

    def test_empty_revision_is_stable_opaque_and_does_not_create_a_feed(self):
        first = self.brain.changes('alpha')
        self.assertRegex(first['recall_revision'], r'^[a-f0-9]{64}$')
        self.assertEqual(first, self.brain.changes('alpha'))
        self.assertEqual(first['changes'], [])
        self.assertEqual(first['head'], 0)
        self.assertEqual(first['cursor'], 0)
        self.assertFalse(first['resync_required'])
        self.active()
        self.assertEqual(first['recall_revision'], self.revision())

    def test_search_changes_only_recall_revision_after_memory_head(self):
        self.active()
        head = self.brain.changes('alpha')['head']
        before = self.brain.changes('alpha', after=head)
        found = self.brain.search('timeout', 'alpha')
        self.assertIsNotNone(found['trace_id'])
        after = self.brain.changes('alpha', after=head)
        self.assertNotEqual(before['recall_revision'], after['recall_revision'])
        self.assertEqual({key: value for key, value in before.items() if key != 'recall_revision'},
                         {key: value for key, value in after.items() if key != 'recall_revision'})
        self.assertEqual(after['recall_revision'], self.revision())

    def test_empty_search_also_changes_revision_without_creating_memory_events(self):
        before = self.brain.changes('alpha')
        found = self.brain.search('no matching synthetic knowledge', 'alpha')
        self.assertEqual(found['results'], [])
        after = self.brain.changes('alpha')
        self.assertNotEqual(before['recall_revision'], after['recall_revision'])
        self.assertEqual(after['head'], 0)
        self.assertEqual(after['changes'], [])

    def test_other_project_and_user_do_not_change_scoped_revision(self):
        self.brain.search('synthetic query', 'alpha')
        own = self.revision()
        other_user = self.revision(user='another-user')
        self.brain.search('synthetic query', 'beta')
        self.assertEqual(own, self.revision())
        self.brain.search('synthetic query', 'alpha', user_id='another-user')
        self.assertEqual(own, self.revision())
        self.assertNotEqual(other_user, self.revision(user='another-user'))

    def test_other_scope_retention_eviction_updates_only_affected_revision(self):
        self.brain.limits['max_traces'] = 2
        empty = self.revision()
        self.brain.search('synthetic query', 'alpha')
        populated = self.revision()
        self.brain.search('first synthetic query', 'beta')
        self.assertEqual(populated, self.revision())
        self.brain.search('second synthetic query', 'beta')
        self.assertEqual(empty, self.revision())
        self.assertEqual(self.brain.changes('alpha')['head'], 0)

    def test_forget_removes_recall_revision_and_preserves_other_scope(self):
        empty = self.revision()
        memory = self.active()
        self.brain.search('timeout', 'alpha')
        self.brain.search('synthetic query', 'beta')
        own, other = self.revision(), self.revision('beta')
        self.brain.forget(memory['id'], 'Tester', 'Erase the synthetic memory and its recall references.')
        self.assertNotEqual(own, self.revision())
        self.assertEqual(empty, self.revision())
        self.assertEqual(other, self.revision('beta'))
        self.assertEqual(self.brain.changes('alpha')['changes'][-1]['operation'], 'forgotten')

    def test_unrecorded_search_leaves_revision_unchanged(self):
        self.active()
        before = self.brain.changes('alpha')
        with patch.object(self.brain.budget, 'allocation', side_effect=StorageLimitError('Synthetic full budget')):
            found = self.brain.search('timeout', 'alpha')
        self.assertTrue(found['results'])
        self.assertIsNone(found['trace_id'])
        self.assertEqual(before, self.brain.changes('alpha'))

if __name__ == '__main__':
    unittest.main()
