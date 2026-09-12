"""Render advisory allowance evidence without presenting collection time as usage time."""
from datetime import datetime, timezone
from html import escape


def render_usage(report):
    current = datetime.now(timezone.utc)
    threshold = report['worker_start_threshold_pct']
    labels = {'codex': 'ASTRA / OpenAI', 'claude': 'Claude / Fable', 'grok': 'Grok Build',
              'grok-bot': 'Grok Bot', 'gemini': 'Gemini', 'antigravity-claude': 'Claude / GPT via Antigravity',
              'local-chat': 'Local chat'}
    rows = []
    for worker in report['workers']:
        windows = worker['windows']
        known = [w for w in windows if not w.get('reset_passed') and not w['id'].endswith('provider-block')]
        if worker['status'] == 'local':
            badge, detail = 'LOCAL', 'No hosted allowance'
        elif not worker['allowed']:
            badge, detail = 'USE ANOTHER PROVIDER', ''
        else:
            badge = {'fresh': 'READY', 'cached': 'READY · CACHED', 'unknown': 'READY · USAGE UNKNOWN'}[worker['reading_status']]
            detail = ''
        if known:
            limiting = min(known, key=lambda w: w['available_pct'])
            value = limiting['available_pct']
            detail += (f'<strong>{value:g}% available after reservations</strong>'
                       f'<div class="bar" aria-label="{value:g} percent estimated available"><i style="width:{value}%"></i></div>')
            if worker['reading_status'] == 'unknown':
                detail += '<small>Other required windows are unknown. This is a partial reading.</small>'
        elif worker['status'] != 'local':
            detail += '<strong>Current allowance unknown</strong><small>Collection does not block task startup.</small>'
        expires = []
        for window in windows:
            if window['id'].endswith('provider-block'):
                continue  # Admission flags are not measured percentage windows.
            observed = datetime.fromisoformat(window['observed_at'].replace('Z', '+00:00'))
            age = max(0, int((current - observed).total_seconds()))
            age_label = f'{age // 3600}h {(age % 3600) // 60}m' if age >= 3600 else f'{age // 60}m {age % 60}s'
            historical = window.get('reset_passed', False)
            reading = 'Previous period' if historical else 'Last observed'
            detail += ('<details><summary>' + escape(window['id']) + f' · {window["remaining_pct"]:g}% {reading.lower()} · {age_label} ago'
                       + '</summary><small>' + reading + f': {window["remaining_pct"]:g}%. '
                       + f'Reservations: {window["reserved_pct"]:g}% estimated.</small>'
                       + '<small>Observed <time datetime="' + escape(window['observed_at'], quote=True) + '">'
                       + escape(observed.astimezone().strftime('%b %d, %Y %I:%M:%S %p %Z'))
                       + '</time> · ' + age_label + ' ago</small>')
            detail += '<small>Source: ' + escape(str(window['source'])) + '</small>'
            if window.get('reset_at'):
                reset = datetime.fromisoformat(window['reset_at'].replace('Z', '+00:00'))
                detail += '<small>' + ('Reset passed: ' if historical else 'Resets: ') + escape(reset.astimezone().strftime('%b %d, %I:%M %p %Z')) + '</small>'
                expires.append(reset.timestamp())
            elif window.get('reset_display'):
                detail += '<small>Reset: ' + escape(str(window['reset_display'])) + '</small>'
            detail += '</details>'
            expires.append(observed.timestamp() + window['max_age_seconds'])
        errors = worker.get('provider_refresh_errors', {})
        for provider, failure in errors.items():
            detail += '<small class="warning">Latest ' + escape(provider) + ' refresh failed: ' + escape(str(failure.get('error', 'Unknown'))) + ' · ' + escape(failure['at']) + '</small>'
        reasons = worker.get('reasons', []) + worker.get('warnings', [])
        detail += '<small class="reason">' + escape('; '.join(reasons)) + '</small>'
        expiry = str(min(expires)) if expires else ''
        rows.append('<tr data-valid-until="' + expiry + '"><td>' + escape(labels.get(worker['worker'], worker['worker']))
                    + '</td><td class="badge ' + ('ready' if worker['allowed'] else 'held') + '">' + escape(badge)
                    + '</td><td>' + detail + '<small class="expiry-note"></small></td></tr>')
    return '''<!doctype html><html lang="en"><meta charset="utf-8"><meta http-equiv="refresh" content="30">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>Model usage</title>
<style>body{background:#10151f;color:#e6edf6;font:16px system-ui;margin:5vw;max-width:1200px}h1{font-size:36px}p,small{color:#aebbcf}table{border-collapse:collapse;width:100%;margin:2em 0}th,td{text-align:left;padding:18px 12px;border-bottom:1px solid #344055;vertical-align:top}small{display:block;margin-top:7px}.ready{color:#7ce0b4}.held{color:#ffb4a5}.warning{color:#ffd37d}strong{color:#e6edf6}.bar{height:5px;max-width:300px;background:#293447;border-radius:6px;margin-top:8px;overflow:hidden}.bar i{display:block;height:100%;background:#7ce0b4}.badge{font-size:12px;letter-spacing:.04em}details{margin-top:12px}summary{cursor:pointer}td:first-child{min-width:140px}@media(max-width:650px){body{margin:20px;font-size:14px}td,th{padding:14px 7px}td:first-child{min-width:90px}}</style>
<h1>Model usage</h1><p><strong>Start work without waiting for an allowance check.</strong>
Use the last recorded reading. Missing readings and collection timeouts are visible warnings.</p>
<p>Prefer another provider at ''' + f'{threshold:g}' + '''% remaining or below after reservations.
Confirmed quota rejection also triggers an eligible replacement. Estimates are planning allowances, not measured consumption.</p>
<table><thead><tr><th>Worker</th><th>Routing status</th><th>Last recorded allowance</th></tr></thead><tbody>''' + ''.join(rows) + '''</tbody></table>
<p>Snapshot generated ''' + escape(report['updated_at']) + ' · ' + str(report['active_reservations']) + ''' active reservations.
Observation times above show when usage was actually measured.</p>
<p>Usage collects in the background on task dispatch and every five minutes while the monitor runs.
This page reloads every 30 seconds. The orchestrator can read the same saved evidence with <code>python orchestrator.py status</code>.</p>
<script>function stale(){for(const row of document.querySelectorAll('tr[data-valid-until]')){const until=Number(row.dataset.validUntil);if(until&&Date.now()/1000>until){row.querySelector('.expiry-note').textContent='Cached evidence: the freshness or reset boundary has passed. Collection continues in the background.';}}}stale();setInterval(stale,10000);</script></html>'''
