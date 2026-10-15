"""Brain review and selected-context controls used by the maintained dashboard."""
SCRIPT = r"""
const recallSelection={key:'',ids:new Set(),busy:false};
let sourceTaskRequest=0;
function filterLoadedLibrary(rows,status,tags,mode){
  const selected=tags.map(tag=>tag.trim().toLowerCase()).filter(Boolean);
  return rows.filter(row=>{
    if(status!=='all'&&row.status!==status)return false;
    const values=new Set((row.tags||[]).map(tag=>String(tag).toLowerCase()));
    return !selected.length||(mode==='any'?selected.some(tag=>values.has(tag)):selected.every(tag=>values.has(tag)));
  });
}
function libraryTagControls(){
  const host=el('div','filters'),label=el('label','','Tags (comma separated)'),input=el('input');
  input.id='library-tags';input.maxLength=500;input.value=(state.tagFilters||[]).join(', ');
  input.setAttribute('aria-label','Filter loaded library by tags');
  input.addEventListener('change',()=>{state.tagFilters=input.value.split(',').map(tag=>tag.trim()).filter(Boolean).slice(0,12);render();});
  label.append(input);const mode=el('select');mode.id='library-tag-mode';mode.setAttribute('aria-label','Tag matching');
  for(const [value,text] of [['all','Match all tags'],['any','Match any tag']]){const option=el('option','',text);option.value=value;mode.append(option);}
  mode.value=state.tagMatch||'all';mode.addEventListener('change',()=>{state.tagMatch=mode.value;render();});
  host.append(label,mode,button('Clear tags',()=>{state.tagFilters=[];render();}));return host;
}
async function renderSourceCheck(host,memory,actions){
  if(memory.status!=='pending')return;
  const box=section('Current source check'),badge=el('p','pill','Checking source...');
  badge.id='source-check-status';const approve=[...actions.querySelectorAll('button')].find(node=>node.textContent==='Approve memory');
  const recheck=button('Check source again',check);box.append(badge,recheck);host.append(box);
  async function check(){
    const project=state.project;badge.textContent='Checking source...';recheck.disabled=true;if(approve)approve.disabled=true;
    try{const receipt=await api('/api/source-check?project_id='+encodeURIComponent(project)+'&memory_id='+encodeURIComponent(memory.id));
      if(state.project!==project||state.detail?.id!==memory.id||!box.isConnected)return;
      badge.textContent='Source '+receipt.status+' / '+receipt.reason;
      badge.className='pill '+(receipt.status==='current'&&receipt.approval_ready?'active':'pending');
      if(approve)approve.disabled=!receipt.approval_ready;
    }catch(error){if(box.isConnected)badge.textContent='Source unavailable / '+error.message;}
    finally{recheck.disabled=false;}
  }
  await check();
}
function selectedRecallPanel(found){
  const host=el('section','review-info');host.id='recall-selection';host.append(el('h3','','Selected recall context'));
  if(!found.trace_id){host.append(el('p','','This search has no saved recall receipt. Search again before preparing context.'));return host;}
  const key=state.project+'/'+found.trace_id;if(recallSelection.key!==key){recallSelection.key=key;recallSelection.ids.clear();}
  const choices=el('div'),count=el('p'),output=el('pre','evidence-preview'),exclusions=el('ul');output.id='context-output';output.hidden=true;exclusions.id='context-excluded';exclusions.hidden=true;
  const preview=button('Preview selected context',()=>prepare(false)),copy=button('Copy freshly checked context',()=>prepare(true));
  preview.id='context-preview';copy.id='context-copy';const inputs=[];
  function update(){count.textContent=recallSelection.ids.size+' of 24 selected. Preview and Copy each check current evidence.';
    preview.disabled=copy.disabled=recallSelection.busy||!recallSelection.ids.size;
    for(const input of inputs)input.disabled=recallSelection.busy||(!input.checked&&recallSelection.ids.size>=24);}
  for(const memory of (found.results||[]).slice(0,24)){
    const label=el('label'),input=el('input');label.style.display='block';input.type='checkbox';input.style.width='auto';input.style.marginRight='8px';input.dataset.contextMemory=memory.id;input.checked=recallSelection.ids.has(memory.id);
    input.addEventListener('change',()=>{if(input.checked)recallSelection.ids.add(memory.id);else recallSelection.ids.delete(memory.id);output.hidden=true;exclusions.hidden=true;update();});
    inputs.push(input);label.append(input,document.createTextNode(memory.title||memory.id));choices.append(label);
  }
  async function prepare(toClipboard){
    const project=state.project,trace=found.trace_id,ids=[...recallSelection.ids];recallSelection.busy=true;update();output.hidden=true;exclusions.hidden=true;
    try{const receipt=await api('/api/context',{project_id:project,trace_id:trace,memory_ids:ids});
      if(state.project!==project||state.searchResults?.trace_id!==trace||!host.isConnected)return;
      output.textContent=receipt.context;output.hidden=false;
      exclusions.replaceChildren();for(const item of receipt.excluded.slice(0,24))exclusions.append(el('li','',item.memory_id+' / '+item.reason));exclusions.hidden=!receipt.excluded.length;
      if(toClipboard){if(!receipt.memory_ids.length)throw new Error('No selected evidence is current; nothing was copied.');await navigator.clipboard.writeText(receipt.context);}
      notice((toClipboard?'Copied':'Previewed')+' '+receipt.memory_ids.length+' memories after fresh checks; '+receipt.excluded.length+' excluded.');
    }catch(error){notice(error.message,true);}finally{recallSelection.busy=false;update();}
  }
  host.append(choices,count,preview,copy,output,exclusions);update();return host;
}
function configureSourceMode(){
  const task=$('new-source-type').value==='task';$('task-source-fields').hidden=!task;
  $('new-source').required=!task;$('new-task').required=task;
  if(task)loadSourceTasks();
}
async function loadSourceTasks(){
  const project=$('new-project').value.trim(),request=++sourceTaskRequest,choice=$('new-task'),status=$('task-source-status');
  choice.replaceChildren();const first=el('option','','Choose a finalized task');first.value='';choice.append(first);status.textContent='Loading finalized task evidence...';
  try{const listing=await api('/api/source-tasks?project_id='+encodeURIComponent(project));
    if(request!==sourceTaskRequest||$('new-project').value.trim()!==project)return;
    for(const task of listing.tasks){const option=el('option','',task.title+' / '+task.job_id+' / '+(task.accepted?'Accepted':'Awaiting accepted review'));option.value=task.job_id;choice.append(option);}
    status.textContent=listing.tasks.length+' tasks loaded from up to '+listing.scan_limit+' canonical files. Approval still requires an accepted task review.'+(listing.truncated?' This list is bounded.':'');
  }catch(error){if(request===sourceTaskRequest)status.textContent=error.message;}
}
function selectedProposalSource(){
  if($('new-source-type').value==='task'){
    if(!$('new-task').value)throw new Error('Choose a finalized task in this project.');
    return {type:'task',job_id:$('new-task').value};
  }
  return {type:'user',note:$('new-source').value.trim()};
}
"""
