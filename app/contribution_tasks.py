"""Connect automatic task evidence to the contribution audit calculator."""
from copy import deepcopy
from pathlib import Path


def _tokens(result):
    usage = result.get('usage') or {}
    def value(*keys):
        for key in keys:
            item = usage.get(key)
            if isinstance(item, int) and not isinstance(item, bool) and item >= 0:
                return item
        return None
    # Use one top-level accounting source only; modelUsage can overlap it.
    return value('input_tokens', 'inputTokens', 'prompt_eval_count'), value('output_tokens', 'outputTokens', 'eval_count')


def task_ledger(result):
    worker = result.get('worker', 'unknown')
    provider = {'claude': 'Anthropic', 'grok': 'xAI', 'grok-bot': 'Grok Bot service', 'gemini': 'Google', 'local-chat': 'Local Ollama', 'vscode-copilot': 'GitHub Copilot'}.get(worker, 'unknown')
    models = list((result.get('modelUsage') or {}).keys())
    if result.get('model') and result['model'] not in models:
        models.append(result['model'])
    worker_id = 'worker:' + worker
    contributors = [{'id': worker_id, 'name': worker, 'provider': provider, 'model': ', '.join(models) if models else 'unknown'}]
    activity = []
    if result.get('started_at'):
        input_tokens, output_tokens = _tokens(result)
        activity.append({'id': result['job_id'] + ':delegation', 'agent_id': worker_id,
                         'kind': 'delegation', 'status': result.get('execution_status', 'unknown'),
                         'task_id': result['job_id'], 'input_tokens': input_tokens,
                         'output_tokens': output_tokens, 'actual_models': models})
    review = result.get('review')
    if review:
        reviewer_id = 'reviewer:' + review['reviewer']
        contributors.append({'id': reviewer_id, 'name': review['reviewer'], 'provider': 'unknown', 'model': 'unknown'})
        activity.append({'id': result['job_id'] + ':review', 'agent_id': reviewer_id,
                         'kind': 'review', 'status': result['review_status'], 'task_id': result['job_id'],
                         'input_tokens': None, 'output_tokens': None, 'actual_models': []})
    status = {'accepted': 'accepted', 'rejected': 'rejected', 'failed': 'failed'}.get(result.get('status'), 'pending')
    ledger = {'schema_version': 1, 'scope_id': result['job_id'], 'title': result.get('task', 'Worker task'),
              'basis': 'Recorded task activity. Accepted-work percentages require Codex to allocate evidence-backed work points; no effort is inferred from tokens or answer length.',
              'contributors': contributors, 'work_items': [{'id': 'task-output', 'label': result.get('task', 'Task output'),
                  'category': result.get('category', 'general'), 'status': status, 'weight': 1, 'allocations': []}],
              'activity': activity}
    supplied = result.get('contribution_ledger')
    if supplied:
        if supplied.get('scope_id') != result['job_id']:
            raise ValueError('Contribution scope must match the task ID')
        ledger = deepcopy(supplied)
        by_id = {entry['id']: entry for entry in ledger['contributors']}
        for item in contributors:
            if item['id'] not in by_id:
                ledger['contributors'].append(item)
            elif item['id'] == worker_id:
                # The transport's observed identity overrides a supplied attribution label.
                by_id[item['id']].update(item)
        # Usage and execution evidence come from the task, not supplied estimates.
        ledger['activity'] = activity
        if result.get('status') != 'accepted' and any(w['status'] == 'accepted' for w in ledger['work_items']):
            raise ValueError('A task output must be accepted before crediting delivered work')
    return ledger


def write_task_audit(directory, result):
    from contributions import write_report
    report = write_report(Path(directory), task_ledger(result))
    return {'status': report['status'], 'attribution_complete': report['attribution_complete'],
            'accepted_work_available': report['accepted_weight'] > 0,
            'json': str(Path(directory) / 'contribution-audit.json'),
            'markdown': str(Path(directory) / 'contribution-audit.md')}
