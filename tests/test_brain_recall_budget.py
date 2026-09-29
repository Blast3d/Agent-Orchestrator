"""Task-sized delivery, project isolation and bounded startup receipts."""
import argparse
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'app'))
from brain_store import BrainStore
from brain_recall_budget import resolve
from memory_usage import recall_plan, contract_fields
from orchestration_lifecycle import start_run
from memory_delivery import fit, heading
from storage_budget import StorageLimitError


class RecallBudgetTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.brain = BrainStore(self.root)
        mocked = patch('jev_openrouter.load_config', return_value={'status': 'disabled'})
        mocked.start(); self.addCleanup(mocked.stop)

    def memory(self, index, project='alpha', content=None):
        value = self.brain.propose(dict(project_id=project, kind='fact',
            title='Retry step ' + str(index), content=content or ('Retry detail ' + str(index)),
            source={'type':'user','note':'Synthetic bounded recall test'}))
        return self.brain.approve(value['id'], 'Tester', 'Checked this synthetic fixture evidence.')

    def test_larger_task_receives_more_distinct_current_evidence_without_cross_scope(self):
        for index in range(30): self.memory(index)
        foreign = self.memory(999, project='private-project')
        removed = self.memory(998)
        self.brain.forget(removed['id'], 'Tester', 'Remove synthetic obsolete evidence')
        for depth, expected in [('compact',6), ('balanced',12), ('deep',24)]:
            result = self.brain.search('retry', 'alpha', depth=depth, record_trace=False)
            self.assertEqual(len(result['results']), expected)
            self.assertNotIn(foreign['id'], result['context'])
            self.assertNotIn(removed['id'], result['context'])
            self.assertLessEqual(result['context_chars'], resolve(depth)['max_chars'])
            self.assertEqual(result['retrieval']['budget']['depth'], depth)

    def test_wider_corpus_does_not_pad_unmatched_query_or_small_result(self):
        for index in range(20): self.memory(index)
        result = self.brain.search('platypus', 'alpha', depth='deep', strategy='keyword', record_trace=False)
        self.assertEqual(result['results'], [])
        one = self.memory(50, content='Unique zirconium calibration procedure')
        result = self.brain.search('zirconium', 'alpha', depth='deep', strategy='keyword', record_trace=False)
        self.assertEqual([r['id'] for r in result['results']], [one['id']])

    def test_worker_auto_depth_is_task_size_and_override_is_assignment_identity(self):
        args = argparse.Namespace(project='alpha',task='Retry implementation',size='large',category='implementation')
        deep = recall_plan(args)
        self.assertEqual(deep['budget']['depth'], 'deep')
        args.memory_depth = 'compact'
        compact = recall_plan(args)
        self.assertNotEqual(contract_fields(deep), contract_fields(compact))
        args.no_memory = True
        self.assertEqual(contract_fields(recall_plan(args)), {'memory_policy':'disabled','memory_profile':'implementation'})

    def test_explicit_caps_and_invalid_types(self):
        self.assertEqual(resolve('deep',limit=1000,max_chars=100000)['limit'],24)
        self.assertEqual(resolve('deep',limit=1000,max_chars=100000)['max_chars'],32000)
        for params in ({'limit':True},{'max_chars':'2000'},{'depth':'giant'}):
            with self.assertRaises(ValueError): resolve(**params)

    def test_wide_unicode_startup_fits_receipt_without_repeating_lookup(self):
        for index in range(24): self.memory(index,content='Retry '+ ('\u2603' * 900))
        with patch.object(BrainStore, 'search', autospec=True, side_effect=BrainStore.search) as search:
            result = start_run(workspace=self.root,name='unicode',objective='retry',project='alpha',
                               root=self.root,memory_depth='deep')
        self.assertEqual(search.call_count, 1)
        packet = self.root / '.orchestration' / result['run_id'] / 'startup-context.json'
        self.assertLessEqual(packet.stat().st_size, 64*1024)
        memory = result['project_memory']
        self.assertLess(len(memory['results']),24)
        self.assertGreater(len(memory['results']),0)
        self.assertEqual(memory['startup_delivery']['delivered_count'],len(memory['results']))
        self.assertEqual(json.loads(packet.read_text())['project_memory']['context'],memory['context'])

    def test_unicode_memory_fits_worker_bytes_without_altering_task_or_warning(self):
        for index in range(24): self.memory(index,content='Retry '+ ('\u2603' * 900))
        found = self.brain.search('retry','alpha',depth='deep',record_trace=False)
        found['recall_incomplete'] = True
        found['source_validation']['excluded_memories'] = 1
        base = 'Operating guide and exact assigned brief.\n' * 80
        for worker,maximum in [('gemini',12000),('vscode-copilot',32768)]:
            result = fit(found,worker=worker,base_prompt=base,prefix='Worker: ')
            supplied = 'Worker: '+base+heading('general')+result['context']
            self.assertLessEqual(len(supplied.encode('utf-8')),maximum)
            self.assertIn(base,supplied)
            self.assertIn('Recall scan limit reached',result['context'])
            self.assertIn('Unverifiable task-backed memories were excluded',result['context'])
            self.assertEqual(result['delivery']['delivered_count'],len(result['results']))
        with self.assertRaises(ValueError):
            fit(found,worker='gemini',base_prompt='x'*12001)

    def test_trace_cleanup_failure_preserves_recall_and_reports_reservation_problem(self):
        memory = self.memory(1)
        with patch.object(self.brain.budget,'release',side_effect=StorageLimitError('Synthetic release failure')):
            result = self.brain.search('retry','alpha')
        self.assertEqual(result['results'][0]['id'],memory['id'])
        self.assertEqual(result['trace_cleanup_status'],'failed')
        self.assertIn('reservation cleanup',' '.join(result['warnings']))

    def test_initial_and_final_trace_share_one_reservation_and_release_it(self):
        for index in range(8): self.memory(index)
        config={'status':'ready','key_present':True,'authorized_projects':['alpha'],
                'purposes':['memory_rank'],'min_confidence':.8}
        def evaluate(root, project, purpose, state, questions, **kwargs):
            self.assertEqual(self.brain.budget.status()['reservation_count'],1)
            return {'status':'ok','provider_calls':1,'answers':{
                key:{'type':'score','score':2,'confidence':.9} for key in questions}}
        with patch('jev_openrouter.load_config',return_value=config), \
                patch('jev_openrouter.evaluate',side_effect=evaluate), \
                patch.object(self.brain.budget,'reserve',wraps=self.brain.budget.reserve) as reserve:
            result=self.brain.search('retry','alpha')
        self.assertEqual(reserve.call_count,1)
        self.assertIsNotNone(result['trace_id'])
        self.assertEqual(self.brain.budget.status()['reservation_count'],0)


if __name__ == '__main__': unittest.main()
