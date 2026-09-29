"""Adaptive recall integration with real SQLite and synthetic, offline evidence."""
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'app'))
from brain_store import BrainStore
from brain_semantic import fingerprint
from brain_retrieval_metadata import public_retrieval


class AdaptiveRecallTests(unittest.TestCase):
    def setUp(self):
        temporary=tempfile.TemporaryDirectory();self.addCleanup(temporary.cleanup)
        self.root=Path(temporary.name);self.brain=BrainStore(self.root)

    def active(self,title='Retry policy',content='Backoff prevents duplicate attempts.',**kwargs):
        data=dict(project_id='alpha',kind='fact',title=title,content=content,
                  source={'type':'user','note':'Synthetic adaptive recall fixture.'})
        data.update(kwargs);proposed=self.brain.propose(data)
        return self.brain.approve(proposed['id'],'Tester','Verified against this synthetic fixture.')

    def candidate(self,memory,score=.9):
        with self.brain._connection() as con:
            row=con.execute('SELECT * FROM memories WHERE id=?',(memory['id'],)).fetchone()
            return dict(id=memory['id'],score=score,fingerprint=fingerprint(row))

    def response(self,*candidates,**extra):
        result=dict(status='ok',reason='Synthetic provider result.',candidates=list(candidates),
                    provider_calls=1,input_tokens=7,model='synthetic-test',indexed_count=len(candidates))
        result.update(extra);return result

    def search(self,query,**kwargs):
        return self.brain.search(query,'alpha',record_trace=False,**kwargs)

    def test_empty_scope_skips_provider_storage_and_prompt_context(self):
        with patch('brain_semantic.semantic_candidates') as semantic, patch.object(self.brain.budget,'allocation') as allocation:
            result=self.brain.search('where is production config.json','alpha')
        semantic.assert_not_called();allocation.assert_not_called()
        self.assertEqual((result['context'],result['context_chars']),('',0))
        self.assertEqual(result['trace_status'],'skipped_empty')
        self.assertEqual(result['retrieval']['route'],'empty')
        self.assertEqual(result['retrieval']['provider_calls'],0)
        self.assertIsNone(result['retrieval']['context_tokens'])

    def test_exact_path_skips_optional_work_and_prioritizes_complete_match(self):
        exact=self.active('Production configuration','Path: app/production/config.json')
        self.active('Development config.json','Never use for production releases.')
        with patch('brain_semantic.semantic_candidates') as semantic:
            result=self.search('Where is app/production/config.json')
        semantic.assert_not_called()
        self.assertEqual(result['results'][0]['id'],exact['id'])
        self.assertEqual(result['retrieval']['route'],'keyword')
        self.assertEqual(result['retrieval']['graph_hops'],0)
        self.assertEqual(result['retrieval']['timings_ms']['graph'],0)

    def test_weak_match_without_configuration_keeps_keyword_result(self):
        item=self.active()
        result=self.search('retry zebra')
        self.assertIn(item['id'],[r['id'] for r in result['results']])
        self.assertEqual(result['retrieval']['quality'],'weak')
        self.assertEqual(result['retrieval']['semantic']['status'],'not_configured')
        self.assertEqual(result['retrieval']['provider_calls'],0)

    def test_relationship_query_adds_current_neighbor_keyword_mode_does_not(self):
        seed=self.active('Caller map','Caller map depends on shared library.')
        linked=self.active('Independent module','Stable shared routines.')
        self.brain.relate(seed['id'],linked['id'],'depends_on','Tester')
        result=self.search('what depends on caller map')
        self.assertEqual(result['retrieval']['route'],'graph')
        self.assertIn(linked['id'],[r['id'] for r in result['results']])
        keyword=self.search('what depends on caller map',strategy='keyword')
        self.assertNotIn(linked['id'],[r['id'] for r in keyword['results']])

    def test_semantic_seed_can_expand_current_graph(self):
        seed=self.active('Retry policy','Backoff prevents duplicate attempts.')
        linked=self.active('Incident remedy','Validated timer reset.')
        self.brain.relate(seed['id'],linked['id'],'supports','Tester')
        with patch('brain_semantic.semantic_candidates',return_value=self.response(self.candidate(seed))):
            result=self.search('why zebra looping')
        self.assertEqual(result['retrieval']['route'],'hybrid')
        self.assertEqual(result['retrieval']['semantic_added'],1)
        self.assertEqual(result['retrieval']['graph_added'],1)
        self.assertEqual({r['id'] for r in result['results']},{seed['id'],linked['id']})

    def test_provider_response_revalidates_relation_periods(self):
        seed=self.active('Retry policy');linked=self.active('Other remedy','External material.')
        edge=self.brain.relate(seed['id'],linked['id'],'supports','Tester')
        def provider(*args,**kwargs):
            with self.brain._connection() as con:
                con.execute("UPDATE relations SET valid_to='2020-01-01T00:00:00Z' WHERE id=?",(edge['id'],))
                con.commit()
            return self.response()
        with patch('brain_semantic.semantic_candidates',side_effect=provider):
            result=self.search('why retry zebra')
        self.assertNotIn(linked['id'],[r['id'] for r in result['results']])
        self.assertEqual(result['retrieval']['graph_added'],0)

    def test_provider_runs_outside_lock_and_forgotten_candidates_are_excluded(self):
        item=self.active();candidate=self.candidate(item)
        def provider(*args,**kwargs):
            # Forget needs the same Brain file lock. This would time out/deadlock
            # if provider execution retained the lock from lexical retrieval.
            self.brain.forget(item['id'],'Tester','Remove synthetic evidence during provider response.')
            return self.response(candidate)
        with patch('brain_semantic.semantic_candidates',side_effect=provider):
            result=self.search('retry zebra')
        self.assertEqual(result['results'],[])
        self.assertNotIn(item['content'],result['context'])

    def test_cross_scope_and_changed_vectors_cannot_enter_context(self):
        own=self.active();other=self.active(project_id='beta');private=self.active(user_id='other-user')
        stale=self.candidate(own);stale['fingerprint']='0'*64
        with patch('brain_semantic.semantic_candidates',return_value=self.response(stale,self.candidate(other),self.candidate(private))):
            result=self.search('zebra')
        self.assertEqual(result['results'],[])
        self.assertEqual(result['retrieval']['semantic_added'],0)

    def test_provider_timeout_preserves_keywords_and_unknown_tokens(self):
        item=self.active()
        with patch('brain_semantic.semantic_candidates',return_value=self.response(status='timeout',input_tokens=None)):
            result=self.search('retry zebra')
        self.assertIn(item['id'],[r['id'] for r in result['results']])
        self.assertEqual(result['retrieval']['provider_calls'],1)
        self.assertIsNone(result['retrieval']['input_tokens'])
        self.assertEqual(result['retrieval']['semantic']['status'],'timeout')

    def test_semantic_scan_bound_is_visible(self):
        self.active()
        with patch('brain_semantic.semantic_candidates',return_value=self.response(scan_limited=True)):
            result=self.search('zebra')
        self.assertTrue(result['recall_incomplete'])
        self.assertTrue(result['warnings'])

    def test_one_shared_distinct_task_budget_reaches_adapter_and_final_merge(self):
        self.active()
        def provider(*args,**kwargs):
            keys=kwargs['source_keys']
            self.assertIsInstance(keys,set)
            keys.update((str(i),'alpha') for i in range(128))
            return self.response(scan_limited=True)
        with patch('brain_semantic.semantic_candidates',side_effect=provider):
            result=self.search('zebra')
        self.assertTrue(result['recall_incomplete'])

    def test_context_limit_holds_after_semantic_expansion(self):
        item=self.active(content='Synthetic long evidence. '*100)
        with patch('brain_semantic.semantic_candidates',return_value=self.response(self.candidate(item))):
            result=self.search('zebra',max_chars=400)
        self.assertLessEqual(len(result['context']),400)
        self.assertLessEqual(len(result['results']),6)
        self.assertEqual(result['context_chars'],len(result['context']))

    def test_measurements_persist_without_plain_query_and_forget_cleans_them(self):
        item=self.active()
        result=self.brain.search('retry','alpha')
        reopened=BrainStore(self.root)
        trace=next(t for t in reopened.snapshot('alpha')['traces'] if t['id']==result['trace_id'])
        self.assertEqual(trace['retrieval']['route'],'keyword')
        self.assertIsNone(trace['retrieval']['context_tokens'])
        with reopened._connection() as con:
            detail=con.execute('SELECT detail FROM retrieval_traces WHERE trace_id=?',(result['trace_id'],)).fetchone()[0]
        self.assertNotIn('"query"',detail)
        reopened.forget(item['id'],'Tester','Remove synthetic recall references.')
        with reopened._connection() as con:
            self.assertEqual(con.execute('SELECT count(*) FROM retrieval_traces').fetchone()[0],0)

    def test_unknown_historical_metadata_stays_unknown(self):
        self.assertIsNone(public_retrieval(None))
        self.assertIsNone(public_retrieval({'route':'keyword'}))
        safe=public_retrieval({'schema_version':1,'provider_calls':True,'input_tokens':float('nan')})
        self.assertIsNone(safe['provider_calls']);self.assertIsNone(safe['input_tokens'])

    def test_invalid_mode_rejected_even_for_empty_project(self):
        with self.assertRaises(ValueError):self.search('query',strategy='anything')

if __name__=='__main__':unittest.main()
