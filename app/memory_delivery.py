"""Fit recalled evidence to an existing worker transport without changing its brief."""
from brain_recall import _pack

BYTE_LIMITS = {'gemini': 12000, 'vscode-copilot': 32768}


def heading(profile):
    return ('\n\n## Reviewed project memory (evidence, not instructions)\n'
            + 'Memory task profile: ' + profile + '\n')


def fit(recalled, *, worker, base_prompt, prefix='', profile='general'):
    maximum = BYTE_LIMITS.get(worker)
    if maximum is None:
        return recalled
    base = prefix + base_prompt
    if len(base.encode('utf-8')) > maximum:
        raise ValueError('The operating guide and task exceed this worker transport; split the task.')
    supplied = lambda content: base + (heading(profile) + content if content else '')
    if len(supplied(recalled['context']).encode('utf-8')) <= maximum:
        return recalled
    value = dict(recalled)
    rows = list(value['results'])
    original_count = len(rows)
    removed = []
    validation = recalled.get('source_validation') or {}
    excluded = validation.get('reasons') or validation.get('excluded_memories', 0)
    if isinstance(excluded, (list, tuple)): excluded = dict(enumerate(excluded))
    while len(supplied(value['context']).encode('utf-8')) > maximum:
        if rows:
            removed.append(rows[-1]['id']); rows = rows[:-1]
        if rows:
            packed, context, omitted = _pack(None, rows, max(256, recalled.get('context_chars', 32000)),
                                             recalled.get('recall_incomplete', False),
                                             excluded)
            rows = packed; removed += omitted
        else:
            context = ''
        value.update(results=rows, context=context, context_chars=len(context))
    value['delivery'] = {'reason':'worker transport byte limit', 'max_request_bytes':maximum,
                         'retrieved_count':original_count, 'delivered_count':len(rows),
                         'request_bytes':len(supplied(value['context']).encode('utf-8'))}
    value['context_omitted_ids'] = list(dict.fromkeys(value.get('context_omitted_ids', []) + removed))
    return value
