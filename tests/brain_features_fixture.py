"""Disposable real dashboard for browser acceptance; never opens the user's Brain."""
import argparse
import json
from pathlib import Path
import sys
import tempfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'app'))
from brain_dashboard import make_server
from brain_store import BrainStore


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--port', type=int, default=0)
    args = parser.parse_args()
    with tempfile.TemporaryDirectory(prefix='brain-winners-') as temporary:
        root = Path(temporary)
        store = BrainStore(root)
        paths = {}
        for job, accepted in [('a'*32, True), ('b'*32, False)]:
            path = root / 'runs/tasks' / job / 'result.json'
            path.parent.mkdir(parents=True)
            path.write_text(json.dumps(dict(job_id=job, assignment_project_id='alpha',
                task='Accepted synthetic task' if accepted else 'Finalized task awaiting review',
                response='Needle synthetic canonical evidence', execution_status='succeeded',
                finalized_at='2026-09-01T00:00:00Z', status='accepted' if accepted else 'awaiting_review',
                review_status='accepted' if accepted else 'pending', review={'note':'Synthetic review'})), encoding='utf-8')
            paths[job] = str(path)
        records = {}
        for title, tags, source, active in [
            ('Needle road survey', ['road','survey'], {'type':'task','job_id':'a'*32}, True),
            ('Needle road only', ['road'], {'type':'user','note':'Independent synthetic source'}, True),
            ('Pending synthetic source', ['survey'], {'type':'task','job_id':'a'*32}, False),
        ]:
            value = store.propose(dict(project_id='alpha', kind='fact', title=title,
                content='Needle synthetic evidence. This fixture contains no user memories.', tags=tags, source=source))
            if active: value = store.approve(value['id'], 'Synthetic reviewer', 'Reviewed independently for disposable browser acceptance.')
            records[title] = value['id']
        server = make_server(root, port=args.port)
        print(json.dumps({'origin':server.origin, 'project':'alpha', 'fixture_root':str(root),
                          'synthetic_task_paths':paths, 'memory_ids':records}), flush=True)
        try:
            server.serve_forever(poll_interval=.1)
        except KeyboardInterrupt:
            pass
        finally:
            server.server_close()


if __name__ == '__main__': main()
