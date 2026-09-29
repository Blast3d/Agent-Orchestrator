"""Small public projection of saved retrieval evidence; old records stay unknown."""
import math
from jev_profiles import PROFILES


def public_retrieval(value):
    if not isinstance(value,dict) or value.get('schema_version')!=1:return None
    def number(v):
        try:return v if type(v) in (int,float) and math.isfinite(v) and v>=0 else None
        except OverflowError:return None
    def short(v):return v[:500] if isinstance(v,str) else None
    result={'schema_version':1}
    result['profile']=value.get('profile') if value.get('profile') in PROFILES else None
    for key,choices in {'requested_strategy':('auto','keyword','graph','semantic'),
                        'route':('empty','keyword','graph','semantic','hybrid'),
                        'quality':('exact','strong','weak','none')}.items():
        result[key]=value.get(key) if value.get(key) in choices else None
    result['reason']=short(value.get('reason'))
    for key in ('graph_hops','graph_added','lexical_candidates','semantic_added','context_tokens','provider_calls','input_tokens','candidate_count'):
        result[key]=number(value.get(key))
    budget=value.get('budget')
    if isinstance(budget,dict):
        result['budget']={key:number(budget.get(key)) for key in ('limit','max_chars','candidate_limit')}
        result['budget']['depth']=budget.get('depth') if budget.get('depth') in ('compact','balanced','deep') else None
    timings=value.get('timings_ms');timings=timings if isinstance(timings,dict) else {}
    result['timings_ms']={key:number(timings.get(key)) for key in ('lexical','graph','semantic','packing','trace','total')}
    semantic=value.get('semantic');semantic=semantic if isinstance(semantic,dict) else {}
    result['semantic']={key:short(semantic.get(key)) for key in ('status','reason','model')}
    result['semantic']['indexed_count']=number(semantic.get('indexed_count'))
    jev=value.get('jev')
    if isinstance(jev,dict):
        result['jev']={key:short(jev.get(key)) for key in ('status','reason','model','provider','request_id')}
        result['jev'].update({key:number(jev.get(key)) for key in ('provider_calls','input_tokens','output_tokens','cost_usd','elapsed_ms')})
        result['jev'].update({key:number(jev.get(key)) for key in (
            'candidate_count','scored_candidate_count','revalidated_candidate_count',
            'eligible_candidate_count','held_candidate_count','returned_count',
            'confidence_threshold','request_bytes','promoted_count','flagged_memory_count',
            'batch_count','request_limit','max_parallel_requests','requested_candidate_count',
            'not_scored_candidate_count','valid_batch_count','failed_batch_count',
            'request_byte_limit','max_request_bytes','conflict_pair_count')})
        result['jev']['conflict_coverage']=short(jev.get('conflict_coverage'))
        result['jev']['purpose']=short(jev.get('purpose'))
        result['jev']['passage_review']=jev.get('passage_review') is True
        result['jev']['passage_review_status']=short(jev.get('passage_review_status'))
        cache=jev.get('cache')
        if isinstance(cache,dict):
            result['jev']['cache']={'status':short(cache.get('status'))}
        result['jev']['ordering_policy']=short(jev.get('ordering_policy'))
        result['jev']['fallback']=jev.get('fallback') if type(jev.get('fallback')) is bool else None
        result['jev']['order_changed']=jev.get('order_changed') if type(jev.get('order_changed')) is bool else None
        result['jev']['applied']=jev.get('applied') is True
        result['jev']['profile']=jev.get('profile') if jev.get('profile') in PROFILES else None
        result['timings_ms']['jev']=number(timings.get('jev'))
    return result
