PAGE = r'''<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<meta name="viewer-token" content="__TOKEN__">
<title>Experiments</title>
<style nonce="__NONCE__">
:root{--bg:var(--ws-bg);--panel:var(--ws-surface);--accent:var(--ws-accent);--text:var(--ws-text);--muted:var(--ws-muted);--err:var(--ws-bad)}
*{box-sizing:border-box}html,body{margin:0;background:var(--bg);color:var(--text);font:15px/1.45 system-ui,sans-serif}
body{min-height:100vh;max-width:1440px;margin:auto;overflow-wrap:anywhere}a{color:var(--accent)}header,main,.panel{padding:12px 16px}
header{display:flex;flex-wrap:wrap;gap:10px 18px;align-items:center;border-bottom:1px solid var(--ws-line)}

h1{font-size:1.15rem;margin:0}.muted{color:var(--muted)}.err{color:var(--err)}
.row{display:flex;flex-wrap:wrap;gap:8px;align-items:center}
select,button{background:var(--ws-surface-2);color:var(--text);border:1px solid var(--ws-line-strong);border-radius:6px;padding:8px 10px;min-height:40px;max-width:100%}label{min-width:0;max-width:100%}select{width:min(100%,600px)}
button:focus,select:focus,a:focus,summary:focus{outline:2px solid var(--accent);outline-offset:2px}
.panel{background:var(--panel);border-radius:10px;margin:12px 16px}
dl{display:grid;grid-template-columns:minmax(8rem,14rem) 1fr;gap:4px 12px;margin:0}dt{color:var(--muted)}
pre{white-space:pre-wrap;word-break:break-word;background:var(--ws-surface-2);padding:10px;border-radius:6px;max-height:50vh;overflow:auto}
table{width:100%;border-collapse:collapse;font-size:.9rem}.table-wrap{overflow:auto;max-width:100%;margin:12px 0}th,td{text-align:left;padding:10px;border-bottom:1px solid var(--ws-line);vertical-align:top;min-width:80px}td{max-width:320px}h2{margin:8px 0 18px}h3{margin-top:26px}.cards{display:grid;grid-template-columns:repeat(auto-fit,minmax(160px,1fr));gap:12px;margin:20px 0}.card{padding:16px;background:var(--ws-surface-2);border:1px solid var(--ws-line-strong);border-radius:9px}.card strong{display:block;font-size:24px;color:var(--accent)}.call-details{margin:10px 0;padding:12px;background:var(--ws-surface-2);border-radius:9px}summary{cursor:pointer}
.status{display:inline-block;padding:2px 8px;border-radius:999px;border:1px solid var(--ws-line-strong)}
ul.plain{margin:0;padding-left:1.2rem}@media(max-width:390px){dl{grid-template-columns:1fr}header,main,.panel{padding:10px}}

__WORKSPACE_NAV_STYLE__</style>
</head>
<body>
__WORKSPACE_NAV__
<header>
<h1>Experiments</h1>
</header>
<main>
<p id="banner" class="muted" role="status">Loading…</p>
<div class="row">
<label>Suite <select id="suite"></select></label>
<label>Condition <select id="cond"></select></label>
<button type="button" id="reload">Reload</button>
</div>
<p id="empty" class="muted" hidden>No experiments yet</p>
<p id="hist" hidden></p>
</main>
<section class="panel" id="detail" hidden></section>
<section class="panel" id="outwrap" hidden>
<h2>Output</h2>
<pre id="out" tabindex="0"></pre>
</section>
<script nonce="__NONCE__" src="/workspace-navigation.js" defer></script>
<script nonce="__NONCE__">
const TOKEN=document.querySelector('meta[name="viewer-token"]').content;
const qs=new URLSearchParams(location.search);
let wantRun=qs.get('run')||'', wantCond=qs.get('condition')||'';
let suites=[], selSuite='', selCond='', detail=null, inflight=0, lastOk=0;
let outScroll=0, condScroll=0, outItem='', outputEpoch=0, listBusy=false, detailSignature='';
const caseFilters={}, caseOpen={};
const $=id=>document.getElementById(id);
function fmt(v){return v==null?'Unknown':String(v)}
function num(v){return v==null?'Unknown':typeof v==='number'?v.toLocaleString(undefined,{maximumFractionDigits:2}):String(v)}
function el(tag,text){const n=document.createElement(tag);if(text!=null)n.textContent=text;return n}
function fetchApi(url){
  const ctl=new AbortController();
  const t=setTimeout(()=>ctl.abort(),20000);
  return fetch(url,{headers:{'X-Viewer-Token':TOKEN},cache:'no-store',signal:ctl.signal})
    .finally(()=>clearTimeout(t));
}
async function readJson(r){
  const j=await r.json().catch(()=>({error:'Invalid JSON'}));
  if(!r.ok) throw new Error(j.error||r.statusText||'Request failed');
  if(j&&j.error) throw new Error(j.error);
  return j;
}
function usageLine(u){
  if(!u) return 'Unknown';
  const parts=[num(u.calls)+' recorded calls',num(u.succeeded_calls)+' successful',
    num(u.known_total_tokens)+' reported tokens'+(u.unknown_usage_calls?' (partial subtotal)':''),
    'Additional charge (USD): '+num(u.additional_charge_usd)];
  return parts.join(' · ');
}
function fillSelect(sel,items,getId,getLabel,cur){
  const keep=cur; sel.textContent='';
  items.forEach(it=>{
    const o=el('option',getLabel(it)); o.value=getId(it); sel.appendChild(o);
  });
  if(items.some(it=>getId(it)===keep)) sel.value=keep;
  else if(keep) sel.value='';
  else if(items.length) sel.selectedIndex=0;
}
function rosterNote(m,family){
  const c=m.contestant_count,h=m.helper_count;
  if(family==='jev_memory') return 'One Claude Opus contestant at medium effort, zero helpers, and a fresh supplied-text session per condition.';
  if(c===1 && h===0) return 'Solo: one OpenAI contestant, zero helper bots. Its sequential calls carry its own conversation; ASTRA setup is separate.';
  return 'Contestants '+fmt(c)+', helpers '+fmt(h)+'. Call count is not worker count.';
}
function renderList(){
  const sEl=$('suite'), cEl=$('cond');
  fillSelect(sEl,suites,s=>s.id,s=>(s.title||s.id)+' ['+(s.status||'')+']'+(s.historical?' historical':''),selSuite);
  selSuite=sEl.value||'';
  const suite=suites.find(s=>s.id===selSuite);
  const conds=suite&&suite.conditions?suite.conditions:[];
  fillSelect(cEl,conds,c=>c.id,c=>{
    const bits=[c.label||c.id,c.status||''];
    return bits.join(' · ');
  },selCond);
  selCond=cEl.value||'';
  $('empty').hidden=suites.length>0;
  $('banner').textContent=suites.length?'Select a condition to inspect its own usage (not suite totals).':'';
}
function kv(dl,k,v){dl.appendChild(el('dt',k));dl.appendChild(el('dd',v));}
function table(headers,rows){
  const t=el('table'); const thead=el('thead'); const tr=el('tr');
  headers.forEach(h=>tr.appendChild(el('th',h))); thead.appendChild(tr); t.appendChild(thead);
  const tb=el('tbody');
  rows.forEach(r=>{const tr=el('tr'); r.forEach(c=>{const td=el('td'); if(c&&c.nodeType) td.appendChild(c); else td.textContent=c==null?'Unknown':String(c); tr.appendChild(td);}); tb.appendChild(tr);});
  t.appendChild(tb); const wrap=el('div');wrap.className='table-wrap';wrap.tabIndex=0;wrap.appendChild(t);return wrap;
}
function gradeBlock(title,g){
  const wrap=el('div'); wrap.appendChild(el('h3',title));
  if(!g){wrap.appendChild(el('p','Unknown')); return wrap;}
  wrap.appendChild(el('p', 'Passed '+fmt(g.passed)+' / '+fmt(g.total)));
  (g.groups||[]).forEach(gr=>wrap.appendChild(el('p', gr.name+': '+fmt(gr.passed)+'/'+fmt(gr.total))));
  const filt=el('div'); filt.className='row';
  ['all','normal','edge','unclassified'].forEach(f=>{
    const b=el('button',f); b.type='button'; b.dataset.f=f;
    b.addEventListener('click',()=>{caseFilters[title]=f;drawCases(list,g.cases||[],f,title);});
    filt.appendChild(b);
  });
  wrap.appendChild(el('p','Checks are correlated with the condition; they do not change usage totals and have no per-case tokens.'));
  wrap.appendChild(filt);
  const list=el('div'); wrap.appendChild(list);
  drawCases(list,g.cases||[],caseFilters[title]||'all',title);
  return wrap;
}
function drawCases(host,cases,f,key){
  host.textContent='';
  const d=el('details'); d.open=Boolean(caseOpen[key]); d.appendChild(el('summary','Show individual checks ('+f+')'));
  d.addEventListener('toggle',()=>caseOpen[key]=d.open);
  const ul=el('ul'); ul.className='plain';
  cases.filter(c=>f==='all'||(c.group||'unclassified')===f||(f==='unclassified'&&!c.group)).forEach(c=>{
    const li=el('li', (c.passed===true?'pass':c.passed===false?'fail':'unknown')+' · '+(c.group||'unclassified')+' · '+(c.name||'')+' — '+(c.detail||''));
    ul.appendChild(li);
  });
  d.appendChild(ul); host.appendChild(d);
}
function renderDetail(d){
  const host=$('detail'); host.hidden=false; host.textContent='';
  const h=el('h2', (d.label||d.id||'Condition')+' · '+(d.status||'')); host.appendChild(h);
  const hist=$('hist');
  if(d.historical){
    hist.hidden=false; hist.textContent='';
    hist.appendChild(document.createTextNode('Historical condition view; shared original run '));
    const a=el('a','open run'); a.href='/?run='+encodeURIComponent(d.run_id||d.experiment_id||'');
    hist.appendChild(a);
  } else {
    hist.hidden=false; hist.textContent='Separate run for this condition.';
  }
  const m=d.metrics||{}, mem=d.memory||{}, u=d.usage||{};
  const cards=el('div');cards.className='cards';
  [['Contestants',m.contestant_count],['Helper bots',m.helper_count],['Worker calls',m.calls],['Wall seconds',m.wall_seconds],['Input tokens',m.input_tokens],['Output tokens',m.output_tokens]].forEach(([k,v])=>{const c=el('div',k);c.className='card';c.appendChild(el('strong',num(v)));cards.appendChild(c);});
  host.appendChild(cards);host.appendChild(el('p',rosterNote(m,d.family)));
  if(m.observed_contestant_count!==undefined)host.appendChild(el('p','Contestants with recorded calls: '+num(m.observed_contestant_count)+' of '+num(m.contestant_count)+' declared.'));
  host.appendChild(el('p', 'Memory: '+fmt(mem.mode)+' · '+num((mem.ids||[]).length)+' supplied memories.'));
  const dl=el('dl');
  kv(dl,'Condition',d.id); kv(dl,'Experiment',d.experiment_id); kv(dl,'Run',d.run_id);
  kv(dl,'Status',fmt(d.status)); kv(dl,'Memory mode',fmt(mem.mode)); kv(dl,'Memory project',fmt(mem.project_id));
  if(d.stage)kv(dl,'Phase',d.stage);
  kv(dl,'Memory ids',(mem.ids&&mem.ids.length)?mem.ids.join(', '):'none');
  kv(dl,'Memory lookup ms',num(mem.lookup_ms)); kv(dl,'Memory note',fmt(mem.note));
  kv(dl,'Contestants',num(m.contestant_count)); kv(dl,'Helpers',num(m.helper_count)); kv(dl,'Providers',num(m.provider_count));
  kv(dl,'Wall seconds',num(m.wall_seconds)); kv(dl,'Call-path seconds',num(m.call_path_seconds));
  kv(dl,'Calls',num(m.calls)); kv(dl,'Input tokens',num(m.input_tokens)); kv(dl,'Output tokens',num(m.output_tokens));
  kv(dl,'Setup', (d.setup&&d.setup.note)||'Measured usage excludes setup/controller.');
  kv(dl,'Setup usage known', d.setup&&d.setup.usage_known===true?'yes':'no');
  const meta=el('details');meta.appendChild(el('summary','Run identity, memory scope and setup accounting'));meta.appendChild(dl);host.appendChild(meta);
  host.appendChild(el('p','Usage below belongs to this execution. Setup and the coordinating conversation are excluded; their usage was not measured.'));
  host.appendChild(el('h3','Usage totals')); host.appendChild(el('p', usageLine(u.totals)));
  if(u.totals?.unknown_usage_calls)host.appendChild(el('p',num(u.totals.unknown_usage_calls)+' calls have incomplete token reports. Affected totals stay Unknown; the reported subtotal includes only available measurements.'));
  host.appendChild(el('p','Unknown means the measurement was not recorded or could not be reconciled. Tokens do not establish a charge; billing needs separate verification.'));
  const accounting=el('details');accounting.appendChild(el('summary','How usage is counted'));
  accounting.appendChild(el('p','Cached input is part of input usage, not an extra total. Provider reports are reconciled before adding them. Failed calls remain included when their usage is available.'));
  accounting.appendChild(el('p','Reported input subtotal: '+num(u.totals?.known_input_tokens)+' · Reported output subtotal: '+num(u.totals?.known_output_tokens)));
  host.appendChild(accounting);
  host.appendChild(el('h3','By provider'));
  host.appendChild(table(['Provider','Calls','Input tokens','Output tokens','Cached input','Additional charge (USD)'], (u.by_provider||[]).map(p=>[p.provider,num(p.calls),num(p.input_tokens),num(p.output_tokens),num(p.cached_input_tokens),num(p.additional_charge_usd)])));
  host.appendChild(el('h3','By worker'));
  host.appendChild(table(['Worker','Provider','Calls','Input tokens','Output tokens'], (u.by_worker||[]).map(w=>[w.worker_id,w.provider,num(w.calls),num(w.input_tokens),num(w.output_tokens)])));
  host.appendChild(el('h3','Roster'));
  host.appendChild(table(['Worker ID','Name','Provider','Role','Requested model','Effort','Reported models','Calls','Helpers'],
    (d.roster||[]).map(r=>[r.worker_id,r.name,r.provider,r.role,fmt(r.requested_model),fmt(r.requested_effort),(r.actual_models||[]).join(', ')||'Not reported',num(r.calls),num(r.helper_count)])));
  host.appendChild(el('h3','Calls'));
  (d.calls||[]).forEach(c=>{
    const section=el('div');section.className='call-details';
    section.appendChild(el('p',c.id+' · '+c.provider+' · '+c.status));
    if(c.preflight_only)section.appendChild(el('p','Preflight failed before any model request was sent.'));
    section.appendChild(el('p','Input '+num(c.usage?.input_tokens)+' · Output '+num(c.usage?.output_tokens)+' · '+num(c.elapsed_seconds)+' seconds · Memories '+num(c.memory_count)));
    const more=el('details');more.appendChild(el('summary','Call timing and model identity'));more.appendChild(el('p','Started '+fmt(c.started_at)+' · Ended '+fmt(c.ended_at)+' · Requested '+fmt(c.requested_model)+' · Observed '+((c.actual_models||[]).join(', ')||'Not reported')));section.appendChild(more);
    const btns=el('div'); btns.className='row';
    [['prompt',':prompt'],['response',':response'],['memory',':memory']].forEach(([lab,suf])=>{
      const b=el('button',lab); b.type='button';
      b.addEventListener('click',()=>loadOut('call:'+c.id+suf));
      btns.appendChild(b);
    });
    section.appendChild(btns);host.appendChild(section);
  });
  host.appendChild(el('h3','Artifacts'));
  const art=el('div'); art.className='row';
  (d.artifacts||[]).forEach(a=>{
    const b=el('button', a.label||a.id); b.type='button';
    b.addEventListener('click',()=>loadOut(a.id));
    art.appendChild(b);
  });
  host.appendChild(art);
  host.appendChild(gradeBlock('Initial checks', d.grades&&d.grades.initial));
  host.appendChild(gradeBlock('Final checks', d.grades&&d.grades.final));
  if(d.grades&&d.grades.note) host.appendChild(el('p', d.grades.note));
  (d.notes||[]).forEach(n=>host.appendChild(el('p',n)));
}
async function loadOut(item,preserve=false){
  if(!detail)return;
  const revision=++outputEpoch,run=detail.experiment_id,condition=detail.id;
  outScroll=preserve?$('out').scrollTop:0;
  outItem=item; $('outwrap').hidden=false; if(!preserve)$('out').textContent='Loading…';
  try{
    const r=await fetchApi('/api/experiment-output?run='+encodeURIComponent(run)+'&condition='+encodeURIComponent(condition)+'&item='+encodeURIComponent(item));
    const j=await readJson(r);
    if(revision!==outputEpoch||detail?.experiment_id!==run||detail?.id!==condition)return;
    $('out').textContent=(j.title||item)+'\n\n'+(j.content||'')+(j.truncated?'\n\n[truncated]':'');
    $('out').scrollTop=outScroll;
  }catch(e){if(revision===outputEpoch)$('out').textContent=e.message||String(e);}
}
async function loadSuites(preserve){
  if(listBusy)return;
  listBusy=true;
  const id=++inflight;
  try{
    const r=await fetchApi('/api/experiments');
    const j=await readJson(r);
    if(id!==inflight) return;
    suites=j.experiments||[];
    if(wantRun){
      const hit=suites.find(s=>s.id===wantRun|| (s.conditions||[]).some(c=>c.run_id===wantRun));
      if(!hit){$('banner').textContent='The requested experiment is unavailable. Select another explicitly.';selSuite=wantRun;renderList();$('detail').hidden=true;$('banner').textContent='The requested experiment is unavailable. Select another explicitly.';return;}
      selSuite=hit.id;
      if(wantCond)selCond=wantCond;
      else if(wantRun!==hit.id)selCond=(hit.conditions||[]).find(c=>c.run_id===wantRun)?.id||'';
      wantRun='';wantCond='';
    }
    renderList();
    $('banner').className='muted';
    if(j.errors&&j.errors.length)$('banner').textContent=j.errors.map(e=>e.id+': '+e.error).join('; ');
    if(selCond) await loadDetail(preserve);else{$('detail').hidden=true;if(suites.length)$('banner').textContent='Requested condition unavailable. Select a condition explicitly.';}
  }catch(e){ if(id!==inflight) return; $('banner').textContent=e.message||String(e); $('banner').className='err'; }
  finally{listBusy=false;}
}
async function loadDetail(preserve){
  const id=++inflight;
  const suite=suites.find(s=>s.id===selSuite);
  const cond=(suite&&suite.conditions||[]).find(c=>c.id===selCond);
  const run=cond&&cond.run_id;
  if(!selCond||!run){ $('detail').hidden=true; return; }
  try{
    const r=await fetchApi('/api/experiment?run='+encodeURIComponent(selSuite)+'&condition='+encodeURIComponent(selCond));
    const j=await readJson(r);
    if(id!==inflight) return;
    lastOk=Date.now(); detail=j;
    const y=window.scrollY;
    const signature=JSON.stringify(j);
    if(signature!==detailSignature){renderDetail(j);detailSignature=signature;}
    if(preserve) window.scrollTo(0,y);
    if(outItem) loadOut(outItem,true);
  }catch(e){ if(id!==inflight) return; $('detail').hidden=false; $('detail').textContent=''; $('detail').appendChild(el('p', e.message||String(e))).className='err'; }
}
function changed(){inflight++;outputEpoch++;outItem='';detail=null;detailSignature='';$('outwrap').hidden=true;$('detail').hidden=true;wantRun='';wantCond='';history.replaceState(null,'','/experiments?run='+encodeURIComponent(selSuite)+'&condition='+encodeURIComponent(selCond));loadDetail(false);}
$('suite').addEventListener('change',()=>{selSuite=$('suite').value;selCond='';renderList();changed();});
$('cond').addEventListener('change',()=>{selCond=$('cond').value;changed();});
$('reload').addEventListener('click',()=>loadSuites(true));
document.addEventListener('visibilitychange',()=>{ if(!document.hidden) loadSuites(true); });
setInterval(()=>{ if(!document.hidden) loadSuites(true); },5000);
loadSuites(false);
</script>
</body>
</html>
'''

from workspace_navigation import STYLE, THEME, static_markup  # noqa: E402

PAGE = PAGE.replace('__WORKSPACE_NAV__', static_markup('experiments', 'viewer', '__WORKSPACE_LEAD__')).replace('__WORKSPACE_NAV_STYLE__', THEME + STYLE)
