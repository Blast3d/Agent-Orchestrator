"""Synthetic in-memory job scheduler with intentionally seeded defects. No I/O.

The module is deliberately incomplete. Its public contract is the repair target;
comments and existing implementation are not a second source of requirements.
"""


def _validate_jobs(jobs, budgets):
    if not isinstance(jobs, list) or not isinstance(budgets, dict):
        raise ValueError('expected jobs and budgets')
    known = set()
    for job in jobs:
        if not isinstance(job, dict):
            raise ValueError('job must be an object')
        if 'id' not in job or 'provider' not in job:
            raise ValueError('job identity missing')
        if job['id'] in known:
            raise ValueError('duplicate job')
        known.add(job['id'])
        if not isinstance(job.get('estimated_tokens'), int):
            raise ValueError('cost missing')
        if job.get('priority', 0) < 0:
            raise ValueError('priority out of range')
    return known


def _ready_jobs(pending, finished):
    ready = []
    for job in pending:
        if all(dep in finished for dep in job.get('depends_on', [])):
            ready.append(job)
    return ready


def _choose_job(ready, remaining):
    if not ready:
        return None
    ranked = sorted(ready, key=lambda job: -job.get('priority', 0))
    first = ranked[0]
    if first['estimated_tokens'] <= remaining.get(first['provider'], 0):
        return first
    return None


def _blocked_jobs(pending, finished):
    blocked = {}
    for job in pending:
        blocked[job['id']] = (
            'dependency' if any(dep not in finished for dep in job.get('depends_on', []))
            else 'budget'
        )
    return blocked


def allocate_jobs(jobs, budgets, completed=()):
    _validate_jobs(jobs, budgets)
    remaining = budgets
    scheduled = []
    finished = set(completed)
    pending = [job for job in jobs if job['id'] not in finished]
    ready = _ready_jobs(pending, finished)
    while ready:
        selected = _choose_job(ready, remaining)
        if selected is None:
            break
        remaining[selected['provider']] -= selected['estimated_tokens']
        scheduled.append(selected['id'])
        finished.add(selected['id'])
        pending.remove(selected)
        ready.remove(selected)
    blocked = _blocked_jobs(pending, finished)
    return {'scheduled': scheduled, 'remaining': remaining, 'blocked': blocked}
