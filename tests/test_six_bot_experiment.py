"""Six-seat evidence remains distinct across shared providers and conditions."""
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'app'))
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'benchmarks/memory_control'))
from test_experiment_view import ExperimentViewTests,write
from six_bot_roster import declaration
from transport import write as durable_write

class SixWorkerViewTests(ExperimentViewTests):
    def prepare_six(self):
        write(self.parent/'experiment.json',{'schema_version':1,'family':'six_bot_memory','conditions':[
          {'id':'team-cold','run_id':self.child.name,'label':'Six cold'},
          {'id':'team-warm','run_id':self.other.name,'label':'Six warm'}]})
        for run,key in ((self.child,'team-cold'),(self.other,'team-warm')):
            write(run/'condition.json',{'schema_version':1,'family':'six_bot_memory','parent_run_id':'suite',
              'run_id':run.name,'id':key,'project_id':run.name,'status':'completed','helper_count':0,
              'contestant_count':6,'memory_mode':'seeded' if key=='team-warm' else 'disabled','roster':declaration()})

    def test_six_seats_same_provider_usage_and_outputs_stay_separate(self):
        self.prepare_six()
        names=[]
        for i,r in enumerate(declaration(),1):
            name,_=self.call(self.child,'team-cold',r['id'],r['provider'],role=r['role'],
              usage={'input_tokens':i*10,'output_tokens':i,'total_tokens':i*11,
                     'cache_read_input_tokens':0,'cache_creation_input_tokens':0})
            names.append(name)
        self.call(self.other,'team-warm','openai_a','OpenAI',role='normalization',usage={'input_tokens':999,'output_tokens':1})
        result=self.store.detail('suite','team-cold')
        self.assertEqual(result['metrics']['contestant_count'],6)
        self.assertEqual(result['metrics']['provider_count'],3)
        self.assertEqual(result['metrics']['input_tokens'],210)
        self.assertEqual([r['calls'] for r in result['roster']],[1]*6)
        self.assertEqual(len({r['worker_id'] for r in result['calls']}),6)
        with self.assertRaises(ValueError):self.store.output('suite','team-warm','call:'+names[0]+':response')

    def test_same_provider_actor_cannot_relabel_another_seat_call(self):
        self.prepare_six()
        name,record=self.call(self.child,'team-cold','openai_a','OpenAI',role='normalization')
        record.update(actor_id='openai_b',role='ledger')
        write(self.child/'calls'/name/'record.json',record)
        with self.assertRaises(ValueError):self.store.detail('suite','team-cold')

class RecordWriteTests(unittest.TestCase):
    def test_transient_windows_read_lock_retries_only_local_record_write(self):
        with tempfile.TemporaryDirectory() as folder,patch('transport.write_json',side_effect=[PermissionError(),None]) as writer,patch('transport.time.sleep'):
            durable_write(Path(folder)/'record.json',{'status':'running'})
            self.assertEqual(writer.call_count,2)
    def test_persistent_denial_is_not_hidden(self):
        with tempfile.TemporaryDirectory() as folder,patch('transport.write_json',side_effect=PermissionError()) as writer,patch('transport.time.sleep'):
            with self.assertRaises(PermissionError):durable_write(Path(folder)/'record.json',{})
            self.assertEqual(writer.call_count,6)
