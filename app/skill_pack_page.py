"""Skill pack selector page for the local Brain dashboard.

Exports PAGE as raw HTML. The lead decorates it with the workspace navigation
and the server fills in the script nonce and the page token placeholders.
"""

__all__ = ["PAGE"]

PAGE = """<!doctype html>
<html lang='en'>
<head>
<meta charset='utf-8'>
<meta name='viewport' content='width=device-width, initial-scale=1'>
<meta name="brain-token" content="__TOKEN__">
<title>Skill packs</title>
<style nonce="__NONCE__">
:root{color-scheme:light dark;--sp-bg:#f6f7f9;--sp-panel:#ffffff;--sp-text:#1b1f24;--sp-muted:#5b6470;--sp-line:#d9dee5;--sp-accent:#2f5fd0;--sp-accent-text:#ffffff;--sp-soft:#eef3fd;--sp-ok:#1f7a4a;--sp-warn:#8a5a00;--sp-err:#b3261e;--sp-code:#f1f3f6}
@media (prefers-color-scheme:dark){:root{--sp-bg:#14171b;--sp-panel:#1c2026;--sp-text:#e6e9ee;--sp-muted:#9aa4b1;--sp-line:#313843;--sp-accent:#8fb0ff;--sp-accent-text:#0e1320;--sp-soft:#222b3d;--sp-ok:#6fd39b;--sp-warn:#e8b65a;--sp-err:#ff8f86;--sp-code:#111418}}
*{box-sizing:border-box}
body{margin:0;background:var(--sp-bg);color:var(--sp-text);font:14px/1.5 system-ui,-apple-system,'Segoe UI',sans-serif}
[hidden]{display:none!important}
.sp{max-width:1240px;margin:0 auto;padding:16px}
.sp h1{font-size:20px;margin:0 0 4px}
.sp h2{font-size:15px;margin:0 0 8px}
.sp h3{font-size:13px;margin:0 0 6px}
.sp p{margin:0 0 8px}
.sp a{color:var(--sp-accent)}
.top{display:flex;flex-wrap:wrap;gap:12px;justify-content:space-between;align-items:flex-start;margin-bottom:12px}
.top nav{display:flex;gap:8px}
.top nav a{border:1px solid var(--sp-line);border-radius:6px;padding:5px 10px;text-decoration:none;background:var(--sp-panel)}
.note{font-size:12px;color:var(--sp-muted)}
.mono{font-family:ui-monospace,Consolas,monospace;overflow-wrap:anywhere}
.panel{background:var(--sp-panel);border:1px solid var(--sp-line);border-radius:10px;padding:14px;min-width:0}
.fields{display:grid;grid-template-columns:repeat(auto-fit,minmax(190px,1fr));gap:10px;align-items:end}
.field{display:flex;flex-direction:column;gap:4px;min-width:0}
.field>span,.lbl{display:block;font-size:12px;color:var(--sp-muted);font-weight:600}
.lbl{margin:8px 0 4px}
.sp input[type=text],.sp select,.sp textarea{width:100%;font:inherit;color:inherit;background:var(--sp-bg);border:1px solid var(--sp-line);border-radius:6px;padding:7px 9px}
.sp textarea{resize:vertical;min-height:64px}
.sp textarea.ctx{min-height:260px;font-family:ui-monospace,Consolas,monospace;font-size:12px;background:var(--sp-code)}
.sp button{font:inherit;border:1px solid var(--sp-line);background:var(--sp-panel);color:inherit;border-radius:6px;padding:7px 12px;cursor:pointer}
.sp button.primary{background:var(--sp-accent);border-color:var(--sp-accent);color:var(--sp-accent-text);font-weight:600}
.sp button:disabled,.sp select:disabled,.sp input:disabled{opacity:.55;cursor:not-allowed}
.sp :focus-visible{outline:2px solid var(--sp-accent);outline-offset:2px}
.row{display:flex;flex-wrap:wrap;gap:8px;align-items:center;margin:8px 0}
.status{margin:8px 0;padding:7px 10px;border-radius:6px;border:1px solid var(--sp-line);background:var(--sp-soft);overflow-wrap:anywhere}
.status.ok{border-color:var(--sp-ok);color:var(--sp-ok)}
.status.warn{border-color:var(--sp-warn);color:var(--sp-warn)}
.status.error{border-color:var(--sp-err);color:var(--sp-err)}
.stages{list-style:none;margin:12px 0;padding:0;display:grid;grid-template-columns:repeat(auto-fit,minmax(170px,1fr));gap:8px}
.stages li{background:var(--sp-panel);border:1px solid var(--sp-line);border-radius:8px;padding:8px 10px;display:flex;flex-direction:column;min-width:0}
.st-k{font-size:12px;color:var(--sp-muted);font-weight:600}
.st-v{overflow-wrap:anywhere}
.layout{display:grid;gap:14px;align-items:start;grid-template-columns:minmax(0,1fr) minmax(0,1fr);grid-template-rows:auto auto auto auto 1fr;grid-template-areas:'task catalog' 'review catalog' 'run catalog' 'outcome catalog' '. catalog'}
#p-task{grid-area:task}
#p-catalog{grid-area:catalog}
#p-review{grid-area:review}
#p-run{grid-area:run}
#p-outcome{grid-area:outcome}
.catalog-list{max-height:72vh;overflow:auto;margin-top:8px}
@media (max-width:880px){.layout{grid-template-columns:minmax(0,1fr);grid-template-rows:none;grid-template-areas:'task' 'catalog' 'review' 'run' 'outcome'}.catalog-list{max-height:55vh}}
.pack{border:1px solid var(--sp-line);border-radius:8px;padding:6px 10px;margin-bottom:8px}
.pack.rec,.skill.rec{border-color:var(--sp-accent);background:var(--sp-soft)}
.pack summary{cursor:pointer;font-weight:600}
.count{color:var(--sp-muted);font-weight:400;font-size:12px;margin-left:8px}
.badge{display:inline-block;margin-left:8px;padding:1px 7px;border-radius:999px;background:var(--sp-accent);color:var(--sp-accent-text);font-size:11px;font-weight:600}
.tag{display:inline-block;margin-left:8px;padding:1px 7px;border-radius:999px;border:1px solid var(--sp-line);color:var(--sp-muted);font-size:11px}
.skills{list-style:none;margin:6px 0;padding:0}
.skill{border:1px solid transparent;border-radius:6px;padding:4px 6px}
.skill label{display:flex;gap:8px;align-items:flex-start;cursor:pointer}
.skill-body{min-width:0;overflow-wrap:anywhere}
.skill-name{font-weight:600}
.skill-id,.skill-desc{display:block;color:var(--sp-muted);font-size:12px}
.skill-id{font-family:ui-monospace,Consolas,monospace}
.rec-box{margin-top:10px;padding:10px;border:1px solid var(--sp-accent);border-radius:8px;background:var(--sp-soft)}
.sub{margin-top:12px;padding-top:12px;border-top:1px solid var(--sp-line)}
.sp dl{display:grid;grid-template-columns:max-content minmax(0,1fr);gap:2px 12px;margin:0 0 8px}
.sp dt{color:var(--sp-muted)}
.sp dd{margin:0;overflow-wrap:anywhere}
.sp pre{margin:6px 0;padding:10px;background:var(--sp-code);border:1px solid var(--sp-line);border-radius:6px;font-family:ui-monospace,Consolas,monospace;font-size:12px;white-space:pre-wrap;overflow-wrap:anywhere;max-height:420px;overflow:auto}
</style>
</head>
<body>
__WORKSPACE_NAV__
<div class='sp'>
<header class='top'>
<div>
<h1>Skill packs</h1>
<p class='note'>Task, then JEV advice, then lead review and load, then a supplied-text worker, then explicit acceptance and a usefulness judgment. Each step is separate and nothing runs on its own.</p>
</div>
<nav aria-label='Related pages'>
<a id='link-home' href='/'>Dashboard</a>
<a id='link-jev' href='/jev'>JEV</a>
</nav>
</header>
<main>
<section class='panel' aria-labelledby='h-scope'>
<h2 id='h-scope'>Scope</h2>
<div class='fields'>
<label class='field' for='project'><span>Project</span>
<select id='project'>
<option value='agent-orchestrator'>agent-orchestrator</option>
<option value='openwhispr'>openwhispr</option>
<option value='civil3d'>civil3d</option>
</select></label>
<label class='field' for='run'><span>Run ID (optional, never guessed)</span>
<input type='text' id='run' maxlength='128' autocomplete='off' spellcheck='false'></label>
<label class='field' for='reviewer'><span>Reviewer name (review, acceptance, usefulness)</span>
<input type='text' id='reviewer' maxlength='100' autocomplete='name'></label>
<label class='field' for='plans'><span>Saved plans</span>
<select id='plans'><option value=''>No saved plans</option></select></label>
<button type='button' id='refresh'>Refresh (no provider call)</button>
</div>
<p id='scope-status' class='status' role='status' aria-live='polite' hidden></p>
<p id='plans-status' class='status' role='status' aria-live='polite' hidden></p>
</section>
<ol class='stages' aria-label='Workflow state for the open plan'>
<li><span class='st-k'>1. Recommendation</span><span class='st-v' id='st-rec'></span></li>
<li><span class='st-k'>2. Lead review and load</span><span class='st-v' id='st-review'></span></li>
<li><span class='st-k'>3. Requested execution</span><span class='st-v' id='st-exec'></span></li>
<li><span class='st-k'>4. Acceptance</span><span class='st-v' id='st-accept'></span></li>
<li><span class='st-k'>5. Usefulness judgment</span><span class='st-v' id='st-use'></span></li>
</ol>
<div class='layout'>
<section class='panel' id='p-task' aria-labelledby='h-task'>
<h2 id='h-task'>1. Task and recommendation</h2>
<label class='lbl' for='task'>Task for the worker</label>
<textarea id='task' maxlength='4000' rows='5' aria-describedby='task-count task-help'></textarea>
<p class='note'><span id='task-count'>0 / 4000</span> - <span id='task-help'>Editing the task clears the recommendation and plan shown here.</span></p>
<div class='row'>
<button type='button' class='primary' id='ask-jev'>Ask JEV to recommend (up to two calls)</button>
<button type='button' id='manual'>Start manual plan (no provider call)</button>
</div>
<p class='note'>JEV advises a pack, then a skill, with up to two billed requests; validated cache hits make no new calls. Opening this page, refreshing, or opening a saved plan never calls a provider. If JEV is unavailable or unsure, start a manual plan and choose from the local catalog.</p>
<p id='task-status' class='status' role='status' aria-live='polite' hidden></p>
<div id='rec' class='rec-box' tabindex='-1' hidden></div>
</section>
<section class='panel' id='p-catalog' aria-labelledby='h-catalog'>
<h2 id='h-catalog'>Local skill catalog</h2>
<p class='note' id='catalog-help'>Dormant packs stay outside session discovery. Only the SKILL.md text of the skills you select and review is loaded, and only for this task; nothing is activated globally.</p>
<label class='lbl' for='filter'>Filter skills</label>
<input type='text' id='filter' maxlength='80' autocomplete='off'>
<p id='selected-summary' class='note' role='status' aria-live='polite'></p>
<p id='catalog-meta' class='note mono'></p>
<p id='catalog-status' class='status' role='status' aria-live='polite' hidden></p>
<div id='catalog' class='catalog-list' role='group' aria-labelledby='h-catalog' aria-describedby='catalog-help'></div>
</section>
<section class='panel' id='p-review' aria-labelledby='h-review'>
<h2 id='h-review'>2. Lead review and load</h2>
<form id='review-form' novalidate>
<label class='lbl' for='review-note'>Review note (20 to 1000 characters)</label>
<textarea id='review-note' minlength='20' maxlength='1000' rows='3' aria-describedby='review-hint'></textarea>
<p id='review-hint' class='note'></p>
<div class='row'><button type='submit' class='primary' id='review-btn'>Review and load selected skills for this task</button></div>
</form>
<p id='review-status' class='status' role='status' aria-live='polite' hidden></p>
<div id='context-box' class='sub' tabindex='-1' hidden>
<h3>Loaded context (this task only)</h3>
<dl id='review-facts'></dl>
<p id='context-meta' class='note mono'></p>
<div class='row'><button type='button' id='copy-btn'>Copy loaded context</button><button type='button' id='copy-voice'>Copy OpenWhispr request</button></div>
<label class='lbl' for='voice-request'>Paste this into the Orchestrator target in OpenWhispr</label>
<textarea id='voice-request' rows='3' readonly></textarea>
<p id='copy-status' class='status' role='status' aria-live='polite' hidden></p>
<details id='context-details'>
<summary>Show full loaded skill context (read-only)</summary>
<label class='lbl' for='context-text'>Loaded skill context</label>
<textarea id='context-text' class='ctx' rows='14' readonly></textarea>
</details>
</div>
</section>
<section class='panel' id='p-run' aria-labelledby='h-run'>
<h2 id='h-run'>3. Worker execution</h2>
<form id='exec-form' novalidate>
<div class='fields'>
<label class='field' for='worker'><span>Worker</span>
<select id='worker' aria-describedby='worker-help'>
<option value='claude'>claude</option>
<option value='grok'>grok</option>
<option value='codex'>codex</option>
</select></label>
<button type='submit' class='primary' id='exec-btn'>Request execution (sent once)</button>
</div>
<p id='worker-help' class='note'>Every worker route is supplied-text only: the worker receives the task and the loaded context as text and has no tools. The request uses the configured route, is sent only when you press the button, and is never retried from this page.</p>
<p id='exec-hint' class='note'></p>
</form>
<p id='run-status' class='status' role='status' aria-live='polite' hidden></p>
<div id='exec-box' class='sub' hidden>
<h3>Requested execution</h3>
<dl id='exec-facts'></dl>
<p id='exec-note' class='note'></p>
<div id='answer-box' hidden>
<h3>Saved worker answer</h3>
<pre id='answer' tabindex='0'></pre>
</div>
</div>
</section>
<section class='panel' id='p-outcome' aria-labelledby='h-outcome'>
<h2 id='h-outcome'>4. Acceptance and usefulness</h2>
<p id='outcome-empty' class='note'>Available after a requested execution has finished and its answer is saved.</p>
<form id='accept-form' novalidate hidden>
<h3>Accept the saved answer</h3>
<p class='note'>Acceptance is your explicit decision about the saved worker answer shown in step 3. Brain then records compact accepted evidence.</p>
<p id='accept-state' class='note'></p>
<label class='lbl' for='accept-note'>Acceptance note</label>
<textarea id='accept-note' maxlength='1000' rows='2'></textarea>
<div class='row'><button type='submit' class='primary' id='accept-btn'>Accept saved answer</button></div>
<p id='accept-status' class='status' role='status' aria-live='polite' hidden></p>
</form>
<form id='feedback-form' class='sub' novalidate hidden>
<h3>Usefulness of the loaded skills</h3>
<p class='note'>Usefulness is a reviewer judgment. It is recorded separately from the fact that context was supplied, and it is not a measured effect.</p>
<dl id='use-facts'></dl>
<p id='use-note' class='note'></p>
<div class='fields'>
<label class='field' for='rating'><span>Reviewer rating</span>
<select id='rating'>
<option value=''>Choose a rating</option>
<option value='helped'>helped</option>
<option value='neutral'>neutral</option>
<option value='harmful'>harmful</option>
</select></label>
</div>
<label class='lbl' for='feedback-note'>Usefulness note</label>
<textarea id='feedback-note' maxlength='1000' rows='2'></textarea>
<div class='row'><button type='submit' class='primary' id='feedback-btn'>Record usefulness judgment</button></div>
<p id='feedback-status' class='status' role='status' aria-live='polite' hidden></p>
</form>
</section>
</div>
</main>
</div>
<script nonce="__NONCE__">
(function () {
'use strict';
var PROJECTS = ['agent-orchestrator', 'openwhispr', 'civil3d'];
var WORKERS = ['claude', 'grok', 'codex'];
var RATINGS = ['helped', 'neutral', 'harmful'];
var MAX_SKILLS = 3;
var TASK_MAX = 4000;
var POLL_MS = 2000;
var RUN_RE = /^[A-Za-z0-9._:-]{1,128}$/;
var PLAN_MESSAGES = ['plans-status', 'task-status', 'review-status', 'copy-status', 'run-status', 'accept-status', 'feedback-status'];
var REVIEWER_MSG = 'Enter a reviewer name (up to 100 characters) in the Scope panel.';
var tokenMeta = document.getElementsByName('brain-token')[0];
var token = tokenMeta ? tokenMeta.getAttribute('content') || '' : '';
var state = {project: PROJECTS[0], run: '', catalog: null, index: Object.create(null), skillCount: 0, plans: [], plan: null, selected: [], use: null, useKey: '', useNote: '', busy: '', filter: ''};
var scopeSeq = 0, planSeq = 0, pollTimer = null, leaving = false;
var catalogKey = null, recKey = null, plansKey = null, lastContext = null, lastAnswer = null;
var sendLock = Object.create(null), feedbackLock = Object.create(null);

function $(id) { return document.getElementById(id); }
function str(v) { return typeof v === 'string' ? v : (v === null || v === undefined ? '' : String(v)); }
function fmt(v) {
  if (v === null || v === undefined) return '';
  if (typeof v === 'string') return v;
  try { return JSON.stringify(v).slice(0, 2000); } catch (e) { return String(v); }
}
function el(tag, cls, text) {
  var n = document.createElement(tag);
  if (cls) n.className = cls;
  if (text !== undefined && text !== null) n.textContent = String(text);
  return n;
}
function clear(n) { while (n.firstChild) n.removeChild(n.firstChild); }
function addRow(dl, k, v) {
  dl.appendChild(el('dt', '', k));
  dl.appendChild(el('dd', '', v === '' || v === null || v === undefined ? '-' : v));
}
function setStatus(id, text, kind) {
  var n = $(id);
  n.textContent = text || '';
  n.className = 'status' + (kind ? ' ' + kind : '');
  n.hidden = !text;
}
function clearPlanMessages() { PLAN_MESSAGES.forEach(function (id) { setStatus(id, '', ''); }); }
function errText(err) {
  var m = err && err.message ? str(err.message) : str(err);
  if (err && (err.status === 401 || err.status === 403)) m += ' (reload this page to get a fresh token)';
  m = m.slice(0, 500);
  if (m && '.!?'.indexOf(m.charAt(m.length - 1)) < 0) m += '.';
  return m;
}
function definite(err) { return !!err && err.status >= 400 && err.status < 500; }
function reveal(id) {
  var n = $(id);
  if (n.hidden) return;
  try { n.focus({preventScroll: true}); } catch (e) {}
  if (n.scrollIntoView) n.scrollIntoView({block: 'nearest'});
}
function fence() {
  var s = scopeSeq, p = planSeq;
  return function () { return !leaving && s === scopeSeq && p === planSeq; };
}

function api(method, path, params, body) {
  var url = path;
  if (params) url += '?' + new URLSearchParams(params).toString();
  var opts = {method: method, credentials: 'same-origin', cache: 'no-store', headers: {'X-Brain-Token': token, 'Accept': 'application/json'}};
  if (body) {
    opts.headers['Content-Type'] = 'application/json';
    opts.body = JSON.stringify(body);
  }
  var controller = new AbortController();
  opts.signal = controller.signal;
  var timeout = setTimeout(function () { controller.abort(); }, 70000);
  return fetch(url, opts).then(function (res) {
    return res.text().then(function (text) {
      var data = null;
      if (text) { try { data = JSON.parse(text); } catch (e) { data = null; } }
      if (!res.ok) {
        var msg = data && typeof data === 'object' ? (data.error || data.message || data.detail) : '';
        var err = new Error(typeof msg === 'string' && msg ? msg : 'Request failed (HTTP ' + res.status + ')');
        err.status = res.status;
        throw err;
      }
      if (!data || typeof data !== 'object') throw new Error('Unexpected response from the server');
      return data;
    });
  }).finally(function () { clearTimeout(timeout); });
}

function scopeHash() {
  var p = new URLSearchParams();
  p.set('project', state.project);
  if (state.run) p.set('run', state.run);
  return '#' + p.toString();
}
function readHash() {
  var p = new URLSearchParams(window.location.hash.replace('#', ''));
  var project = p.get('project') || '';
  var run = p.get('run') || '';
  return {project: PROJECTS.indexOf(project) >= 0 ? project : PROJECTS[0], run: RUN_RE.test(run) ? run : ''};
}
function syncLinks() {
  var h = scopeHash();
  $('link-home').setAttribute('href', '/' + h);
  $('link-jev').setAttribute('href', '/jev' + h);
  if (window.location.hash !== h) { try { window.history.replaceState(null, '', h); } catch (e) {} }
}

function validPlan(p) {
  return !!p && typeof p.id === 'string' && !!p.id && (p.project_id === undefined || p.project_id === null || p.project_id === state.project);
}
function isReviewed(plan) { return !!plan && (plan.status === 'reviewed' || !!plan.review); }
function isActive(plan) {
  var e = plan && plan.execution;
  return !!e && (e.status === 'starting' || e.status === 'running');
}
function pick(plan, name) {
  var e = plan && plan.execution;
  if (e && e[name] !== undefined && e[name] !== null) return e[name];
  return plan ? plan[name] : undefined;
}
function isAccepted() {
  var plan = state.plan;
  if (!plan || !plan.execution) return false;
  if (state.use) return true;
  return str(pick(plan, 'review_status')) === 'accepted';
}
function currentFeedback() {
  return (state.use && state.use.feedback) || (state.plan && state.plan.feedback) || null;
}
function catalogLocked() { return isReviewed(state.plan) || !!state.busy; }
function skillName(id) {
  id = str(id);
  var s = state.index[id];
  return s && s.name ? str(s.name) : id;
}
function packLabel(id) {
  var packs = state.catalog && Array.isArray(state.catalog.packs) ? state.catalog.packs : [];
  for (var i = 0; i < packs.length; i += 1) {
    if (packs[i] && str(packs[i].id) === id) return str(packs[i].label) || id;
  }
  return id;
}
function indexCatalog(cat) {
  state.index = Object.create(null);
  state.skillCount = 0;
  var packs = cat && Array.isArray(cat.packs) ? cat.packs : [];
  packs.forEach(function (pack) {
    var skills = pack && Array.isArray(pack.skills) ? pack.skills : [];
    skills.forEach(function (skill) {
      if (skill && typeof skill.id === 'string' && skill.id) {
        state.index[skill.id] = skill;
        state.skillCount += 1;
      }
    });
  });
}
function resetPlan() {
  state.plan = null;
  state.selected = [];
  state.use = null;
  state.useKey = '';
  state.useNote = '';
}
function updateCount() { $('task-count').textContent = $('task').value.length + ' / ' + TASK_MAX; }
function setBusy(name) { state.busy = name; render(); }

function stopPoll() {
  if (pollTimer !== null) { clearTimeout(pollTimer); pollTimer = null; }
}
function schedulePoll() {
  stopPoll();
  if (leaving || !isActive(state.plan)) return;
  var ok = fence(), id = state.plan.id;
  pollTimer = setTimeout(function () {
    pollTimer = null;
    if (!ok()) return;
    api('GET', '/api/skills/plan', {project_id: state.project, plan_id: id}).then(function (plan) {
      if (!ok()) return;
      if (!validPlan(plan) || plan.id !== id) throw new Error('Unexpected plan in the poll response');
      setStatus('run-status', '', '');
      adoptPlan(plan, false);
      if (!isActive(plan)) loadPlans();
    }).catch(function (err) {
      if (!ok()) return;
      setStatus('run-status', 'Could not read the saved plan: ' + errText(err) + ' Still checking saved state; the execution is not re-sent.', 'warn');
      schedulePoll();
    });
  }, POLL_MS);
}

function adoptPlan(plan, resetSelection) {
  state.plan = plan;
  if (plan.review && Array.isArray(plan.review.skill_ids)) state.selected = plan.review.skill_ids.map(str).slice(0, MAX_SKILLS);
  else if (resetSelection) state.selected = [];
  render();
  maybeLoadUse();
  schedulePoll();
}

function loadCatalog() {
  var s = scopeSeq;
  setStatus('catalog-status', 'Loading the local catalog...', '');
  api('GET', '/api/skills/catalog', {project_id: state.project}).then(function (data) {
    if (s !== scopeSeq || leaving) return;
    state.catalog = data;
    indexCatalog(data);
    if (!catalogLocked()) state.selected = state.selected.filter(function (id) { return !!state.index[id]; });
    var warnings = Array.isArray(data.warnings) ? data.warnings.map(fmt) : [];
    setStatus('catalog-status', warnings.length ? 'Catalog warnings: ' + warnings.join('; ') : '', warnings.length ? 'warn' : '');
    render();
  }).catch(function (err) {
    if (s !== scopeSeq || leaving) return;
    state.catalog = null;
    indexCatalog(null);
    setStatus('catalog-status', 'The catalog could not be loaded: ' + errText(err), 'error');
    render();
  });
}
function loadPlans() {
  var s = scopeSeq;
  api('GET', '/api/skills/plans', {project_id: state.project}).then(function (data) {
    if (s !== scopeSeq || leaving) return;
    state.plans = Array.isArray(data.plans) ? data.plans : [];
    renderPlans();
  }).catch(function (err) {
    if (s !== scopeSeq || leaving) return;
    setStatus('scope-status', 'Saved plans could not be listed: ' + errText(err), 'error');
  });
}
function refreshPlan(id, ok) {
  api('GET', '/api/skills/plan', {project_id: state.project, plan_id: id}).then(function (plan) {
    if (!ok()) return;
    if (!validPlan(plan) || plan.id !== id) throw new Error('Unexpected plan response');
    adoptPlan(plan, false);
  }).catch(function (err) {
    if (!ok()) return;
    setStatus('plans-status', 'Could not reload the saved plan: ' + errText(err), 'error');
  });
}
function openPlan(id) {
  planSeq += 1;
  stopPoll();
  resetPlan();
  clearPlanMessages();
  render();
  if (!id) return;
  var ok = fence();
  setStatus('plans-status', 'Opening the saved plan (no provider call)...', '');
  api('GET', '/api/skills/plan', {project_id: state.project, plan_id: id}).then(function (plan) {
    if (!ok()) return;
    if (!validPlan(plan) || plan.id !== id) throw new Error('Unexpected plan response');
    setStatus('plans-status', '', '');
    $('task').value = str(plan.task);
    updateCount();
    adoptPlan(plan, true);
  }).catch(function (err) {
    if (!ok()) return;
    setStatus('plans-status', 'Could not open the saved plan: ' + errText(err), 'error');
    render();
  });
}
function maybeLoadUse() {
  var plan = state.plan, e = plan && plan.execution;
  if (!e || e.status !== 'finished' || !e.job_id || str(pick(plan, 'review_status')) !== 'accepted') return;
  var job = str(e.job_id);
  var key = plan.id + '|' + job + '|' + str(pick(plan, 'review_status')) + '|' + (plan.feedback ? '1' : '0');
  if (key === state.useKey) return;
  state.useKey = key;
  var ok = fence();
  api('GET', '/api/skills/use', {project_id: state.project, job_id: job}).then(function (use) {
    if (!ok()) return;
    if (str(use.job_id) !== job) throw new Error('Unexpected job in the response');
    state.use = use;
    state.useNote = '';
    render();
  }).catch(function (err) {
    if (!ok()) return;
    state.use = null;
    state.useNote = 'Not eligible for a usefulness judgment yet (only an accepted, finalized task is eligible): ' + errText(err);
    render();
  });
}

function applyScope(project, run) {
  scopeSeq += 1;
  planSeq += 1;
  stopPoll();
  state.project = project;
  state.run = run;
  state.catalog = null;
  indexCatalog(null);
  state.plans = [];
  resetPlan();
  $('project').value = project;
  $('run').value = run;
  syncLinks();
  clearPlanMessages();
  setStatus('scope-status', '', '');
  setStatus('catalog-status', '', '');
  render();
  loadCatalog();
  loadPlans();
}
function refresh() {
  if (state.busy) return;
  setStatus('scope-status', '', '');
  loadCatalog();
  loadPlans();
  if (state.plan) refreshPlan(state.plan.id, fence());
}

function recommend(useJev) {
  if (state.busy) return;
  var task = $('task').value.trim();
  if (!task) { setStatus('task-status', 'Enter a task first.', 'error'); $('task').focus(); return; }
  if (task.length > TASK_MAX) { setStatus('task-status', 'The task is limited to ' + TASK_MAX + ' characters.', 'error'); return; }
  planSeq += 1;
  stopPoll();
  var keep = isReviewed(state.plan) ? [] : state.selected.slice();
  resetPlan();
  state.selected = keep;
  clearPlanMessages();
  var ok = fence(), s = scopeSeq;
  var body = {project_id: state.project, task: task, jev: !!useJev};
  if (state.run) body.run_id = state.run;
  setBusy(useJev ? 'recommend' : 'manual');
  setStatus('task-status', useJev ? 'Asking JEV (up to two billed calls)...' : 'Creating a manual plan (no provider call)...', '');
  api('POST', '/api/skills/recommend', null, body).then(function (plan) {
    if (!ok()) { if (s === scopeSeq && !leaving) loadPlans(); return; }
    if (!validPlan(plan)) throw new Error('Unexpected plan response');
    setStatus('task-status', useJev ? 'JEV answered. The advice is below; nothing has been loaded or run.' : 'Manual plan created without a provider call. Select skills in the catalog, then review.', 'ok');
    adoptPlan(plan, false);
    loadPlans();
    reveal('rec');
  }).catch(function (err) {
    if (!ok()) return;
    setStatus('task-status', (useJev ? 'The JEV recommendation did not complete: ' : 'The manual plan was not created: ') + errText(err) + (useJev ? ' The request is not retried. The local catalog still works: use Start manual plan (no provider call) and choose skills yourself.' : ''), 'error');
    if (useJev) loadPlans();
  }).then(function () { setBusy(''); });
}

function review() {
  if (state.busy) return;
  var plan = state.plan, cat = state.catalog;
  var reviewer = $('reviewer').value.trim(), note = $('review-note').value.trim();
  var unknown = state.selected.filter(function (id) { return !state.index[id]; });
  var problem = '';
  if (!plan) problem = 'Create or open a plan first.';
  else if (isReviewed(plan)) problem = 'This plan is already reviewed.';
  else if (!cat || !cat.manifest_sha256) problem = 'The catalog is not loaded.';
  else if (!state.selected.length || state.selected.length > MAX_SKILLS) problem = 'Select one to three skills in the catalog.';
  else if (unknown.length) problem = 'A selected skill is not in the current catalog: ' + unknown.join(', ') + '.';
  else if (!reviewer || reviewer.length > 100) problem = REVIEWER_MSG;
  else if (note.length < 20 || note.length > 1000) problem = 'Write a review note of 20 to 1000 characters.';
  if (problem) { setStatus('review-status', problem, 'error'); return; }
  var ok = fence(), id = plan.id;
  var body = {project_id: state.project, plan_id: id, skill_ids: state.selected.slice(), expected_manifest_sha256: str(cat.manifest_sha256), reviewer: reviewer, note: note};
  setBusy('review');
  setStatus('review-status', 'Loading the selected skill text for this task...', '');
  api('POST', '/api/skills/review', null, body).then(function (p) {
    if (!ok()) return;
    if (!validPlan(p) || p.id !== id) throw new Error('Unexpected plan response');
    setStatus('review-status', 'Reviewed. The context is loaded for this task only; nothing has been executed.', 'ok');
    adoptPlan(p, false);
    loadPlans();
    reveal('context-box');
  }).catch(function (err) {
    if (!ok()) return;
    setStatus('review-status', 'The review was not confirmed: ' + errText(err) + ' Reloading the saved plan.', 'error');
    refreshPlan(id, ok);
  }).then(function () { setBusy(''); });
}

function copyContext() {
  var text = state.plan && state.plan.context ? str(state.plan.context.text) : '';
  if (!text) { setStatus('copy-status', 'There is no loaded context to copy.', 'error'); return; }
  var done = function () { setStatus('copy-status', 'Copied to the clipboard.', 'ok'); };
  var fallback = function (err) {
    try {
      var ta = $('context-text');
      $('context-details').open = true;
      ta.focus();
      ta.select();
      if (document.execCommand('copy')) { done(); return; }
    } catch (e) {}
    setStatus('copy-status', 'Copy failed' + (err ? ': ' + errText(err) : '.') + ' The full text is open below; select and copy it manually.', 'error');
  };
  try {
    if (navigator.clipboard && navigator.clipboard.writeText) navigator.clipboard.writeText(text).then(done, fallback);
    else fallback(null);
  } catch (e) { fallback(e); }
}

function execute() {
  if (state.busy) return;
  var plan = state.plan, worker = $('worker').value;
  var problem = '';
  if (!plan || !isReviewed(plan) || !plan.context) problem = 'Review and load context for this plan first.';
  else if (plan.execution) problem = 'An execution was already requested for this plan (' + str(plan.execution.status) + '). It is not re-sent; start a new plan to run again.';
  else if (sendLock[plan.id]) problem = 'An earlier request for this plan had an uncertain outcome. It is not re-sent.';
  else if (WORKERS.indexOf(worker) < 0) problem = 'Choose a worker.';
  if (problem) { setStatus('run-status', problem, 'error'); return; }
  var ok = fence(), id = plan.id;
  setBusy('execute');
  setStatus('run-status', 'Requesting execution by ' + worker + ' (sent once)...', '');
  api('POST', '/api/skills/execute', null, {project_id: state.project, plan_id: id, worker: worker}).then(function (p) {
    if (!ok()) return;
    if (!validPlan(p) || p.id !== id) throw new Error('Unexpected plan response');
    setStatus('run-status', 'Execution requested from ' + worker + '.', 'ok');
    adoptPlan(p, false);
    loadPlans();
  }).catch(function (err) {
    if (!definite(err)) sendLock[id] = true;
    if (!ok()) return;
    setStatus('run-status', definite(err) ? 'The execution request was rejected: ' + errText(err) : 'The execution request did not return cleanly: ' + errText(err) + ' It may or may not have started, so it is not re-sent. Reloading the saved plan to show its recorded state.', 'error');
    refreshPlan(id, ok);
  }).then(function () { setBusy(''); });
}

function accept() {
  if (state.busy) return;
  var plan = state.plan, e = plan && plan.execution;
  var reviewer = $('reviewer').value.trim(), note = $('accept-note').value.trim();
  var problem = '';
  if (!e || e.status !== 'finished') problem = 'Only a finished execution with a saved answer can be accepted.';
  else if (isAccepted()) problem = 'This answer is already accepted.';
  else if (!reviewer || reviewer.length > 100) problem = REVIEWER_MSG;
  else if (!note || note.length > 1000) problem = 'Write an acceptance note (up to 1000 characters).';
  if (problem) { setStatus('accept-status', problem, 'error'); return; }
  var ok = fence(), id = plan.id;
  setBusy('accept');
  setStatus('accept-status', 'Recording acceptance...', '');
  api('POST', '/api/skills/accept', null, {project_id: state.project, plan_id: id, reviewer: reviewer, note: note}).then(function (p) {
    if (!ok()) return;
    if (!validPlan(p) || p.id !== id) throw new Error('Unexpected plan response');
    setStatus('accept-status', 'Accepted. Memory outcome: ' + (fmt(pick(p, 'memory_outcome')) || 'not reported') + '.', 'ok');
    state.useKey = '';
    adoptPlan(p, false);
    loadPlans();
  }).catch(function (err) {
    if (!ok()) return;
    setStatus('accept-status', 'Acceptance was not confirmed: ' + errText(err) + ' Reloading the saved plan.', 'error');
    refreshPlan(id, ok);
  }).then(function () { setBusy(''); });
}

function feedback() {
  if (state.busy) return;
  var use = state.use, plan = state.plan, rating = $('rating').value;
  var reviewer = $('reviewer').value.trim(), note = $('feedback-note').value.trim();
  var problem = '';
  if (!use || !plan) problem = 'Only an accepted, finalized task is eligible for a usefulness judgment.';
  else if (currentFeedback() || feedbackLock[str(use.job_id)]) problem = 'A usefulness judgment was already sent for this job.';
  else if (RATINGS.indexOf(rating) < 0) problem = 'Choose a rating.';
  else if (!reviewer || reviewer.length > 100) problem = REVIEWER_MSG;
  else if (!note || note.length > 1000) problem = 'Write a usefulness note (up to 1000 characters).';
  if (problem) { setStatus('feedback-status', problem, 'error'); return; }
  var ok = fence(), job = str(use.job_id), id = plan.id;
  var body = {project_id: state.project, job_id: job, rating: rating, reviewer: reviewer, note: note, expected_source_sha256: str(use.source_sha256), expected_context_sha256: str(use.context_sha256)};
  setBusy('feedback');
  setStatus('feedback-status', 'Recording the reviewer judgment...', '');
  api('POST', '/api/skills/feedback', null, body).then(function (res) {
    feedbackLock[job] = true;
    if (!ok()) return;
    setStatus('feedback-status', 'Reviewer judgment recorded: ' + (str(res.rating) || rating) + '. Status: ' + (str(res.status) || 'not reported') + '. Memory outcome: ' + (fmt(res.memory_outcome) || 'not reported') + '.', 'ok');
    state.useKey = '';
    maybeLoadUse();
    refreshPlan(id, ok);
    loadPlans();
  }).catch(function (err) {
    if (!definite(err)) feedbackLock[job] = true;
    if (!ok()) return;
    setStatus('feedback-status', 'The usefulness judgment was not confirmed: ' + errText(err) + (definite(err) ? '' : ' It is not re-sent from this page.'), 'error');
  }).then(function () { setBusy(''); });
}

function planLabel(p) {
  var task = str(p.task);
  return str(p.status) + (p.execution ? ' / ' + str(p.execution.status) : '') + ' - ' + (task.length > 70 ? task.slice(0, 70) + '...' : task);
}
function renderPlans() {
  var sel = $('plans'), cur = state.plan ? state.plan.id : '';
  var plans = state.plans.filter(function (p) { return !!p && typeof p.id === 'string' && !!p.id; });
  var key = cur + '|' + plans.map(function (p) { return p.id + ':' + planLabel(p); }).join('|');
  if (key === plansKey) return;
  plansKey = key;
  clear(sel);
  var first = el('option', '', plans.length ? 'Open a saved plan (' + plans.length + ')' : 'No saved plans');
  first.value = '';
  sel.appendChild(first);
  plans.forEach(function (p) {
    var o = el('option', '', planLabel(p));
    o.value = p.id;
    sel.appendChild(o);
  });
  sel.value = cur;
  if (sel.value !== cur) sel.value = '';
}

function renderStages() {
  var plan = state.plan, e = plan && plan.execution, rec = plan && plan.recommendation, fb = currentFeedback();
  var hasRec = !!rec && (!!rec.skill_id || !!rec.pack_id);
  $('st-rec').textContent = !plan ? 'no plan open' : hasRec ? 'advice: ' + (str(rec.status) || 'received') : 'manual plan, no advice' + (rec && rec.status ? ' (' + str(rec.status) + ')' : '');
  $('st-review').textContent = !plan ? '-' : isReviewed(plan) ? 'context loaded' + (plan.review && plan.review.reviewer ? ' by ' + str(plan.review.reviewer) : '') : 'not reviewed, nothing loaded';
  $('st-exec').textContent = !plan ? '-' : !e ? 'not requested' : str(e.status) || 'unknown';
  $('st-accept').textContent = !e ? '-' : isAccepted() ? 'accepted' : e.status === 'finished' ? 'awaiting explicit acceptance' : 'not eligible';
  $('st-use').textContent = !e ? '-' : fb ? 'reviewer rated: ' + (typeof fb === 'object' && fb.rating ? str(fb.rating) : 'recorded') : 'no reviewer rating';
}

function renderRecommendation() {
  var box = $('rec'), plan = state.plan;
  var key = plan ? [plan.id, str(plan.status), fmt(plan.recommendation), state.catalog ? str(state.catalog.manifest_sha256) : '', Array.isArray(plan.decisions) ? plan.decisions.length : 0].join('|') : '';
  if (key === recKey) return;
  recKey = key;
  clear(box);
  box.hidden = !plan;
  if (!plan) return;
  var rec = plan.recommendation;
  var recSkill = rec ? str(rec.skill_id) : '', recPack = rec ? str(rec.pack_id) : '';
  box.appendChild(el('h3', '', 'Recommendation (advice only)'));
  if (!recSkill && !recPack) {
    box.appendChild(el('p', '', 'This plan has no recommendation' + (rec && rec.status ? ' (status: ' + str(rec.status) + ')' : '') + '. Choose skills from the local catalog yourself.'));
  } else {
    var dl = el('dl');
    addRow(dl, 'Status', str(rec.status));
    addRow(dl, 'Pack', recPack ? packLabel(recPack) + ' (' + recPack + ')' : '');
    addRow(dl, 'Skill', recSkill ? skillName(recSkill) + ' (' + recSkill + ')' : '');
    box.appendChild(dl);
    if (recSkill && state.index[recSkill]) {
      var btn = el('button', '', 'Select recommended skill');
      btn.type = 'button';
      btn.id = 'rec-select';
      btn.value = recSkill;
      btn.addEventListener('click', function () {
        if (catalogLocked() || state.selected.indexOf(recSkill) >= 0 || state.selected.length >= MAX_SKILLS) return;
        state.selected.push(recSkill);
        syncSelection();
        renderActions();
      });
      box.appendChild(btn);
    } else if (recSkill && !state.catalog) {
      box.appendChild(el('p', 'note', 'Loading the catalog before checking the recommended skill.'));
    } else if (recSkill) {
      box.appendChild(el('p', '', 'The recommended skill is not in the current catalog. Choose from the catalog yourself.'));
    }
    box.appendChild(el('p', 'note', 'If the status shows JEV was unavailable or unsure, ignore the advice and choose from the catalog.'));
  }
  box.appendChild(el('p', 'note mono', 'Plan ' + plan.id + ' - status ' + (str(plan.status) || 'unknown')));
  box.appendChild(el('p', 'note', 'A recommendation loads nothing and runs nothing. The lead review in step 2 decides what is loaded.'));
  if (Array.isArray(plan.decisions) && plan.decisions.length) {
    var det = el('details');
    det.appendChild(el('summary', '', 'Decision receipts (' + plan.decisions.length + ')'));
    var pre = el('pre');
    var text = '';
    try { text = JSON.stringify(plan.decisions, null, 2); } catch (e) { text = String(plan.decisions); }
    pre.textContent = text.slice(0, 6000);
    det.appendChild(pre);
    box.appendChild(det);
  }
}

function matchesFilter(pack, skill) {
  if (!state.filter) return true;
  var hay = [skill.id, skill.name, skill.description, pack.label].map(str).join(' ').toLowerCase();
  return hay.indexOf(state.filter) >= 0;
}
function skillRow(skill, recSkill) {
  var id = str(skill.id);
  var li = el('li', 'skill');
  var label = el('label');
  var cb = document.createElement('input');
  cb.type = 'checkbox';
  cb.className = 'skill-cb';
  cb.value = id;
  label.appendChild(cb);
  var body = el('span', 'skill-body');
  body.appendChild(el('span', 'skill-name', str(skill.name) || id));
  if (recSkill && id === recSkill) {
    li.className = 'skill rec';
    body.appendChild(el('span', 'badge', 'Recommended'));
  }
  if (skill.availability !== undefined && skill.availability !== null && skill.availability !== '') body.appendChild(el('span', 'tag', fmt(skill.availability)));
  body.appendChild(el('span', 'skill-id', id));
  if (skill.description) body.appendChild(el('span', 'skill-desc', str(skill.description)));
  label.appendChild(body);
  li.appendChild(label);
  return li;
}
function syncSelection() {
  var full = state.selected.length >= MAX_SKILLS, locked = catalogLocked();
  var boxes = $('catalog').getElementsByTagName('input');
  for (var i = 0; i < boxes.length; i += 1) {
    var on = state.selected.indexOf(boxes[i].value) >= 0;
    boxes[i].checked = on;
    boxes[i].disabled = locked || (!on && full);
  }
  var names = state.selected.map(skillName);
  $('selected-summary').textContent = state.selected.length + ' of ' + MAX_SKILLS + ' selected' + (names.length ? ': ' + names.join(', ') : '') + (isReviewed(state.plan) ? ' (locked by the saved review)' : full ? ' - limit reached' : '');
}
function renderCatalog() {
  var box = $('catalog'), cat = state.catalog, plan = state.plan;
  var rec = plan && plan.recommendation ? plan.recommendation : null;
  var recSkill = rec ? str(rec.skill_id) : '', recPack = rec ? str(rec.pack_id) : '';
  var key = [cat ? 'c' + str(cat.manifest_sha256) : '-', plan ? plan.id : '-', recSkill, recPack, state.filter].join('|');
  if (key !== catalogKey) {
    catalogKey = key;
    clear(box);
    if (!cat) {
      box.appendChild(el('p', 'note', 'No catalog is loaded.'));
    } else {
      var packs = Array.isArray(cat.packs) ? cat.packs : [];
      var shown = 0;
      packs.forEach(function (pack) {
        if (!pack || typeof pack !== 'object') return;
        var skills = (Array.isArray(pack.skills) ? pack.skills : []).filter(function (s) { return !!s && typeof s.id === 'string' && !!s.id; });
        var matches = skills.filter(function (s) { return matchesFilter(pack, s); });
        if (state.filter && !matches.length) return;
        shown += 1;
        var isRecPack = !!recPack && str(pack.id) === recPack;
        var hasSelected = skills.some(function (s) { return state.selected.indexOf(s.id) >= 0; });
        var d = el('details', isRecPack ? 'pack rec' : 'pack');
        d.open = !!state.filter || isRecPack || hasSelected || packs.length === 1;
        var sum = el('summary');
        sum.appendChild(el('span', '', str(pack.label) || str(pack.id)));
        sum.appendChild(el('span', 'count', matches.length + (state.filter ? ' of ' + skills.length : '') + ' skills'));
        if (isRecPack) sum.appendChild(el('span', 'badge', 'Recommended pack'));
        d.appendChild(sum);
        if (pack.description) d.appendChild(el('p', 'note', str(pack.description)));
        var list = el('ul', 'skills');
        matches.forEach(function (skill) { list.appendChild(skillRow(skill, recSkill)); });
        d.appendChild(list);
        box.appendChild(d);
      });
      if (!shown) box.appendChild(el('p', 'note', state.filter ? 'No skills match the filter.' : 'The catalog has no packs.'));
    }
  }
  $('catalog-meta').textContent = cat ? 'Manifest ' + (str(cat.manifest_sha256) || 'unknown') + ' - ' + (Array.isArray(cat.packs) ? cat.packs.length : 0) + ' packs, ' + state.skillCount + ' skills' : '';
  syncSelection();
}

function renderContext() {
  var plan = state.plan, ctx = plan && plan.context;
  $('context-box').hidden = !ctx;
  var text = ctx ? str(ctx.text) : '';
  $('copy-voice').disabled = !ctx || state.project !== 'openwhispr';
  $('voice-request').value = ctx && state.project === 'openwhispr' ? 'use skill plan ' + plan.id + ': ' + plan.task : '';
  if (text !== lastContext) {
    lastContext = text;
    $('context-text').value = text;
  }
  var dl = $('review-facts');
  clear(dl);
  if (!ctx) { $('context-meta').textContent = ''; return; }
  var rv = plan.review || {};
  addRow(dl, 'Reviewer', str(rv.reviewer));
  addRow(dl, 'Review note', str(rv.note));
  addRow(dl, 'Loaded skills', (Array.isArray(rv.skill_ids) ? rv.skill_ids : []).map(skillName).join(', '));
  $('context-meta').textContent = 'Context SHA-256 ' + (str(ctx.sha256) || 'unknown') + ' - ' + text.length + ' characters';
}

function renderRun() {
  var plan = state.plan, e = plan && plan.execution;
  $('exec-box').hidden = !e;
  var dl = $('exec-facts');
  clear(dl);
  var answer = '';
  if (e) {
    addRow(dl, 'Execution status', (str(e.status) || 'unknown') + (isActive(plan) ? ' (checking the saved plan every 2 seconds)' : ''));
    addRow(dl, 'Job', str(e.job_id));
    addRow(dl, 'Run', str(e.run_id));
    if (e.error) addRow(dl, 'Error', fmt(e.error));
    addRow(dl, 'Review status', fmt(pick(plan, 'review_status')));
    addRow(dl, 'Memory outcome', fmt(pick(plan, 'memory_outcome')));
    var note = '';
    if (e.status === 'uncertain') note = 'The outcome of this execution is uncertain. It is not retried from this page.';
    else if (e.status === 'failed') note = 'The execution failed. It is not re-sent from this page; start a new plan to run again.';
    else if (e.status === 'finished') note = isAccepted() ? 'The worker answer below was accepted.' : 'The worker answer is saved but not accepted. Read it, then accept it explicitly in step 4.';
    else if (isActive(plan)) note = 'The request was sent once. This page only reads the saved plan while it runs.';
    $('exec-note').textContent = note;
    if (typeof e.response === 'string') answer = e.response;
    else if (e.response !== null && e.response !== undefined) { try { answer = JSON.stringify(e.response, null, 2); } catch (err) { answer = String(e.response); } }
  }
  $('answer-box').hidden = !answer;
  if (answer !== lastAnswer) {
    lastAnswer = answer;
    $('answer').textContent = answer;
  }
}

function renderOutcome() {
  var plan = state.plan, e = plan && plan.execution, fin = !!e && e.status === 'finished';
  $('outcome-empty').hidden = fin;
  $('accept-form').hidden = !fin;
  $('feedback-form').hidden = !fin;
  var dl = $('use-facts');
  clear(dl);
  if (!fin) return;
  $('accept-state').textContent = isAccepted() ? 'This answer is accepted.' : 'Not accepted yet. Read the saved answer in step 3 before accepting.';
  var use = state.use, fb = currentFeedback();
  if (use) {
    addRow(dl, 'Job', str(use.job_id));
    addRow(dl, 'Source SHA-256', str(use.source_sha256));
    addRow(dl, 'Supplied context SHA-256', str(use.context_sha256));
    addRow(dl, 'Recorded judgment', fb ? fmt(fb) : 'none');
  }
  $('use-note').textContent = use ? (fb ? 'A usefulness judgment is already recorded for this job.' : 'Accepted and finalized: eligible for one reviewer judgment.') : (state.useNote || 'Only an accepted, finalized task is eligible.');
}

function renderActions() {
  var busy = !!state.busy, plan = state.plan, e = plan && plan.execution, cat = state.catalog;
  var reviewed = isReviewed(plan), full = state.selected.length >= MAX_SKILLS;
  $('ask-jev').disabled = busy;
  $('manual').disabled = busy;
  $('refresh').disabled = busy;
  $('review-btn').disabled = busy || !plan || reviewed || !cat || !state.selected.length;
  var hint = '';
  if (!plan) hint = 'Create or open a plan first. A manual plan makes no provider call.';
  else if (reviewed) hint = 'This plan is reviewed and its selection is locked. To load different skills, start a new plan.';
  else if (!cat) hint = 'The catalog is not loaded, so nothing can be reviewed yet.';
  else if (plan.manifest_sha256 && cat.manifest_sha256 && plan.manifest_sha256 !== cat.manifest_sha256) hint = 'The catalog changed since this plan was created. The review sends the manifest shown now; if the server rejects it, start a new plan.';
  else if (!state.selected.length) hint = 'Select one to three skills in the catalog.';
  else hint = 'Ready: ' + state.selected.length + ' selected. Loading applies to this task only.';
  $('review-hint').textContent = hint;
  var canRun = !!plan && reviewed && !!plan.context && !e && !sendLock[plan.id];
  $('exec-btn').disabled = busy || !canRun;
  $('worker').disabled = busy || !canRun;
  var execHint = '';
  if (!plan || !reviewed || !plan.context) execHint = 'Review and load context in step 2 before requesting an execution.';
  else if (e) execHint = 'An execution was already requested for this plan. It is not re-sent.';
  else if (sendLock[plan.id]) execHint = 'An earlier request for this plan had an uncertain outcome. It is not re-sent.';
  $('exec-hint').textContent = execHint;
  var fin = !!e && e.status === 'finished';
  $('accept-btn').disabled = busy || !fin || isAccepted();
  var use = state.use;
  $('feedback-btn').disabled = busy || !fin || !use || !!currentFeedback() || !!feedbackLock[str(use && use.job_id)];
  var rs = $('rec-select');
  if (rs) rs.disabled = busy || catalogLocked() || full || state.selected.indexOf(rs.value) >= 0;
}

function render() {
  renderPlans();
  renderStages();
  renderRecommendation();
  renderCatalog();
  renderContext();
  renderRun();
  renderOutcome();
  renderActions();
}

$('project').addEventListener('change', function () {
  var p = $('project').value;
  if (PROJECTS.indexOf(p) < 0) { $('project').value = state.project; return; }
  if (p !== state.project) applyScope(p, state.run);
});
$('run').addEventListener('change', function () {
  var r = $('run').value.trim();
  if (r && !RUN_RE.test(r)) {
    $('run').value = state.run;
    setStatus('scope-status', 'The run ID may use letters, digits, dot, underscore, colon and hyphen (up to 128 characters).', 'error');
    return;
  }
  if (r !== state.run) applyScope(state.project, r);
  else $('run').value = r;
});
$('plans').addEventListener('change', function () { openPlan($('plans').value); });
$('refresh').addEventListener('click', refresh);
$('task').addEventListener('input', function () {
  updateCount();
  planSeq += 1;
  stopPoll();
  var had = !!state.plan;
  if (had) resetPlan();
  clearPlanMessages();
  if (had) setStatus('task-status', 'The task changed, so the previous recommendation and plan were cleared from view. The saved plan stays under Saved plans.', '');
  render();
});
$('ask-jev').addEventListener('click', function () { recommend(true); });
$('manual').addEventListener('click', function () { recommend(false); });
$('filter').addEventListener('input', function () {
  state.filter = $('filter').value.trim().toLowerCase();
  renderCatalog();
});
$('catalog').addEventListener('change', function (ev) {
  var t = ev.target;
  if (!t || t.className !== 'skill-cb') return;
  if (!catalogLocked()) {
    var at = state.selected.indexOf(t.value);
    if (t.checked) {
      if (at < 0 && state.selected.length < MAX_SKILLS) state.selected.push(t.value);
    } else if (at >= 0) {
      state.selected.splice(at, 1);
    }
    setStatus('review-status', '', '');
  }
  syncSelection();
  renderActions();
});
$('review-form').addEventListener('submit', function (ev) { ev.preventDefault(); review(); });
$('copy-btn').addEventListener('click', copyContext);
$('copy-voice').addEventListener('click', function () {
  var text = $('voice-request').value;
  if (!text) return;
  navigator.clipboard.writeText(text).then(function () { setStatus('copy-status', 'OpenWhispr request copied.', 'ok'); })
    .catch(function () { $('voice-request').focus(); $('voice-request').select(); setStatus('copy-status', 'Select and copy the prepared request below.', 'warn'); });
});
$('exec-form').addEventListener('submit', function (ev) { ev.preventDefault(); execute(); });
$('accept-form').addEventListener('submit', function (ev) { ev.preventDefault(); accept(); });
$('feedback-form').addEventListener('submit', function (ev) { ev.preventDefault(); feedback(); });
window.addEventListener('hashchange', function () {
  var h = readHash();
  if (h.project !== state.project || h.run !== state.run) applyScope(h.project, h.run);
  else syncLinks();
});
window.addEventListener('pagehide', function () { leaving = true; stopPoll(); });
window.addEventListener('pageshow', function (ev) {
  if (ev.persisted) { leaving = false; schedulePoll(); }
});

var initial = readHash();
updateCount();
applyScope(initial.project, initial.run);
if (!token) setStatus('scope-status', 'The page token is missing. Reload this page from the dashboard.', 'error');
}());
</script>
</body>
</html>
"""
