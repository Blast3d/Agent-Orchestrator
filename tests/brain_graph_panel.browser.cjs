const assert=require('node:assert/strict');
const {spawnSync}=require('node:child_process');
const path=require('node:path');
const {chromium}=require(process.env.PLAYWRIGHT_MODULE||'playwright');
const repo=path.resolve(__dirname,'..');
const read=name=>{const result=spawnSync('python',['-c',`import sys;sys.path.insert(0,"app");import ${name};sys.stdout.reconfigure(encoding="utf-8");print(${name}.SCRIPT)`],{cwd:repo,encoding:'utf8'});if(result.status)throw Error(result.stderr);return result.stdout;};
(async()=>{
 const browser=await chromium.launch({channel:'msedge',headless:true});
 const page=await browser.newPage({viewport:{width:1100,height:1000}}),errors=[],posts=[];
 page.on('pageerror',e=>errors.push(e.message));
 let delayWrite=false,pendingWrite,delayForeign=false,pendingForeign;
 const graph=project=>({project_id:project,total_nodes:project==='alpha'?2:3,nodes:project==='alpha'?[{id:'a',title:'Alpha source',kind:'fact'},{id:'b',title:'Alpha target',kind:'fact'}]:[{id:'z',title:'Beta original',kind:'fact'},{id:'ref',title:'Existing linked copy',kind:'fact',reference:{project_id:'gamma',memory_id:'g'}},{id:'hostile',title:'<img src=x onerror=alert(1)>',kind:'fact'}],relations:[]});
 await page.route('http://brain.test/**',async route=>{
  const request=route.request(),url=new URL(request.url());
  if(url.pathname==='/')return route.fulfill({contentType:'text/html',body:'<style>[hidden]{display:none!important}body{background:#101722;color:white}</style><main></main>'});
  if(url.pathname==='/api/graph'){
   if(delayForeign&&url.searchParams.get('project_id')==='beta')await new Promise(r=>{pendingForeign=r;});
   return route.fulfill({contentType:'application/json',body:JSON.stringify(graph(url.searchParams.get('project_id')))}).catch(()=>{});
  }
  if(request.method()==='POST'){
   posts.push({path:url.pathname,body:request.postDataJSON()});
   if(delayWrite)await new Promise(r=>{pendingWrite=r;});
   return route.fulfill({contentType:'application/json',body:JSON.stringify(url.pathname==='/api/links'?{reference:{id:'new-ref',kind:'fact',title:'Linked beta original',source:{type:'memory_reference',project_id:'beta',memory_id:'z'}}}: {id:'saved'})}).catch(()=>{});
  }
  return route.fulfill({status:404,body:'{}'});
 });
 await page.goto('http://brain.test/');
 await page.addScriptTag({content:`
 const state={project:'alpha',projectInitialized:true,status:{projects:['alpha','beta']},pending:0};const token='test-token';const kinds={fact:'Fact',preference:'Preference',procedure:'Procedure',episode:'Episode'};
 function el(tag,cls,text){const n=document.createElement(tag);if(cls)n.className=cls;if(text)n.textContent=text;return n;}
 function button(label,fn){const n=el('button','',label);n.addEventListener('click',fn);return n;}
 function panel(title,description){const node=el('section'),header=el('header');header.append(el('h2','',title),el('p','',description));node.append(header);return {node,header};}
 function showDetail(id){window.detail=id;}function renderRefreshButton(){window.refreshMarked=true;}
 ${read('brain_graph_ui')}
 ${read('brain_graph_panel')}
 window.mount=(project)=>{disposeGraphView();graphCache=null;state.project=project;document.querySelector('main').replaceChildren(graphPanel());};window.mount('alpha');
 `});
 await page.locator('canvas').waitFor();
 await page.getByRole('button',{name:'Connect memory',exact:true}).click();
 await page.getByLabel('Target memory',{exact:true}).selectOption('b');await page.getByRole('button',{name:'Save connection',exact:true}).click();
 await page.getByText('Connection saved.',{exact:true}).waitFor();
 assert.deepEqual(posts[0],{path:'/api/relate',body:{project_id:'alpha',memory_id:'a',target_id:'b',relation:'related_to',actor:'Local user'}});
 assert.equal(await page.evaluate(()=>window.refreshMarked),true);
 await page.getByRole('button',{name:'Connect memory',exact:true}).click();await page.getByLabel('Connection project',{exact:true}).selectOption('beta');
 await page.getByLabel('Target memory',{exact:true}).selectOption('z');assert.equal(await page.getByLabel('Target memory',{exact:true}).locator('option').count(),3);
 assert.equal(await page.locator('img').count(),0);assert.equal(await page.getByText('Linked copies are excluded; select their original project.',{exact:false}).count(),1);
 await page.getByLabel('Why reuse this memory?',{exact:true}).fill('Shared evidence needed by this approved project.');await page.getByRole('button',{name:'Reuse and connect memory',exact:true}).click();
 await page.getByText('Memory reused and connected. The original evidence stays linked; recall rechecks it.',{exact:true}).waitFor();
 assert.equal(posts[1].path,'/api/links');assert.equal(posts[1].body.project_id,'alpha');assert.equal(posts[1].body.target_project_id,'beta');assert.equal(posts[1].body.reuse,true);
 // The main project changes while a write is pending; no stale success or node is attached.
 delayWrite=true;await page.getByRole('button',{name:'Connect memory',exact:true}).click();await page.getByLabel('Target memory',{exact:true}).selectOption('b');await page.getByRole('button',{name:'Save connection',exact:true}).click();
 await page.waitForTimeout(20);assert.ok(pendingWrite);await page.evaluate(()=>window.mount('beta'));pendingWrite();await page.locator('canvas[data-selected-memory-id="z"]').waitFor();
 assert.equal(await page.locator('[data-graph-node-count]').getAttribute('data-graph-node-count'),'3');assert.equal(posts.at(-1).body.project_id,'alpha');assert.equal(await page.getByText('Connection saved.',{exact:true}).count(),0);
 // A project browse response that arrives after panel disposal is equally ignored.
 delayWrite=false;await page.evaluate(()=>window.mount('alpha'));await page.locator('canvas[data-selected-memory-id="a"]').waitFor();
 delayForeign=true;await page.getByRole('button',{name:'Connect memory',exact:true}).click();await page.getByLabel('Connection project',{exact:true}).selectOption('beta');
 await page.waitForTimeout(20);assert.ok(pendingForeign);await page.evaluate(()=>{disposeGraphView();document.querySelector('main').replaceChildren();});pendingForeign();
 await page.waitForTimeout(40);assert.equal(await page.locator('canvas').count(),0);assert.deepEqual(errors,[]);
 console.log(JSON.stringify({passed:5,checks:['same-project API body and cache invalidation','explicit cross-project API body','reference filtering and hostile title literal rendering','main project switch suppresses in-flight write reply','destroy cancels foreign browse reply'],pageErrors:errors},null,2));
 await browser.close();
})().catch(error=>{console.error(error);process.exit(1);});
