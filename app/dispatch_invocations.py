"""Immutable invocation receipts separate from the original canonical task."""
from datetime import datetime, timezone
import json
from pathlib import Path
import uuid

from brain_store import safe_path


def record_invocation(store, result):
    job=result.get('job_id')
    if not job:return None
    directory=store.directory(job)
    if not (directory/'result.json').exists() and not (directory/'record.json').exists():return None
    root=Path(store.root).resolve()
    folder=safe_path(root,directory/'dispatch-invocations')
    folder.mkdir(exist_ok=True)
    receipt={'schema_version':1,'invocation_id':uuid.uuid4().hex,'job_id':job,
             'requested_at':datetime.now(timezone.utc).isoformat(),'reused':result.get('assignment_reused') is True,
             'original_created_at':result.get('created_at'),'assignment_id':result.get('assignment_id'),
             'project_id':result.get('assignment_project_id'),
             'provider_calls':0 if result.get('assignment_reused') is True else None,
             'note':'Original assignment reused; no new inference was sent.' if result.get('assignment_reused') is True else 'Dispatch invocation recorded; provider execution is described by the canonical task.'}
    prefix=datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ-')
    with (folder/(prefix+receipt['invocation_id']+'.json')).open('x',encoding='utf-8') as handle:
        json.dump(receipt,handle,indent=2);handle.write('\n')
    return receipt


def invocation_history(store, job_id):
    root=Path(store.root).resolve()
    folder=safe_path(root,store.directory(job_id)/'dispatch-invocations')
    rows=[]
    for path in sorted(folder.glob('*.json'),reverse=True)[:50]:
        path=safe_path(root,path)
        if path.stat().st_size>8192:continue
        value=json.loads(path.read_text(encoding='utf-8'))
        if value.get('job_id')==job_id:rows.append(value)
    return sorted(rows,key=lambda r:r['requested_at'],reverse=True)
