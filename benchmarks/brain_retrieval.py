"""Offline retrieval comparison. Temporary synthetic Brain; no bot/model run."""
import argparse
import json
from pathlib import Path
import statistics
import sys
import tempfile

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'app'))
from brain_store import BrainStore


def compare(repeats=5):
    with tempfile.TemporaryDirectory(prefix='brain-retrieval-') as temporary:
        brain=BrainStore(Path(temporary))
        def add(title,content):
            proposal=brain.propose(dict(project_id='synthetic',kind='fact',title=title,content=content,
                source={'type':'user','note':'Synthetic retrieval benchmark; no real task answer.'}))
            return brain.approve(proposal['id'],'Tester','Checked against the synthetic fixture.')
        location=add('Config path','The production config lives at app/production/config.json.')
        caller=add('Caller map','The caller map depends on shared routines.')
        library=add('Shared routines','The helper owns the validation procedure.')
        brain.relate(caller['id'],library['id'],'depends_on','Tester')
        cases=[('file_location','Where is app/production/config.json','synthetic',location['id']),
               ('relationship','What depends on caller map','synthetic',caller['id']),
               ('weak_match','production migration guidance','synthetic',location['id']),
               ('empty_project','Where is config.json','empty',None)]
        rows=[]
        for case,query,project,expected in cases:
            for mode in ('keyword','graph','auto','semantic'):
                # Rotate repetitions across modes in a larger performance study;
                # these tiny samples are diagnostic, not a speedup estimate.
                trials=[brain.search(query,project,strategy=mode,record_trace=False) for _ in range(repeats)]
                measured=brain.search(query,project,strategy=mode)
                assert measured['retrieval']['provider_calls']==0
                expected_found=expected in [m['id'] for m in measured['results']] if expected else not measured['results']
                assert expected_found,(case,mode)
                rows.append(dict(case_id=case,mode=mode,project=project,query=query,
                    check_sections={'normal':{'expected_seed_found':expected_found},
                                    'edge':{'context_within_bound':len(measured['context'])<=8000,
                                            'no_provider_requests':measured['retrieval']['provider_calls']==0}},
                    linked_memory_found=library['id'] in [m['id'] for m in measured['results']],
                    retrieval=measured['retrieval'],context_chars=measured['context_chars'],
                    lookup_ms=measured['lookup_ms'],elapsed_ms=measured['elapsed_ms'],
                    trace_status=measured['trace_status'],
                    unrecorded_lookup_median_ms=statistics.median(t['lookup_ms'] for t in trials),
                    repetitions=repeats))
        return dict(schema_version=1,kind='offline_retrieval_comparison',rows=rows,
            limits=['Synthetic memories; no model tasks or live embeddings.',
                    'Modes use the same frozen memory content; trace writes are measured separately.',
                    'Small diagnostic samples cannot establish a task speedup or cost advantage.',
                    'Semantic mode exercises the unconfigured fallback only.'],
            provider_calls=0,context_tokens=None,cost=None)


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args();result=compare()
    args.output.parent.mkdir(parents=True,exist_ok=True)
    args.output.write_text(json.dumps(result,indent=2)+'\n',encoding='utf-8')
    print(json.dumps({'output':str(args.output),'cases':len(result['rows']),'provider_calls':0}))
