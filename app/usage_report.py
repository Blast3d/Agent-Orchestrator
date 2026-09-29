"""Render allowance evidence as plain per-bot readiness, without presenting collection time as usage time.

`bot_summary` turns one evaluated worker into a status (can new work start?),
a reading-age class and one plain sentence with a next step. Raw window and
bucket identifiers stay in a Technical details disclosure. The Usage page and
the Orchestrator viewer's readiness strip share these summaries.
"""
from datetime import datetime, timezone
from html import escape

from workspace_navigation import THEME

LABELS = {'codex': 'Codex (ASTRA / Sol)', 'claude': 'Claude Code (Opus)', 'grok': 'Grok Build',
          'grok-bot': 'Grok Bot', 'gemini': 'Gemini', 'antigravity-claude': 'Claude / GPT via Antigravity',
          'local-chat': 'Local chat', 'notebooklm-chat': 'NotebookLM chat', 'notebooklm-audio': 'NotebookLM audio',
          'notebooklm-slides': 'NotebookLM slides', 'vscode-copilot': 'VS Code Copilot'}
# Which `orchestrator.py refresh --provider` value collects each bot's reading.
REFRESH_PROVIDER = {'codex': 'codex', 'claude': 'claude', 'grok': 'grok',
                    'gemini': 'antigravity', 'antigravity-claude': 'antigravity'}
FRESH_SECONDS = 600
AGING_SECONDS = 24 * 3600
STATE_ORDER = {'held': 0, 'ready': 1, 'unknown': 2, 'local': 3}
STATUS_TEXT = {'held': 'Held', 'ready': 'Ready', 'unknown': 'Ready · usage unknown', 'local': 'Local'}
# Internal reason fragments -> plain wording. Threshold and cooldown reasons get
# their own sentence with numbers; these cover the remaining (mostly strict-mode) holds.
PLAIN_REASONS = (('usage unknown', 'there is no usage reading yet'),
                 ('reading is stale', 'the last reading is too old to trust'),
                 ('latest quota refresh failed', 'the latest usage check failed'),
                 ('reset boundary passed', 'the allowance period reset after the last reading'),
                 ('safety buffer exceeds', 'too little allowance is left for a task of this size'),
                 ('No applicable quota windows', 'no allowance windows are set up for this bot'))


def _instant(value):
    try:
        parsed = datetime.fromisoformat(str(value).replace('Z', '+00:00'))
    except (TypeError, ValueError):
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def _local(moment, pattern='%b %d, %I:%M %p'):
    return moment.astimezone().strftime(pattern).replace(' 0', ' ')


def age_class(seconds):
    """fresh under 10 minutes, aging under 24 hours, stale after that."""
    if seconds is None:
        return 'unknown'
    return 'fresh' if seconds < FRESH_SECONDS else 'aging' if seconds < AGING_SECONDS else 'stale'


def age_words(seconds):
    """Plain reading age; the page script keeps the same wording current."""
    if seconds is None:
        return 'no reading yet'
    if seconds < 60:
        return 'reading under a minute old'
    if seconds < 3600:
        return f'reading {seconds // 60} min old'
    if seconds < 48 * 3600:
        return f'reading {seconds // 3600} h old'
    return f'reading {seconds // 86400} days old'


def _reservation_text(window):
    pending = window.get('pending_pct', window['reserved_pct'])
    text = f'Running work: {pending:g}% estimated.'
    unsettled = window.get('unsettled_finished_pct') or 0
    if unsettled:
        text += (f' Plus {unsettled:g}% for finished work not yet in a reading'
                 ' (not holding work while the reading is stale).')
    elif window['reserved_pct'] > pending:
        text += f' Plus {window["reserved_pct"] - pending:g}% for finished work not yet in this fresh reading.'
    return text


def bot_summary(worker, threshold=20, mode='advisory', current=None):
    """One bot's readiness in plain words; raw reasons are kept only as technical detail."""
    current = current or datetime.now(timezone.utc)
    name = str(worker.get('worker', 'unknown'))
    windows = [w for w in worker.get('windows', []) if isinstance(w, dict) and isinstance(w.get('id'), str)]
    measured = [w for w in windows if not w['id'].endswith('provider-block')]
    known = [w for w in measured if not w.get('reset_passed')]
    if not known:
        # Admission flags are not usage percentages; use one only when nothing else was measured.
        known = [w for w in windows if w['id'].endswith('provider-block') and not w.get('reset_passed')]
    limiting = min(known, key=lambda w: w['available_pct']) if known else None
    times = [t for t in (_instant(w.get('observed_at')) for w in known) if t]
    oldest = min(times) if times else None
    age = max(0, int((current - oldest).total_seconds())) if oldest else None
    reasons = [str(item) for item in worker.get('reasons', [])]
    warnings = [str(item) for item in worker.get('warnings', [])]
    if worker.get('status') == 'local':
        state = 'local'
    elif not worker.get('allowed', True):
        state = 'held'
    elif not known:
        state = 'unknown'
    else:
        state = 'ready'
    free = limiting['available_pct'] if limiting else None
    remaining = limiting['remaining_pct'] if limiting else None
    reserved = limiting.get('reserved_pct', 0) if limiting else 0
    pending = limiting.get('pending_pct', reserved) if limiting else 0
    settled = max(0, reserved - pending)
    provider = REFRESH_PROVIDER.get(name)
    refresh = ('run "python orchestrator.py refresh --provider ' + provider + '"') if provider \
        else 'enter a current account reading'
    next_step = ''
    if state == 'local':
        sentence = 'Runs on this computer, so there is no hosted allowance to check.'
    elif state == 'held':
        parts = []
        cooling = any('cooldown active' in reason for reason in reasons)
        low = any('worker start threshold' in reason for reason in reasons) and limiting is not None
        if cooling:
            ends = [t for t in (_instant(v) for v in (worker.get('cooldown_active_pools') or {}).values()) if t]
            parts.append('the provider rejected recent work, so new work waits '
                         + ('until ' + _local(max(ends)) if ends else 'for a cooldown to end'))
        if low:
            parts.append(f'only {free:g}% is free to start work, and new work needs more than {threshold:g}%')
        for fragment, plain in PLAIN_REASONS:
            if any(fragment in reason for reason in reasons) and plain not in parts:
                parts.append(plain)
        if not parts:
            parts.append('the last usage check did not allow new work')
        sentence = 'Held: ' + '; '.join(parts) + '.'
        if low and pending:
            count = worker.get('active_reservation_count')
            task_label = (f'{count} unfinished task' + ('' if count == 1 else 's')
                          if isinstance(count, int) and count > 0 else 'Unfinished tasks')
            verb = 'reserves' if count == 1 else 'reserve'
            sentence += f' {task_label} {verb} {pending:g}% of the {remaining:g}% left.'
        if low and settled:
            sentence += f' Finished tasks not yet in a reading hold another {settled:g}%.'
        if cooling:
            next_step = 'Wait for the cooldown to end, or give the work to another bot.'
        elif low and pending:
            next_step = ('Ask your lead to close tasks that have finished so their reservations are released, or '
                         + refresh + ' for a fresh reading.')
        elif low:
            next_step = 'Wait for the allowance to reset, or give the work to another bot.'
        else:
            next_step = refresh[0].upper() + refresh[1:] + ' for a fresh reading, or give the work to another bot.'
    elif state == 'unknown':
        sentence = 'No usage reading yet. Work can start; usage is collected in the background.'
    else:
        sentence = f'{free:g}% is free to start work after reservations.'
        if age is not None and age >= AGING_SECONDS:
            next_step = refresh[0].upper() + refresh[1:] + ' when you want a current reading.'
    if state == 'held':
        blocking = 'Blocking new work'
    elif state != 'local' and mode == 'advisory' and (state == 'unknown' or worker.get('reading_status') != 'fresh'):
        blocking = 'Not blocking new work (advisory mode)'
    else:
        blocking = 'Not blocking new work'
    failures = []
    for provider_name, failure in (worker.get('provider_refresh_errors') or {}).items():
        failure = failure if isinstance(failure, dict) else {}
        failures.append({'provider': str(provider_name), 'error': str(failure.get('error', 'Unknown')),
                         'at': str(failure.get('at', ''))})
    return {'id': name, 'label': LABELS.get(name, name), 'state': state, 'status_text': STATUS_TEXT[state],
            'blocking': state == 'held', 'blocking_text': blocking, 'sentence': sentence, 'next_step': next_step,
            'free_pct': free, 'remaining_pct': remaining, 'reserved_pct': reserved if limiting else None,
            'observed_at': oldest.isoformat() if oldest else None, 'age_seconds': age,
            'age_class': age_class(age), 'age_text': age_words(age),
            'refresh_failures': failures, 'technical': reasons + warnings}


def summaries(report, current=None):
    """Held bots first, then ready, unknown and local; ties keep the report order."""
    current = current or datetime.now(timezone.utc)
    threshold = report.get('worker_start_threshold_pct', 20)
    mode = report.get('admission_mode', 'advisory')
    rows = [bot_summary(worker, threshold, mode, current) for worker in report.get('workers', [])]
    return sorted(rows, key=lambda row: STATE_ORDER[row['state']])


def readiness(report, current=None):
    """Compact JSON for the viewer's readiness strip; raw identifiers stay in `technical`."""
    current = current or datetime.now(timezone.utc)
    bots = summaries(report, current)
    counts = {state: sum(row['state'] == state for row in bots) for state in STATE_ORDER}
    ages = [row['age_seconds'] for row in bots if row['age_seconds'] is not None]
    return {'generated_at': report.get('updated_at'), 'admission_mode': report.get('admission_mode', 'advisory'),
            'threshold_pct': report.get('worker_start_threshold_pct', 20),
            'active_reservations': report.get('active_reservations', 0),
            'counts': counts, 'oldest_age_seconds': max(ages) if ages else None,
            'headline': headline(counts, max(ages) if ages else None), 'bots': bots}


def headline(counts, oldest):
    pieces = [f'{counts["ready"]} ready', f'{counts["held"]} held']
    if counts['unknown']:
        pieces.append(f'{counts["unknown"]} without a reading')
    if counts['local']:
        pieces.append(f'{counts["local"]} local')
    text = 'Bots: ' + ' · '.join(pieces)
    if oldest is not None:
        text += ' · oldest reading ' + age_words(oldest).removeprefix('reading ').removesuffix(' old')
    return text


def _window_details(window, current):
    observed = _instant(window['observed_at']) or current
    age = max(0, int((current - observed).total_seconds()))
    age_label = f'{age // 3600}h {(age % 3600) // 60}m' if age >= 3600 else f'{age // 60}m {age % 60}s'
    historical = window.get('reset_passed', False)
    reading = 'Previous period' if historical else 'Last observed'
    age_html = f'<span class="age" data-observed="{int(observed.timestamp())}">{age_label}</span>'
    detail = ('<details><summary>' + escape(window['id']) + f' · {window["remaining_pct"]:g}% {reading.lower()} · {age_html} ago'
              + '</summary><small>' + reading + f': {window["remaining_pct"]:g}%. '
              + escape(_reservation_text(window)) + '</small>'
              + '<small>Observed <time datetime="' + escape(window['observed_at'], quote=True) + '">'
              + escape(observed.astimezone().strftime('%b %d, %Y %I:%M:%S %p %Z'))
              + '</time> · ' + age_html + ' ago</small>'
              + '<small>Source: ' + escape(str(window['source'])) + '</small>')
    if window.get('reset_at'):
        reset = _instant(window['reset_at'])
        if reset:
            detail += ('<small>' + ('Reset passed: ' if historical else 'Resets: ')
                       + escape(_local(reset, '%b %d, %I:%M %p %Z')) + '</small>')
    elif window.get('reset_display'):
        detail += '<small>Reset: ' + escape(str(window['reset_display'])) + '</small>'
    return detail + '</details>'


def _row(worker, summary, current):
    epoch = ''
    if summary['observed_at']:
        epoch = str(int(_instant(summary['observed_at']).timestamp()))
    chip = ('<span class="age-chip ' + summary['age_class'] + '"' + (' data-reading-at="' + epoch + '"' if epoch else '')
            + '>' + escape(summary['age_text']) + '</span>')
    status = ('<span class="pill ' + summary['state'] + '">' + escape(summary['status_text']) + '</span>' + chip
              + '<small class="blocking ' + ('blocked' if summary['blocking'] else 'open') + '">'
              + escape(summary['blocking_text']) + '</small>')
    body = '<p class="why">' + escape(summary['sentence']) + '</p>'
    if summary['free_pct'] is not None:
        free, reserved = summary['free_pct'], summary['reserved_pct'] or 0
        numbers = [f'{summary["remaining_pct"]:g}% left in the last reading']
        if reserved:
            numbers.append(f'{reserved:g}% reserved')
        body += ('<p class="numbers">' + escape(' · '.join(numbers)) + ' · <strong>'
                 + f'{free:g}% available after reservations</strong></p>'
                 + f'<div class="bar" role="img" aria-label="{free:g} percent free, {reserved:g} percent reserved">'
                 + f'<i class="free" style="width:{min(100, max(0, free)):g}%"></i>'
                 + f'<i class="reserved" style="width:{min(100, max(0, reserved)):g}%"></i></div>')
        if worker.get('reading_status') == 'unknown' and summary['state'] != 'local':
            body += '<small>Other required windows are unknown. This is a partial reading.</small>'
    elif summary['state'] not in ('local', 'held'):
        body += '<small>Current allowance unknown. Collection does not block task startup.</small>'
    for failure in summary['refresh_failures']:
        at = _instant(failure['at'])
        body += ('<small class="warning">The latest ' + escape(failure['provider']) + ' usage check failed: '
                 + escape(failure['error']) + (' (' + escape(_local(at)) + ')' if at else '') + '</small>')
    if summary['next_step']:
        body += '<p class="next"><strong>Next:</strong> ' + escape(summary['next_step']) + '</p>'
    technical = ''.join(_window_details(w, current) for w in worker.get('windows', [])
                        if not w['id'].endswith('provider-block'))
    if summary['technical']:
        technical += ('<ul class="raw">' + ''.join('<li>' + escape(item) + '</li>' for item in summary['technical'])
                      + '</ul>')
    if technical:
        body += '<details class="technical"><summary>Technical details</summary>' + technical + '</details>'
    return ('<tr class="bot-row ' + summary['state'] + '"><th scope="row">' + escape(summary['label'])
            + '</th><td class="status">' + status + '</td><td>' + body + '</td></tr>')


STYLE = '''
:root{--bg:var(--ws-bg);--panel:var(--ws-surface);--line:var(--ws-line);--text:var(--ws-text);--muted:var(--ws-muted);--accent:var(--ws-accent);--good:var(--ws-good);--good-bg:var(--ws-good-bg);--warn:var(--ws-warn);--warn-bg:var(--ws-warn-bg);--aging:var(--ws-aging);--aging-bg:var(--ws-aging-bg);--bad:var(--ws-bad);--bad-bg:var(--ws-bad-bg);--track:var(--ws-track);--chip:var(--ws-chip)}
body{background:var(--bg);color:var(--text);font:16px/1.5 var(--ws-font);margin:0 auto;padding:32px 5vw 48px;max-width:1200px}body>.workspace-navigation{margin:0 0 24px}
h1{font-size:36px;margin:0 0 12px}h2{font-size:22px;margin:32px 0 8px}p,small{color:var(--muted)}strong{color:var(--text)}a{color:var(--accent)}a:hover{text-decoration-thickness:2px}
a:focus-visible,summary:focus-visible{outline:3px solid var(--accent);outline-offset:3px;border-radius:2px}
.readiness{display:flex;flex-wrap:wrap;gap:6px 18px;align-items:baseline;justify-content:space-between;border:1px solid var(--line);background:var(--panel);border-radius:12px;padding:14px 18px;margin:18px 0 0}
.readiness strong{font-size:17px}.readiness small{margin:0}
table{border-collapse:collapse;width:100%;margin:18px 0 28px}th,td{text-align:left;padding:18px 12px;border-bottom:1px solid var(--line);vertical-align:top}thead th{font-size:13px;color:var(--muted);font-weight:600}
tbody th{min-width:140px;font-weight:650}small{display:block;margin-top:7px}.status{min-width:190px}td{overflow-wrap:anywhere}
.pill{display:inline-block;font-size:12px;font-weight:700;letter-spacing:.03em;padding:3px 10px;border-radius:20px;margin:0 8px 6px 0;background:var(--chip);color:var(--muted)}
.pill.ready{background:var(--good-bg);color:var(--good)}.pill.held{background:var(--bad-bg);color:var(--bad)}
.age-chip{display:inline-block;font-size:12px;padding:3px 9px;border-radius:20px;background:var(--chip);color:var(--muted);white-space:nowrap}
.age-chip.fresh{background:var(--good-bg);color:var(--good)}.age-chip.aging{background:var(--aging-bg);color:var(--aging)}.age-chip.stale{background:var(--warn-bg);color:var(--warn)}
.blocking.blocked{color:var(--bad);font-weight:600}.why{color:var(--text);margin:0 0 6px}.numbers{margin:6px 0}.next{margin:10px 0 0;color:var(--text)}
.warning{color:var(--warn)}.bar{display:flex;height:6px;max-width:320px;background:var(--track);border-radius:6px;margin-top:8px;overflow:hidden}
.bar i{display:block;height:100%}.bar .free{background:var(--good)}.bar .reserved{background:repeating-linear-gradient(135deg,var(--warn) 0 4px,transparent 4px 7px)}
details{margin-top:12px}summary{cursor:pointer;color:var(--muted)}details.technical>summary{font-size:14px}.raw{margin:10px 0 0;padding-left:20px;font-size:13px;color:var(--muted)}
.table-wrap{overflow-x:auto}.tasks th,.tasks td{padding:12px 10px;font-size:14px}.task-review{margin-top:8px}
@media(max-width:650px){body{padding:20px 16px 32px;font-size:15px}h1{font-size:28px}.bots,.bots thead,.bots tbody,.bots tr,.bots th,.bots td{display:block}.bots thead{display:none}
.bots tr.bot-row{border:1px solid var(--line);border-radius:12px;background:var(--panel);margin:0 0 12px;padding:4px 0}.bots tbody th,.bots tbody td{border:0;padding:8px 14px}.status{min-width:0}.tasks th,.tasks td{padding:10px 8px;font-size:13px}}
'''

SCRIPT = r'''(function(){
function words(s){if(s<60)return 'reading under a minute old';if(s<3600)return 'reading '+Math.floor(s/60)+' min old';if(s<172800)return 'reading '+Math.floor(s/3600)+' h old';return 'reading '+Math.floor(s/86400)+' days old';}
function cls(s){return s<600?'fresh':s<86400?'aging':'stale';}
function tick(){const now=Date.now()/1000;
for(const chip of document.querySelectorAll('.age-chip[data-reading-at]')){const age=Math.max(0,Math.floor(now-Number(chip.dataset.readingAt)));chip.textContent=words(age);chip.className='age-chip '+cls(age);}
for(const node of document.querySelectorAll('.age[data-observed]')){const age=Math.max(0,Math.floor(now-Number(node.dataset.observed)));node.textContent=age>=3600?Math.floor(age/3600)+'h '+Math.floor(age%3600/60)+'m':Math.floor(age/60)+'m '+age%60+'s';}
const page=document.getElementById('page-age');if(page&&page.dataset.generated){const age=Math.max(0,Math.floor(now-Number(page.dataset.generated)));page.textContent=age<60?'under a minute ago':age<3600?Math.floor(age/60)+' min ago':Math.floor(age/3600)+' h ago';}}
tick();setInterval(tick,10000);})();'''


def render_usage(report):
    """Advisory or strict usage page; the task review section is appended before </html> by the caller."""
    current = datetime.now(timezone.utc)
    threshold = report['worker_start_threshold_pct']
    mode = report.get('admission_mode', 'advisory')
    by_id = {str(worker.get('worker')): worker for worker in report['workers']}
    summary = readiness(report, current)
    rows = ''.join(_row(by_id[bot['id']], bot, current) for bot in summary['bots'])
    generated = _instant(report.get('updated_at')) or current
    rule = (f'New work for a bot is held when {threshold:g}% or less of its allowance is free after reservations, '
            'or after a confirmed quota rejection. Other readings are advisory: stale or missing readings do not block work.'
            if mode == 'advisory' else
            f'New work for a bot is held when {threshold:g}% or less is free, or when its reading is missing or stale.')
    return ('<!doctype html><html lang="en"><head><meta charset="utf-8"><meta http-equiv="refresh" content="30">'
            '<meta name="viewport" content="width=device-width,initial-scale=1"><title>Usage · Orchestrator</title>'
            '<style>' + THEME + STYLE + '</style></head><body><main>'
            '<h1>Usage</h1><p><strong>Which bots can start new work now.</strong> '
            'Each row says whether new work is blocked, how old its reading is, and what to do next. '
            'Estimates are planning allowances, not measured consumption.</p><p>' + escape(rule) + '</p>'
            '<section class="readiness" aria-label="Bot readiness"><strong>' + escape(summary['headline'])
            + '</strong><small>Page data from ' + escape(_local(generated))
            + ' (<span id="page-age" data-generated="' + str(int(generated.timestamp())) + '">just now</span>) · '
            + str(report['active_reservations']) + ' active reservations</small></section>'
            '<table class="bots"><thead><tr><th scope="col">Bot</th><th scope="col">Can new work start?</th>'
            '<th scope="col">Allowance and next step</th></tr></thead><tbody>' + rows + '</tbody></table>'
            '<p>Snapshot generated ' + escape(report['updated_at']) + '. Reading ages above show when usage was actually measured.</p>'
            '<p>Usage collects in the background on task dispatch, when status or the dashboard is read (at most every '
            'five minutes per provider), and every five minutes while the monitor runs. '
            'This page reloads every 30 seconds. The orchestrator can read the same saved evidence with '
            '<code>python orchestrator.py status</code>.</p></main>'
            '<script>' + SCRIPT + '</script></html>')
