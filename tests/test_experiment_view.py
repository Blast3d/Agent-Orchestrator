"""Condition evidence boundaries, partial accounting and authenticated HTTP."""
from copy import deepcopy
from http.client import HTTPConnection
import json
import os
from pathlib import Path
import sys
import tempfile
from threading import Thread
import unittest

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'app'))
from experiment_view import ExperimentStore, grades
from coordinator_viewer import ViewerServer


def write(path,data):
    path.parent.mkdir(parents=True,exist_ok=True)
    path.write_text(json.dumps(data),encoding='utf-8')


class ExperimentViewTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name);self.parent=self.root/'.orchestration/suite'
        self.child=self.root/'.orchestration/solo-run'
        self.other=self.root/'.orchestration/team-run'
        write(self.parent/'run.json',{'display_name':'Synthetic suite','status':'in_progress'})
        write(self.parent/'experiment.json',{'schema_version':1,'family':'memory_control','conditions':[
            {'id':'solo-cold','run_id':'solo-run','label':'Solo cold'},
            {'id':'team-cold','run_id':'team-run','label':'Team cold'}]})
        for run,key,roster in [(self.child,'solo-cold',[('solo','OpenAI')]),(self.other,'team-cold',[
                ('summary','OpenAI'),('aggregation','Anthropic'),('allocation','xAI')])]:
            write(run/'run.json',{'project_id':run.name})
            write(run/'condition.json',{'schema_version':1,'family':'memory_control','parent_run_id':'suite',
                'id':key,'run_id':run.name,'project_id':run.name,'status':'planned','helper_count':0,
                'contestant_count':len(roster),'memory_mode':'disabled',
                'roster':[{'id':actor,'provider':provider,'role':actor,'model':'requested-test-model'} for actor,provider in roster]})
        self.store=ExperimentStore(self.root)

    def call(self,run=None,key='solo-cold',actor='solo',provider='OpenAI',suffix='turn1',**extra):
        run=run or self.child;name=key+'-'+actor+'-'+suffix
        data={'call_id':name,'provider':provider,'actor_id':actor,'role':actor,'status':'succeeded',
              'run_id':run.name,'parent_run_id':'suite','condition_id':key,'scope':'contestant','helper_count':0,
              'usage':{'input_tokens':10,'output_tokens':2},'response':'answer <script>unsafe()</script>',
              'memory_ids':[],'memory_enabled':False,'actual_models':[]}
        data.update(extra);write(run/'calls'/name/'record.json',data)
        (run/'calls'/name/'prompt.md').write_text('Only this condition',encoding='utf-8')
        return name,data

    def test_conditions_have_separate_totals_and_declared_models_before_execution(self):
        self.call();self.call(self.other,'team-cold','summary',usage={'input_tokens':900,'output_tokens':7})
        solo=self.store.detail('suite','solo-cold');team=self.store.detail('suite','team-cold')
        self.assertEqual(solo['metrics']['input_tokens'],10)
        self.assertEqual(team['metrics']['input_tokens'],900)
        self.assertEqual(solo['metrics']['helper_count'],0)
        self.assertEqual(team['metrics']['contestant_count'],3)
        self.assertEqual(team['metrics']['observed_contestant_count'],1)
        self.assertEqual(team['roster'][1]['requested_model'],'requested-test-model')
        self.assertEqual(len(self.store.listing()['experiments'][0]['conditions']),2)

    def test_six_condition_memory_jev_suite_is_visible_and_requires_explicit_ranking(self):
        from experiment_view import MEMORY_JEV_CONDITIONS
        declarations=[]
        for key in MEMORY_JEV_CONDITIONS:
            run=self.root/'.orchestration'/key
            template=self.other if key.startswith('team') else self.child
            meta=json.loads((template/'condition.json').read_text())
            mode='jev' if '-jev-' in key else 'ordinary' if key.endswith('warm') else 'disabled'
            meta.update(id=key,run_id=key,project_id=key,suite_mode='jev_comparison',memory_ranking_mode=mode,
                        memory_mode='seeded' if key.endswith('warm') else 'disabled')
            write(run/'run.json',{'project_id':key})
            write(run/'condition.json',meta)
            declarations.append({'id':key,'run_id':key,'label':key})
        write(self.parent/'experiment.json',{'schema_version':1,'family':'memory_control','conditions':declarations})
        listed=self.store.listing()
        self.assertEqual(listed['errors'],[])
        self.assertEqual(len(listed['experiments'][0]['conditions']),6)
        detail=self.store.detail('suite','team-jev-warm')
        self.assertEqual(detail['metrics']['contestant_count'],3)
        self.assertEqual(detail['memory']['mode'],'seeded')
        write(self.parent/'experiment.json',{'schema_version':1,'family':'memory_control','conditions':declarations[4:]})
        with self.assertRaises(ValueError):self.store.detail('suite','solo-jev-warm')

    def test_running_unknown_and_unlisted_failed_calls_remain_in_totals(self):
        first,_=self.call()
        write(self.child/'trials/solo-cold/summary.json',{'id':'solo-cold','calls':[first]})
        self.call(suffix='turn2',status='uncertain',usage=None)
        detail=self.store.detail('suite','solo-cold')
        self.assertEqual(detail['metrics']['calls'],2)
        self.assertIsNone(detail['metrics']['input_tokens'])
        self.assertEqual(detail['usage']['totals']['known_input_tokens'],10)
        self.assertEqual(detail['usage']['totals']['unknown_usage_calls'],1)
        self.assertIsNone(detail['usage']['totals']['additional_charge_usd'])

    def test_preflight_failure_does_not_claim_model_execution(self):
        self.call(status='failed',provider_calls=0,usage=None)
        detail=self.store.detail('suite','solo-cold')
        self.assertTrue(detail['calls'][0]['preflight_only'])
        self.assertEqual(detail['roster'][0]['actual_models'],[])
        self.assertIsNone(detail['metrics']['input_tokens'])

    def test_call_execution_and_role_must_belong_to_child(self):
        name,data=self.call()
        for field,value in [('run_id','team-run'),('parent_run_id','elsewhere'),('condition_id','team-cold'),
                            ('provider','xAI'),('actor_id','allocation'),('scope','setup'),('helper_count',1)]:
            with self.subTest(field=field):
                write(self.child/'calls'/name/'record.json',dict(data,**{field:value}))
                with self.assertRaises(ValueError):self.store.detail('suite','solo-cold')
        write(self.child/'calls'/name/'record.json',data)

    def test_output_cannot_cross_conditions_or_read_arbitrary_files(self):
        first,_=self.call();other,_=self.call(self.other,'team-cold','summary')
        self.assertIn('answer',self.store.output('suite','solo-cold','call:'+first+':response')['content'])
        for item in ('call:'+other+':response','../../secret','call:'+first+':../../secret'):
            with self.subTest(item=item),self.assertRaises(ValueError):self.store.output('suite','solo-cold',item)
        with self.assertRaises(ValueError):self.store.detail('../suite','solo-cold')

    def test_parent_rejects_shared_memory_scopes(self):
        path=self.other/'condition.json';data=json.loads(path.read_text());data['project_id']='solo-run';write(path,data)
        with self.assertRaises(ValueError):self.store.detail('suite','solo-cold')

    def test_oversized_call_is_rejected(self):
        name,data=self.call();path=self.child/'calls'/name/'record.json'
        path.write_text('x'*(2*1024**2+1),encoding='utf-8')
        with self.assertRaises(ValueError):self.store.detail('suite','solo-cold')

    def test_foreign_directory_link_is_rejected(self):
        name,data=self.call();folder=self.child/'calls'/name
        target=self.other/'borrowed';folder.rename(target)
        if os.name=='nt':
            import _winapi
            _winapi.CreateJunction(str(target),str(folder))
        else:folder.symlink_to(target,target_is_directory=True)
        self.addCleanup(lambda:folder.rmdir() if os.name=='nt' else folder.unlink())
        with self.assertRaises(ValueError):self.store.detail('suite','solo-cold')

    def test_unknown_condition_and_invalid_memory_policy_are_rejected(self):
        path=self.child/'condition.json';data=json.loads(path.read_text())
        for field,value in [('memory_mode','maybe'),('helper_count',False),('contestant_count',True)]:
            with self.subTest(field=field):
                write(path,dict(data,**{field:value}))
                with self.assertRaises(ValueError):self.store.detail('suite','solo-cold')
        write(path,data)
        path=self.parent/'experiment.json';data=json.loads(path.read_text())
        data['conditions'][0]['id']='invented-condition';write(path,data)
        with self.assertRaises(ValueError):self.store.detail('suite','invented-condition')

    def test_grade_groups_never_guess_from_public_visibility(self):
        result=grades({'passed':2,'total':3,'cases':[
            {'case':'ordinary','public':True,'passed':True,'group':'normal'},
            {'case':'boundary','public':True,'passed':False,'group':'edge'},
            {'case':'historical','public':False,'passed':True}]})
        self.assertEqual(result['groups'],[{'name':'normal','passed':1,'total':1},
            {'name':'edge','passed':0,'total':1},{'name':'unclassified','passed':1,'total':1}])

    def test_historical_projection_preserves_original_bytes_and_calls(self):
        old=self.root/'.orchestration/old';write(old/'run.json',{'status':'completed'})
        write(old/'review/experiment-lock.json',{'order':['solo-cold','team-cold','solo-warm','team-warm']})
        name,data=self.call(run=old);write(old/'trials/solo-cold/summary.json',{'calls':[name]})
        self.call(run=old,suffix='unscored-setup',usage={'input_tokens':9999,'output_tokens':1})
        before={str(p):p.read_bytes() for p in old.rglob('*') if p.is_file()}
        detail=self.store.detail('old','solo-cold')
        self.assertTrue(detail['historical']);self.assertEqual(detail['metrics']['input_tokens'],10)
        self.assertEqual(before,{str(p):p.read_bytes() for p in old.rglob('*') if p.is_file()})

    def test_api_requires_token_and_rejects_ambiguous_or_cross_condition_requests(self):
        self.call();server=ViewerServer(self.root)
        thread=Thread(target=server.serve_forever,daemon=True);thread.start()
        self.addCleanup(lambda:(server.shutdown(),server.server_close(),thread.join()))
        def request(path,token=True):
            connection=HTTPConnection('127.0.0.1',server.server_port,timeout=5)
            connection.request('GET',path,headers={'X-Viewer-Token':server.viewer_token} if token else {})
            response=connection.getresponse();body=response.read();status=response.status;connection.close();return status,body
        self.assertEqual(request('/api/experiments',False)[0],403)
        status,body=request('/api/experiment?run=suite&condition=solo-cold')
        self.assertEqual(status,200);self.assertEqual(json.loads(body)['metrics']['calls'],1)
        self.assertEqual(request('/api/experiment?run=suite&run=other&condition=solo-cold')[0],400)
        status,page=request('/experiments?run=suite&condition=solo-cold',False)
        self.assertEqual(status,200);self.assertIn(b'X-Viewer-Token',page);self.assertNotIn(b'__NONCE__',page)


if __name__=='__main__':unittest.main()
