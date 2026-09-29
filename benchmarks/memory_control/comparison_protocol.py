"""No-inference declarations and evidence checks for the optional matched suite."""
import hashlib


TRIALS = ('solo-cold', 'team-cold', 'solo-warm', 'team-warm')
JEV_TRIALS = TRIALS + ('solo-jev-warm', 'team-jev-warm')


def conditions(jev_comparison=False):
    rows = []
    for name in JEV_TRIALS if jev_comparison else TRIALS:
        team = name.startswith('team')
        warm = name.endswith('warm')
        ranking = ('jev' if '-jev-' in name else 'ordinary') if warm else 'disabled'
        roster = ([dict(id='solo', role='all', provider='OpenAI', model='gpt-6-astra', effort='medium')]
            if not team else [dict(id='aggregation', role='aggregation', provider='Anthropic', model='opus', effort='medium'),
                dict(id='allocation', role='allocation', provider='xAI', model='configured grok-4.6', effort='low'),
                dict(id='summary', role='summary', provider='OpenAI', model='gpt-6-astra', effort='medium')])
        label = ('Solo OpenAI' if not team else 'OpenAI + Claude + Grok')
        label += ((' / Brain + JEV' if ranking == 'jev' else ' / ordinary seeded Brain')
                  if warm and jev_comparison else ' / seeded Brain' if warm else ' / memory disabled')
        rows.append(dict(id=name, label=label, memory_mode='seeded' if warm else 'disabled', roster=roster,
                         suite_mode='jev_comparison' if jev_comparison else 'four_condition',
                         memory_ranking_mode=ranking if jev_comparison else 'configured'))
    return rows


def child_project(parent_name, condition_id):
    return 'experiment-' + hashlib.sha256(parent_name.encode()).hexdigest()[:16] + '-' + condition_id


def ranking_policy(condition, project, config):
    """Check exact authorization without changing configuration or searching Brain."""
    mode = condition.get('memory_ranking_mode', 'configured')
    authorized = project in config.get('authorized_projects', [])
    receipt = {key: config.get(key) for key in ('status', 'enabled', 'key_present', 'model',
        'purposes', 'min_confidence', 'cache_enabled', 'cache_ttl_seconds', 'max_requests_per_day',
        'timeout_seconds')}
    receipt.update(project_id=project, project_authorized=authorized, expected_mode=mode)
    if condition.get('suite_mode') != 'jev_comparison':
        return receipt
    if mode not in ('ordinary', 'jev', 'disabled'):
        raise ValueError('Matched condition is missing an explicit ranking mode')
    if mode in ('ordinary', 'disabled') and authorized:
        raise ValueError('Cold/ordinary child must not be authorized for JEV: ' + project)
    if mode == 'jev' and (config.get('status') != 'ready' or not config.get('key_present')
                         or not authorized or 'memory_rank' not in config.get('purposes', [])):
        raise ValueError('JEV child requires ready memory_rank and exact project authorization: ' + project)
    return receipt


def memory_telemetry(recalled, *, native=False):
    """Retain the full available receipt, including all nested JEV batch metrics."""
    recalled = recalled or {}
    context = recalled.get('context', '')
    ids = ([row['id'] for row in recalled.get('results', [])] if native else recalled.get('ids', []))
    return dict(memory_ids=ids, memory_context=context,
        memory_sha256=hashlib.sha256(context.encode()).hexdigest() if recalled else None,
        memory_lookup_ms=recalled.get('lookup_ms'), memory_elapsed_ms=recalled.get('elapsed_ms'),
        memory_trace_id=recalled.get('trace_id'), memory_trace_status=recalled.get('trace_status'),
        memory_retrieval=recalled.get('retrieval'), memory_receipt=recalled,
        memory_execution_requested=recalled.get('execution_requested', False))


def validate_retrieval(condition, record):
    if condition.get('suite_mode') != 'jev_comparison' or condition['memory_mode'] != 'seeded':
        return
    retrieval = record.get('memory_retrieval')
    if not isinstance(retrieval, dict):
        raise ValueError('Matched warm call lacks retrieval telemetry')
    jev = retrieval.get('jev')
    if condition['memory_ranking_mode'] == 'ordinary' and jev is not None:
        raise ValueError('Ordinary Brain call unexpectedly invoked JEV')
    if condition['memory_ranking_mode'] == 'jev' and not isinstance(jev, dict):
        raise ValueError('JEV condition lacks a JEV attempt receipt')
    # A recorded fallback, cache hit, or unchanged order is valid evidence. Never
    # turn applied=True into an inferred order_changed=True or missing cost into 0.
