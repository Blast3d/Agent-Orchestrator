"""Measured performance becomes recallable evidence without altering legacy receipts."""
from copy import deepcopy
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'app'))
from automatic_memory import record_accepted_outcome, _payload
from brain_store import BrainStore, digest
from task_store import TaskStore
from task_performance import snapshot
from project_memory_usage import _capture
from orchestration_lifecycle import _receipt


class TaskPerformanceTests(unittest.TestCase):
    def setUp(self):
        temp=tempfile.TemporaryDirectory();self.addCleanup(temp.cleanup)
        self.root=Path(temp.name);self.store=TaskStore(self.root/'runs/tasks')
        self.task=self.store.create(worker='grok',task='Retry cancellation implementation',size='medium',
            category='coding',assignment_project_id='alpha',assignment_id='retry-v1')
        self.task.update(created_at='2026-09-25T12:00:00+00:00',started_at='2026-09-25T12:00:10+00:00',
            ended_at='2026-09-25T12:01:00+00:00',finalized_at='2026-09-25T12:01:02+00:00',
            status='accepted',execution_status='succeeded',review_status='accepted',response='PRIVATE ANSWER',
            review={'reviewer':'Tester','note':'Verified the cancellation implementation and actual task timing.'},
            usage={'input_tokens':123,'output_tokens':45,'cache_read_input_tokens':67,'total_tokens':168},
            modelUsage={'grok-test':{'costUSD':.005}},phase_durations_ms={'provider_execution':50000},
            memory_context={'ids':['a'*32],'execution_requested':False,'elapsed_ms':250,
                            'retrieval':{'jev':{'provider_calls':0,'cost_usd':0,'cache':{'origin_usage':{'cost_usd':9}}}}})
        self.save()

    def save(self):self.store.save(self.task['job_id'],self.task)

    def test_wall_execution_tokens_and_billing_remain_separate(self):
        value=snapshot(self.task)
        self.assertEqual((value['wall_seconds'],value['execution_seconds']),(62,50))
        self.assertEqual((value['input_tokens'],value['output_tokens'],value['cached_input_tokens']),(123,45,67))
        self.assertEqual(value['provider_reported_cost_usd'],.005)
        self.assertIsNone(value['verified_additional_charge_usd'])
        self.assertEqual((value['jev_calls'],value['jev_cost_usd']),(0,0))
        self.assertEqual((value['memories_recalled'],value['memories_supplied']),(1,0))

    def test_unknowns_and_invalid_dates_are_not_zero(self):
        value=snapshot({'worker':'grok','created_at':'bad','finalized_at':'bad',
                        'phase_durations_ms':{'provider_execution':float('nan')},'usage':{'input_tokens':True}})
        for key in ('wall_seconds','execution_seconds','input_tokens','output_tokens','provider_reported_cost_usd','jev_calls'):
            self.assertIsNone(value[key])

    def test_transport_reduction_preserves_retrieved_and_supplied_counts(self):
        context = self.task['memory_context']
        context.update(execution_requested=True, delivery={
            'reason':'worker transport byte limit', 'retrieved_count':24, 'delivered_count':1})
        value = snapshot(self.task)
        self.assertEqual((value['memories_recalled'], value['memories_supplied']), (24, 1))
        context['ids'] = []
        context['delivery']['delivered_count'] = 0
        value = snapshot(self.task)
        self.assertEqual((value['memories_recalled'], value['memories_supplied']), (24, 0))
        context['delivery']['retrieved_count'] = 999
        self.assertEqual(snapshot(self.task)['memories_recalled'], 0)

    def test_automatic_performance_is_searchable_and_receipts_validate(self):
        receipt=record_accepted_outcome(self.store,self.task['job_id'])
        self.assertEqual(receipt['status'],'remembered',receipt)
        self.assertEqual(receipt['automatic_version'],2)
        brain=BrainStore(self.root);memory=brain.get(receipt['memory_id'])
        self.assertIn('"wall_seconds":62.0',memory['content'])
        self.assertIn('performance',memory['tags'])
        self.assertNotIn('PRIVATE ANSWER',memory['content'])
        self.assertEqual(brain.search('performance retry duration','alpha',record_trace=False)['results'][0]['id'],memory['id'])
        self.assertEqual(_capture(receipt,None,self.task)['status'],'remembered')
        self.assertEqual(_receipt(self.root,self.task,'alpha',brain)['mode'],'automatic')

    def test_changed_usage_invalidates_new_memory_without_recreating_it(self):
        receipt=record_accepted_outcome(self.store,self.task['job_id']);brain=BrainStore(self.root)
        self.task['usage']['input_tokens']=999;self.save()
        self.assertEqual(brain.search('retry','alpha',record_trace=False)['results'],[])
        self.assertEqual(record_accepted_outcome(self.store,self.task['job_id'])['status'],'error')
        self.assertEqual(_capture(receipt,None,self.task)['status'],'stale')

    def test_legacy_receipt_retries_without_upgrading_or_resurrection(self):
        original=_payload
        with patch('automatic_memory._payload',side_effect=lambda r,c,**kw:original(r,c)):
            receipt=record_accepted_outcome(self.store,self.task['job_id'])
        path=self.store.directory(self.task['job_id'])/'memory-outcome.json'
        receipt.pop('automatic_version');path.write_text(json.dumps(receipt))
        again=record_accepted_outcome(self.store,self.task['job_id'])
        self.assertEqual(again['status'],'remembered',again)
        brain=BrainStore(self.root);self.assertNotIn('performance_sha256',brain.get(receipt['memory_id'])['source'])
        brain.forget(receipt['memory_id'],'Tester','Forget this synthetic legacy episode')
        final=record_accepted_outcome(self.store,self.task['job_id'])
        self.assertEqual(final['reason'],'forgotten')
        self.assertEqual(len(brain.list_memories('alpha')),1)


if __name__=='__main__':unittest.main()
