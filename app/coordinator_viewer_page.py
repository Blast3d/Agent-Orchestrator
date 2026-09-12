"""Local coordinator viewer markup. All session content is rendered as text."""

PAGE = r'''<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="viewer-token" content="__TOKEN__">
<title>Orchestrator · Session viewer</title>
<style nonce="__NONCE__">
:root{color-scheme:dark;--bg:#10151d;--panel:#18212d;--line:#303d4e;--muted:#a7b5c7;--text:#edf3fb;--accent:#9edbc7;--warn:#ffd792;--bad:#ffb5ae}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--text);font:15px/1.55 system-ui,-apple-system,"Segoe UI",sans-serif}button,select{font:inherit}button,select{border:1px solid var(--line);border-radius:9px;padding:10px 13px;background:#202c3b;color:var(--text)}button{cursor:pointer;font-weight:600}button:hover:not(:disabled){border-color:var(--accent)}button:disabled{opacity:.5;cursor:default}button:focus-visible,select:focus-visible,a:focus-visible,summary:focus-visible,[tabindex]:focus-visible{outline:3px solid var(--accent);outline-offset:3px}button.primary{background:var(--accent);color:#10271f;border-color:var(--accent)}button.small{font-size:13px;padding:7px 11px}select{width:100%;max-width:650px}h1,h2,h3,p{margin:0}h1{font-size:clamp(25px,4vw,34px);letter-spacing:-.8px;line-height:1.2}h2{font-size:18px}h3{font-size:13px;letter-spacing:.07em;text-transform:uppercase;color:var(--muted)}a{color:var(--accent)}.shell{max-width:1500px;margin:auto;padding:30px 28px}.topline,.row,.history-top{display:flex;align-items:center;gap:12px;flex-wrap:wrap}.topline{justify-content:space-between;margin-bottom:25px}.brand{font-size:12px;letter-spacing:.15em;font-weight:700;color:var(--accent);margin-bottom:7px}.brand a{color:var(--muted);text-decoration:none}.brand a:hover{color:var(--accent);text-decoration:underline}.crumb-sep{color:var(--line);margin:0 2px}.muted{color:var(--muted)}.smalltext{font-size:13px}.spaced{margin-top:9px}.status-dot{width:8px;height:8px;display:inline-block;margin-right:7px;border-radius:50%;background:var(--accent)}.status-dot.paused{background:var(--warn)}.status-dot.offline{background:var(--bad)}.panel{border:1px solid var(--line);border-radius:15px;background:var(--panel)}.session{padding:22px;margin-bottom:18px}.session-grid{display:grid;grid-template-columns:minmax(200px,1fr) auto;gap:20px;align-items:start}.label{display:block;margin-bottom:6px;font-size:13px;font-weight:650;color:var(--muted)}.metrics{display:flex;flex-wrap:wrap;gap:15px 30px;margin-top:21px}.metric{min-width:105px}.metric strong{display:block;font-size:18px;margin-top:2px}.badge{display:inline-flex;border:1px solid var(--line);padding:3px 9px;border-radius:20px;font-size:12px;color:var(--muted)}.badge.waiting{border-color:#786244;color:var(--warn);background:#332a1c}.attach-area{max-width:335px}.attach-area button{width:100%}.attach-area p{margin-top:8px;font-size:12px;color:var(--muted)}.banner{border:1px solid #6a5741;background:#2e281e;border-radius:10px;padding:13px 16px;margin:15px 0;color:var(--warn)}.banner.error{background:#342321;border-color:#78514d;color:var(--bad)}.banner.info{background:#1b2e33;border-color:#36535a;color:#b9dbe3}.banner[hidden],[hidden]{display:none!important}.layout{display:grid;grid-template-columns:minmax(240px,330px) minmax(0,1fr);gap:18px;align-items:start}.checkpoint{padding:22px}.checkpoint section+section{border-top:1px solid var(--line);margin-top:19px;padding-top:19px}.checkpoint ul{padding-left:20px;margin:10px 0 0}.checkpoint li+li{margin-top:7px}.checkpoint p{white-space:pre-wrap;overflow-wrap:anywhere;margin-top:9px}.history{overflow:hidden}.history-top{padding:18px 22px;justify-content:space-between;border-bottom:1px solid var(--line)}.history-sub{padding:10px 22px;border-bottom:1px solid var(--line);font-size:12px;color:var(--muted)}.conversation{height:clamp(360px,65vh,850px);overflow-y:auto;overscroll-behavior:contain;scrollbar-gutter:stable;padding:18px 22px}.older{display:block;margin:0 auto 18px}.message{padding:17px 0;border-top:1px solid var(--line)}.message:first-child{border-top:0}.message-head{display:flex;gap:9px;align-items:center;margin-bottom:8px;flex-wrap:wrap;font-size:12px;color:var(--muted)}.message-role{font-size:13px;color:var(--accent);font-weight:700}.message.user .message-role{color:#b7cbff}.message-text{white-space:pre-wrap;overflow-wrap:anywhere;tab-size:2;line-height:1.65}.empty{color:var(--muted);padding:48px 10px;text-align:center}.history-bottom{display:flex;align-items:center;justify-content:space-between;gap:12px;flex-wrap:wrap;padding:13px 22px;border-top:1px solid var(--line);font-size:12px;color:var(--muted)}details{margin-top:19px;border-top:1px solid var(--line);padding-top:14px;font-size:12px;color:var(--muted)}summary{cursor:pointer}dl{margin:10px 0 0;display:grid;grid-template-columns:100px minmax(0,1fr);gap:6px}dt{font-weight:650}dd{margin:0;overflow-wrap:anywhere}footer{font-size:12px;color:var(--muted);margin-top:18px;max-width:1000px}.sr-only{position:absolute;width:1px;height:1px;padding:0;margin:-1px;overflow:hidden;clip:rect(0,0,0,0);white-space:nowrap;border:0}
@media(max-width:850px){.shell{padding:22px 16px}.layout{grid-template-columns:1fr}.checkpoint{display:grid;grid-template-columns:1fr 1fr;gap:18px}.checkpoint section+section{border:0;margin:0;padding:0}.checkpoint section:first-child,.checkpoint details{grid-column:1/-1}.conversation{height:65vh}.session-grid{grid-template-columns:1fr}.attach-area{max-width:none}.attach-area button{width:auto}}@media(max-width:500px){.checkpoint{display:block}.checkpoint section+section{margin-top:19px;border-top:1px solid var(--line);padding-top:19px}.session,.checkpoint{padding:17px}.history-top,.history-sub,.history-bottom{padding-left:16px;padding-right:16px}.conversation{padding:12px 16px}.metrics{gap:12px 20px}.topline{align-items:start}.attach-area button{width:100%}}
.workspace-crumbs{display:flex;align-items:center;flex-wrap:wrap;gap:8px 10px;font-size:12px;margin-bottom:9px;line-height:1.8;color:var(--muted)}.workspace-crumbs [aria-current=page]{color:var(--accent);font-weight:700}.workspace-crumbs a{color:var(--muted);text-decoration:none}.workspace-crumbs a:hover{color:var(--accent);text-decoration:underline}
</style>
<style nonce="__NONCE__">
.workers{margin-bottom:18px;overflow:hidden}.worker-summary{padding:12px 22px;color:var(--muted);font-size:13px}.worker-table-wrap{overflow:auto}.worker-table{width:100%;border-collapse:collapse;font-size:13px}.worker-table th{text-align:left;color:var(--muted);font-weight:600}.worker-table th,.worker-table td{padding:12px 18px;border-top:1px solid var(--line);vertical-align:top}.worker-table small{display:block;color:var(--muted);max-width:290px;overflow-wrap:anywhere}.scope{margin-top:16px}.scope select{max-width:400px;padding:6px 10px}.worker-phase{color:var(--accent);font-weight:650}.worker-phase.waiting{color:var(--warn)}
</style>
</head>
<body>
<main class="shell">
  <header class="topline">
    <div><nav class="workspace-crumbs" aria-label="Workspace pages"><span aria-current="page">Orchestrator</span><span aria-hidden="true">/</span><a id="crumb-brain" href="#" title="Open the Memory dashboard">Memory</a><span aria-hidden="true">/</span><a id="crumb-usage" href="/usage">Provider usage</a><span aria-hidden="true">/</span><a id="crumb-contributions" href="/contributions">Contribution maps</a></nav><h1>Your session, in view.</h1><p><a href="/system-map" target="_blank" rel="noopener">Explore system map</a></p><p class="muted spaced">Follow the lead. Open its conversation whenever you need to step in.</p></div>
    <div class="row"><span id="connection" class="smalltext muted"><span class="status-dot"></span>Connecting</span><button id="refresh" type="button" class="small" hidden>Retry now</button></div>
  </header>
  <div id="error" class="banner error" role="alert" hidden></div>
  <section class="panel session" aria-label="Usage monitor">
    <div class="session-grid">
      <div><h2>Usage monitor</h2><p id="monitor-help" class="muted smalltext spaced">Turn on account allowance checks while coding; turn them off when finished.</p><p id="monitor-note" class="muted smalltext spaced" role="status">Checking monitor status...</p></div>
      <button id="monitor-toggle" type="button" role="switch" aria-checked="false" aria-label="Usage monitor" aria-describedby="monitor-help monitor-note" disabled>Checking...</button>
    </div>
  </section>
  <section class="panel session" aria-label="Selected orchestrator">
    <div class="session-grid">
      <div><label class="label" for="run">Project run</label><select id="run"><option value="">Loading runs…</option></select><p id="run-id" class="muted smalltext spaced"></p></div>
      <div class="attach-area"><button id="attach" type="button" class="primary" aria-describedby="attach-help attach-status" disabled>Connect to lead</button><p id="attach-help">Select a run to see how to reach its lead.</p><p id="attach-status" role="status"></p></div>
    </div>
    <div class="metrics">
      <div class="metric"><span class="label">Lead orchestrator</span><strong id="owner">—</strong></div>
      <div class="metric"><span class="label">Run</span><strong id="run-status">—</strong></div>
      <div class="metric"><span class="label">Provider session</span><strong id="provider-status">—</strong></div>
      <div class="metric"><span class="label">Last observed</span><strong id="observed" class="smalltext">—</strong></div>
    </div>
    <div class="scope"><label class="label" for="memory-project">Shared memory project</label><select id="memory-project" disabled><option>No mapped project</option></select></div>
    <div id="waiting" class="banner" role="status" hidden></div>
    <div id="provider-note" class="banner info" hidden></div>
  </section>
  <section class="panel workers" aria-labelledby="workers-title">
    <div class="history-top"><div><h2 id="workers-title">Worker activity</h2><p class="muted smalltext">See where work is waiting and how long each step takes.</p></div><span id="worker-count" class="badge">Loading</span></div>
    <p id="worker-summary" class="worker-summary">Select a run to see its recorded tasks.</p>
    <div class="worker-table-wrap"><table class="worker-table"><thead><tr><th scope="col">Worker</th><th scope="col">Current step</th><th scope="col">Elapsed</th><th scope="col">Last update</th><th scope="col">Deadline</th></tr></thead><tbody id="workers"></tbody></table></div>
  </section>
  <div class="layout">
    <aside class="panel checkpoint" aria-label="Run checkpoint">
      <section><h2>Run checkpoint</h2><p id="checkpoint-time" class="muted smalltext">Waiting for a run.</p><p id="objective">Select a run to view its work.</p></section>
      <section><h3>Next steps</h3><div id="next-steps"></div></section>
      <section><h3>Completed</h3><div id="completed"></div></section>
      <section><h3>Open jobs</h3><div id="open-jobs"></div></section>
      <details><summary>Session details</summary><dl><dt>Provider</dt><dd id="provider-name">—</dd><dt>Generation</dt><dd id="generation">—</dd><dt>Lead session</dt><dd id="lead-session">—</dd><dt>Background ID</dt><dd id="background-id">—</dd><dt>Saved session</dt><dd id="saved-session">—</dd></dl></details>
    </aside>
    <section class="panel history" aria-labelledby="history-title">
      <div class="history-top"><div><h2 id="history-title">Saved conversation</h2><p class="muted smalltext">User and assistant messages from this lead’s saved session.</p></div><span id="history-badge" class="badge">Waiting</span></div>
      <p id="history-note" class="history-sub">Viewing is read-only and does not start or resume a model.</p>
      <div id="conversation" class="conversation" tabindex="0" aria-label="Saved conversation; scroll to browse messages">
        <button id="older" type="button" class="older small" hidden>Load older messages</button>
        <div id="messages"></div><p id="history-empty" class="empty">Select a run to view its conversation.</p>
      </div>
      <div class="history-bottom"><span id="message-count">0 messages loaded</span><button id="latest" type="button" class="small">Jump to latest</button><span id="history-updated">Updates every 5 seconds</span></div>
    </section>
  </div>
  <footer>Checkpoint state and provider activity are separate observations. A completed run can still have an open console; an old checkpoint does not prove the model is idle. Conversation availability depends on the provider’s saved history. Refresh pauses while this tab is hidden.</footer>
  <p id="announcement" class="sr-only" aria-live="polite"></p>
</main>
<script nonce="__NONCE__">
'use strict';
(() => {
  const $ = id => document.getElementById(id);
  const token = document.querySelector('meta[name="viewer-token"]').content;
  let selected = new URLSearchParams(window.location.search).get('run') || '', epoch = 0, detail = null, sessionKey = '', refreshing = false;
  let messages = [], before = null, hasMore = false, olderBusy = false, attaching = false;
  let lastSuccess = 0, lastFailure = '', runsSignature = '', historyRevision = '', historyEpoch = 0, historyNotice = '';
  let memoryProject = new URLSearchParams(window.location.search).get('project') || '';
  const seenText = value => typeof value === 'string' ? value : value == null ? '' : typeof value === 'object' ? JSON.stringify(value, null, 2) : String(value);
  const label = value => seenText(value).replace(/[_-]+/g, ' ').trim();
  function date(value) {
    if (!value) return null;
    const number = typeof value === 'number' ? value : /^\d+(\.\d+)?$/.test(String(value)) ? Number(value) : null;
    const parsed = new Date(number === null ? value : number < 1e12 ? number * 1000 : number);
    return Number.isNaN(parsed.getTime()) ? null : parsed;
  }
  function time(value) { const parsed = date(value); return parsed ? parsed.toLocaleString() : value ? seenText(value) : 'Not recorded'; }
  function elapsed(value) {
    const parsed = date(value); if (!parsed) return 'Unknown';
    const seconds = Math.max(0, Math.floor((Date.now() - parsed.getTime()) / 1000));
    return seconds < 60 ? seconds + 's ago' : seconds < 3600 ? Math.floor(seconds / 60) + 'm ago' : Math.floor(seconds / 3600) + 'h ago';
  }
  async function api(path, body) {
    const controller = new AbortController();
    const timeout = setTimeout(() => controller.abort(), 15000);
    try {
      const headers = {'X-Viewer-Token': token};
      const options = {headers, signal: controller.signal, cache: 'no-store'};
      if (body !== undefined) { headers['Content-Type'] = 'application/json'; options.method = 'POST'; options.body = JSON.stringify(body); }
      const response = await fetch(path, options);
      let data; try { data = await response.json(); } catch (_) { throw new Error('The viewer returned an unreadable response. Try refreshing.'); }
      if (!response.ok) throw new Error(data.error || 'The viewer could not complete that request.');
      return data;
    } catch (error) { if (error.name === 'AbortError') throw new Error('The viewer took too long to respond. It will retry.'); throw error; }
    finally { clearTimeout(timeout); }
  }
  function showError(error) { lastFailure = error.message || 'The local viewer is unavailable.'; $('error').textContent = lastFailure; $('error').hidden = false; connection(); }
  function clearError() { lastFailure = ''; $('error').hidden = true; }
  let monitorState = null, monitorBusy = false, monitorReading = false, monitorEpoch = 0;
  function renderMonitor() {
    const state = monitorState?.status;
    const on = state === 'on', stopping = state === 'stopping';
    $('monitor-toggle').setAttribute('aria-checked', String(on));
    $('monitor-toggle').disabled = monitorBusy || stopping || !['on', 'off'].includes(state);
    $('monitor-toggle').textContent = monitorBusy ? 'Updating...' : stopping ? 'Stopping...' : on ? 'On - turn off' : state === 'off' ? 'Off - turn on' : 'Unavailable';
    $('monitor-toggle').classList.toggle('primary', on);
    if (monitorState) $('monitor-note').textContent = (stopping ? 'The current check is ending. ' : on ? 'Account allowances refresh about every five minutes. ' : 'Background allowance checks are off. ') + (monitorState.startup_registered ? 'Windows sign-in startup is enabled.' : 'Windows sign-in startup is disabled.');
  }
  async function loadMonitor() {
    if (monitorBusy || monitorReading) return;
    monitorReading = true;
    const revision = monitorEpoch;
    try {
      const state = await api('/api/usage-monitor');
      if (revision === monitorEpoch) { monitorState = state; renderMonitor(); }
    } catch (error) {
      if (revision === monitorEpoch) { monitorState = null; renderMonitor(); $('monitor-note').textContent = 'Monitor status unavailable. ' + error.message; }
    } finally { monitorReading = false; }
  }
  $('monitor-toggle').addEventListener('click', async () => {
    if (monitorBusy || !['on', 'off'].includes(monitorState?.status)) return;
    const enabled = monitorState.status === 'off';
    monitorEpoch++; monitorBusy = true; renderMonitor();
    try { monitorState = await api('/api/usage-monitor', {enabled}); }
    catch (error) { monitorState = null; $('monitor-note').textContent = error.message; }
    finally { monitorBusy = false; renderMonitor(); }
  });
  let services = [];
  function crumbTarget() { const brain = services.find(s => s.id === 'brain'); if (!brain || !brain.origin) return ''; const scope = new URLSearchParams(); if (memoryProject) scope.set('project', memoryProject); if (selected) scope.set('run', selected); return brain.origin + '/' + (scope.size ? '#' + scope : ''); }
  function renderCrumb() { const scope = new URLSearchParams(); if (memoryProject) scope.set('project', memoryProject); if (selected) scope.set('run', selected); for (const page of ['usage', 'contributions']) $('crumb-' + page).href = '/' + page + (scope.size ? '?' + scope : ''); const target = crumbTarget(); $('crumb-brain').href = target || '#'; $('crumb-brain').title = target ? 'Open the Memory dashboard' + (selected ? ' for this run' : '') : 'Start the Memory dashboard'; }
  async function loadServices() { try { services = (await api('/api/services')).services || []; } catch (_) { services = []; } renderCrumb(); }
  $('crumb-brain').addEventListener('click', async event => {
    if (event.ctrlKey || event.metaKey || event.shiftKey || event.altKey) return;
    event.preventDefault(); $('crumb-brain').textContent = 'MEMORY…';
    try { const opened = await api('/api/open', {target: 'brain'}); services = [{id: 'brain', origin: opened.origin}]; window.location.href = crumbTarget(); }
    catch (error) { $('crumb-brain').textContent = 'MEMORY'; showError(error); }
  });
  function connection() {
    const dot = document.createElement('span'); dot.className = 'status-dot' + (lastFailure ? ' offline' : document.hidden ? ' paused' : '');
    $('connection').replaceChildren(dot, document.createTextNode(lastFailure ? 'Connection interrupted · retrying automatically' : document.hidden ? 'Refresh paused' : lastSuccess ? 'Auto-refresh on · every 5 seconds' : 'Connecting'));
    $('refresh').hidden = !lastFailure;
    if (detail) { $('observed').textContent = elapsed((detail.provider || {}).checked_at); $('observed').title = time((detail.provider || {}).checked_at); }
  }
  function list(target, value, empty) {
    const items = Array.isArray(value) ? value : value ? [value] : [];
    const node = document.createElement(items.length ? 'ul' : 'p');
    if (!items.length) { node.className = 'muted smalltext'; node.textContent = empty; }
    for (const item of items) { const entry = document.createElement('li'); entry.textContent = seenText(item); node.append(entry); }
    $(target).replaceChildren(node);
  }
  function resetHistory() {
    messages = []; before = null; hasMore = false; historyRevision = ''; historyEpoch++; historyNotice = ''; olderBusy = false;
    $('messages').replaceChildren(); $('older').hidden = true; $('older').disabled = false; $('older').textContent = 'Load older messages'; $('history-empty').hidden = false;
    $('history-empty').textContent = 'Loading saved conversation…'; $('history-badge').textContent = 'Loading';
    $('history-note').textContent = 'Viewing is read-only and does not start or resume a model.';
    $('message-count').textContent = '0 messages loaded'; $('latest').textContent = 'Jump to latest'; $('history-updated').textContent = 'Waiting for history';
  }
  function interaction() {
    const provider = detail?.provider || {};
    return provider.interaction || {kind: 'claude_console', label: 'Open interactive console',
      available: Boolean(provider.can_attach), session_id: provider.session_id || null,
      background_id: provider.background_id || null,
      reason: provider.can_attach ? 'Send messages and answer approvals in the existing Claude console.' : 'This session cannot be connected from the viewer. Check the provider details below.'};
  }
  function attachEnabled() {
    const action = interaction();
    $('attach').disabled = attaching || !detail || !action.available;
    $('attach').textContent = detail ? action.label || 'Connect to lead' : 'Connect to lead';
    $('attach-help').textContent = detail ? action.reason || 'Open the selected lead in its application.' : 'Select a run to see how to reach its lead.';
    $('attach').title = $('attach-help').textContent;
  }
  function showDetail(data) {
    const provider = data.provider || {}, checkpoint = data.checkpoint || {};
    // Checkpoint generations can change without changing the conversation.
    // Keep loaded pages until the actual lead or provider session changes.
    const nextKey = JSON.stringify([data.id, data.owner, data.session, provider.background_id, provider.session_id]);
    if (nextKey !== sessionKey) { sessionKey = nextKey; resetHistory(); $('attach-status').textContent = ''; }
    detail = data;
    const projects = Array.isArray(data.memory_projects) ? data.memory_projects : [];
    if (!projects.includes(memoryProject)) memoryProject = projects[0] || '';
    $('memory-project').replaceChildren(...projects.map(project => { const option = document.createElement('option'); option.value = project; option.textContent = project; return option; }));
    $('memory-project').value = memoryProject; $('memory-project').disabled = !projects.length; renderCrumb();
    showActivity(data.activity);
    $('owner').textContent = label(data.owner) || 'Unassigned';
    $('run-status').textContent = label(data.run_status || data.status) || 'Unknown';
    $('provider-status').textContent = label(provider.status || provider.state) || 'Unknown';
    $('run-id').textContent = data.id || selected;
    $('objective').textContent = seenText(checkpoint.objective || data.objective) || 'No objective saved yet.';
    $('checkpoint-time').textContent = 'Saved ' + time(data.checkpoint_at);
    list('next-steps', checkpoint.next_steps, 'No next steps recorded.');
    list('completed', checkpoint.completed, 'No completed work recorded.');
    list('open-jobs', checkpoint.open_jobs, 'No open jobs recorded.');
    $('provider-name').textContent = label(provider.name) || 'Unknown';
    $('generation').textContent = seenText(data.generation) || '—';
    $('lead-session').textContent = data.session || 'Not recorded';
    $('background-id').textContent = provider.background_id || (provider.format === 'codex' ? 'Not used by Codex' : 'Unavailable');
    $('saved-session').textContent = provider.session_id || 'Not recorded';
    const waiting = provider.waiting_for;
    $('waiting').hidden = !waiting;
    $('waiting').textContent = waiting ? 'Session needs attention' + (waiting === true ? '.' : ': ' + label(waiting)) + ' Open the interactive console to respond.' : '';
    const notes = [provider.error, provider.history_note];
    if (!interaction().available && !provider.history_note) notes.push(interaction().reason);
    $('provider-note').textContent = notes.filter(Boolean).map(seenText).join(' ');
    $('provider-note').hidden = !$('provider-note').textContent;
    attachEnabled(); connection();
  }
  function duration(seconds) { const n = Number(seconds); if (seconds == null || seconds === '' || !Number.isFinite(n) || n < 0) return 'Not recorded'; return n < 60 ? Math.floor(n) + 's' : n < 3600 ? Math.floor(n / 60) + 'm ' + Math.floor(n % 60) + 's' : Math.floor(n / 3600) + 'h ' + Math.floor(n % 3600 / 60) + 'm'; }
  function showActivity(activity) {
    const tasks = Array.isArray(activity?.tasks) ? activity.tasks : [];
    $('worker-count').textContent = (activity?.truncated ? 'Showing ' : '') + tasks.length + (tasks.length === 1 ? ' task' : ' tasks') + (activity?.truncated ? ' · partial list' : '');
    $('worker-summary').textContent = activity?.error || (tasks.length ? 'Activity checked ' + time(activity.checked_at) + '. Native durations include pauses; updates come from saved session records.' : 'No worker records are mapped to this run yet. Lead checkpoint updates appear below.');
    const fragment = document.createDocumentFragment();
    for (const task of tasks) {
      const row = document.createElement('tr');
      const cell = (text, note) => { const td = document.createElement('td'); td.textContent = text; if (note) { const small = document.createElement('small'); small.textContent = note; td.append(small); } row.append(td); return td; };
      const native = String(task.job_id || '').startsWith('native:');
      cell(native ? label(String(task.job_id).slice(7)) : label(task.provider) || 'Worker', native ? task.activity_source === 'codex_local_metadata' ? 'Native agent · saved activity' : 'Native agent · checkpoint' : String(task.job_id || '').slice(0, 12));
      const terminal = /accepted|rejected|failed|succeeded|completed|review|cancel|timeout|timed_out/.test(task.status || '');
      const progress = task.progress || {}, signals = [];
      if (task.reason) signals.push(task.reason);
      if (!terminal && progress.present && progress.state) signals.push('Provider: ' + label(progress.state));
      if (Number(progress.retry_count) > 0) signals.push(progress.retry_count + ' retries');
      if (Number.isFinite(progress.first_answer_s)) signals.push('First answer after ' + duration(progress.first_answer_s));
      if (task.memory_status) signals.push('Memory capture: ' + label(task.memory_status));
      if (task.memory_use) signals.push(task.memory_use.label + (task.memory_use.memory_count ? ' (' + task.memory_use.memory_count + ')' : ''));
      if (task.memory_use?.feedback?.status === 'reviewed') signals.push('Usefulness review: ' + task.memory_use.feedback.rating);
      const phase = cell(label(task.phase || task.status) || 'Unknown', signals.join(' · ') || (task.phase !== task.status ? label(task.status) : ''));
      phase.className = 'worker-phase' + (/wait|hold|quota|review/.test(task.phase || task.status || '') ? ' waiting' : '');
      const timings = Object.entries(task.phase_durations_ms || {}).filter(([, ms]) => Number.isFinite(ms)).map(([name, ms]) => label(name) + ' ' + duration(ms / 1000)).join(' · ');
      const timeNotes = [task.elapsed_label, timings];
      if (native && Number.isFinite(task.last_turn_duration_seconds)) timeNotes.push('Latest turn: ' + duration(task.last_turn_duration_seconds));
      cell(task.elapsed_seconds == null && native ? 'Timing unavailable' : duration(task.elapsed_seconds), timeNotes.filter(Boolean).join(' · '));
      cell(task.updated_at ? elapsed(task.updated_at) : native ? 'Activity unavailable' : 'Unknown', [task.updated_label, task.updated_at ? time(task.updated_at) : task.activity_error || 'No saved activity timestamp'].filter(Boolean).join(' · '));
      const deadline = date(task.deadline_at);
      cell(deadline ? time(task.deadline_at) : native ? 'No enforced deadline' : 'Not recorded', deadline && !terminal && Date.now() > deadline.getTime() ? 'Past recorded deadline; awaiting a status update' : native ? 'Native agent; dispatcher timeouts do not apply' : '');
      fragment.append(row);
    }
    $('workers').replaceChildren(fragment);
  }
  function renderMessages(initial, older, newCount) {
    const area = $('conversation'), height = area.scrollHeight, top = area.scrollTop;
    const nearBottom = height - top - area.clientHeight < 70;
    const selection = window.getSelection();
    const selectionInside = selection && !selection.isCollapsed && area.contains(selection.anchorNode);
    const fragment = document.createDocumentFragment();
    for (const message of messages) {
      const article = document.createElement('article'); article.className = 'message' + (message.role === 'user' ? ' user' : '');
      const header = document.createElement('div'); header.className = 'message-head';
      const role = document.createElement('span'); role.className = 'message-role'; role.textContent = message.role === 'user' ? 'You' : label(message.role) || 'Message';
      const stamp = document.createElement('time'); stamp.textContent = time(message.timestamp);
      const parsed = date(message.timestamp); if (parsed) stamp.dateTime = parsed.toISOString();
      header.append(role, stamp);
      if (message.kind && message.kind !== 'text') { const kind = document.createElement('span'); kind.className = 'badge'; kind.textContent = label(message.kind); header.append(kind); }
      const text = document.createElement('div'); text.className = 'message-text'; text.textContent = seenText(message.text);
      article.append(header, text); fragment.append(article);
    }
    $('messages').replaceChildren(fragment);
    $('message-count').textContent = messages.length + (messages.length === 1 ? ' message loaded' : ' messages loaded');
    $('history-empty').hidden = messages.length > 0;
    $('older').hidden = !hasMore; $('older').disabled = olderBusy;
    if (older) area.scrollTop = top + area.scrollHeight - height;
    else if (initial || (nearBottom && !selectionInside)) area.scrollTop = area.scrollHeight;
    else { area.scrollTop = top; if (newCount) $('latest').textContent = 'New messages · jump to latest'; }
  }
  async function loadHistory(runId, capturedEpoch, key, older = false) {
    const capturedHistoryEpoch = historyEpoch;
    const cursor = older ? before : null;
    const query = new URLSearchParams({run: runId}); if (cursor !== null) query.set('before', cursor);
    const data = await api('/api/history?' + query);
    if (selected !== runId || capturedEpoch !== epoch || key !== sessionKey || capturedHistoryEpoch !== historyEpoch) return;
    const incoming = Array.isArray(data.messages) ? data.messages : [];
    // Identity is stable on append. A new source or shorter file invalidates
    // the old offsets, cursor and displayed messages, even for the same lead.
    const revisionParts = value => /^([a-f0-9]{20}):(\d+):(\d+)$/.exec(value || '');
    const previousRevision = revisionParts(historyRevision), currentRevision = revisionParts(data.revision);
    const sourceChanged = previousRevision && currentRevision && (previousRevision[1] !== currentRevision[1] || BigInt(currentRevision[2]) < BigInt(previousRevision[2]));
    if (sourceChanged) { resetHistory(); historyNotice = 'Saved conversation changed. Reloaded this history page; load older messages to browse this version.'; }
    // Bounded latest pages can advance past every loaded message. Start a new
    // contiguous page range instead of silently joining history across a gap.
    const offset = value => /^\d+$/.test(String(value)) ? BigInt(value) : null;
    const loadedOffsets = messages.map(item => offset(item.id));
    const maximumLoaded = loadedOffsets.length && loadedOffsets.every(value => value !== null) ? loadedOffsets.reduce((maximum, value) => value > maximum ? value : maximum) : null;
    const pageBoundary = offset(String(data.before || '').split(':')[0]);
    const movedPastLoaded = !older && !!data.has_more && maximumLoaded !== null && incoming.every(item => offset(item.id) !== null && offset(item.id) > maximumLoaded) && (incoming.length > 0 || (pageBoundary !== null && pageBoundary > maximumLoaded));
    if (movedPastLoaded) { resetHistory(); historyNotice = 'History advanced beyond the loaded page. Showing the latest page; load older messages to continue through the saved history.'; }
    const initial = messages.length === 0;
    if (!data.available) {
      resetHistory();
      $('history-badge').textContent = 'Unavailable';
      $('history-empty').textContent = data.notice || 'Saved conversation is not available for this session.';
      $('history-note').textContent = data.notice || 'The checkpoint above remains available. Viewing does not resume a model.';
      renderMessages(false, false, 0); return;
    }
    const existing = new Map(messages.map(item => [String(item.id), item]));
    const newCount = incoming.filter(item => !existing.has(String(item.id))).length;
    const contentChanged = incoming.some(item => JSON.stringify(item) !== JSON.stringify(existing.get(String(item.id))));
    const merged = new Map();
    for (const item of older ? [...incoming, ...messages] : [...messages, ...incoming]) merged.set(String(item.id), item);
    messages = [...merged.values()];
    if (initial || older) { before = data.before == null ? null : data.before; hasMore = !!data.has_more && before !== null; }
    $('history-badge').textContent = hasMore ? 'More history available' : 'Saved history';
    if (older && !sourceChanged) historyNotice = '';
    $('history-note').textContent = [historyNotice, data.notice].filter(Boolean).join(' ') || 'Viewing is read-only. Tool internals and private reasoning are not displayed.';
    $('history-empty').textContent = 'No saved user or assistant messages yet. This view updates automatically.';
    // Rebuilding only changed pages preserves text selection during idle refreshes.
    if (initial || older || contentChanged) renderMessages(initial, older, newCount);
    historyRevision = data.revision;
    $('history-updated').textContent = 'History checked ' + new Date().toLocaleTimeString();
  }
  async function refresh() {
    if (refreshing || document.hidden) return;
    refreshing = true;
    const capturedEpoch = epoch;
    try {
      const data = await api('/api/runs');
      if (capturedEpoch !== epoch || document.hidden) return;
      const runs = Array.isArray(data.runs) ? data.runs : [];
      const signature = JSON.stringify(runs.map(item => [item.id, item.owner, item.run_status, item.status]));
      if (signature !== runsSignature) {
        runsSignature = signature;
        const options = runs.map(item => { const option = document.createElement('option'); option.value = item.id; option.textContent = (label(item.owner) || 'Unassigned') + ' · ' + (label(item.run_status || item.status) || 'Unknown') + ' · ' + item.id; return option; });
        if (!options.length) { const option = document.createElement('option'); option.value = ''; option.textContent = 'No recorded runs'; options.push(option); }
        $('run').replaceChildren(...options);
      }
      if (selected && !runs.some(item => item.id === selected)) {
        // A missing selected run never silently redirects an interactive action to another run.
        if (![...$('run').options].some(option => option.value === selected)) { const option = document.createElement('option'); option.value = selected; option.textContent = 'Unavailable · ' + selected; $('run').append(option); }
        $('run').value = selected; detail = null; attachEnabled(); throw new Error('The selected run is no longer listed. Choose another run or retry.');
      }
      if (!selected && runs.length) selected = (memoryProject ? runs.find(run => (run.memory_projects || []).includes(memoryProject)) : runs[0])?.id || '';
      $('run').value = selected;
      if (!selected) {
        detail = null; attachEnabled(); showActivity(null); $('history-empty').textContent = memoryProject ? 'No saved run is mapped to this memory project. Choose a run explicitly to view it.' : 'No orchestration runs have been recorded yet.';
        $('history-badge').textContent = 'No runs'; lastSuccess = Date.now(); clearError(); return;
      }
      const runId = selected;
      const state = await api('/api/run?' + new URLSearchParams({run: runId}));
      if (capturedEpoch !== epoch || runId !== selected || document.hidden) return;
      showDetail(state);
      await loadHistory(runId, capturedEpoch, sessionKey);
      if (capturedEpoch !== epoch || runId !== selected) return;
      lastSuccess = Date.now(); clearError();
    } catch (error) { if (capturedEpoch === epoch) showError(error); }
    finally { refreshing = false; connection(); if (capturedEpoch !== epoch && !document.hidden) refresh(); }
  }
  $('run').addEventListener('change', () => {
    selected = $('run').value; epoch++; detail = null; sessionKey = ''; attachEnabled(); resetHistory();
    const address = new URL(window.location.href); if (selected) address.searchParams.set('run', selected); else address.searchParams.delete('run');
    window.history.replaceState(null, '', address);
    $('attach-status').textContent = ''; $('waiting').hidden = true; $('provider-note').hidden = true;
    for (const id of ['owner', 'run-status', 'provider-status', 'observed', 'provider-name', 'generation', 'lead-session', 'background-id', 'saved-session']) $(id).textContent = 'Loading…';
    $('run-id').textContent = selected; renderCrumb();
    $('objective').textContent = 'Loading the selected run…'; $('checkpoint-time').textContent = '';
    for (const id of ['next-steps', 'completed', 'open-jobs']) $(id).replaceChildren();
    showActivity(null);
    refresh();
  });
  $('memory-project').addEventListener('change', () => { memoryProject = $('memory-project').value; const address = new URL(location.href); address.searchParams.set('project', memoryProject); if (selected) address.searchParams.set('run', selected); history.replaceState(null, '', address); renderCrumb(); });
  $('refresh').addEventListener('click', () => { loadServices(); loadMonitor(); refresh(); });
  $('older').addEventListener('click', async () => {
    if (olderBusy || !hasMore || !selected) return;
    olderBusy = true; $('older').disabled = true; $('older').textContent = 'Loading older messages…';
    const capturedEpoch = epoch, runId = selected, key = sessionKey, capturedHistoryEpoch = historyEpoch;
    try { await loadHistory(runId, capturedEpoch, key, true); }
    catch (error) { if (capturedEpoch === epoch && key === sessionKey && capturedHistoryEpoch === historyEpoch) showError(error); }
    finally { if (capturedEpoch === epoch && key === sessionKey && capturedHistoryEpoch === historyEpoch) { olderBusy = false; $('older').disabled = false; $('older').textContent = 'Load older messages'; } }
  });
  $('latest').addEventListener('click', () => { $('conversation').scrollTop = $('conversation').scrollHeight; $('latest').textContent = 'Jump to latest'; });
  $('attach').addEventListener('click', async () => {
    const action = interaction();
    if (attaching || !detail || !action.available) return;
    const capturedEpoch = epoch;
    const modern = Boolean(detail.provider?.interaction);
    const body = {run: selected, session: detail.session, generation: detail.generation, background_id: action.background_id};
    if (modern) { body.kind = action.kind; body.session_id = action.session_id; }
    attaching = true; attachEnabled(); $('attach-status').textContent = action.kind === 'codex_conversation' ? 'Opening this Codex conversation…' : 'Opening this session’s interactive console…';
    try {
      const result = await api(modern ? '/api/interact' : '/api/attach', body);
      if (capturedEpoch !== epoch) return;
      if (!result.ok || !['opened', 'already opened'].includes(result.status)) throw new Error(result.error || 'The conversation could not be opened. Refresh the session and retry.');
      $('attach-status').textContent = action.kind === 'codex_conversation' ? 'Sent this conversation to VS Code. Continue messages and approvals in Codex.' : result.status === 'already opened' ? 'This console was just opened. Continue messages and approvals there.' : 'Console opened. Continue messages and approvals there.';
      $('announcement').textContent = $('attach-status').textContent;
    } catch (error) { if (capturedEpoch === epoch) { $('attach-status').textContent = error.message; showError(error); } }
    finally { attaching = false; attachEnabled(); }
  });
  document.addEventListener('visibilitychange', () => { connection(); if (!document.hidden) { loadMonitor(); refresh(); } });
  setInterval(() => { connection(); if (!document.hidden) { loadMonitor(); refresh(); } }, 5000);
  renderCrumb(); loadServices(); loadMonitor(); refresh();
})();
</script>
</body>
</html>
'''
