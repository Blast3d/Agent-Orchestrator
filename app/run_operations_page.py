"""Viewer forms backed by exact-run local review and planning APIs."""

HTML = r'''
<section class="panel session run-operations" aria-labelledby="operator-heading">
 <h2 id="operator-heading">Run review and acceptance</h2>
 <p class="muted smalltext">The selected run only. Planning progress does not complete a run or grant provider credit.</p>
 <button id="operator-reload" type="button" class="small">Refresh saved evidence</button>
 <p id="operator-status" role="status" aria-live="polite"></p>
 <h3 class="spaced">Family coverage preview</h3><p id="operator-coverage"></p><ul id="operator-coverage-notices"></ul>
 <h3 class="spaced">Recovery evidence</h3><div id="operator-recovery"></div>
 <h3 class="spaced">Dispatch invocation receipts</h3><div id="operator-reuse"></div>
 <h3 class="spaced">Ranked acceptance checklist</h3>
 <ol id="operator-checklist"></ol><button id="operator-add" type="button" class="small">Add outcome</button>
 <button id="operator-save" type="button" class="primary">Save checklist</button>
 <p class="muted smalltext">Verified requires an existing file inside this run. Move items to change priority; changes save only when you choose Save.</p>
 <h3 class="spaced">Accept task with curated knowledge</h3>
 <label class="label" for="operator-task">Successfully executed task awaiting review</label><select id="operator-task"></select>
 <details><summary>Saved task answer</summary><pre id="operator-answer"></pre><p id="operator-answer-notice" class="muted smalltext"></p></details>
 <label class="label" for="operator-reviewer">Reviewer</label><input id="operator-reviewer" maxlength="100">
 <label class="label" for="operator-note">Evidence checked and review explanation</label><textarea id="operator-note" rows="3" maxlength="1000"></textarea>
 <label class="label" for="operator-payload">Curated memory JSON (candidate or bundle)</label><textarea id="operator-payload" rows="6"></textarea>
 <p class="muted smalltext">Use kind, title and content for a fact/procedure candidate, or memories with unique keys and optional supported relations. Only reviewed knowledge enters Brain; provider calls remain zero.</p>
 <button id="operator-review" type="button" class="primary">Accept and capture curated knowledge</button>
 <h3 class="spaced">Attach existing voice request</h3><p id="operator-voice-scope" class="muted smalltext"></p>
 <select id="operator-voice"></select><button id="operator-voice-attach" type="button" class="small">Attach request metadata to this run</button>
 <ul id="operator-voice-attached" aria-label="Attached voice requests"></ul>
</section>
'''

STYLE = r'''
.run-operations input,.run-operations textarea{font:inherit;background:var(--inset);color:var(--text);border:1px solid var(--line);border-radius:9px;padding:10px;max-width:100%}
.run-operations>input,.run-operations>textarea{display:block;width:min(100%,850px);margin-bottom:12px}
.run-operations input:focus-visible,.run-operations textarea:focus-visible{outline:3px solid var(--accent);outline-offset:3px}
.run-operations h3{margin-top:24px;margin-bottom:8px}.run-operations .label{margin-top:12px}
.run-operations pre{white-space:pre-wrap;overflow-wrap:anywhere;max-height:320px;overflow:auto;background:var(--inset);padding:12px;border-radius:9px}
#operator-checklist li{margin:12px 0}#operator-checklist input{width:min(100%,420px);margin:4px}#operator-checklist select{width:auto;margin:4px}#operator-checklist button{margin:4px}
'''

JS = r'''
  let operatorKey = '', operatorData = null, checklistDraft = [];
  const operatorNode = (tag, text) => { const n=document.createElement(tag); if(text!==undefined)n.textContent=text;return n; };
  const operatorTuple = () => ({run_id:detail.id,owner:detail.owner,session:detail.session,generation:detail.generation});
  function operatorMessage(message){$('operator-status').textContent=message;}
  function showOperations(data){
    const key=JSON.stringify([data.id,data.owner,data.session,data.generation]);
    if(key===operatorKey)return;
    operatorKey=key;operatorData=null;checklistDraft=[];renderChecklist();
    loadOperations();
  }
  async function loadOperations(){
    if(!detail)return;const key=operatorKey;operatorMessage('Loading current saved evidence…');
    try{
      const value=await api('/api/operator?run='+encodeURIComponent(detail.id));if(key!==operatorKey)return;
      operatorData=value;checklistDraft=value.checklist.items.map(row=>({...row,evidence:[...row.evidence]}));renderChecklist();
      const c=value.coverage;$('operator-coverage').textContent=c.family_count+' verified families: '+(c.families.join(', ')||'none')+'. Minimum '+c.minimum_families+': '+(c.minimum_met?'met':'unmet')+'. Preview only; full closeout remains separate.';
      $('operator-coverage-notices').replaceChildren(...[...(c.warnings||[]),...(c.notices||[]).map(n=>n.job_id+': '+n.reason)].map(n=>operatorNode('li',n)));
      const recovery=value.tasks.filter(t=>t.recovery);$('operator-recovery').replaceChildren(...(recovery.length?recovery.map(t=>{const box=operatorNode('div');box.append(operatorNode('p',t.title+' · '+t.job_id+' · saved PID '+(t.recovery.process_pid??'unknown')+' · provider execution unknown · reservation '+t.recovery.reservation_state));const list=operatorNode('ol');t.recovery.checks.forEach(check=>list.append(operatorNode('li',check)));box.append(list,operatorNode('p',t.recovery.note),operatorNode('pre',JSON.stringify({process_status:t.recovery.process_status,local_process_terminated:t.recovery.local_process_terminated,saved_progress:t.recovery.saved_progress,termination:t.recovery.termination},null,2)));return box;}):[operatorNode('p','No uncertain task is recorded in this run.')]));
      const reused=value.tasks.flatMap(t=>t.invocations.filter(i=>i.reused).map(i=>({t,i})));$('operator-reuse').replaceChildren(...(reused.length?reused.map(({t,i})=>operatorNode('p','Reused assignment '+(i.assignment_id||'')+' · original job '+t.job_id+' · original task created '+i.original_created_at+' · invocation '+i.requested_at+' · no new inference')):[operatorNode('p','No saved dedup reuse invocation receipt in this run.')]));
      $('operator-task').replaceChildren(...value.tasks.filter(t=>t.status==='awaiting_review').map(t=>{const n=operatorNode('option',t.title+' · '+t.job_id);n.value=t.job_id;return n;}));
      showOperatorAnswer();
      $('operator-review').disabled=!$('operator-task').options.length;
      $('operator-voice-scope').textContent='Project '+value.project_id+' · up to '+(value.voice.bounded||100)+' recent request metadata rows; no transcript bodies are copied.';
      $('operator-voice').replaceChildren(...value.voice.requests.map(r=>{const n=operatorNode('option',r.request_id+' · '+r.status+' · '+r.created_at);n.value=r.request_id;return n;}));
      $('operator-voice-attach').disabled=!value.voice.requests.length;
      renderVoiceAttachments(value.voice.attachments);
      operatorMessage('Saved evidence loaded. No review or dispatch was performed.');
    }catch(error){operatorMessage(error.message);}
  }
  function showOperatorAnswer(){
    const task=operatorData?.tasks.find(t=>t.job_id===$('operator-task').value);
    $('operator-answer').textContent=task?.answer_preview||'No task answer is selected.';
    $('operator-answer-notice').textContent=task?.answer_truncated?'Preview limited to 12,000 characters. Read the full answer in this run’s Task Inbox before reviewing.':'';
  }
  function renderVoiceAttachments(requests){
    $('operator-voice-attached').replaceChildren(...(requests||[]).map(r=>operatorNode('li','Attached '+r.request_id+' · '+r.status+' · '+r.created_at)));
  }
  function renderChecklist(){
    $('operator-checklist').replaceChildren(...checklistDraft.map((item,index)=>{
      const li=operatorNode('li'),title=operatorNode('input');title.value=item.title;title.maxLength=160;title.setAttribute('aria-label','Outcome title');title.addEventListener('input',()=>item.title=title.value);
      const state=operatorNode('select');['pending','working','verified'].forEach(s=>{const n=operatorNode('option',s);n.value=s;state.append(n);});state.value=item.state;state.setAttribute('aria-label','Outcome state');state.addEventListener('change',()=>item.state=state.value);
      const evidence=operatorNode('input');evidence.value=item.evidence.join(', ');evidence.placeholder='review/test-report.json';evidence.setAttribute('aria-label','Evidence paths separated by commas');evidence.addEventListener('input',()=>item.evidence=evidence.value.split(',').map(s=>s.trim()).filter(Boolean));
      const up=operatorNode('button','Move up');up.type='button';up.disabled=index===0;up.addEventListener('click',()=>{[checklistDraft[index-1],checklistDraft[index]]=[item,checklistDraft[index-1]];renderChecklist();});
      const down=operatorNode('button','Move down');down.type='button';down.disabled=index===checklistDraft.length-1;down.addEventListener('click',()=>{[checklistDraft[index+1],checklistDraft[index]]=[item,checklistDraft[index+1]];renderChecklist();});
      li.append(title,state,evidence,up,down);if(item.evidence_notice)li.append(operatorNode('p',item.evidence_notice));return li;
    }));
  }
  $('operator-add').addEventListener('click',()=>{checklistDraft.push({id:'outcome-'+crypto.randomUUID().replaceAll('-',''),title:'New outcome',state:'pending',evidence:[]});renderChecklist();});
  $('operator-reload').addEventListener('click',loadOperations);
  $('operator-task').addEventListener('change',showOperatorAnswer);
  $('operator-save').addEventListener('click',async()=>{if(!detail)return;try{const result=await api('/api/checklist',{...operatorTuple(),items:checklistDraft.map(({id,title,state,evidence})=>({id,title,state,evidence}))});operatorMessage('Checklist saved. '+result.items.length+' outcomes; coordinator remains unchanged.');}catch(error){operatorMessage(error.message);}});
  $('operator-review').addEventListener('click',async()=>{
    const task=operatorData?.tasks.find(t=>t.job_id===$('operator-task').value);if(!task)return;
    $('operator-review').disabled=true;
    try{const result=await api('/api/task-review',{...operatorTuple(),job_id:task.job_id,source_sha256:task.source_sha256,reviewer:$('operator-reviewer').value,note:$('operator-note').value,curated_payload:JSON.parse($('operator-payload').value)});operatorMessage('Review: '+result.status+'. Memory capture: '+(result.memory_outcome?.status||'unknown')+'. Inspect the actual receipt before any retry.');}
    catch(error){operatorMessage(error.message);}finally{$('operator-review').disabled=false;}
  });
  $('operator-voice-attach').addEventListener('click',async()=>{if(!operatorData)return;try{const result=await api('/api/voice-attach',{...operatorTuple(),project_id:operatorData.project_id,request_id:$('operator-voice').value});renderVoiceAttachments(result.requests);operatorMessage('Attached request metadata. '+result.requests.length+' total attachments; no message sent.');}catch(error){operatorMessage(error.message);}});
  $('operator-payload').value=JSON.stringify({kind:'fact',title:'Reviewed finding',content:'Enter the specific verified knowledge to retain.'},null,2);
'''
