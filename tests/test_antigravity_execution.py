"""Real child-process checks for AGY parser selection and interruption cleanup."""
import json,os,sys,tempfile,unittest
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'app'))
from worker_execution import invoke_cloud,WorkerInterrupted

class AntigravityExecutionTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name)
    def run_events(self,events,tail=''):
        code='import json,time,sys\n'
        for e in events:code+='print('+repr(json.dumps(e))+',flush=True)\n'
        code+=tail
        return invoke_cloud([sys.executable,'-u','-c',code,'--output-format','stream-json'],None,self.root,os.environ.copy(),protocol='antigravity',expected_model='gemini-test',timeout_seconds=3)
    def init(self):
        return {'event':'init','conversation_id':'test-id','init':{'cwd':str(self.root),'model':'gemini-test','permission_mode':'request-review','tools':['view_file']}}
    def result(self):
        return {'event':'result','result':{'conversation_id':'test-id','status':'SUCCESS','response':'correct','usage':{'input_tokens':2,'output_tokens':1}}}
    def test_valid_agy_stream_returns_answer(self):
        r=self.run_events([self.init(),self.result()])
        self.assertEqual(json.loads(r.stdout)['response'],'correct')
        self.assertTrue(r.progress['terminal_received'])
    def test_tool_violation_during_pipe_drain_stays_uncertain(self):
        tool={'event':'step_update','step_update':{'conversation_id':'test-id','step_type':'tool','tool_info':{},'tool_name':'view_file'}}
        with self.assertRaises(WorkerInterrupted) as cm:
            self.run_events([self.init(),tool,tool,self.result()],'time.sleep(8)')
        self.assertEqual(cm.exception.cause,'antigravity_tool_event')
        self.assertTrue(cm.exception.process_terminated)
        self.assertFalse(cm.exception.progress['terminal_received'])
    def test_missing_terminal_nonzero_exit_is_uncertain(self):
        with self.assertRaises(WorkerInterrupted) as cm:self.run_events([self.init()],'sys.exit(1)')
        self.assertEqual(cm.exception.cause,'missing_terminal_result')
    def test_wrong_workspace_is_rejected(self):
        init=self.init();init['init']['cwd']=str(self.root/'different')
        with self.assertRaises(WorkerInterrupted) as cm:self.run_events([init,self.result()])
        self.assertEqual(cm.exception.cause,'antigravity_workspace_mismatch')
    def test_thinking_is_not_saved(self):
        step={'event':'step_update','step_update':{'conversation_id':'test-id','step_index':1,'step_type':'agent_response','state':'ACTIVE','thinking':'PRIVATE_THOUGHT','text_delta':'draft'}}
        self.run_events([self.init(),step,self.result()])
        for p in self.root.iterdir():self.assertNotIn('PRIVATE_THOUGHT',p.read_text(encoding='utf-8'))

if __name__=='__main__':unittest.main()
