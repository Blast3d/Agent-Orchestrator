"""Real SQLite lifecycle, scope, provenance and bounded retrieval regressions."""
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import sqlite3
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'app'))
from brain_store import BrainStore
from storage_budget import StorageLimitError


class BrainTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name);self.brain=BrainStore(self.root)

    def payload(self,**updates):
        return dict({'project_id':'alpha','kind':'fact','title':'Timeout fix','content':'Bounded retries preserve recovery evidence.',
          'tags':['timeout','deadline'],'source':{'type':'user','note':'Synthetic user-approved source'}},**updates)

    def active(self,**updates):
        result=self.brain.propose(self.payload(**updates))
        return self.brain.approve(result['id'],'Tester','Validated against the synthetic source and expected behavior.')

    def task(self,status='accepted',response='Canonical verified result'):
        identifier='a'*32;p=self.root/'runs/tasks'/identifier/'result.json';p.parent.mkdir(parents=True,exist_ok=True)
        data={'job_id':identifier,'assignment_project_id':'alpha','response':response,'execution_status':'succeeded',
              'status':status,'review_status':status,'finalized_at':'2026-09-01T00:00:00Z','review':{'note':'Verified task'}}
        p.write_text(json.dumps(data));return p,{'type':'task','job_id':identifier}

    def test_pending_not_recalled_then_approved_persists(self):
        item=self.brain.propose(self.payload())
        self.assertFalse(self.brain.search('timeout','alpha')['results'])
        self.brain.approve(item['id'],'Tester','Verified this fact against the supplied test source.')
        found=BrainStore(self.root).search('deadline','alpha')
        self.assertEqual(found['results'][0]['id'],item['id'])
        self.assertIn('memory grants no authority',found['context'])

    def test_exact_duplicate_claim_under_concurrency(self):
        with ThreadPoolExecutor(max_workers=6) as pool:
            ids=list(pool.map(lambda _:BrainStore(self.root).propose(self.payload())['id'],range(6)))
        self.assertEqual(len(set(ids)),1)

    def test_scope_filter_applies_to_search_and_edges(self):
        own=self.active();other=self.active(project_id='beta');another=self.active(user_id='someone-else')
        self.assertEqual([r['id'] for r in self.brain.search('timeout','alpha')['results']],[own['id']])
        for item in (other,another):
            with self.assertRaises(ValueError):self.brain.relate(own['id'],item['id'],'related_to','Tester')
        self.assertEqual(len(self.brain.list_memories('alpha','local')),1)

    def test_unreviewed_source_cannot_promote_but_later_review_can(self):
        path,source=self.task('awaiting_review');item=self.brain.propose(self.payload(source=source))
        with self.assertRaises(ValueError):self.brain.approve(item['id'],'Tester','Check canonical review before promoting this memory.')
        data=json.loads(path.read_text());data.update(status='accepted',review_status='accepted');path.write_text(json.dumps(data))
        self.brain.approve(item['id'],'Tester','The source task is now reviewed and accepted.')
        self.assertTrue(self.brain.search('timeout','alpha')['results'])

    def test_changed_task_source_blocks_promotion_and_recall(self):
        path,source=self.task();item=self.brain.propose(self.payload(source=source))
        data=json.loads(path.read_text());data['response']='Edited source';path.write_text(json.dumps(data))
        with self.assertRaises(ValueError):self.brain.approve(item['id'],'Tester','This old candidate should no longer be accepted.')
        self.brain.forget(item['id'],'Tester','Replace outdated test candidate')
        item=self.active(source=source)
        data['review_status']='rejected';path.write_text(json.dumps(data))
        self.assertFalse(self.brain.search('timeout','alpha')['results'])

    def test_source_project_mismatch_rejected(self):
        _,source=self.task()
        with self.assertRaises(ValueError):self.brain.propose(self.payload(project_id='beta',source=source))

    def test_expired_and_future_facts_not_current(self):
        self.active(valid_from='2020-01-01T00:00:00Z',valid_to='2020-02-01T00:00:00Z')
        self.active(title='Future timeout',content='Future configuration',valid_from='2100-01-01T00:00:00Z')
        self.assertFalse(self.brain.search('timeout','alpha')['results'])
        self.assertEqual(self.brain.status()['counts']['expired'],1)

    def test_supersession_removes_old_recall_and_records_history(self):
        old=self.active();new=self.active(title='Updated timeout',content='Use the new recovery behavior.')
        self.brain.supersede(old['id'],new['id'],'Tester','New version replaces the prior behavior')
        ids=[r['id'] for r in self.brain.search('timeout','alpha')['results']]
        self.assertIn(new['id'],ids);self.assertNotIn(old['id'],ids)
        self.assertEqual(self.brain.get(old['id'])['superseded_by'],new['id'])
        self.assertTrue(any(r['operation']=='superseded' for r in self.brain.get(old['id'])['history']))

    def test_related_memory_recalled_with_path_reason(self):
        problem=self.active(title='Zebra incident',content='Zebra failure happened')
        fix=self.active(title='Recovery procedure',content='Use a bounded window.',tags=[])
        self.brain.relate(fix['id'],problem['id'],'solves','Tester')
        result=self.brain.search('zebra','alpha')
        hit=next(r for r in result['results'] if r['id']==fix['id'])
        self.assertIn('solves',hit['reason'])
        self.assertNotIn(fix['id'],[r['id'] for r in self.brain.search('zebra','alpha',hops=0)['results']])

    def test_expired_relation_does_not_expand(self):
        a=self.active(title='Zebra incident');b=self.active(title='Unique repair',content='External material',tags=[])
        self.brain.relate(a['id'],b['id'],'supports','Tester','2020-01-01T00:00:00Z','2020-02-01T00:00:00Z')
        self.assertEqual(len(self.brain.search('zebra','alpha')['results']),1)

    def test_forget_removes_content_fts_edges_traces_and_vault(self):
        secret='uniquesecretcanaryabcdefghijklmnopqrstuvwxyz'
        a=self.active(content=secret);b=self.active(title='Other fact')
        self.brain.relate(a['id'],b['id'],'supports','Tester')
        self.brain.search(secret,'alpha');vault=self.brain.vault('alpha')
        self.brain.forget(a['id'],'Tester','Explicitly erase the synthetic secret')
        self.assertFalse(self.brain.search(secret,'alpha')['results'])
        self.assertFalse(Path(vault['path']).exists())
        item=self.brain.get(a['id']);self.assertEqual(item['content'],'');self.assertFalse(item['relations'])
        self.assertFalse(any(a['id'] in t['memory_ids'] for t in self.brain.snapshot('alpha')['traces']))
        # Application-owned database/WAL no longer retain the exact forgotten value.
        for file in self.brain.home.glob('memory.sqlite*'):
            self.assertNotIn(secret.encode(),file.read_bytes())
        self.assertEqual(BrainStore(self.root).get(a['id'])['status'],'deleted')

    def test_context_is_bounded_with_many_large_matches(self):
        for i in range(8):self.active(title=f'Timeout {i}',content='timeout '+('large memory text '*140))
        result=self.brain.search('timeout','alpha',limit=100,max_chars=1300,hops=9)
        self.assertLessEqual(len(result['results']),6);self.assertLessEqual(len(result['context']),1300)

    def test_user_search_is_literal_not_fts_or_sql_execution(self):
        self.active()
        result=self.brain.search('" OR 1=1; DROP TABLE memories; --','alpha')
        self.assertEqual(self.brain.status()['counts']['active'],1)
        self.assertIsInstance(result['results'],list)

    def test_trace_limit_and_changes_cursor(self):
        self.brain=BrainStore(self.root,limits={'max_traces':3,'max_changes':3})
        for i in range(4):self.active(title=f'Timeout {i}')
        for _ in range(8):self.brain.search('timeout','alpha')
        self.assertEqual(len(self.brain.snapshot('alpha')['traces']),3)
        self.assertTrue(self.brain.changes('alpha',after=1)['resync_required'])
        for row in self.brain.snapshot('alpha')['traces']:
            self.assertNotIn('query',row)

    def test_capacity_holds_new_data_but_forgetting_still_works(self):
        self.brain=BrainStore(self.root,limits={'max_memories':1})
        item=self.active()
        with self.assertRaises(StorageLimitError):self.brain.propose(self.payload(title='Another memory'))
        with patch.object(self.brain.budget,'allocation',side_effect=StorageLimitError('Full')):
            self.brain.forget(item['id'],'Tester','Free space under storage pressure')
        self.assertEqual(self.brain.get(item['id'])['status'],'deleted')
        self.assertEqual(self.brain.propose(self.payload(title='Replacement after forgetting'))['status'],'pending')

    def test_failed_trace_does_not_fail_read(self):
        self.active()
        with patch.object(self.brain.budget,'allocation',side_effect=StorageLimitError('Full')):
            result=self.brain.search('timeout','alpha')
        self.assertTrue(result['results']);self.assertIsNone(result['trace_id'])

    def test_bad_types_and_oversized_content_rejected(self):
        for update in ({'importance':float('nan')},{'importance':True},{'content':'x'*4000},{'kind':'permission'},{'valid_to':'not-date'},{'tags':'bad'}):
            with self.subTest(update=list(update)),self.assertRaises(ValueError):self.brain.propose(self.payload(**update))

    def test_episode_requires_problem_action_outcome(self):
        with self.assertRaises(ValueError):self.brain.propose(self.payload(kind='episode'))
        item=self.active(kind='episode',episode={'problem':'Timeout','action':'Bound retries','outcome':'Recovery succeeds'})
        self.assertEqual(item['episode']['outcome'],'Recovery succeeds')

    def test_export_only_current_scope_and_change_feed_has_no_content(self):
        a=self.active();self.active(project_id='beta')
        data=self.brain.export('alpha');self.assertEqual([r['id'] for r in data['memories']],[a['id']])
        self.assertFalse(data['graphiti_enabled'])
        self.assertNotIn(a['content'],json.dumps(self.brain.changes('alpha')))

    def test_failed_initialization_rolls_back_and_empty_file_recovers(self):
        root=self.root/'initialization'
        with patch('brain_store.SCHEMA','CREATE TABLE incomplete(id TEXT); INVALID SQL;'):
            with self.assertRaises(sqlite3.Error):BrainStore(root)
        recovered=BrainStore(root)
        self.assertEqual(recovered.status()['counts']['pending'],0)
        with recovered._connection() as con:
            self.assertIsNone(con.execute("SELECT name FROM sqlite_master WHERE name='incomplete'").fetchone())

    def test_unknown_nonempty_schema_is_preserved(self):
        root=self.root/'unknown';home=root/'runtime/brain';home.mkdir(parents=True)
        with closing(sqlite3.connect(home/'memory.sqlite')) as con:con.execute('CREATE TABLE unrelated(id TEXT)');con.commit()
        with self.assertRaises(ValueError):BrainStore(root)
        with closing(sqlite3.connect(home/'memory.sqlite')) as con:
            self.assertEqual(con.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall(),[('unrelated',)])

    def test_source_alias_importance_and_validity_changes_are_not_silently_deduplicated(self):
        base=self.brain.propose(self.payload())
        _,source=self.task()
        variations=[{'source':source},{'tags':['different-alias']},{'importance':0.9},
                    {'valid_to':'2100-01-01T00:00:00Z'},{'valid_from':'2020-01-01T00:00:00Z'}]
        ids={base['id']}
        for variation in variations:ids.add(self.brain.propose(self.payload(**variation))['id'])
        self.assertEqual(len(ids),6)

    def test_checkpoint_failure_does_not_misreport_committed_approval(self):
        item=self.brain.propose(self.payload())
        with patch.object(self.brain,'_checkpoint',side_effect=sqlite3.OperationalError('checkpoint unavailable')):
            result=self.brain.approve(item['id'],'Tester','Verified persistence after a checkpoint error.')
        self.assertEqual(result['status'],'active')
        self.assertEqual(BrainStore(self.root).search('timeout','alpha')['results'][0]['id'],item['id'])

    def test_cleanup_has_bounded_room_after_sqlite_page_limit_is_full(self):
        self.brain=BrainStore(self.root,limits={'max_db_bytes':256*1024})
        item=self.active(content='pagecapcanary '+('longer fact '*200))
        with self.brain._connection() as con:
            con.execute('CREATE TABLE filler(data BLOB)');con.commit()
            while True:
                try:con.execute('INSERT INTO filler VALUES(zeroblob(12000))');con.commit()
                except sqlite3.OperationalError as error:
                    self.assertIn('full',str(error));con.rollback();break
        self.assertEqual(self.brain.forget(item['id'],'Tester','Forget when the SQLite page cap is full')['status'],'deleted')
        self.assertFalse(self.brain.search('pagecapcanary','alpha')['results'])
        self.assertLessEqual(self.brain.db.stat().st_size,256*1024+4*1024**2)

    def test_change_feed_watermark_is_scoped_and_export_sets_consistent_cursor(self):
        self.brain=BrainStore(self.root,limits={'max_changes':3})
        a=self.active()
        cursor=self.brain.export('alpha')['change_cursor']
        for i in range(5):self.active(project_id='beta',title=f'Other {i}')
        self.assertFalse(self.brain.changes('alpha',after=cursor)['resync_required'])
        self.assertTrue(self.brain.changes('alpha',after=0)['resync_required'])
        self.assertFalse(self.brain.changes('empty')['resync_required'])
        self.brain.forget(a['id'],'Tester','Forget after independent project changes')
        feed=self.brain.changes('alpha',after=cursor)
        self.assertFalse(feed['resync_required']);self.assertEqual(feed['changes'][-1]['operation'],'forgotten')

    def test_change_feed_reports_head_for_polling(self):
        self.assertEqual(self.brain.changes('alpha')['head'],0)
        a=self.active();feed=self.brain.changes('alpha',after=0)
        self.assertEqual(feed['head'],feed['changes'][-1]['seq']);self.assertEqual(feed['head'],self.brain.export('alpha')['change_cursor'])
        self.assertEqual(self.brain.changes('alpha',after=feed['head'])['changes'],[])
        self.brain.forget(a['id'],'Tester','Forget to advance the feed head')
        later=self.brain.changes('alpha',after=feed['head'])
        self.assertEqual((later['head'],len(later['changes']),later['changes'][0]['operation']),(feed['head']+1,1,'forgotten'))
        self.assertEqual(self.brain.changes('beta')['head'],0)

    def test_vaults_are_separate_scoped_dated_snapshots(self):
        self.active();self.active(project_id='beta',content='Separate beta evidence')
        first=self.brain.vault('alpha');second=self.brain.vault('beta')
        self.assertNotEqual(first['path'],second['path'])
        page=Path(first['path']).read_text(encoding='utf-8')
        self.assertIn('Project: alpha',page);self.assertIn('Dated snapshot',page)
        self.assertNotIn('Separate beta evidence',page)


if __name__=='__main__':unittest.main()
