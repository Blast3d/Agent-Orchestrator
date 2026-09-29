"""Snapshot relations stay scoped to the listed memories (audit B13)."""
from pathlib import Path
import sys
import tempfile
import unittest
import uuid

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'app'))
from brain_store import BrainStore


class SnapshotRelationTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.brain = BrainStore(Path(temporary.name))

    def memory(self, project, title):
        row = self.brain.propose({'project_id': project, 'kind': 'fact', 'title': title,
                                  'content': 'Synthetic reviewed content for ' + title + '.',
                                  'source': {'type': 'user', 'note': 'Reviewed synthetic source.'}})
        return self.brain.approve(row['id'], 'Tester', 'Checked the complete synthetic evidence.')

    def relate(self, con, source, target, stamp):
        con.execute('INSERT INTO relations(id,source_id,target_id,relation,valid_from,valid_to,actor) '
                    'VALUES (?,?,?,?,?,NULL,?)', (uuid.uuid4().hex, source, target, 'supports', stamp, 'Tester'))

    def test_older_project_relation_survives_many_newer_relations_elsewhere(self):
        a, b = self.memory('alpha', 'Alpha one'), self.memory('alpha', 'Alpha two')
        c, d = self.memory('beta', 'Beta one'), self.memory('beta', 'Beta two')
        with self.brain._connection() as con:
            self.relate(con, a['id'], b['id'], '2020-01-01T00:00:00+00:00')
            for index in range(2005):
                self.relate(con, c['id'], d['id'], f'2026-01-01T00:00:{index // 60:02d}.{index % 60:06d}+00:00')
            con.commit()
        relations = self.brain.snapshot('alpha')['relations']
        self.assertEqual([(r['source_id'], r['target_id']) for r in relations], [(a['id'], b['id'])])
        self.assertEqual(self.brain.snapshot('gamma')['relations'], [])


if __name__ == '__main__':
    unittest.main()
