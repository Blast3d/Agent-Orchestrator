"""Scope-bound lifecycle and loading for the large relationship graph."""
SCRIPT = r'''
let graphController = null;
let graphRequest = null;
let graphEpoch = 0;
let graphLimit = 3000;
let graphCache = null;
let graphView3D = false;
try { graphView3D = localStorage.getItem('brain-graph-mode') === '3d'; } catch (_) {}
function disposeGraphView() {
  ++graphEpoch;
  if (graphRequest) graphRequest.abort();
  graphRequest = null;
  if (graphController) graphController.destroy();
  graphController = null;
}
function graphPanel() {
  const result = panel('Relationship map','Explore approved memories across this project.');
  result.node.dataset.brainGraphPanel = 'true';
  result.node.style.minWidth = '0';
  const legend = el('div','legend');
  const palette={fact:'var(--ws-kind-fact)',preference:'var(--ws-kind-preference)',episode:'var(--ws-kind-episode)',procedure:'var(--ws-kind-procedure)'};
  Object.keys(kinds).forEach(kind=>{const item=el('span','',kind[0].toUpperCase()+kind.slice(1));item.style.color=palette[kind];legend.append(item);});
  result.header.append(legend);
  const controls=el('div','panel-body');controls.style.display='flex';controls.style.flexWrap='wrap';controls.style.gap='8px';controls.style.alignItems='center';
  const label=el('label','','Show up to');const choice=el('select');choice.setAttribute('aria-label','Map node limit');choice.style.width='auto';
  [3000,5000,10000].forEach(limit=>{const option=el('option','',limit.toLocaleString()+' memories');option.value=String(limit);choice.append(option);});choice.value=String(graphLimit);
  label.append(choice);controls.append(label);
  const host=el('div','panel-body');host.style.minWidth='0';
  const caption=el('div','graph-caption','Loading project relationships…');caption.setAttribute('role','status');
  result.node.append(controls,host,caption);
  const project=state.project;
  async function load(force=false) {
    disposeGraphView();host.replaceChildren();caption.textContent='Loading project relationships…';
    const epoch=graphEpoch;const limit=graphLimit;const key=project+'|'+limit;
    function current(){return epoch===graphEpoch&&project===state.project&&result.node.isConnected;}
    async function graphApi(path,body,signal){
      if(!current())throw new DOMException('The project changed.','AbortError');
      const controller=new AbortController();
      const abort=()=>controller.abort();
      if(signal?.aborted)abort();else signal?.addEventListener('abort',abort,{once:true});
      const timeout=setTimeout(()=>controller.abort(),15000);
      const request={headers:{'X-Brain-Token':token},cache:'no-store',credentials:'same-origin',signal:controller.signal};
      if(body!==undefined){request.method='POST';request.headers['Content-Type']='application/json';request.body=JSON.stringify(body);}
      try{
        const response=await fetch(path,request);const reply=await response.json();
        if(!current())throw new DOMException('The project changed.','AbortError');
        if(!response.ok)throw new Error(reply.error||'The connection could not be saved.');
        return reply;
      }catch(error){
        if(error.name==='AbortError'&&current()&&!signal?.aborted)throw new Error(body===undefined?'Loading took too long. Select the project again to retry.':'The save timed out. Refresh this project before retrying; it may already have saved.');
        throw error;
      }finally{clearTimeout(timeout);signal?.removeEventListener('abort',abort);}
    }
    function connectionSaved(){
      if(!current())return;
      graphCache=null;state.pending=Math.max(1,state.pending||0);renderRefreshButton();
      caption.textContent='Connection saved. Refresh to reload the full project map. Source evidence is rechecked during recall.';
    }
    try {
      let data;
      if (!force && graphCache?.key===key) data=graphCache.data;
      else {
        const request=new AbortController();graphRequest=request;
        const query=new URLSearchParams({project_id:project,limit:String(limit)});
        const response=await fetch('/api/graph?'+query,{headers:{'X-Brain-Token':token},signal:request.signal});
        data=await response.json();if(!response.ok)throw new Error(data.error||'Could not load the relationship map.');
      }
      if(epoch!==graphEpoch||project!==state.project||!result.node.isConnected)return;
      if(data.project_id!==project||!Array.isArray(data.nodes)||!Array.isArray(data.relations))throw new Error('The map response does not match this project.');
      graphCache={key,data};graphController=createBrainGraph(data,id=>{if(epoch===graphEpoch&&project===state.project)showDetail(id);},{
        mode3D:graphView3D,onModeChange:value=>{graphView3D=value;try{localStorage.setItem('brain-graph-mode',value?'3d':'2d');}catch(_){}},
        projectId:project,projects:state.status?.projects||[],
        onLoadProjectGraph:async(targetProject,requestOptions)=>{
          const query=new URLSearchParams({project_id:targetProject,limit:String(limit)});
          return graphApi('/api/graph?'+query,undefined,requestOptions.signal);
        },
        onCreateRelation:async(link,requestOptions)=>{
          const result=await graphApi('/api/relate',{project_id:project,memory_id:link.sourceId,target_id:link.targetId,relation:link.relation,actor:link.actor},requestOptions.signal);
          connectionSaved();return result;
        },
        onCreateCrossProjectReference:async(link,requestOptions)=>{
          const result=await graphApi('/api/links',{project_id:project,memory_id:link.sourceId,target_project_id:link.targetProjectId,target_id:link.targetMemoryId,relation:link.relation,actor:link.actor,note:link.note,reuse:true},requestOptions.signal);
          connectionSaved();return result;
        }
      });
      host.replaceChildren(graphController.element);
      const total=data.total_nodes;
      caption.textContent=data.nodes.length.toLocaleString()+' of '+Number(total).toLocaleString()+' memories · '+data.relations.length.toLocaleString()+' connections'+
        (data.truncated?' · Increase the limit to show more memories.':'.')+
        (data.relations_truncated?' Additional connections are outside the displayed limit.':'')+
        ' Source evidence is rechecked during recall.';
    } catch(error) {
      if(epoch!==graphEpoch||project!==state.project||error.name==='AbortError')return;
      caption.textContent='Map unavailable: '+error.message;
      host.replaceChildren(button('Retry map',()=>load(true)));
    } finally {if(epoch===graphEpoch)graphRequest=null;}
  }
  choice.addEventListener('change',()=>{graphLimit=Number(choice.value);load();});
  // The panel must be attached before measuring the canvas or accepting a reply.
  queueMicrotask(()=>{if(state.projectInitialized&&result.node.isConnected&&project===state.project)load();});
  return result.node;
}
'''
