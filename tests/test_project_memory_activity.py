"""Recall telemetry must remain independent of attribution and private content."""
import json
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'app'))
from contributions import build_report
from project_memory_activity import memory_activity
from project_visuals import project_from_report


def event(trace='t1', **metadata):
    return {'id': trace, 'task_id': trace, 'agent_id': 'jev', 'kind': 'memory_recall',
            'status': 'succeeded', 'input_tokens': 12, 'output_tokens': 3, 'jev': metadata}


class MemoryActivityTests(unittest.TestCase):
    def test_real_recall_with_zero_credit_is_visible(self):
        data={'schema_version':1,'scope_id':'test','title':'Test','basis':'Reviewed estimate',
              'contributors':[{'id':'jev','name':'Jev','provider':'TypeSafe','model':'unknown'},
                              {'id':'lead','name':'Lead','provider':'OpenAI','model':'unknown'}],
              'work_items':[{'id':'work','label':'Finished','category':'code','status':'accepted','weight':1,
                             'allocations':[{'agent_id':'lead','percent':100,'evidence':'private/path'}]}],
              'activity':[event(provider_calls=1,elapsed_ms=1188.087,order_changed=False,
                                status='low_confidence',candidate_count=16,scored_candidate_count=15,
                                returned_count=6,cache={'status':'miss'})]}
        view=project_from_report(build_report(data),'2026-09-25')
        jev=view['people'][0]
        self.assertEqual(jev['share_pct'],0)
        self.assertEqual(jev['attempts'],0)
        self.assertEqual(jev['memory']['recalls'],1)
        self.assertEqual(jev['memory']['provider_calls']['value'],1)
        self.assertEqual(jev['memory']['elapsed_ms']['value'],1188.087)
        self.assertEqual(jev['memory']['order_changed'],{'value':0,'recorded':1,'total':1})
        self.assertEqual(jev['memory']['low_confidence']['value'],1)
        self.assertNotIn('memory',view['people'][1])
        self.assertNotIn('private/path',json.dumps(view))

    def test_cache_hit_has_zero_new_usage_ignores_origin(self):
        row=event(provider_calls=0,input_tokens=0,output_tokens=0,cost_usd=0,
                  cache={'status':'hit'},origin_usage={'input_tokens':99999,'provider_calls':1})
        result=memory_activity([row])
        self.assertEqual(result['recalls'],1)
        for key in ['provider_calls','input_tokens','output_tokens','cost_usd']:
            self.assertEqual(result[key]['value'],0)
        self.assertEqual(result['cache_hit']['value'],1)

    def test_legacy_activity_never_infers_a_provider_call(self):
        row=event();row.pop('jev');row['evidence']='one call; latency 1ms'
        result=memory_activity([row])
        self.assertIsNone(result['provider_calls']['value'])
        self.assertIsNone(result['elapsed_ms']['value'])
        self.assertIsNone(result['order_changed']['value'])
        self.assertEqual(result['input_tokens']['value'],12)

    def test_partial_coverage_is_explicit_and_zero_is_known(self):
        result=memory_activity([event('a',provider_calls=0),event('b')])
        self.assertEqual(result['provider_calls'],{'value':0,'recorded':1,'total':2})

    def test_duplicate_trace_is_counted_once_preferring_metadata(self):
        old=event();old.pop('jev')
        row=event(provider_calls=1,elapsed_ms=500)
        result=memory_activity([old,row,row])
        self.assertEqual(result['recalls'],1)
        self.assertEqual(result['provider_calls']['value'],1)
        self.assertEqual(result['input_tokens']['value'],12)

    def test_invalid_values_stay_unknown(self):
        for value in [True,False,-1,float('inf'),float('nan'),'1',{},10**400]:
            with self.subTest(value=str(value)[:40]):
                result=memory_activity([event(provider_calls=value,elapsed_ms=value,order_changed='false')])
                self.assertIsNone(result['provider_calls']['value'])
                self.assertIsNone(result['elapsed_ms']['value'])
                self.assertIsNone(result['order_changed']['value'])

    def test_only_explicit_recall_events_are_included(self):
        row=event();row['kind']='delegation'
        self.assertIsNone(memory_activity([row]))
        self.assertIsNone(memory_activity([]))

    def test_private_or_arbitrary_fields_never_enter_view(self):
        row=event(provider_calls=1,reason='private query',model='private model',
                  source={'path':'secret/path'},cache={'status':'miss','key':'secret-key'})
        row['evidence']='private source text';row['query']='private query'
        result=json.dumps(memory_activity([row]))
        for private in ['private','secret','query','model','t1']:
            self.assertNotIn(private,result)

    def test_invocations_are_summed_without_claiming_unique_memories(self):
        result=memory_activity([event('a',provider_calls=1,returned_count=6,order_changed=False),
                                event('b',provider_calls=0,returned_count=6,order_changed=True)])
        self.assertEqual(result['recalls'],2)
        self.assertEqual(result['returned_count']['value'],12)
        self.assertEqual(result['order_changed'],{'value':1,'recorded':2,'total':2})


if __name__=='__main__': unittest.main()
