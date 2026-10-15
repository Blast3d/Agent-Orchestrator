"""Disposable browser fixture; serves synthetic local data without provider tools."""
from contextlib import closing
import json
from pathlib import Path
import sqlite3
import sys
import tempfile

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'app'))
from coordinator_handoff import Coordinator
from coordinator_viewer import ViewerServer, ViewerStore
from dispatch_invocations import record_invocation
from init_run import create_run
from task_store import TaskStore


def prepare(root):
    run,_=create_run(root,'operator-ui','Synthetic operator browser acceptance',project_id='alpha',lead='astra')
    other,_=create_run(root,'operator-ui-other','Synthetic other project',project_id='beta',lead='astra')
    (run/'review/browser-proof.txt').write_text('Synthetic browser fixture evidence',encoding='utf-8')
    store=TaskStore(root/'runs/tasks')
    for title,project,run_id,status in [('Synthetic review task','alpha',run.name,'awaiting_review'),('Synthetic uncertain task','alpha',run.name,'recovery_required'),('Synthetic other project task','beta',other.name,'awaiting_review')]:
        row=store.create(worker='claude',task=title,category='review',assignment_project_id=project,run_id=run_id)
        row.update(status=status,execution_status='uncertain' if status=='recovery_required' else 'succeeded',review_status='pending',response='Synthetic answer only',finalized_at='2026-10-01T00:01:00Z',started_at='2026-10-01T00:00:00Z',synthetic_input=True)
        if status=='recovery_required':row.update(process_pid=12345,process_status='timeout',reservation_state='held_for_reconciliation',local_process_terminated=True,execution_progress={'state':'deadline_reached','elapsed_s':60,'process_status':'terminated'})
        store.save(row['job_id'],row)
        if project=='alpha':record_invocation(store,row|{'assignment_reused':True})
    voice=root/'synthetic-voice.sqlite'
    with closing(sqlite3.connect(voice)) as db:
        db.execute('CREATE TABLE requests(id TEXT PRIMARY KEY,project_id TEXT,status TEXT,conversation_id TEXT,created_at TEXT)')
        db.execute('INSERT INTO requests VALUES(?,?,?,?,?)',('synthetic-request-1','alpha','final','synthetic-conversation','2026-10-01T00:00:00Z'))
        db.commit()
    return run,voice


if __name__=='__main__':
    with tempfile.TemporaryDirectory(prefix='operator-ui-') as temporary:
        root=Path(temporary);run,voice=prepare(root)
        store=ViewerStore(root,collector=lambda:[],codex_capability=lambda:{'available':False,'reason':'Synthetic fixture'},home=root/'synthetic-home')
        with ViewerServer(root,store=store,voice_path=voice) as server:
            print(json.dumps({'origin':server.origin,'run_id':run.name,'url':server.origin+'/?run='+run.name+'&project=alpha','scope':'temporary synthetic data only'}),flush=True)
            try:server.serve_forever(poll_interval=.2)
            except KeyboardInterrupt:pass
