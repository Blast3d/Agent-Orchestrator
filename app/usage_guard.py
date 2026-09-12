"""Quota monitoring and atomic reservations for Codex's model workers."""
import argparse
from contextlib import contextmanager
from datetime import datetime, timezone, timedelta
import html
import json
import math
import os
from pathlib import Path
import subprocess
import sys
import time
import uuid
from paths import STATE, APP, WORKSPACES

UTC = timezone.utc
DEFAULT_ROOT = STATE
SETUP = APP
POOLS = {
    'codex': [],
    'gemini': ['agy:gemini-weekly'],
    'antigravity-claude': ['agy:3p-weekly'],
    'grok': ['grok-weekly'],
    'grok-bot': ['grokbot-weekly'],
    'claude': ['claude-five-hour', 'claude-seven-day'],
    'vscode-copilot': ['copilot-account'],
    'notebooklm-chat': ['notebooklm-chat-daily'],
    'notebooklm-audio': ['notebooklm-audio-daily'],
    'notebooklm-slides': ['notebooklm-slides-daily'],
    'local-chat': [],
}
DEFAULT_POLICY = {'warning_pct': 20, 'floor_pct': 10,
                  'estimates_pct': {'tiny': 1, 'small': 3, 'medium': 8, 'large': 15},
                  'worker_pools': POOLS}
REFRESH_PROVIDERS = ('codex', 'antigravity', 'grok', 'claude')
REFRESH_LOCK_TIMEOUT = 75


class _RefreshCancelled(Exception):
    pass


def quota_reader_failure(stdout):
    """Keep actionable known collector failures; never echo arbitrary CLI output."""
    generic = 'Official CLI quota panel could not be read'
    try:
        payload = json.loads(stdout)
    except (ValueError, TypeError):
        return generic
    if not isinstance(payload, dict) or payload.get('status') != 'unknown':
        return generic
    error = payload.get('error')
    if not isinstance(error, str):
        return generic
    # Collector messages are fixed local strings. Do not forward raw terminal
    # output, URLs, account names, exception traces or arbitrary provider prose.
    causes = {
        'No complete Grok usage display appeared within the time limit.':
            'Grok usage panel timed out; no fresh allowance was confirmed.',
        'No complete Claude Code /usage panel appeared within the time limit.':
            'Claude usage panel timed out; no fresh allowance was confirmed.',
        'Claude Code interactive onboarding, authorization or isolated-folder trust is incomplete.':
            'Claude needs interactive setup, account authorization or isolated-folder trust.',
        "Claude's current usage display has not yet been live-validated; quota remains unknown.":
            'The installed Claude usage display needs adapter validation.',
        'Claude usage refresh could not be distinguished from cached data; quota remains unknown.':
            'Claude did not show a verifiable fresh allowance refresh.',
        'The quota-only session did not verify zero model tokens.':
            'The Claude reader could not confirm a quota-only session.',
        'The official Grok CLI is not installed at the configured location.':
            'The configured Grok executable is missing.',
        'Claude Code is not installed at its configured location.':
            'The configured Claude executable is missing.',
        "Claude's isolated worker folder has not been prepared.":
            'The Claude quota workspace is missing.',
        'Could not verify the installed Claude Code version.':
            'The Claude executable version check failed.',
    }
    return causes.get(error, generic)

def validate_policy(policy):
    """Reject malformed editable budgets before they can weaken admission."""
    if not isinstance(policy, dict):
        raise ValueError('Quota policy must be an object')
    def percent(value, label, positive=False):
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise ValueError(label + ' must be a finite numeric percentage')
        if not math.isfinite(value) or not 0 <= value <= 100 or (positive and value == 0):
            raise ValueError(label + ' must be ' + ('greater than zero and ' if positive else '') + 'within 0 to 100')
    for key in ('warning_pct', 'floor_pct'):
        percent(policy.get(key), key)
    if policy.get('quota_admission_mode', 'strict') not in ('strict', 'advisory'):
        raise ValueError('Quota admission mode must be strict or advisory')
    percent(policy.get('worker_start_threshold_pct', 20), 'worker_start_threshold_pct')
    fallbacks = policy.get('automatic_fallbacks', {})
    if not isinstance(fallbacks, dict):
        raise ValueError('Automatic fallbacks must map hosted workers to alternatives')
    for primary, alternates in fallbacks.items():
        if (primary not in ('claude', 'grok') or not isinstance(alternates, list)
                or any(w not in ('claude', 'grok') or w == primary for w in alternates)
                or len(alternates) != len(set(alternates))):
            raise ValueError('Automatic fallbacks require distinct hosted Claude/Grok routes')
    if policy['floor_pct'] > policy['warning_pct']:
        raise ValueError('Quota warning percentage must be at least the safety floor')
    estimates = policy.get('estimates_pct')
    if not isinstance(estimates, dict) or not {'tiny', 'small', 'medium', 'large'}.issubset(estimates):
        raise ValueError('Quota policy requires tiny, small, medium and large task estimates')
    for size, value in estimates.items():
        percent(value, 'Task estimate ' + str(size), positive=True)
    pools = policy.get('worker_pools')
    if not isinstance(pools, dict) or not pools:
        raise ValueError('Quota policy requires worker pool mappings')
    for worker, keys in pools.items():
        if not isinstance(worker, str) or not worker or not isinstance(keys, list):
            raise ValueError('Quota worker mappings must name a worker and a list of pools')
        if any(not isinstance(key, str) or not key for key in keys) or len(keys) != len(set(keys)):
            raise ValueError('Quota worker pools must contain unique nonempty pool IDs')

def now():
    return datetime.now(UTC)

def stamp(value=None):
    return (value or now()).isoformat()

def instant(value):
    result = datetime.fromisoformat(value.replace('Z', '+00:00'))
    if result.tzinfo is None:
        raise ValueError('Timestamp needs a time zone')
    return result.astimezone(UTC)

def pool_owner(key, fallback):
    if key.startswith('agy:'):
        return 'antigravity'
    prefix = key.split('-')[0]
    return prefix if prefix in ('grok', 'claude', 'codex', 'notebooklm') else fallback

@contextmanager
def file_lock(path, timeout=10, stop_requested=None):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('a+b') as handle:
        handle.seek(0, 2)
        if handle.tell() == 0:
            handle.write(b'0')
            handle.flush()
        deadline = time.monotonic() + timeout
        while True:
            if stop_requested is not None and stop_requested():
                raise _RefreshCancelled()
            try:
                handle.seek(0)
                if os.name == 'nt':
                    import msvcrt
                    msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
                else:
                    import fcntl
                    fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except OSError:
                if time.monotonic() >= deadline:
                    raise TimeoutError('Usage state is locked by another process')
                time.sleep(.05)
        try:
            yield
        finally:
            handle.seek(0)
            if os.name == 'nt':
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(handle, fcntl.LOCK_UN)

def write_json(path, value):
    temporary = path.with_name(path.name + '.' + uuid.uuid4().hex + '.tmp')
    created = False
    try:
        with temporary.open('x', encoding='utf-8', newline='\n') as handle:
            created = True
            json.dump(value, handle, indent=2)
            handle.write('\n')
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        if created:
            temporary.unlink(missing_ok=True)

class Guard:
    def __init__(self, root=DEFAULT_ROOT):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        with file_lock(self.root / 'state.lock'):
            if not (self.root / 'policy.json').exists():
                write_json(self.root / 'policy.json', DEFAULT_POLICY)
        self.policy = json.loads((self.root / 'policy.json').read_text(encoding='utf-8'))
        validate_policy(self.policy)

    @contextmanager
    def state(self):
        with file_lock(self.root / 'state.lock'):
            path = self.root / 'usage.json'
            data = json.loads(path.read_text(encoding='utf-8')) if path.exists() else {
                'schema_version': 1, 'windows': {}, 'reservations': {}, 'cooldowns': {}, 'alerts': {}, 'refresh_errors': {}}
            yield data
            write_json(path, data)

    def observe(self, windows, provider, complete=False, memberships=None):
        checked = []
        identities = set()
        for item in windows:
            item = dict(item)
            identity = item.get('id')
            if not isinstance(identity, str) or not identity:
                raise ValueError('Quota evidence needs a nonempty pool ID')
            if identity in identities:
                raise ValueError('Quota snapshot contains duplicate pool IDs')
            identities.add(identity)
            value = float(item['remaining_pct'])
            if not math.isfinite(value) or not 0 <= value <= 100:
                raise ValueError('Remaining percentage must be between 0 and 100')
            observed = instant(item['observed_at'])
            if observed > now() + timedelta(seconds=60):
                raise ValueError('Quota timestamp is in the future')
            if not item.get('source') or not item.get('id'):
                raise ValueError('Quota evidence needs its source and pool ID')
            if item.get('reset_at'):
                instant(item['reset_at'])
            item['remaining_pct'] = value
            item['provider'] = pool_owner(item['id'], provider)
            item['max_age_seconds'] = min(3600, max(30, int(item.get('max_age_seconds', 600))))
            checked.append(item)
        if not checked:
            raise ValueError('No quota windows returned')
        # Complete snapshots use the oldest observation as their safe request
        # boundary. A late reply must not resurrect a pool a newer reply omitted.
        snapshot_at = min(instant(item['observed_at']) for item in checked)
        with self.state() as data:
            snapshots = data.setdefault('provider_snapshots', {})
            previous_snapshot = snapshots.get(provider)
            if complete and previous_snapshot and snapshot_at <= instant(previous_snapshot):
                return False
            incoming = {item['id'] for item in checked}
            if complete:
                missing = [key for key, item in data['windows'].items()
                           if pool_owner(key, item.get('provider')) == provider and key not in incoming
                           and instant(item['observed_at']) <= snapshot_at]
                for key in missing:
                    del data['windows'][key]
            if memberships is not None:
                dynamic = data.setdefault('worker_pools', {})
                for worker, keys in memberships.items():
                    # Retain previously applicable pools as required if a partial
                    # report omits them; their missing readings will hold admission.
                    prior = dynamic.get(worker, self.policy['worker_pools'].get(worker, []))
                    dynamic[worker] = sorted(set(prior) | set(keys))
                    for reservation in data['reservations'].values():
                        if reservation['worker'] == worker:
                            reservation['pools'] = sorted(set(reservation['pools']) | set(dynamic[worker]))
            for item in checked:
                prior = data['windows'].get(item['id'])
                if prior and instant(prior['observed_at']) > instant(item['observed_at']):
                    continue
                prior_complete = snapshots.get(item['provider'])
                if not prior and prior_complete and instant(item['observed_at']) <= instant(prior_complete):
                    continue
                data['windows'][item['id']] = item
            if complete:
                snapshots[provider] = stamp(snapshot_at)
            failure = data['refresh_errors'].get(provider)
            if not failure or max(instant(item['observed_at']) for item in checked) > instant(failure['at']):
                data['refresh_errors'].pop(provider, None)
        return True

    def evaluate(self, data, worker, size='small'):
        validate_policy(self.policy)
        if self.policy.get('quota_admission_mode') == 'advisory':
            from quota_admission import evaluate_advisory
            return evaluate_advisory(self.policy, data, worker, size, now())
        if worker not in self.policy['worker_pools']:
            raise ValueError('Unknown worker')
        if size not in self.policy['estimates_pct']:
            raise ValueError('Unknown task size')
        keys = data.get('worker_pools', {}).get(worker, self.policy['worker_pools'][worker])
        estimate = self.policy['estimates_pct'][size]
        result = {'worker': worker, 'size': size, 'allowed': True,
                  'status': 'ready' if keys else 'local', 'estimate_pct': estimate if keys else 0,
                  'reasons': [], 'windows': []}
        if not keys and worker != 'local-chat':
            result['reasons'].append('No applicable quota windows have been verified')
        for key in keys:
            window = data['windows'].get(key)
            if not window:
                result['reasons'].append(f'{key}: usage unknown; refresh or supply a current account reading')
                continue
            observed = instant(window['observed_at'])
            provider = 'antigravity' if key.startswith('agy:') else key.split('-')[0]
            failure = data['refresh_errors'].get(provider)
            if failure and instant(failure['at']) >= observed:
                result['reasons'].append(f'{key}: latest quota refresh failed')
            if (now() - observed).total_seconds() > window['max_age_seconds']:
                result['reasons'].append(f'{key}: reading is stale')
            if window.get('reset_at') and instant(window['reset_at']) <= now():
                result['reasons'].append(f'{key}: reset boundary passed; fetch a fresh reading')
            cooldown = data['cooldowns'].get(key)
            if cooldown and instant(cooldown['until']) > now():
                result['reasons'].append(f'{key}: provider rejected work; cooldown active')
            held = sum(r['estimate_pct'] for r in data['reservations'].values()
                       if key in r['pools'] and (not r.get('finished_at') or instant(r['finished_at']) >= observed))
            available = max(0, window['remaining_pct'] - held)
            result['windows'].append({'id': key, 'remaining_pct': round(window['remaining_pct'], 2),
                                      'reserved_pct': round(held, 2), 'available_pct': round(available, 2),
                                      'reset_at': window.get('reset_at'), 'reset_display': window.get('reset_display'),
                                      'observed_at': window['observed_at'], 'max_age_seconds': window['max_age_seconds'],
                                      'source': window['source']})
            if available - estimate < self.policy['floor_pct']:
                result['reasons'].append(f'{key}: task plus safety buffer exceeds available quota')
            if available <= self.policy['warning_pct']:
                result['status'] = 'low'
        if result['reasons']:
            result['allowed'] = False
            result['status'] = 'held'
        return result

    def check(self, worker, size='small', reserve=False, task=None):
        with self.state() as data:
            result = self.evaluate(data, worker, size)
            if reserve and result['allowed']:
                if not task:
                    raise ValueError('A reservation requires a task label')
                token = uuid.uuid4().hex
                data['reservations'][token] = {'worker': worker, 'task': task,
                    'pools': data.get('worker_pools', {}).get(worker, self.policy['worker_pools'][worker]), 'estimate_pct': result['estimate_pct'],
                    'created_at': stamp(), 'finished_at': None}
                result['reservation_id'] = token
            return result

    def finish(self, token, status, usage=None):
        with self.state() as data:
            reservation = data['reservations'][token]
            if reservation.get('finished_at'):
                raise ValueError('Reservation already completed')
            reservation.update({'finished_at': stamp(), 'outcome': status, 'reported_usage': usage})
            # Hold the estimate until a quota reading taken after completion arrives.

    def block(self, worker, seconds=900):
        with self.state() as data:
            for pool in data.get('worker_pools', {}).get(worker, self.policy['worker_pools'][worker]):
                data['cooldowns'][pool] = {'until': stamp(now() + timedelta(seconds=seconds)), 'reason': 'provider rejection'}

    def refresh(self, provider='all', stop_requested=None):
        results = {}
        targets = REFRESH_PROVIDERS if provider == 'all' else [provider]
        for target in targets:
            # Only the monitor supplies cancellation. A stale monitor.stop file
            # must never cancel an explicit user/dispatcher quota refresh.
            if stop_requested is not None and stop_requested():
                break
            requested_at = stamp()
            try:
                if target not in REFRESH_PROVIDERS:
                    raise ValueError('No automatic reader for this provider')
                # Serialize only the same provider's quota-only collector. The
                # state lock is never held while an external CLI is running.
                with file_lock(self.root / ('refresh-' + target + '.lock'),
                               timeout=REFRESH_LOCK_TIMEOUT, stop_requested=stop_requested):
                    if stop_requested is not None and stop_requested():
                        break
                    shared = self._coalesced_refresh_result(target, requested_at)
                    if shared is not None:
                        results[target] = dict(shared, coalesced=True)
                        continue
                    try:
                        result = self._refresh_one(target)
                    except (OSError, ValueError, KeyError, TypeError, RuntimeError, subprocess.TimeoutExpired) as exc:
                        result = self._refresh_failure(target, exc)
                    with self.state() as data:
                        data.setdefault('refresh_attempts', {})[target] = {
                            'finished_at': stamp(), 'result': result,
                        }
                    results[target] = result
            except _RefreshCancelled:
                break
            except (OSError, ValueError, KeyError, TypeError, RuntimeError, subprocess.TimeoutExpired) as exc:
                results[target] = self._refresh_failure(target, exc)
        return results

    def request_refresh(self, provider):
        """Queue bounded collection without putting a quota CLI on the task path."""
        from background_usage import request_refresh
        return request_refresh(self.root, provider)

    def _refresh_failure(self, target, exc):
        result = {'ok': False, 'reason': str(exc) if isinstance(exc, RuntimeError) else type(exc).__name__}
        with self.state() as data:
            data['refresh_errors'][target] = {'at': stamp(), 'error': result['reason']}
        return result

    def _coalesced_refresh_result(self, target, requested_at):
        """Reuse overlapping work only; never advance any observation timestamp."""
        with self.state() as data:
            attempt = data.get('refresh_attempts', {}).get(target)
            if not attempt:
                return None
            try:
                requested = instant(requested_at)
                if instant(attempt['finished_at']) < requested:
                    return None  # Explicit sequential calls must collect again.
                result = attempt['result']
                if result.get('ok') is False:
                    # A failed in-flight collector is shared only with callers
                    # that overlapped it. It grants no allowance and is not
                    # rewritten with a later failure/observation timestamp.
                    return result
                if result.get('ok') is not True or not result.get('windows'):
                    return None
                failure = data.get('refresh_errors', {}).get(target)
                current = now()
                for identity in result['windows']:
                    window = data['windows'].get(identity)
                    if not window:
                        return None
                    observed = instant(window['observed_at'])
                    if observed < requested or (current - observed).total_seconds() > window['max_age_seconds']:
                        return None
                    if window.get('reset_at') and instant(window['reset_at']) <= current:
                        return None
                    if failure and instant(failure['at']) >= observed:
                        return None
                return result
            except (KeyError, TypeError, ValueError, AttributeError, OverflowError):
                return None  # Malformed receipts cannot authorize reuse.

    def _refresh_one(self, target):
        poll_started = stamp()
        memberships = None
        if target == 'antigravity':
            exe = Path.home() / 'AppData/Local/agy/bin/agy.exe'
            response = subprocess.run([str(exe), '-p', '/usage', '--output-format', 'json', '--print-timeout', '30s'],
                cwd=WORKSPACES / 'google', capture_output=True, text=True, encoding='utf-8', timeout=40,
                creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0)
            if response.returncode:
                raise RuntimeError('Quota command failed')
            payload = json.loads(response.stdout)
            if payload.get('num_turns') != 0 or payload.get('command', {}).get('name') != 'usage':
                raise ValueError('Expected the local quota command, not a model response')
            windows = []
            memberships = {'gemini': [], 'antigravity-claude': []}
            for group in payload['command']['data']['groups']:
                group_name = group['name'].lower()
                worker = 'gemini' if group_name == 'gemini models' else 'antigravity-claude' if 'claude' in group_name and 'gpt' in group_name else None
                if worker is None:
                    raise ValueError('Unrecognized quota group; model membership needs review')
                for bucket in group['buckets']:
                    windows.append({'id': 'agy:' + bucket['id'], 'remaining_pct': 100 * bucket['remaining_fraction'],
                        'reset_at': bucket.get('reset_time'), 'observed_at': poll_started, 'max_age_seconds': 600,
                        'source': 'official agy /usage JSON', 'group': group['name']})
                    memberships[worker].append('agy:' + bucket['id'])
        elif target in ('grok', 'claude', 'codex'):
            adapter = SETUP / ('quota_codex.py' if target == 'codex' else 'quota_tui.py')
            if not adapter.is_file():
                raise RuntimeError('Official TUI quota adapter not available yet')
            command = [sys.executable, str(adapter)]
            if target != 'codex':
                command += ['--provider', target]
            response = subprocess.run(command,
                cwd=SETUP, capture_output=True, text=True, encoding='utf-8', timeout=65,
                creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0)
            if response.returncode:
                raise RuntimeError(quota_reader_failure(response.stdout))
            payload = json.loads(response.stdout)
            windows = payload['windows']
            for window in windows:
                # A reply received after job completion can still describe
                # a snapshot requested before completion. Use the earlier time.
                if instant(window['observed_at']) > instant(poll_started):
                    window['observed_at'] = poll_started
            memberships = {target: [window['id'] for window in windows]}
        else:
            raise ValueError('No automatic reader for this provider')
        self.observe(windows, target, complete=True, memberships=memberships)
        return {'ok': True, 'windows': [w['id'] for w in windows]}

    def status(self):
        with self.state() as data:
            workers = [self.evaluate(data, worker) for worker in self.policy['worker_pools']]
            return {'updated_at': stamp(), 'snapshot_generated_at': stamp(),
                    'admission_mode': self.policy.get('quota_admission_mode', 'strict'),
                    'worker_start_threshold_pct': self.policy.get('worker_start_threshold_pct', 20),
                    'workers': workers, 'refresh_errors': data['refresh_errors'],
                    'refresh_attempts': data.get('refresh_attempts', {}),
                    'active_reservations': sum(not r.get('finished_at') for r in data['reservations'].values())}

    def dashboard(self):
        report = self.status()
        rows = []
        labels = {'codex': 'Codex', 'gemini': 'Gemini', 'antigravity-claude': 'Claude / GPT via Antigravity',
                  'grok': 'Grok Build', 'grok-bot': 'Grok Bot', 'claude': 'Claude Code', 'local-chat': 'Local Qwen chat',
                  'notebooklm-chat': 'NotebookLM · chat', 'notebooklm-audio': 'NotebookLM · audio',
                  'notebooklm-slides': 'NotebookLM · slides'}
        for worker in report['workers']:
            windows = [w for w in worker['windows'] if not w['id'].endswith('provider-block')]
            expiry = ''
            if windows:
                limiting = min(windows, key=lambda w: w['available_pct'])
                value = limiting['available_pct']
                detail = f'<strong>{value:g}% available</strong><div class="bar" aria-label="{value:g} percent available"><i style="width:{value}%"></i></div>'
                reset = limiting.get('reset_display')
                if limiting.get('reset_at'):
                    reset = instant(limiting['reset_at']).astimezone().strftime('%b %d, %I:%M %p')
                detail += '<small>' + (html.escape('Resets ' + reset) if reset else 'Reset time not supplied') + '</small>'
                deadlines = []
                # Admission flags also expire, even though they are not visible
                # percentage bars. Mirror the exact freshness/reset gate in UI.
                for window in worker['windows']:
                    deadlines.append(instant(window['observed_at']).timestamp() + window['max_age_seconds'])
                    if window.get('reset_at'):
                        deadlines.append(instant(window['reset_at']).timestamp())
                expiry = str(min(deadlines))
            else:
                detail = 'Runs on your computer' if worker['status'] == 'local' else 'Awaiting a current account reading'
            reason = ''
            if worker['status'] == 'held':
                if worker['worker'].startswith('notebooklm'):
                    reason = 'Check this feature’s allowance in NotebookLM before assigning work.'
                elif not windows:
                    reason = 'Complete account setup or refresh the official usage screen.'
                else:
                    reason = 'Quota needs refreshing or there is too little room for another task.'
            rows.append('<tr data-valid-until="' + expiry + '"><td>' + html.escape(labels.get(worker['worker'], worker['worker'])) +
                '</td><td class="badge ' + worker['status'] + '">' + ('QUOTA READY' if worker['status'] == 'ready' else worker['status'].upper()) + '</td><td>' +
                detail + '<small class="reason">' + html.escape(reason) + '</small></td></tr>')
        page = '''<!doctype html><html lang="en"><meta charset="utf-8"><meta http-equiv="refresh" content="30">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>Model usage</title>
<style>body{background:#10151f;color:#e6edf6;font:16px system-ui;margin:5vw;max-width:1100px}h1{font-size:36px}p,small{color:#aebbcf}table{border-collapse:collapse;width:100%;margin:2em 0}th,td{text-align:left;padding:18px 12px;border-bottom:1px solid #344055}small{display:block;margin-top:7px}.ready,.local{color:#7ce0b4}.held{color:#ffb4a5}.low{color:#ffd37d}strong{color:#e6edf6}.bar{height:5px;max-width:300px;background:#293447;border-radius:6px;margin-top:8px;overflow:hidden}.bar i{display:block;height:100%;background:#7ce0b4}.badge{font-size:12px;letter-spacing:.07em}td:first-child{min-width:180px}@media(max-width:650px){body{margin:20px;font-size:14px}td,th{padding:14px 7px}td:first-child{min-width:95px}}</style>
<h1>Model usage</h1><p>Codex coordinates the work. Quota checks protect each worker's remaining allowance.</p>
<p><strong>Warn at 20%. Preserve 10%.</strong> Larger tasks also need room for their estimated usage.
Unknown or stale cloud quotas hold new assignments. Estimates are conservative planning allowances, not guarantees.</p>
<table><thead><tr><th>Worker</th><th>Quota status</th><th>Available allowance</th></tr></thead><tbody>'''
        page += ''.join(rows) + '</tbody></table><p>Updated ' + html.escape(report['updated_at']) + ' · ' + str(report['active_reservations']) + ' active reservations.</p>'
        page += '''<p>Automatic readers refresh every five minutes while the monitor runs. This page refreshes every 30 seconds. NotebookLM feature counters need a current account reading.</p>
<script>function stale(){for(const row of document.querySelectorAll('tr[data-valid-until]')){const until=Number(row.dataset.validUntil);if(until&&Date.now()/1000>until){const badge=row.querySelector('.badge');badge.textContent='STALE';badge.className='badge held';row.querySelector('.reason').textContent='This reading has expired. Refresh usage before assigning work.';}}}stale();setInterval(stale,10000);</script></html>'''
        if report['admission_mode'] == 'advisory':
            from usage_report import render_usage
            page = render_usage(report)
        from task_panel import render
        page = page.replace('</html>', render() + '</html>')
        from workspace_navigation import decorate_report, write_navigation_script
        from contributions import _atomic_text
        page = decorate_report(page, self.root.parent, 'usage')
        write_navigation_script(self.root.parent, directory=self.root)
        _atomic_text(self.root / 'usage-dashboard.html', page)
        _atomic_text(self.root / 'usage-status.json', json.dumps(report, indent=2))
        return report

    def notify(self, title, message):
        script = SETUP / 'quota-notification.ps1'
        if os.name == 'nt' and script.is_file():
            try:
                completed = subprocess.run(['powershell.exe', '-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', str(script),
                    '-Title', title, '-Message', message], capture_output=True, timeout=15, creationflags=subprocess.CREATE_NO_WINDOW)
                return completed.returncode == 0
            except (OSError, subprocess.TimeoutExpired):
                return False
        return False

    def alerts(self):
        report = self.status()
        pending = []
        for worker in report['workers']:
            if worker['status'] not in ('held', 'low'):
                with self.state() as data:
                    for old_key in (worker['worker'] + ':held', worker['worker'] + ':low'):
                        data['alerts'].pop(old_key, None)
                continue
            # Repeated unknown/stale readings also deserve a visible warning, once per day.
            key = worker['worker'] + ':' + worker['status']
            with self.state() as data:
                previous = data['alerts'].get(key)
            if previous and now() - instant(previous) < timedelta(hours=24):
                continue
            pending.append((key, worker['worker']))
        if pending:
            labels = {'grok': 'Grok Build', 'grok-bot': 'Grok Bot', 'gemini': 'Gemini via Antigravity',
                      'antigravity-claude': 'Claude / GPT via Antigravity', 'claude': 'Claude Code', 'codex': 'Codex'}
            names = ', '.join(sorted({labels.get(name, name.replace('-', ' ')) for _, name in pending}))
            message = names + (': low allowance or provider rejection. Prefer another provider; check the usage dashboard.'
                if self.policy.get('quota_admission_mode') == 'advisory' else
                ': low or unverified allowance. New tasks may be held; check the usage dashboard.')
            if self.notify('Model usage warning', message):
                with self.state() as data:
                    for key, _ in pending:
                        data['alerts'][key] = stamp()

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, default=DEFAULT_ROOT)
    sub = parser.add_subparsers(dest='action', required=True)
    sub.add_parser('status')
    sub.add_parser('dashboard')
    refresh = sub.add_parser('refresh')
    refresh.add_argument('--provider', choices=['all', 'codex', 'antigravity', 'grok', 'claude'], default='all')
    for action in ('check', 'reserve'):
        command = sub.add_parser(action)
        command.add_argument('worker')
        command.add_argument('--size', choices=['tiny', 'small', 'medium', 'large'], default='small')
        if action == 'reserve':
            command.add_argument('--task', required=True)
    record = sub.add_parser('record')
    record.add_argument('pool')
    record.add_argument('--remaining', type=float, required=True)
    record.add_argument('--source', required=True)
    record.add_argument('--observed-at', required=True)
    record.add_argument('--reset-at')
    record.add_argument('--max-age', type=int, default=600)
    finish = sub.add_parser('finish')
    finish.add_argument('reservation')
    finish.add_argument('--status', choices=['completed', 'failed', 'abandoned'], required=True)
    block = sub.add_parser('block')
    block.add_argument('worker')
    block.add_argument('--seconds', type=int, default=900)
    monitor = sub.add_parser('monitor')
    monitor.add_argument('--interval', type=int, default=300)
    sub.add_parser('stop-monitor')
    sub.add_parser('test-notification')
    args = parser.parse_args()
    guard = Guard(args.root)
    if args.action == 'refresh':
        result = guard.refresh(args.provider)
        guard.dashboard()
    elif args.action in ('check', 'reserve'):
        result = guard.check(args.worker, args.size, args.action == 'reserve', getattr(args, 'task', None))
        print(json.dumps(result))
        return 0 if result['allowed'] else 2
    elif args.action == 'record':
        guard.observe([{'id': args.pool, 'remaining_pct': args.remaining, 'source': args.source,
            'observed_at': args.observed_at, 'reset_at': args.reset_at, 'max_age_seconds': args.max_age}], 'manual')
        result = {'recorded': args.pool}
    elif args.action == 'finish':
        guard.finish(args.reservation, args.status)
        result = {'finished': args.reservation}
    elif args.action == 'block':
        guard.block(args.worker, args.seconds)
        result = {'held': args.worker}
    elif args.action == 'test-notification':
        result = {'notification_submitted': guard.notify('Orchestrator usage monitor', 'Usage protection is active. Low allowances will warn you and hold new tasks.')}
    elif args.action == 'stop-monitor':
        (guard.root / 'monitor.stop').touch()
        result = {'stop_requested': True}
    elif args.action == 'monitor':
        with file_lock(guard.root / 'monitor.lock', timeout=1):
            stop = guard.root / 'monitor.stop'
            stop.unlink(missing_ok=True)
            write_json(guard.root / 'monitor-state.json', {'pid': os.getpid(), 'started_at': stamp()})
            while not stop.exists():
                guard.refresh(stop_requested=stop.exists)
                if stop.exists():
                    break
                guard.dashboard()
                guard.alerts()
                for _ in range(max(30, args.interval)):
                    if stop.exists():
                        break
                    time.sleep(1)
        return 0
    else:
        result = guard.dashboard() if args.action == 'dashboard' else guard.status()
    print(json.dumps(result))
    return 0

if __name__ == '__main__':
    try:
        raise SystemExit(main())
    except (OSError, ValueError, KeyError, TimeoutError) as error:
        print(json.dumps({'error': str(error)}))
        raise SystemExit(1)
