"""Render a small local task and routing view without running providers.

Every link is a loopback route (`/tasks`, `/contributions`) marked for the shared
workspace navigation script, which rewrites it to the sibling saved file when
the page is opened from disk. No `file://` address is ever emitted, because a
page served over http cannot navigate to one.
"""
import html
import json
import re

from paths import ROOT, TASKS

RUN_ID = re.compile(r'[A-Za-z0-9][A-Za-z0-9._-]{0,199}')


def _link(page, text, *, fragment=None, run=None):
    href = '/' + page + ('?run=' + run if run else '') + ('#' + fragment if fragment else '')
    attrs = ' data-workspace-page="' + page + '"'
    if fragment:
        attrs += ' data-workspace-hash="' + html.escape(fragment, quote=True) + '"'
    if run:
        attrs += ' data-workspace-run="' + html.escape(run, quote=True) + '"'
    return '<a href="' + html.escape(href, quote=True) + '"' + attrs + '>' + html.escape(text) + '</a>'


def render(root=None, tasks=None):
    root = ROOT if root is None else root
    tasks = TASKS if tasks is None else tasks
    rows = []
    files = sorted(tasks.glob('*/record.json'), key=lambda p: p.stat().st_mtime, reverse=True)[:20]
    for path in files:
        try:
            job = json.loads(path.read_text(encoding='utf-8'))
            identifier = path.parent.name
            evidence = (_link('tasks', 'Open in Tasks', fragment='task=' + identifier)
                        if re.fullmatch(r'[a-f0-9]{32}', identifier) else 'Unreadable task folder')
            rows.append('<tr><td>' + html.escape(str(job.get('task', 'Task'))) + '</td><td>' +
                        html.escape(str(job.get('worker', 'unknown'))) + '</td><td>' +
                        html.escape(str(job.get('status', 'unknown')).replace('_', ' ')) + '</td><td>' +
                        evidence + '</td></tr>')
        except (OSError, ValueError, TypeError):
            rows.append('<tr><td colspan="4">A task record could not be read. Inspect runs/tasks.</td></tr>')
    text = '<section class="task-review" aria-labelledby="task-review-title">'
    text += '<h2 id="task-review-title">Task review</h2><p>A response waits for review before your lead accepts it. Quota readiness alone does not establish task quality.</p>'
    text += ('<p>' + _link('tasks', 'Open Tasks') + ' to search every saved task, read answers, see why work was held '
             'and what needs attention. ' + _link('contributions', 'Open Contributions') + ' to see who helped.</p>')
    if rows:
        text += ('<div class="table-wrap"><table class="tasks"><thead><tr><th>Task</th><th>Worker</th><th>Review state</th>'
                 '<th>Evidence</th></tr></thead><tbody>' + ''.join(rows) + '</tbody></table></div>')
    else:
        text += '<p>No recorded application tasks yet.</p>'
    reports = sorted((root / '.orchestration').glob('*/contribution-audit.json'), key=lambda p: p.stat().st_mtime, reverse=True)[:5]
    if reports:
        text += '<h2>Contribution audits</h2><p>Estimated share of accepted work. Recorded usage is reported separately in each audit.</p>'
        for path in reports:
            try:
                report = json.loads(path.read_text(encoding='utf-8'))
                shares = '; '.join(str(row['name']) + ': ' + (str(row['accepted_work_pct']) + '%' if row['accepted_work_pct'] is not None else 'pending') for row in report['by_agent'])
                if not report['attribution_complete']:
                    shares += '; attribution incomplete'
                scope = str(report.get('scope_id') or path.parent.name)
                title = _link('contributions', str(report['title']), run=scope) if RUN_ID.fullmatch(scope) \
                    else html.escape(str(report['title']))
                text += '<p>' + title + '<br>' + html.escape(shares) + '</p>'
            except (OSError, ValueError, KeyError, TypeError):
                text += '<p>A contribution report could not be read.</p>'
    text += '<p>Gemini uses the guarded Antigravity route. Account access and tool permissions remain separate from the saved allowance shown above. NotebookLM feature usage requires a manual account reading.</p>'
    text += '<p>The project guide is <code>README.md</code> and the audit report is <code>docs/AUDIT.md</code> in the Orchestrator folder.</p>'
    return text + '</section>'
