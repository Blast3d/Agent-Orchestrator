"""Task-sized recall budgets; corpus size never forces irrelevant context."""

DEPTHS = ('compact', 'balanced', 'deep')
MAX_RESULTS = 24
MAX_CONTEXT_CHARS = 32000
MAX_CANDIDATES = 100
_BUDGETS = {'compact': (6, 8000, 16), 'balanced': (12, 16000, 48),
            'deep': (24, 32000, 100)}


def resolve(depth='compact', *, task_size=None, limit=None, max_chars=None):
    if depth == 'auto':
        if task_size not in (None, 'tiny', 'small', 'medium', 'large'):
            raise ValueError('Unknown task size for memory recall')
        depth = {'medium': 'balanced', 'large': 'deep'}.get(task_size, 'compact')
    if depth not in DEPTHS:
        raise ValueError('Memory depth must be compact, balanced, deep or auto')
    count, chars, candidates = _BUDGETS[depth]
    for name, value in (('limit', limit), ('max_chars', max_chars)):
        if value is not None and type(value) is not int:
            raise ValueError('Memory ' + name + ' must be an integer')
    count = count if limit is None else max(1, min(limit, MAX_RESULTS))
    chars = chars if max_chars is None else max(256, min(max_chars, MAX_CONTEXT_CHARS))
    # Explicit larger result limits need a shortlist at least as large.
    candidates = max(candidates, min(MAX_CANDIDATES, count * 4)) if count > 6 else candidates
    return {'depth': depth, 'limit': count, 'max_chars': chars,
            'candidate_limit': candidates}
