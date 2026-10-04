"""Skill flow endpoints behind the dashboard's existing origin/token boundary."""
from urllib.parse import parse_qs
import skill_flow as flow

GETS = {'catalog', 'plans', 'plan', 'use'}
POSTS = {
    'recommend': {'task', 'jev', 'run_id'},
    'review': {'plan_id', 'skill_ids', 'expected_manifest_sha256', 'reviewer', 'note'},
    'execute': {'plan_id', 'worker'},
    'accept': {'plan_id', 'reviewer', 'note'},
    'feedback': {'job_id', 'rating', 'reviewer', 'note', 'expected_source_sha256', 'expected_context_sha256'},
}


def get(handler, address):
    action = address.path.removeprefix('/api/skills/')
    if action not in GETS:
        return handler._error(404, 'This skill page is unavailable.')
    params = parse_qs(address.query, max_num_fields=3, keep_blank_values=True)
    allowed = {'project_id'} | ({'plan_id'} if action == 'plan' else {'job_id'} if action == 'use' else set())
    if set(params) != allowed or any(len(v) != 1 for v in params.values()):
        raise ValueError('Choose an exact skill project and record.')
    project = handler._project(params['project_id'][0])
    root = handler.server.root
    if action == 'catalog':
        result = flow.catalog(root, project)
    elif action == 'plans':
        result = flow.plans(root, project)
    elif action == 'plan':
        result = flow.get_plan(root, project, params['plan_id'][0])
    else:
        result = flow.use_evidence(root, project, params['job_id'][0])
    return handler._reply(200, result)


def post(handler, data):
    action = handler.path.removeprefix('/api/skills/')
    if action not in POSTS:
        return handler._error(404, 'This skill action is unavailable.')
    if set(data) - POSTS[action] - {'project_id', 'user_id'}:
        raise ValueError('The skill request contains unsupported fields.')
    project = handler._project(data.get('project_id'))
    root = handler.server.root
    if action == 'recommend':
        if not handler.server.jev_slots.acquire(blocking=False):
            return handler._error(503, 'Two recommendations are running; wait for one to finish.')
        try:
            result = flow.recommend(root, project, data.get('task'), jev=data.get('jev', False), run_id=data.get('run_id'))
        finally:
            handler.server.jev_slots.release()
    elif action == 'review':
        result = flow.review(root, project, data.get('plan_id'), data.get('skill_ids'),
            data.get('expected_manifest_sha256'), data.get('reviewer'), data.get('note'))
    elif action == 'execute':
        result = flow.execute(root, project, data.get('plan_id'), data.get('worker'))
    elif action == 'accept':
        result = flow.accept(root, project, data.get('plan_id'), data.get('reviewer'), data.get('note'))
    else:
        result = flow.feedback(root, project, data.get('job_id'), data.get('rating'), data.get('reviewer'),
            data.get('note'), data.get('expected_source_sha256'), data.get('expected_context_sha256'))
    return handler._reply(200, result)
