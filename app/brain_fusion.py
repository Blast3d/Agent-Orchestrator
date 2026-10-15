"""Fuse validated keyword and vector ranks without comparing their score scales."""
from brain_retrieval import match_quality

RANK_CONSTANT = 60


def fuse(candidates, lexical_ids, semantic_ids, query):
    """Update the current candidate pool; callers own scope and source validation."""
    lexical = {key: rank for rank, key in enumerate(dict.fromkeys(lexical_ids), 1)
               if key in candidates}
    semantic = {key: rank for rank, key in enumerate(dict.fromkeys(semantic_ids), 1)
                if key in candidates}
    for key, row in candidates.items():
        ranks = [ranking[key] for ranking in (lexical, semantic) if key in ranking]
        complete = match_quality(query, [row])['level'] in ('exact', 'strong')
        score = sum(1 / (RANK_CONSTANT + rank) for rank in ranks)
        # Preserve the existing preference for complete lexical fact matches.
        # This is a deterministic preference, not a confidence probability.
        row['score'] = round(score + (1 if complete else 0), 10)
        row['ranking'] = {'method': 'reciprocal_rank', 'keyword_rank': lexical.get(key),
                          'semantic_rank': semantic.get(key), 'complete_match': complete}
        row['retrieval_method'] = ('hybrid' if key in lexical and key in semantic
                                   else 'keyword' if key in lexical else 'semantic')
        row['reason'] = ('Keyword/vector rank fusion; current source revalidated.' +
                         (' Complete query match retained.' if complete else ''))
    return {'method': 'reciprocal_rank', 'rank_constant': RANK_CONSTANT,
            'keyword_count': len(lexical), 'semantic_count': len(semantic),
            'overlap_count': len(lexical.keys() & semantic.keys())}
