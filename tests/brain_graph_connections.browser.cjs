const assert = require('node:assert/strict');
const {spawnSync} = require('node:child_process');
const fs = require('node:fs');
const path = require('node:path');
const {chromium} = require(process.env.PLAYWRIGHT_MODULE || 'playwright');
const repo = path.resolve(__dirname, '..');
const code = spawnSync('python', ['-c', 'import sys;sys.path.insert(0,"app");import brain_graph_ui;sys.stdout.reconfigure(encoding="utf-8");print(brain_graph_ui.SCRIPT)'], {cwd:repo,encoding:'utf8'});
if(code.status !== 0)throw new Error(code.stderr);
const tests=[];
(async()=>{
  const browser=await chromium.launch({channel:'msedge',headless:true});
  const page=await browser.newPage({viewport:{width:1100,height:1100}});
  const errors=[];page.on('pageerror',error=>errors.push(error.message));
  await page.setContent('<style>[hidden]{display:none!important}body{margin:20px;background:#101722;color:white}button,input,select,textarea{font:inherit}</style><main></main>');
  await page.addScriptTag({content:code.stdout});
  await page.evaluate(()=>{
    const proto=CanvasRenderingContext2D.prototype,arc=proto.arc,clear=proto.fillRect;
    proto.fillRect=function(...args){if(args[0]===0&&args[1]===0)window.circles=[];return clear.apply(this,args);};
    proto.arc=function(x,y,r,...rest){const m=this.getTransform();window.circles.push({x:(m.a*x+m.c*y+m.e)/devicePixelRatio,y:(m.b*x+m.d*y+m.f)/devicePixelRatio});return arc.call(this,x,y,r,...rest);};
    window.calls=[];window.opens=[];window.failSave=false;window.deferWrite=false;window.pendingWrite=null;window.loads={};
    window.mount=()=>{
      window.controller?.destroy();
      const data={project_id:'alpha',nodes:[{id:'a',title:'A source',kind:'fact'},{id:'b',title:'B target',kind:'procedure'},{id:'c',title:'C target',kind:'episode'}],relations:[]};
      window.controller=createBrainGraph(data,id=>window.opens.push(id),{
        projects:['alpha','beta','gamma'],
        onCreateRelation:async(payload,{signal})=>{window.calls.push(payload);if(window.failSave)throw new Error('Fixture save rejected');if(window.deferWrite)return new Promise(resolve=>{window.pendingWrite={resolve,signal};});return {};},
        onLoadProjectGraph:(project,{signal})=>new Promise(resolve=>{window.loads[project]={resolve,signal};}),
        onCreateCrossProjectReference:async(payload)=>{window.calls.push(payload);return {reference:{id:'ref-b',title:'Linked beta fact',kind:'fact'},relation:{}};}
      });document.querySelector('main').replaceChildren(window.controller.element);
    };window.mount();
  });
  const canvas=page.locator('canvas');
  await page.waitForFunction(()=>window.circles?.length===3);
  async function circle(index){const rect=await canvas.boundingBox();const point=await page.evaluate(i=>window.circles[i],index);return {x:rect.x+point.x,y:rect.y+point.y};}
  async function connectDrag(index){const handle=page.getByRole('button',{name:'Connect selected memory',exact:true});await handle.scrollIntoViewIfNeeded();const rect=await handle.boundingBox(),to=await circle(index);await page.mouse.move(rect.x+14,rect.y+14);await page.mouse.down();await page.mouse.move(to.x,to.y,{steps:8});await page.mouse.up();}
  await connectDrag(1);
  await assert.equal(await page.getByLabel('Target memory',{exact:true}).inputValue(),'b');
  await page.getByLabel('Relationship',{exact:true}).selectOption('supports');
  await page.getByRole('button',{name:'Save connection',exact:true}).click();
  await page.getByText('Connection saved.',{exact:true}).waitFor();
  assert.deepEqual(await page.evaluate(()=>window.calls.map(c=>[c.sourceId,c.targetId,c.relation])),[['a','b','supports']]);
  assert.equal(await page.locator('[data-graph-edge-count]').getAttribute('data-graph-edge-count'),'1');tests.push('pointer drag creates reviewed typed relation');
  const before=await circle(2);await page.mouse.move(before.x,before.y);await page.mouse.down();await page.mouse.move(before.x+35,before.y+20,{steps:5});await page.mouse.up();
  const after=await circle(2);assert.ok(after.x>before.x+25);assert.equal(await page.evaluate(()=>window.calls.length),1);tests.push('ordinary node drag remains independent');
  await page.getByRole('button',{name:'Connect memory',exact:true}).click();await page.getByLabel('Target memory',{exact:true}).selectOption('a');await page.keyboard.press('Escape');
  assert.equal(await page.getByRole('region',{name:'Connect memories'}).isVisible(),false);assert.equal(await page.evaluate(()=>window.calls.length),1);tests.push('button keyboard alternative and Escape cancellation');
  await page.getByRole('button',{name:'3D view',exact:true}).click();const box=await canvas.boundingBox();
  await page.mouse.move(box.x+15,box.y+35);await page.mouse.down();await page.mouse.move(box.x+70,box.y+75,{steps:5});await page.mouse.up();
  assert.notEqual(await canvas.getAttribute('data-camera-yaw'),'0');tests.push('3D globe rotation retained');
  await page.getByRole('button',{name:'3D view',exact:true}).click();
  await page.getByRole('button',{name:'Connect memory',exact:true}).click();await page.getByLabel('Target memory',{exact:true}).selectOption('a');
  await page.evaluate(()=>window.failSave=true);await page.getByRole('button',{name:'Save connection',exact:true}).click();
  await page.getByText('Connection not confirmed: Fixture save rejected',{exact:true}).waitFor();assert.equal(await page.getByRole('button',{name:'Save connection',exact:true}).isEnabled(),true);tests.push('failed save visible and retryable');
  await page.evaluate(()=>window.failSave=false);
  await page.getByLabel('Connection project',{exact:true}).selectOption('beta');
  await page.getByLabel('Connection project',{exact:true}).selectOption('gamma');
  assert.equal(await page.evaluate(()=>window.loads.beta.signal.aborted),true);
  await page.evaluate(()=>window.loads.beta.resolve({project_id:'beta',nodes:[{id:'wrong',title:'Wrong stale target'}],total_nodes:1}));
  await page.evaluate(()=>window.loads.gamma.resolve({project_id:'gamma',nodes:[{id:'g',title:'Gamma selected evidence'}],total_nodes:1}));
  await page.getByLabel('Target memory',{exact:true}).selectOption('g');assert.equal(await page.getByLabel('Target memory',{exact:true}).locator('option').count(),2);tests.push('project switch ignores stale foreign target response');
  await page.getByRole('button',{name:'Reuse and connect memory',exact:true}).click();
  await page.getByText('Add at least 20 characters explaining why this memory belongs in this project.',{exact:true}).waitFor();
  await page.getByLabel('Why reuse this memory?',{exact:true}).fill('This original evidence explains our shared integration.');
  await page.getByRole('button',{name:'Reuse and connect memory',exact:true}).click();
  await page.getByText('Memory reused and connected. The original evidence stays linked; recall rechecks it.',{exact:true}).waitFor();
  assert.equal(await page.locator('[data-graph-node-count]').getAttribute('data-graph-node-count'),'4');
  assert.equal(await page.evaluate(()=>window.calls.at(-1).targetProjectId),'gamma');assert.equal(await page.evaluate(()=>window.calls.at(-1).reuse),true);tests.push('explicit cross-project consent note and reference node edge');
  await page.setViewportSize({width:400,height:900});await page.getByRole('button',{name:'Connect memory',exact:true}).click();
  assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),true);tests.push('mobile controls fit without horizontal overflow');
  await page.screenshot({path:path.join(repo,'runtime/graph-wire-mobile.png'),fullPage:true});
  await page.setViewportSize({width:1100,height:1100});await page.evaluate(()=>window.mount());await page.waitForFunction(()=>window.circles?.length===3);
  await page.getByRole('button',{name:'Connect memory',exact:true}).click();await page.getByLabel('Target memory',{exact:true}).selectOption('b');
  await page.evaluate(()=>window.deferWrite=true);await page.getByRole('button',{name:'Save connection',exact:true}).click();
  await page.waitForFunction(()=>window.pendingWrite);await page.evaluate(()=>{window.controller.destroy();window.pendingWrite.resolve({});});
  assert.equal(await page.evaluate(()=>window.pendingWrite.signal.aborted),true);assert.equal(await page.locator('[data-graph-edge-count]').getAttribute('data-graph-edge-count'),'0');tests.push('destroy aborts write request and suppresses stale success');
  assert.deepEqual(errors,[]);
  console.log(JSON.stringify({passed:tests.length,tests,pageErrors:errors},null,2));await browser.close();
})().catch(error=>{console.error(error);process.exit(1);});
