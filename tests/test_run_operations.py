"""Real local operator flows with temporary canonical evidence; no provider calls."""
import hashlib
from contextlib import closing
import json
from pathlib import Path
import sqlite3
import sys
import tempfile
from threading import Thread
import urllib.error
import urllib.request
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'app'))
from init_run import create_run
from coordinator_handoff import Coordinator
from task_store import TaskStore, write_json
from run_operations import RunOperations, recovery_checklist
from contribution_tasks import task_ledger
from dispatch_invocations import record_invocation, invocation_history
from task_inbox import render_page


class OperatorTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.run, _ = create_run(self.root, 'operators', 'Verify scoped operations', project_id='alpha', lead='astra')
        state = Coordinator(self.run).read()
        self.identity = dict(run_id=self.run.name, owner=state['owner'], session=state['session'], generation=state['generation'])
        self.tasks = TaskStore(self.root / 'runs/tasks')
        self.voice = self.root / 'voice.sqlite'
        self.operations = RunOperations(self.root, voice_path=self.voice)

    def task(self, **extra):
        row = self.tasks.create(worker='claude', task='Synthetic accepted output', category='review', assignment_project_id='alpha', run_id=self.run.name)
        row.update(status='accepted', execution_status='succeeded', review_status='accepted', started_at='2026-10-01T00:00:00+00:00', finalized_at='2026-10-01T00:01:00+00:00', response='Synthetic answer', model='claude-opus-5-5', review={'reviewer':'Tester', 'note':'Verified the synthetic output against local fixtures.', 'reviewed_at':'2026-10-01T00:02:00+00:00'})
        row.update(extra)
        self.tasks.save(row['job_id'], row)
        return row

    def test_coverage_canonical_family_not_actor_labels(self):
        row = self.task()
        ledger = task_ledger(row)
        ledger['scope_id'] = self.run.name
        ledger['work_items'][0].update(id=row['job_id'], allocations=[{'agent_id':'worker:claude','percent':100,'evidence':'Verified canonical worker output'}])
        ledger['contributors'].append({'id':'native:fake', 'name':'Fake Google', 'provider':'Google','model':'gemini'})
        ledger['work_items'].append({'id':'fake','label':'Fake','category':'review','status':'accepted','weight':1,'allocations':[{'agent_id':'native:fake','percent':100,'evidence':'Not canonical'}]})
        write_json(self.run / 'contributions-ledger.json', ledger)
        before = (self.run/'coordinator.json').read_bytes()
        view = self.operations.coverage(self.run.name)
        self.assertEqual(view['families'], ['Anthropic'])
        self.assertFalse(view['minimum_met'])
        self.assertEqual((self.run/'coordinator.json').read_bytes(), before)

    def test_every_non_lead_family_requires_a_retained_allocation(self):
        rows=[self.task(),self.task(worker='grok',model='grok-4.6')]
        ledger=task_ledger(rows[0])
        ledger['scope_id']=self.run.name
        def add(row):
            other=task_ledger(row)
            for key in ('contributors','work_items','activity'):ledger[key].extend(other[key])
        def allocate():
            for item,row in zip(ledger['work_items'],rows):
                item.update(id=row['job_id'],allocations=[{'agent_id':'worker:'+row['worker'],'percent':100,'evidence':'Checked the canonical synthetic fixture'}])
            write_json(self.run/'contributions-ledger.json',ledger)
        add(rows[1])
        allocate()
        coverage=self.operations.coverage(self.run.name)
        self.assertEqual(coverage['families'],['Anthropic','xAI'])
        self.assertFalse(coverage['minimum_met'])
        self.assertIn('assign accepted work to gemini (Google)',coverage['warnings'])
        rows.append(self.task(worker='gemini',model='gemini-3.8-flash-high'))
        add(rows[2])
        allocate()
        self.assertTrue(self.operations.coverage(self.run.name)['minimum_met'])
        ledger['work_items'][1]['allocations'][0]['percent']=0
        write_json(self.run/'contributions-ledger.json',ledger)
        self.assertFalse(self.operations.coverage(self.run.name)['minimum_met'])

    def test_uncertain_recovery_never_claims_release(self):
        view = recovery_checklist({'status':'recovery_required','execution_status':'uncertain','process_pid':123,'process_status':'timeout','reservation_state':'held_for_reconciliation','local_process_terminated':True,'execution_progress':{'state':'deadline_reached','elapsed_s':60,'process_status':'terminated','response':'Private provider draft'}})
        self.assertEqual(view['process_pid'],123)
        self.assertEqual(view['process_status'],'terminated')
        self.assertTrue(view['local_process_terminated'])
        self.assertEqual(view['saved_progress']['elapsed_s'],60)
        self.assertNotIn('response',view['saved_progress'])
        self.assertFalse(view['reservation_release_authorized'])
        self.assertEqual(view['provider_execution'], 'unknown')

    def test_owner_tuple_requires_integer_generation(self):
        body=dict(self.identity,items=[],generation=True)
        with self.assertRaises(ValueError):self.operations.save_checklist(body)
        self.assertFalse((self.run/'acceptance-checklist.json').exists())

    def test_checklist_rank_state_evidence_and_stale_tuple(self):
        body = dict(self.identity, items=[{'id':'second','title':'Second','state':'pending','evidence':[]},{'id':'first','title':'First','state':'working','evidence':[]}])
        saved = self.operations.save_checklist(body)
        self.assertEqual([x['rank'] for x in saved['items']], [1,2])
        body['items'][0].update(state='verified')
        with self.assertRaises(ValueError): self.operations.save_checklist(body)
        (self.run/'review/proof.txt').write_text('Actual local test evidence')
        body['items'][0]['evidence']=['review/proof.txt']
        saved = self.operations.save_checklist(body)
        self.assertEqual(saved['items'][0]['proof'][0]['sha256'],hashlib.sha256(b'Actual local test evidence').hexdigest())
        before=(self.run/'acceptance-checklist.json').read_bytes()
        with self.assertRaises(ValueError):self.operations.save_checklist(dict(body,generation=999))
        self.assertEqual((self.run/'acceptance-checklist.json').read_bytes(),before)
        self.assertEqual(Coordinator(self.run).read()['status'],'active')
        body['items'].reverse()
        self.operations.save_checklist(body)
        self.assertEqual([i['id'] for i in self.operations.checklist(self.run.name)['items']],['first','second'])

    def test_review_curated_bundle_real_receipt_and_invalid_atomicity(self):
        row=self.task(status='awaiting_review',review_status='pending',review=None)
        body=dict(self.identity,job_id=row['job_id'],source_sha256=hashlib.sha256((self.tasks.directory(row['job_id'])/'result.json').read_bytes()).hexdigest(),reviewer='Tester',note='Verified the atomic memory against the synthetic task.',curated_payload={'project_id':'alpha','memories':[{'key':'fact','kind':'fact','title':'Synthetic fact','content':'A verified synthetic local fact.'}]})
        bad=dict(body,curated_payload={'project_id':'foreign','memories':[]})
        with self.assertRaises(ValueError):self.operations.review_task(bad)
        self.assertEqual(json.loads((self.tasks.directory(row['job_id'])/'result.json').read_text())['status'],'awaiting_review')
        result=self.operations.review_task(body)
        self.assertEqual(result['memory_outcome']['status'],'remembered')
        self.assertEqual(result['memory_outcome']['mode'],'curated_bundle')

    def test_actual_dispatch_invocation_keeps_original_time_and_canonical(self):
        row=self.task(assignment_id='same')
        before=(self.tasks.directory(row['job_id'])/'result.json').read_bytes()
        receipt=record_invocation(self.tasks,row|{'assignment_reused':True})
        self.assertTrue(receipt['reused'])
        self.assertEqual(receipt['provider_calls'],0)
        self.assertEqual(receipt['original_created_at'],row['created_at'])
        self.assertEqual(invocation_history(self.tasks,row['job_id'])[0],receipt)
        self.assertEqual((self.tasks.directory(row['job_id'])/'result.json').read_bytes(),before)

    def test_voice_metadata_exact_project_and_tuple(self):
        with closing(sqlite3.connect(self.voice)) as db:
            db.execute('CREATE TABLE requests(id TEXT PRIMARY KEY, project_id TEXT, status TEXT, conversation_id TEXT, created_at TEXT)')
            db.execute('INSERT INTO requests VALUES(?,?,?,?,?)',('request-123','alpha','final','synthetic','2026-10-01T00:00:00Z'))
            db.execute('INSERT INTO requests VALUES(?,?,?,?,?)',('foreign-123','other','final',None,'2026-10-01T00:00:00Z'))
            db.commit()
        bound=self.operations.attach_voice(dict(self.identity,request_id='request-123',project_id='alpha'))
        self.assertEqual(bound['requests'][0]['request_id'],'request-123')
        self.assertEqual(self.operations.detail(self.run.name)['voice']['attachments'],bound['requests'])
        with self.assertRaises(ValueError):self.operations.attach_voice(dict(self.identity,request_id='foreign-123',project_id='alpha'))
        with self.assertRaises(ValueError):self.operations.attach_voice(dict(self.identity,request_id='request-123',project_id='alpha',generation=999))
        self.assertNotIn('text',json.dumps(bound))

    def test_curated_review_uses_exact_saved_answer_preview(self):
        row=self.task(response='<script>literal synthetic answer</script>'+'x'*13000)
        view=self.operations.detail(self.run.name)['tasks'][0]
        self.assertEqual(len(view['answer_preview']),12000)
        self.assertTrue(view['answer_truncated'])
        self.assertTrue(view['answer_preview'].startswith('<script>'))

    def test_scoped_inbox_dataset_excludes_other_project_and_run(self):
        self.task()
        self.task(assignment_project_id='beta')
        self.task(run_id='another-run')
        html=render_page(self.root/'runs/tasks',scope={'project':'alpha','run_id':self.run.name})
        data=json.loads(html.split('<script id="inbox-data" type="application/json">')[1].split('</script>')[0])
        self.assertEqual(len(data['tasks']),1)
        self.assertEqual(data['scope']['run_id'],self.run.name)
        self.assertIn('Show all tasks',html)

    def test_changed_evidence_and_foreign_evidence_never_stays_verified(self):
        (self.run/'review/proof.txt').write_text('first')
        body=dict(self.identity,items=[{'id':'proof','title':'Verified outcome','state':'verified','evidence':['review/proof.txt']}])
        self.operations.save_checklist(body)
        (self.run/'review/proof.txt').write_text('changed')
        self.assertEqual(self.operations.checklist(self.run.name)['items'][0]['state'],'pending')
        body['items'][0]['evidence']=['../other-run/proof.txt']
        with self.assertRaises(ValueError):self.operations.save_checklist(body)

    def test_review_source_changed_and_bad_candidate_preserves_task(self):
        row=self.task(status='awaiting_review',review_status='pending',review=None)
        path=self.tasks.directory(row['job_id'])/'result.json'
        before=path.read_bytes()
        body=dict(self.identity,job_id=row['job_id'],source_sha256=hashlib.sha256(before).hexdigest(),reviewer='Tester',note='Verified the synthetic task against local evidence.',curated_payload={'kind':'wrong','title':'Invalid','content':'Invalid candidate'})
        with self.assertRaises(ValueError):self.operations.review_task(body)
        self.assertEqual(path.read_bytes(),before)
        body['source_sha256']='0'*64
        with self.assertRaises(ValueError):self.operations.review_task(body)
        self.assertEqual(path.read_bytes(),before)

    def test_review_race_rechecked_inside_canonical_review_lock(self):
        row=self.task(status='awaiting_review',review_status='pending',review=None)
        path=self.tasks.directory(row['job_id'])/'result.json'
        body=dict(self.identity,job_id=row['job_id'],source_sha256=hashlib.sha256(path.read_bytes()).hexdigest(),reviewer='Tester',note='Verified the original source with synthetic evidence.',curated_payload={'kind':'fact','title':'Reviewed fact','content':'A bounded synthetic fact.'})
        from run_operations import SourceValidator
        candidate=SourceValidator.candidate
        def changed(validator,payload):
            result=candidate(validator,payload)
            row['response']='Changed while the form was being validated';self.tasks.save(row['job_id'],row)
            return result
        with patch.object(SourceValidator,'candidate',changed),self.assertRaises(ValueError):self.operations.review_task(body)
        self.assertEqual(json.loads(path.read_text())['status'],'awaiting_review')

    def test_http_forms_and_origin_tuple_boundaries(self):
        from coordinator_viewer import ViewerServer,ViewerStore
        store=ViewerStore(self.root,collector=lambda:[],codex_capability=lambda:{'available':False},home=self.root/'synthetic-home')
        server=ViewerServer(self.root,store=store,voice_path=self.voice)
        thread=Thread(target=server.serve_forever,daemon=True);thread.start()
        self.addCleanup(server.server_close)
        self.addCleanup(lambda:(server.shutdown(),thread.join(3)))
        def request(path,body=None,authorized=True):
            headers={'X-Viewer-Token':server.viewer_token}
            if body is not None:headers.update({'Content-Type':'application/json','Origin':server.origin if authorized else 'http://foreign.invalid'})
            req=urllib.request.Request(server.origin+path,data=json.dumps(body).encode() if body is not None else None,headers=headers)
            with urllib.request.urlopen(req,timeout=5) as response:return response.read()
        html=request('/').decode()
        self.assertIn('Accept and capture curated knowledge',html)
        self.assertIn('Attach request metadata to this run',html)
        result=json.loads(request('/api/operator?run='+self.run.name))
        self.assertFalse(result['coverage']['minimum_met'])
        body=dict(self.identity,items=[{'id':'first','title':'First outcome','state':'working','evidence':[]}])
        self.assertEqual(json.loads(request('/api/checklist',body))['items'][0]['rank'],1)
        for bad,authorized in ((dict(body,generation=999),True),(body,False)):
            with self.assertRaises(urllib.error.HTTPError):request('/api/checklist',bad,authorized)
        task=self.task(status='awaiting_review',review_status='pending',review=None)
        source=hashlib.sha256((self.tasks.directory(task['job_id'])/'result.json').read_bytes()).hexdigest()
        review=dict(self.identity,job_id=task['job_id'],source_sha256=source,reviewer='Browser fixture',note='Verified the synthetic saved answer against its brief.',curated_payload={'kind':'fact','title':'HTTP fixture fact','content':'A scoped synthetic memory.'})
        self.assertEqual(json.loads(request('/api/task-review',review))['memory_outcome']['status'],'remembered')
        with closing(sqlite3.connect(self.voice)) as db:
            db.execute('CREATE TABLE requests(id TEXT PRIMARY KEY,project_id TEXT,status TEXT,conversation_id TEXT,created_at TEXT)')
            db.execute('INSERT INTO requests VALUES(?,?,?,?,?)',('http-request','alpha','final','synthetic','2026-10-01T00:00:00Z'));db.commit()
        attach=dict(self.identity,project_id='alpha',request_id='http-request')
        self.assertEqual(json.loads(request('/api/voice-attach',attach))['requests'][0]['request_id'],'http-request')
        self.task(assignment_project_id='beta')
        page=request('/tasks?project=alpha&run='+self.run.name).decode()
        inbox=json.loads(page.split('<script id="inbox-data" type="application/json">')[1].split('</script>')[0])
        self.assertEqual(len(inbox['tasks']),1)
        all_page=request('/tasks').decode()
        all_inbox=json.loads(all_page.split('<script id="inbox-data" type="application/json">')[1].split('</script>')[0])
        self.assertEqual(len(all_inbox['tasks']),2)


if __name__=='__main__':unittest.main()
