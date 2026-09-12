"""Render a small local task and routing view without running providers."""
import html
import json
from paths import ROOT, TASKS

def render():
    rows = []
    files = sorted(TASKS.glob('*/record.json'), key=lambda p: p.stat().st_mtime, reverse=True)[:20]
    for path in files:
        try:
            job = json.loads(path.read_text(encoding='utf-8'))
            result_uri = (path.parent / 'result.json').as_uri()
            audit = path.parent / 'contribution-audit.md'
            audit_link = ('<br><a href="' + html.escape(audit.as_uri(), quote=True) + '">Contribution audit</a>') if audit.is_file() else ''
            rows.append('<tr><td>' + html.escape(str(job.get('task', 'Task'))) + '</td><td>' +
                        html.escape(str(job.get('worker', 'unknown'))) + '</td><td>' +
                        html.escape(str(job.get('status', 'unknown')).replace('_', ' ')) + '</td><td><a href="' +
                        html.escape(result_uri, quote=True) + '">Saved result</a>' + audit_link + '</td></tr>')
        except (OSError, ValueError, TypeError):
            rows.append('<tr><td colspan="4">A task record could not be read. Inspect runs/tasks.</td></tr>')
    text = '<p><a href="' + (ROOT / 'runtime/project-map.html').as_uri() + '">Open Project Maps — see who helped</a></p>'
    text += '<h2>Task review</h2><p>A response awaits review before Codex accepts it. Quota readiness alone does not establish task quality.</p>'
    text += '<p><a href="' + (ROOT / 'runtime/task-inbox.html').as_uri() + '">Open Task Inbox</a> to search every saved task, read answers and see what needs attention. Reopen the Task Inbox shortcut to refresh its snapshot.</p>'
    if rows:
        text += '<table><thead><tr><th>Task</th><th>Worker</th><th>Review state</th><th>Evidence</th></tr></thead><tbody>' + ''.join(rows) + '</tbody></table>'
    else:
        text += '<p>No recorded application tasks yet.</p>'
    reports = sorted((ROOT / '.orchestration').glob('*/contribution-audit.json'), key=lambda p: p.stat().st_mtime, reverse=True)[:5]
    if reports:
        text += '<h2>Contribution audits</h2><p>Estimated share of accepted work. Recorded usage is reported separately in each audit.</p>'
        for path in reports:
            try:
                report = json.loads(path.read_text(encoding='utf-8'))
                shares = '; '.join(str(row['name']) + ': ' + (str(row['accepted_work_pct']) + '%' if row['accepted_work_pct'] is not None else 'pending') for row in report['by_agent'])
                if not report['attribution_complete']:
                    shares += '; attribution incomplete'
                text += '<p><a href="' + html.escape(path.with_suffix('.md').as_uri(), quote=True) + '">' + html.escape(report['title']) + '</a><br>' + html.escape(shares) + '</p>'
            except (OSError, ValueError, KeyError, TypeError):
                text += '<p>A contribution report could not be read.</p>'
    text += '<p>Gemini uses the guarded Antigravity route. Account access and tool permissions remain separate from the saved allowance shown above. NotebookLM feature usage requires a manual account reading.</p>'
    text += '<p><a href="' + (ROOT / 'README.md').as_uri() + '">Project guide</a> &middot; <a href="' + (ROOT / 'docs/AUDIT.md').as_uri() + '">Audit report</a></p>'
    return text
