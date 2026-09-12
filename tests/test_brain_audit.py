"""Reproductions from the eight-seat independent brain audit."""
from contextlib import closing
import json
import os
from pathlib import Path
import sqlite3
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'app'))
from brain_store import BrainStore
from storage_budget import StorageLimitError


class BrainAuditTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name);self.b=BrainStore(self.root)

    def active(self,title='needle',content='Verified evidence',**extra):
        p=dict(project_id='audit',kind='fact',title=title,content=content,tags=[],source={'type':'user','note':'Synthetic audit evidence'})
        p.update(extra);m=self.b.propose(p)
        return self.b.approve(m['id'],'Tester','Verified against the synthetic independent audit fixture.')

    def test_stale_source_candidates_do_not_hide_later_valid_hit(self):
        source={'type':'task','job_id':'a'*32};path=self.root/'runs/tasks'/source['job_id']/'result.json'
        path.parent.mkdir(parents=True)
        task={'job_id':source['job_id'],'assignment_project_id':'audit','execution_status':'succeeded',
              'status':'accepted','review_status':'accepted','finalized_at':'2026-09-01T00:00:00Z','response':'Original evidence'}
        path.write_text(json.dumps(task))
        for i in range(60):self.active(title=f'needle needle needle {i}',source=source)
        current=self.active(title='Current fact',content='needle')
        task['response']='Source changed';path.write_text(json.dumps(task))
        self.assertIn(current['id'],[r['id'] for r in self.b.search('needle','audit',hops=0)['results']])

    def test_committed_change_reports_held_cleanup_reservation(self):
        with patch.object(self.b.budget,'release',side_effect=StorageLimitError('Injected release failure')):
            m=self.b.propose({'project_id':'audit','kind':'fact','title':'Committed','content':'Keep the result identity.',
                              'source':{'type':'user','note':'Synthetic audit evidence'}})
        self.assertEqual(self.b.get(m['id'])['status'],'pending')
        self.assertTrue(m['write_status']['committed']);self.assertTrue(m['write_status']['cleanup_pending'])
        self.assertTrue(self.b.budget.release(m['write_status']['reservation_id']))

    def test_release_failure_preserves_original_validation_error(self):
        with patch.object(self.b.budget,'release',side_effect=StorageLimitError('Injected cleanup failure')):
            with self.assertRaisesRegex(ValueError,'Memory not found'):
                self.b.approve('b'*32,'Tester','Missing memory remains the primary error.')

    @unittest.skipUnless(os.name=='nt','Windows sharing semantics')
    def test_locked_vault_has_actionable_error_without_false_forget(self):
        m=self.active();vault=Path(self.b.vault('audit')['path'])
        with vault.open('rb'):
            with self.assertRaisesRegex(ValueError,'close.*retry'):
                self.b.forget(m['id'],'Tester','Forget the synthetic fixture')
        self.assertEqual(self.b.get(m['id'])['status'],'active')
        self.assertEqual(self.b.forget(m['id'],'Tester','Closed the viewer and retried')['status'],'deleted')

    def test_orphan_vault_temp_is_removed_under_brain_lock(self):
        orphan=self.b.home/('vault-'+'a'*32+'.tmp');orphan.write_text('orphan secret canary')
        BrainStore(self.root)
        self.assertFalse(orphan.exists())

    def test_legacy_change_only_scope_has_trim_watermark_after_migration(self):
        with self.b._connection() as con:
            con.execute('DROP TABLE feed_state')
            con.execute("INSERT INTO changes(seq,memory_id,operation,project_id,user_id,at) VALUES(50,?,'forgotten','past','local','2026-09-01')",('a'*32,))
            con.execute('PRAGMA user_version=1');con.commit()
        migrated=BrainStore(self.root)
        self.assertTrue(migrated.changes('past',after=0)['resync_required'])
        self.assertTrue(migrated.changes('never-seen',after=123)['resync_required'])

    def test_hyphenated_tag_alias_is_already_searchable(self):
        m=self.active(title='Unrelated fact',tags=['unique-hyphen-alias'])
        self.assertEqual(self.b.search('unique-hyphen-alias','audit')['results'][0]['id'],m['id'])

    def test_expired_relation_can_have_new_period_without_erasing_history(self):
        a=self.active(title='Zebra incident');b=self.active(title='Unique repair',content='External material')
        old=self.b.relate(a['id'],b['id'],'supports','Tester','2020-01-01T00:00:00Z','2020-02-01T00:00:00Z')
        new=self.b.relate(a['id'],b['id'],'supports','Tester')
        self.assertNotEqual(new['id'],old['id']);self.assertIsNone(new['valid_to'])
        self.assertEqual(len(self.b.get(a['id'])['relations']),2)
        self.assertIn(b['id'],[r['id'] for r in self.b.search('zebra','audit')['results']])
        self.assertEqual(self.b.relate(a['id'],b['id'],'supports','Tester')['id'],new['id'])

    def test_relation_migration_preserves_rows_and_is_repeatable(self):
        a=self.active();b=self.active(title='Second fact')
        edge=self.b.relate(a['id'],b['id'],'supports','Tester')
        with self.b._connection() as con:con.execute('PRAGMA user_version=2')
        for _ in range(2):
            migrated=BrainStore(self.root)
            self.assertEqual(migrated.get(a['id'])['relations'][0]['id'],edge['id'])

    def test_status_does_not_expose_other_user_projects_or_counts(self):
        self.active()
        other=self.active(project_id='private-scope',user_id='other')
        self.assertEqual(self.b.status()['counts']['active'],1)
        self.assertEqual(self.b.status()['projects'],['audit'])
        self.assertEqual(self.b.snapshot('private-scope','other')['status']['projects'],['private-scope'])
        self.b.forget(other['id'],'Tester','Remove synthetic other user scope')
        self.assertEqual(self.b.status('other')['projects'],[])

    def test_scheduled_validity_and_context_omissions_are_explicit(self):
        future=self.active(valid_from='2100-01-01T00:00:00Z')
        self.assertEqual(self.b.get(future['id'])['validity_state'],'scheduled')
        current=self.active(title='needle now',content='needle '+'large '*400)
        recall=self.b.search('needle','audit',max_chars=256)
        self.assertIn(current['id'],recall['context_omitted_ids'])
        self.assertLessEqual(len(recall['context']),256)


if __name__=='__main__':unittest.main()
