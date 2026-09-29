"""Create a read-only, offline task inbox from canonical saved task evidence."""
import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import tempfile
import webbrowser

from paths import ROOT, STATE, TASKS, WORKSPACES
from task_summary_repair import summary_notices
from task_progress_view import enrich_task
from usage_report import bot_summary


def _text(value, default=''):
    return value if isinstance(value, str) else default


def _time(value):
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value.replace('Z', '+00:00'))
        if parsed.tzinfo is None:
            return None
        return parsed.astimezone(timezone.utc).isoformat()
    except (ValueError, OverflowError):
        return None


def _read(path, job_id):
    if not path.exists():
        return None
    # Only inspect the two canonical files in this task folder, never paths in JSON.
    if path.is_symlink() or path.resolve().parent != path.parent.resolve():
        raise ValueError('Task evidence is redirected')
    data = json.loads(path.read_text(encoding='utf-8-sig'))
    if (not isinstance(data, dict) or data.get('job_id') != job_id
            or not isinstance(data.get('status'), str)):
        raise ValueError('Task evidence is malformed')
    return data


def _state(data):
    status = _text(data.get('status'))
    execution = _text(data.get('execution_status'))
    review = _text(data.get('review_status'))
    if (status == 'recovery_required' or execution == 'uncertain'
            or data.get('reservation_state') == 'held_for_reconciliation'):
        return ('uncertain', 'Needs attention', 'Check before retrying',
                'The saved record cannot confirm that the worker stopped. Ask your lead to check the original task before retrying or releasing its allowance.')
    if status in ('accepted', 'rejected'):
        if execution != 'succeeded' or review != status or not _time(data.get('finalized_at')):
            return ('unknown', 'Needs attention', 'Record needs checking',
                    'The saved completion and review details do not agree. Ask your lead to inspect this task before treating the answer as finished.')
        if status == 'accepted':
            return ('accepted', 'Finished', 'Accepted',
                    'The saved answer was accepted. Read the review explanation below for what was checked.')
        return ('rejected', 'Needs attention', 'Not accepted',
                'The answer was reviewed and not accepted. Read the reason below; ask your lead to plan a deliberate revision if more work is needed.')
    if status == 'awaiting_review' and execution == 'succeeded' and _time(data.get('finalized_at')):
        return ('ready', 'Ready to check', 'Ready to check',
                'An answer is saved, but it has not been accepted. Ask your lead to check it against the brief and record the review.')
    if status == 'failed' or execution == 'failed':
        return ('failed', 'Needs attention', 'Did not finish',
                'The task recorded a failure. Ask your lead to inspect the saved evidence and confirm the worker has stopped before deciding whether to try again.')
    if status == 'held' or execution == 'held':
        return ('held', 'Needs attention', 'Held before starting',
                'The task was held before the worker started, so no answer was produced. Check why it was held, then ask your lead to prepare another attempt.')
    if status == 'running' or execution == 'running':
        return ('working', 'Working', 'Recorded as working',
                'This was the last saved state, not a live connection. Reopen Task Inbox for a fresh view; ask your lead to check the original task before starting another copy.')
    if status in ('preparing', 'pending'):
        return ('pending', 'Working', 'Preparing',
                'The task was being prepared when this state was saved. Reopen Task Inbox for a fresh view before asking your lead about its next step.')
    return ('unknown', 'Needs attention', 'State not known',
            'This saved state is not recognized. Ask your lead to inspect the original task before retrying or treating it as finished.')


def _hold(data):
    """Hold-time snapshot from saved admission evidence: numbers, times and plain reasons only.

    The saved quota reading is summarized as it stood when the task was held
    (reading age is measured at the hold time), so it cannot look current later.
    Raw window identifiers are returned only as `technical` detail.
    """
    quota = data.get('quota_before')
    quota = quota if isinstance(quota, dict) else None
    held = data.get('status') == 'held' or data.get('execution_status') == 'held'
    if not held and not (quota and quota.get('allowed') is False):
        return None
    at = _time(data.get('ended_at')) or _time(data.get('finalized_at')) or _time(data.get('created_at'))
    hold = {'at': at, 'reason': _text(data.get('reason')), 'free_pct': None, 'reserved_pct': None,
            'remaining_pct': None, 'reading_age_seconds': None, 'threshold_pct': None,
            'explanation': '', 'next_step': '', 'technical': []}
    if not quota or quota.get('allowed') is not False:
        return hold
    try:
        moment = datetime.fromisoformat(at) if at else datetime.now(timezone.utc)
        worker = {key: quota.get(key) for key in ('worker', 'status', 'allowed', 'reading_status',
                                                   'cooldown_active_pools', 'provider_refresh_errors')}
        worker['worker'] = _text(data.get('worker')) or _text(quota.get('worker'), 'unknown')
        worker['windows'] = [w for w in quota.get('windows', []) if isinstance(w, dict)]             if isinstance(quota.get('windows'), list) else []
        worker['reasons'] = [r for r in quota.get('reasons', []) if isinstance(r, str)]             if isinstance(quota.get('reasons'), list) else []
        worker['warnings'] = [r for r in quota.get('warnings', []) if isinstance(r, str)]             if isinstance(quota.get('warnings'), list) else []
        threshold = quota.get('threshold_pct', 20)
        threshold = threshold if isinstance(threshold, (int, float)) and not isinstance(threshold, bool) else 20
        summary = bot_summary(worker, threshold, _text(quota.get('admission_mode'), 'advisory'), moment)
    except (KeyError, TypeError, ValueError, AttributeError):
        # A damaged snapshot keeps the saved reason; the numbers stay unknown.
        return hold
    hold.update(free_pct=summary['free_pct'], reserved_pct=summary['reserved_pct'],
                remaining_pct=summary['remaining_pct'], reading_age_seconds=summary['age_seconds'],
                threshold_pct=threshold, next_step=summary['next_step'], technical=summary['technical'])
    # The summary speaks in the present tense; keep only the plain cause, in the past tense.
    reasons = worker['reasons']
    if any('cooldown active' in reason for reason in reasons):
        hold['explanation'] = 'The provider had rejected recent work, so a cooldown was running.'
    elif any('worker start threshold' in reason for reason in reasons):
        hold['explanation'] = f'New work needs more than {threshold:g}% free.'
    elif summary['sentence'].startswith('Held: '):
        hold['explanation'] = 'Recorded reason: ' + summary['sentence'].removeprefix('Held: ')
    return hold


def collect(tasks=None, workspaces=None):
    """Read all saved tasks without updating evidence, decisions, or allowances."""
    directory = Path(tasks) if tasks is not None else TASKS
    workspaces_root = Path(workspaces) if workspaces is not None else WORKSPACES
    rows = []
    # Include a surviving result even if its small index was interrupted or damaged.
    folders = {p.parent for pattern in ('*/record.json', '*/result.json') for p in directory.glob(pattern)}
    root = directory.resolve()
    for folder in sorted(folders):
        notices = []
        record = result = None
        modified = []
        if not re.fullmatch(r'[a-f0-9]{32}', folder.name) or folder.resolve().parent != root:
            # Do not follow a task directory redirected outside the task store.
            notices.append('A task folder could not be read safely. Ask your lead to inspect the saved task list.')
        else:
            for filename in ('record.json', 'result.json'):
                path = folder / filename
                try:
                    value = _read(path, folder.name)
                    if value is not None:
                        modified.append(path.stat().st_mtime)
                    if filename == 'record.json':
                        record = value
                    else:
                        result = value
                except (OSError, ValueError, TypeError, RecursionError):
                    notices.append('The saved task summary could not be read.' if filename == 'record.json'
                                   else 'The saved answer could not be read.')
        data = result if result is not None else record
        notices.extend(summary_notices(record, result))
        if data is None:
            data = {'status': 'unknown'}
            notices.append('This task needs its saved files checked. Other tasks are still available.')
        state, group, label, next_step = _state(data)
        answer = _text(result.get('response')) if result else ''
        if result and result.get('response') is not None and not isinstance(result.get('response'), str):
            notices.append('The saved answer is not readable text.')
        if not answer and state in ('ready', 'accepted', 'rejected'):
            notices.append('This record describes an answer, but its saved answer is unavailable in this view.')
        review = data.get('review') if isinstance(data.get('review'), dict) else {}
        if data.get('cleanup_errors') or data.get('reservation_state') == 'cleanup_failed':
            notices.append('The task recorded an unfinished follow-up step. Ask your lead to check the saved evidence and allowance before another attempt.')
        if data.get('export_status') == 'failed':
            notices.append('The extra saved copy could not be written. Any answer shown here comes from the original task record.')
        if notices:
            group = 'Needs attention'
        row = {
            'id': folder.name if re.fullmatch(r'[a-f0-9]{32}', folder.name) else 'unreadable-' + str(len(rows) + 1),
            'title': _text(data.get('task'), 'Task needing inspection') or 'Untitled task',
            'worker': _text(data.get('worker'), 'Unknown worker') or 'Unknown worker',
            'state': state, 'group': group, 'label': label, 'next_step': next_step,
            'answer': answer, 'explanation': _text(data.get('reason')) or _text(data.get('error')),
            'reviewer': _text(review.get('reviewer')),
            'review_note': _text(review.get('note')), 'reviewed_at': _time(review.get('reviewed_at')),
            'created_at': _time(data.get('created_at')), 'started_at': _time(data.get('started_at')),
            'finished_at': _time(data.get('finalized_at')),
            'updated_at': datetime.fromtimestamp(max(modified), timezone.utc).isoformat() if modified else None,
            'notices': notices,
            'hold': _hold(data),
        }
        rows.append(enrich_task(row, data, tasks_root=directory,
                                workspaces_root=workspaces_root,
                                current_known=result is not None))
    rows.sort(key=lambda row: (row['updated_at'] or '', row['created_at'] or '', row['id']), reverse=True)
    return {'schema_version': 1, 'generated_at': datetime.now(timezone.utc).isoformat(),
            'tasks': rows, 'warnings': sum(bool(row['notices']) for row in rows)}


def render_page(tasks=None, workspaces=None, *, live=False, dataset=None):
    """Return the inbox HTML with its data embedded as inert, escaped JSON (no writes)."""
    dataset = dataset if dataset is not None else collect(tasks, workspaces=workspaces)
    if live:
        dataset = dict(dataset, live=True)
    from workspace_navigation import THEME
    # Shared tokens go in before the data, so saved task text never reaches the style placeholder.
    page = (ROOT / 'app/assets/task-inbox.html').read_text(encoding='utf-8').replace('/*__WORKSPACE_THEME__*/', THEME, 1)
    marker = '__TASK_INBOX_DATA__'
    if page.count(marker) != 1:
        raise ValueError('The inbox template needs exactly one data placeholder')
    payload = json.dumps(dataset, ensure_ascii=False, allow_nan=False)
    for character, escaped in (('&', '\\u0026'), ('<', '\\u003c'), ('>', '\\u003e'),
                               ('\u2028', '\\u2028'), ('\u2029', '\\u2029')):
        payload = payload.replace(character, escaped)
    return page.replace(marker, payload)


def generate(output=None, tasks=None, workspaces=None):
    """Write one standalone view; tasks is an optional task-store directory."""
    source = Path(tasks) if tasks is not None else TASKS
    target = Path(output) if output is not None else STATE / 'task-inbox.html'
    if target.resolve().is_relative_to(source.resolve()):
        raise ValueError('The inbox output must be outside the saved task folder')
    dataset = collect(source, workspaces=workspaces)
    page = render_page(dataset=dataset)
    if output is None or target.parent.resolve() == STATE.resolve():
        # The standard saved inbox joins the shared workspace navigation; exports stay standalone.
        from workspace_navigation import decorate_report, write_navigation_script
        page = decorate_report(page, ROOT, 'tasks')
        write_navigation_script(ROOT, directory=target.parent)
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode='w', encoding='utf-8', dir=target.parent,
                                         prefix=target.name + '.', suffix='.tmp', delete=False) as handle:
            temporary = Path(handle.name)
            handle.write(page)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, target)
    finally:
        if temporary:
            temporary.unlink(missing_ok=True)
    return {'output': str(target.resolve()), 'count': len(dataset['tasks']),
            'tasks': len(dataset['tasks']), 'warnings': dataset['warnings']}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, help='Save the offline task page here')
    parser.add_argument('--open', action='store_true', help='Open the saved inbox in a browser')
    args = parser.parse_args()
    result = generate(args.output)
    if args.open:
        webbrowser.open(Path(result['output']).as_uri())
    print(json.dumps(result))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
