"""Reproducible isolated retrieval benchmark; synthetic data, zero model calls."""
import json
from pathlib import Path
import statistics
import sys
import tempfile
import time

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'app'))
from brain_store import BrainStore, digest, now
from usage_guard import file_lock


def benchmark():
    with tempfile.TemporaryDirectory(prefix='orchestrator-brain-benchmark-') as directory:
        brain=BrainStore(Path(directory));at=now()
        source={'type':'user','note':'Synthetic benchmark evidence only'}
        rows=[];fts=[]
        for i in range(10000):
            project='benchmark' if i<9000 else 'other-project'
            title=f'Incident {i} resolution topic{i%37}'
            content=f'For topic{i%37}, bounded retries and canonical evidence resolve deployment recovery. '
            content+='Keep verified configuration, owner identity and acceptance checks alongside each saved procedure. '*3
            rows.append((i+1,f'{i:032x}',project,'local','fact',title,content,
                json.dumps(['recovery',f'alias{i}']),0.5,'active',at,at,None,'Benchmark',
                'Synthetic reviewed fixture; no user information.',at,json.dumps(source),digest(source),'{}',digest(i)))
            fts.append((i+1,title,content,f'recovery alias{i}'))
        with brain.budget.allocation(64*1024**2,kind='brain'),file_lock(brain.lock),brain._connection() as con:
            con.executemany('''INSERT INTO memories(rowid,id,project_id,user_id,kind,title,content,tags,
                importance,status,created_at,valid_from,valid_to,reviewer,review_note,reviewed_at,source,
                source_hash,episode,fingerprint) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)''',rows)
            con.executemany('INSERT INTO memory_fts(rowid,title,content,tags) VALUES(?,?,?,?)',fts)
            con.commit();con.execute('PRAGMA wal_checkpoint(TRUNCATE)')
        durations=[];lookups=[];wall=[]
        for i in range(100):
            query=f'alias{i*83}' if i%2 else f'topic{i%37} recovery'
            start=time.perf_counter();result=brain.search(query,'benchmark')
            wall.append((time.perf_counter()-start)*1000)
            durations.append(result['elapsed_ms']);lookups.append(result['lookup_ms'])
            assert result['results'] and all(r['project_id']=='benchmark' for r in result['results'])
            assert result['context_chars']<=8000
        assert not brain.search('alias9500','benchmark')['results']
        def stats(values):return {'p50_ms':round(statistics.median(values),2),'p95_ms':round(sorted(values)[94],2),'max_ms':round(max(values),2)}
        return {'records':10000,'queries':100,'fixture':'synthetic, 9000 target + 1000 isolated project',
                'method':'direct fixture load; actual BrainStore.search including optional trace admission',
                'lookup':stats(lookups),'reported_total':stats(durations),'caller_wall':stats(wall),
                'brain_bytes':brain.budget.status()['brain_bytes'],'sqlite_file_bytes':brain.db.stat().st_size,
                'model_calls':0,'scope_leak_checks':'passed','context_limit_checks':'passed'}


if __name__=='__main__':print(json.dumps(benchmark(),indent=2))
