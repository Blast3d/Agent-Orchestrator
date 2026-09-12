"""Memory-aware dispatch/review without contacting any provider."""
import argparse
from contextlib import redirect_stdout
import io
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import Mock,patch

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'app'))
from brain_store import BrainStore
from task_store import TaskStore
from storage_budget import StorageBudget,StorageLimitError
import dispatch_worker as dispatch
import automatic_memory


class IntegrationTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name);self.store=TaskStore(self.root/'runs/tasks')
        self.prompt=self.root/'brief.md';self.prompt.write_text('Review supplied code.')
        self.args=argparse.Namespace(worker='claude',prompt_file=self.prompt,output=self.root/'answer.json',task='Synthetic memory task',size='small',project='alpha',assignment_id='review-v1',memory_query='recovery')
        self.guard=Mock();self.guard.refresh.side_effect=lambda p:{p:{'ok':True}}
        self.guard.check.return_value={'allowed':True,'reservation_id':'test-token'}
        self.invoke=Mock(return_value=subprocess.CompletedProcess(['worker'],0,json.dumps({'result':'Synthetic answer'}),''))

    def run_job(self):
        with patch.object(dispatch,'ensure_directories'),patch.object(dispatch,'cloud_command',return_value=([sys.executable],None)),patch.object(dispatch,'invoke_cloud',self.invoke):
            return dispatch.dispatch(self.args,guard_factory=lambda:self.guard,store=self.store,workspaces=self.root/'runtime/workspaces')

    def test_retrieved_context_recorded_and_assignment_reused_without_search(self):
        b=BrainStore(self.root);m=b.propose({'project_id':'alpha','kind':'fact','title':'Recovery','content':'Preserve unknown job evidence.','source':{'type':'user','note':'Synthetic fact'}})
        b.approve(m['id'],'Tester','Checked this memory against the synthetic requirements.')
        result=self.run_job();self.assertEqual(result['memory_context']['ids'],[m['id']])
        import hashlib
        context=result['memory_context']
        self.assertEqual(hashlib.sha256(context['context'].encode()).hexdigest(),context['sha256'])
        self.assertTrue(context['execution_requested'])
        self.assertEqual(result['storage_reservation_state'],'released')
        with patch.object(BrainStore,'search',side_effect=AssertionError('Repeat must use saved assignment')):
            repeated=self.run_job()
        self.assertTrue(repeated['assignment_reused']);self.assertEqual(self.invoke.call_count,1)

    def test_storage_hold_prevents_quota_and_inference(self):
        with patch.object(StorageBudget,'reserve',side_effect=StorageLimitError('No space')):
            result=self.run_job()
        self.assertEqual(result['status'],'held');self.guard.check.assert_not_called();self.invoke.assert_not_called()

    def test_storage_initialization_failure_still_saves_canonical_hold(self):
        with patch.object(dispatch,'StorageBudget',side_effect=StorageLimitError('Unsafe storage')):
            result=self.run_job()
        self.assertEqual(result['status'],'held');self.invoke.assert_not_called()
        self.assertEqual(json.loads((self.store.directory(result['job_id'])/'result.json').read_text())['status'],'held')

    def test_changing_memory_query_cannot_reuse_assignment(self):
        self.run_job();self.args.memory_query='different evidence'
        self.assertEqual(self.run_job()['status'],'held')
        self.assertEqual(self.invoke.call_count,1)

    def test_memory_retrieval_requires_project_before_inference(self):
        self.args.project=None
        with self.assertRaises(ValueError):self.run_job()
        self.invoke.assert_not_called()

    def test_explicit_deadline_reaches_worker_and_changes_assignment_identity(self):
        self.args.timeout_seconds=600;result=self.run_job()
        self.assertEqual(result['timeout_seconds'],600);self.assertEqual(result['timeout_policy'],'explicit')
        self.assertEqual(self.invoke.call_args.kwargs['timeout_seconds'],600)
        self.args.timeout_seconds=900
        self.assertEqual(self.run_job()['status'],'held');self.assertEqual(self.invoke.call_count,1)

    def test_invalid_deadline_stops_before_storage_quota_or_worker(self):
        self.args.timeout_seconds=1801
        with patch.object(dispatch,'StorageBudget') as budget, self.assertRaises(ValueError):self.run_job()
        budget.assert_not_called();self.guard.check.assert_not_called();self.invoke.assert_not_called()

    def test_incomplete_brief_does_not_retrieve_memory(self):
        self.args.require_brief_check=True
        with patch.object(BrainStore,'search',side_effect=AssertionError('Do not retrieve for a held brief')):
            result=self.run_job()
        self.assertEqual(result['status'],'held');self.assertNotIn('memory_context',result)
        self.invoke.assert_not_called()

    def test_quota_hold_keeps_context_marked_unsent(self):
        self.guard.check.return_value={'allowed':False}
        result=self.run_job()
        self.assertEqual(result['status'],'held');self.assertFalse(result['memory_context']['execution_requested'])
        self.invoke.assert_not_called()

    def test_uncertain_job_keeps_storage_reservation(self):
        from worker_execution import WorkerInterrupted
        self.invoke.side_effect=WorkerInterrupted('timeout')
        result=self.run_job()
        self.assertEqual(result['storage_reservation_state'],'held_for_reconciliation')
        self.assertEqual(StorageBudget(self.root).status()['reservation_count'],1)

    def test_review_can_remember_verified_episode_without_another_model(self):
        self.args.memory_query=None;result=self.run_job()
        path=self.root/'episode.json';path.write_text(json.dumps({'kind':'episode','title':'Synthetic successful recovery','content':'Review preserved uncertainty.','episode':{'problem':'Unknown completion','action':'Keep reservation','outcome':'No duplicate worker'}}))
        with patch.object(dispatch,'TASKS',self.store.root),patch.object(dispatch,'Guard',return_value=self.guard),redirect_stdout(io.StringIO()) as output:
            code=dispatch.main(['review',result['job_id'],'--decision','accepted','--reviewer','Tester','--note','Validated the actual task and its compact episode.','--remember-file',str(path)])
        self.assertEqual(code,0,output.getvalue())
        recalled=BrainStore(self.root).search('uncertainty','alpha')
        self.assertEqual(len(recalled['results']),1)
        self.assertEqual(recalled['results'][0]['source']['job_id'],result['job_id'])
        self.assertEqual(self.invoke.call_count,1)

    def test_task_store_acceptance_automatically_saves_review_evidence(self):
        self.args.memory_query=None;result=self.run_job()
        note='Verified recovery retains the accepted answer and prevents duplicate worker execution.'
        reviewed=self.store.review(result['job_id'],'accepted','Tester',note)
        outcome=reviewed['memory_outcome']
        self.assertEqual(outcome['status'],'remembered',outcome)
        memory=BrainStore(self.root).get(outcome['memory_id'])
        self.assertEqual(memory['source']['job_id'],result['job_id'])
        self.assertEqual(memory['episode']['outcome'],note)
        self.assertEqual(memory['status'],'active')
        canonical=json.loads((self.store.directory(result['job_id'])/'result.json').read_text())
        receipt=json.loads((self.store.directory(result['job_id'])/'memory-outcome.json').read_text())
        self.assertEqual(canonical['status'],'accepted')
        self.assertEqual(receipt['memory_id'],outcome['memory_id'])
        self.assertEqual(self.invoke.call_count,1)

    def test_review_cli_remembers_without_requiring_candidate_file(self):
        self.args.memory_query=None;result=self.run_job()
        with patch.object(dispatch,'TASKS',self.store.root),patch.object(dispatch,'Guard',return_value=self.guard),redirect_stdout(io.StringIO()) as output:
            code=dispatch.main(['review',result['job_id'],'--decision','accepted','--reviewer','Tester',
                                '--note','Verified automatic memory is recalled from this accepted review.'])
        summary=json.loads(output.getvalue())
        self.assertEqual(code,0,summary)
        self.assertEqual(summary['memory_outcome']['mode'],'automatic')
        self.assertEqual(summary['memory_outcome']['status'],'remembered')
        self.assertIsNone(summary['memory_error'])
        self.assertIsNotNone(summary['remembered_memory_id'])
        self.assertEqual(self.invoke.call_count,1)

    def test_accepted_task_without_project_skips_memory_without_guessing_scope(self):
        self.args.memory_query=None;self.args.project=None;self.args.assignment_id=None
        result=self.run_job()
        with patch.object(BrainStore,'propose',side_effect=AssertionError('Do not guess a project')):
            reviewed=self.store.review(result['job_id'],'accepted','Tester',
                                       'Verified this task is accepted without a configured project scope.')
        self.assertEqual(reviewed['status'],'accepted')
        self.assertEqual(reviewed['memory_outcome']['status'],'skipped')
        self.assertEqual(reviewed['memory_outcome']['reason'],'missing_project')
        self.assertFalse((self.root/'runtime/brain/memory.sqlite').exists())
        self.assertEqual(self.invoke.call_count,1)

    def test_rejected_review_does_not_call_memory_hook(self):
        self.args.memory_query=None;result=self.run_job()
        with patch.object(automatic_memory,'record_accepted_outcome',side_effect=AssertionError('Rejected evidence is not a learned solution')) as remember:
            reviewed=self.store.review(result['job_id'],'rejected','Tester',
                                       'The saved answer fails the required recovery acceptance checks.')
        self.assertEqual(reviewed['status'],'rejected')
        self.assertNotIn('memory_outcome',reviewed)
        remember.assert_not_called()
        self.assertFalse((self.store.directory(result['job_id'])/'memory-outcome.json').exists())
        self.assertEqual(self.invoke.call_count,1)

    def test_memory_storage_failure_preserves_acceptance_and_retry_cli_recovers(self):
        self.args.memory_query=None;result=self.run_job()
        with patch.object(BrainStore,'propose',side_effect=StorageLimitError('Synthetic storage budget hold')):
            reviewed=self.store.review(result['job_id'],'accepted','Tester',
                                       'Verified recovery behavior before this synthetic storage failure.')
        self.assertEqual(reviewed['status'],'accepted')
        self.assertEqual(reviewed['memory_outcome']['status'],'error')
        canonical_path=self.store.directory(result['job_id'])/'result.json'
        accepted=canonical_path.read_bytes()
        self.assertEqual(json.loads(accepted)['status'],'accepted')
        with patch.object(dispatch,'invoke_cloud',side_effect=AssertionError('Memory retry cannot run another worker')),redirect_stdout(io.StringIO()) as output:
            code=automatic_memory.main([result['job_id'],'--root',str(self.root)])
        recovered=json.loads(output.getvalue())
        self.assertEqual(code,0,recovered)
        self.assertEqual(recovered['status'],'remembered')
        self.assertEqual(canonical_path.read_bytes(),accepted)
        self.assertEqual(len(BrainStore(self.root).list_memories('alpha')),1)
        self.assertEqual(self.invoke.call_count,1)

    def test_cli_reports_memory_failure_without_hiding_accepted_review(self):
        self.args.memory_query=None;result=self.run_job()
        with patch.object(dispatch,'TASKS',self.store.root),patch.object(dispatch,'Guard',return_value=self.guard), \
                patch.object(BrainStore,'propose',side_effect=StorageLimitError('Synthetic memory hold')),redirect_stdout(io.StringIO()) as output:
            code=dispatch.main(['review',result['job_id'],'--decision','accepted','--reviewer','Tester',
                                '--note','Verified the task passes despite the synthetic optional memory hold.'])
        summary=json.loads(output.getvalue())
        self.assertEqual(code,2,summary)
        self.assertEqual(summary['status'],'accepted')
        self.assertEqual(summary['memory_outcome']['status'],'error')
        self.assertIn('memory hold',summary['memory_error'])
        self.assertEqual(self.invoke.call_count,1)

    def test_next_assignment_recalls_automatically_saved_accepted_review(self):
        self.args.memory_query=None;first=self.run_job()
        accepted=self.store.review(first['job_id'],'accepted','Tester',
                                   'Verified recovery avoids duplicate execution by checking saved canonical evidence.')
        self.args.assignment_id='review-v2';self.args.output=self.root/'next-answer.json'
        self.args.memory_query='recovery duplicate execution'
        second=self.run_job()
        self.assertIn(accepted['memory_outcome']['memory_id'],second['memory_context']['ids'])
        self.assertTrue(second['memory_context']['execution_requested'])
        self.assertIn('review evidence',second['memory_context']['context'])
        self.assertEqual(self.invoke.call_count,2)


if __name__=='__main__':unittest.main()
