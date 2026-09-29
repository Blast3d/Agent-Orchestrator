"""Execute the page's actual JavaScript with a small DOM and controlled requests."""
import json
from pathlib import Path
import re
import shutil
import subprocess
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'app'))
from jev_workflow_page import PAGE
from jev_workflows import workflow_catalog


class WorkflowPageMarkupTests(unittest.TestCase):
    def test_accessible_scoped_page_and_no_html_injection_sinks(self):
        self.assertIn('nonce="__NONCE__"', PAGE)
        self.assertIn('content="__TOKEN__"', PAGE)
        self.assertIn('label for="project"', PAGE)
        self.assertIn('label for="workflow"', PAGE)
        self.assertIn('aria-live="polite"', PAGE)
        self.assertIn("'X-Brain-Token':token", PAGE)
        self.assertIn("credentials:'same-origin'", PAGE)
        for unsafe in ('innerHTML', 'outerHTML', 'insertAdjacentHTML', 'document.write', 'eval('):
            self.assertNotIn(unsafe, PAGE)
        for mutation in ('/api/approve', '/api/forget', '/api/supersede', '/api/relate', '/api/dispatch'):
            self.assertNotIn(mutation, PAGE)


@unittest.skipUnless(shutil.which('node'), 'Node is required for browser flow regressions')
class WorkflowPageFlowTests(unittest.TestCase):
    def run_js(self, scenario):
        before_script, script = PAGE.split('<script nonce="__NONCE__">', 1)
        script = script.split('</script>', 1)[0]
        ids = re.findall(r'\bid="([^"]+)"', before_script)
        prelude = r'''
const assert=require('node:assert/strict');
const registry=new Map();
function removeIds(item){if(item.id)registry.delete(item.id);for(const child of item.children||[])removeIds(child);}
class Element{
 constructor(tag){this.tagName=tag;this.children=[];this.listeners={};this.value='';this.textContent='';this.hidden=false;this.disabled=false;this.checked=false;this.classList={toggle(){}};}
 set id(value){this._id=value;registry.set(value,this);}get id(){return this._id;}
 set innerHTML(value){throw Error('Unsafe HTML assignment');}
 append(...items){for(const item of items){if(this.tagName==='select'&&!this.children.length)this.value=item.value;this.children.push(item);}}
 replaceChildren(...items){for(const child of this.children)removeIds(child);this.children=[];if(this.tagName==='select')this.value='';this.append(...items);}
 addEventListener(kind,fn){this.listeners[kind]=fn;}
 focus(){document.activeElement=this;}
 scrollIntoView(options){this.lastScroll=options;}
}
const document={getElementById:id=>registry.get(id)||null,createElement:tag=>new Element(tag),querySelector:()=>({content:'safe-test-token'})};
const location={hash:'#project=alpha&run=exact-run'};
const history={replaceState(a,b,value){location.hash=value;}};
const window={addEventListener(){}};
const requests=[];
let fetch=async(path,options)=>{requests.push({path,options});return {ok:true,status:200,json:async()=>({workflows:CATALOGUE,configuration:{enabled:true,purposes:CATALOGUE.map(row=>row.workflow),cache_enabled:true}})};};
function rendered(item){return item.textContent+(item.children||[]).map(rendered).join(' ');}
function memory(id,extra={}){return {id,project_id:'alpha',user_id:'local',kind:'fact',status:'active',validity_state:'current',reviewer:'Tester',reviewed_at:'2026-09-25',title:'Reviewed '+id,content:'Evidence '+id,...extra};}
function choose(kind){$('workflow').value=kind;renderFields();updateButtons();}
function selectEvidence(){state.memories=[memory('a'.repeat(32))];state.selected=new Set([state.memories[0].id]);renderMemories();updateSpecialSelections();updateButtons();}
'''
        initialize = '\nconst CATALOGUE=' + json.dumps(workflow_catalog()) + ';\n'
        initialize += '\nfor(const id of ' + json.dumps(ids) + ") {const el=new Element(id==='workflow'?'select':'div');el.id=id;}\n"
        executable = prelude + initialize + script + '\n(async()=>{await new Promise(resolve=>setImmediate(resolve));\n'
        executable += scenario + '\n})().catch(error=>{console.error(error);process.exitCode=1;});'
        result = subprocess.run([shutil.which('node')], input=executable, text=True,
                                encoding='utf-8', capture_output=True, timeout=10)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_startup_only_loads_catalogue_and_preserves_project_run(self):
        self.run_js(r'''
assert.equal(requests.length,1);assert.equal(requests[0].path,'/api/jev/workflows?project_id=alpha');
assert.equal(requests[0].options.headers['X-Brain-Token'],'safe-test-token');
assert.equal(state.project,'alpha');assert.equal(state.run,'exact-run');assert.equal(state.workflows.length,14);
assert.equal($('back').href,'/#project=alpha&run=exact-run');assert.equal($('ask').disabled,true);
choose('handoff');assert.equal($('field-run_id').value,'exact-run');
for(const row of CATALOGUE){choose(row.workflow);assert($('description').textContent.length);}
''')

    def test_hostile_strings_render_as_text_and_other_scope_is_filtered(self):
        self.run_js(r'''
const hostile='<img src=x onerror=alert(1)>';
api=async()=>[memory('a'.repeat(32),{title:hostile,content:hostile}),memory('b'.repeat(32),{project_id:'beta'}),memory('c'.repeat(32),{user_id:'other'}),memory('d'.repeat(32),{validity_state:'scheduled'}),memory('e'.repeat(32),{reviewer:null})];
await loadMemories();assert.equal(state.memories.length,1);assert(rendered($('memories')).includes(hostile));
renderResult({status:'ok',reason:hostile,judgments:[{question_id:'conflict_'+hostile,type:'choice',status:'review_required',choice:hostile,confidence:.9}],suggestions:[{action:hostile,choice:hostile}],decision:{provider_calls:1,cost_usd:.001,cache:{status:'miss'}}});
assert(rendered($('result-body')).includes(hostile));assert.equal($('result').hidden,false);
assert(rendered($('result-body')).includes('Lead review required'));assert(!registry.has('onerror'));
''')

    def test_seventh_selection_is_blocked_and_scoped_payload_is_explicit(self):
        self.run_js(r'''
state.memories=Array.from({length:7},(_,i)=>memory(String(i).repeat(32)));state.selected=new Set(state.memories.slice(0,6).map(row=>row.id));renderMemories();
const last=$('memories').children[6].children[0].children[0];assert.equal(last.disabled,true);last.checked=true;last.listeners.change();assert.equal(state.selected.size,6);
choose('memory_support');$('field-claim').value='A reviewed claim.';const payload=buildPayload();assert.equal(payload.memory_ids.length,6);assert.equal(payload.claim,'A reviewed claim.');assert.equal($('ask').disabled,false);
''')

    def test_project_change_clears_selections_and_ignores_old_memory_response(self):
        self.run_js(r'''
selectEvidence();let complete;api=()=>new Promise(resolve=>complete=resolve);const pending=loadMemories();
syncScope('beta','next-run');assert.equal(state.selected.size,0);assert.equal(state.memories.length,0);assert.equal($('result').hidden,true);
complete([memory('a'.repeat(32))]);await pending;assert.equal(state.memories.length,0);assert.equal(state.project,'beta');assert.equal($('back').href,'/#project=beta&run=next-run');
''')

    def test_pending_advice_disables_button_and_cannot_cross_scope(self):
        self.run_js(r'''
choose('memory_support');selectEvidence();$('field-claim').value='A reviewed claim.';
let complete,request;api=(path,body)=>{request={path,body};return new Promise(resolve=>complete=resolve);};
const pending=ask({preventDefault(){}});assert.equal($('ask').disabled,true);assert.equal(state.pending,true);assert.equal(request.path,'/api/jev/workflow');assert.equal(request.body.project_id,'alpha');
syncScope('beta','next-run');complete({project_id:'alpha',workflow:'memory_support',status:'ok',reason:'Old private result',decision:{provider_calls:1}});await pending;
assert.equal($('result').hidden,true);assert(!rendered($('result-body')).includes('Old private result'));assert.equal(state.pending,false);
''')

    def test_wrong_scope_receipt_is_rejected(self):
        self.run_js(r'''
choose('memory_support');selectEvidence();$('field-claim').value='Claim';api=async()=>({project_id:'beta',workflow:'memory_support',status:'ok',reason:'wrong scope'});
await ask({preventDefault(){}});assert.equal($('result').hidden,true);assert($('notice').textContent.includes('different scope'));assert.equal(state.pending,false);assert($('request-status').textContent.includes('different scope'));assert.equal(document.activeElement,$('request-status'));
''')

    def test_plain_language_special_fields_make_typed_payloads(self):
        self.run_js(r'''
choose('skills');$('field-task').value='Review the code';$('field-catalogue').value='review | Inspect changed code\nresearch | Locate primary sources';let payload=buildPayload();assert.equal(payload.catalogue.review,'Inspect changed code');
choose('event');$('field-event').value='A queue item arrived';$('field-handlers').value='queue_item | Append an approved item';payload=buildPayload();assert.equal(payload.handlers.queue_item,'Append an approved item');
choose('recover');selectEvidence();$('field-task').value='Repair';$('field-error').value='Timeout';$('field-current_versions').value='python | 3.12';state.versionFields.values().next().value.value='python | 3.11';payload=buildPayload();assert.equal(payload.current_versions.python,'3.12');assert.equal(payload.memory_versions['a'.repeat(32)].python,'3.11');
choose('stale');payload=buildPayload();assert(!Number.isNaN(Date.parse(payload.as_of)));
choose('evidence_review');$('field-query').value='What changed?';payload=buildPayload();assert.equal(payload.query,'What changed?');assert(!('claim' in payload));
''')

    def test_citation_quote_preserves_whitespace_and_requires_selected_source(self):
        self.run_js(r'''
choose('citations');selectEvidence();$('field-answer').value='Supported answer';const citation=state.citations[0];citation.select.value='a'.repeat(32);citation.quote.value='  exact\nquote  ';citation.claim.value='Supported claim';let payload=buildPayload();assert.equal(payload.citations[0].quote,'  exact\nquote  ');assert.equal(payload.citations[0].claim,'Supported claim');
citation.select.value='b'.repeat(32);assert.throws(()=>buildPayload(),/selected memory/);
assert.throws(()=>parseRows('same | one\nsame | two','Catalogue'),/unique ID/);
''')

    def test_judgment_targets_show_exact_titles_ids_and_bounded_safe_fields(self):
        self.run_js(r'''
const first='a'.repeat(32),second='b'.repeat(32),hostile='<img src=x onerror=alert(1)>';
state.memories=[memory(first,{title:hostile}),memory(second,{title:'Other source'})];state.selected=new Set([first,second]);
renderResult({status:'ok',reason:'Review the pair.',judgments:[{question_id:'q0',type:'choice',status:'review_required',choice:'supports',confidence:.9,target:{from:first,to:second,pair_id:'pair-1',aspect:'relation_choice',arbitrary:'Do not render this unrecognized field'}}],decision:{provider_calls:1}});
let text=rendered($('result-body'));assert(text.includes(hostile));assert(text.includes(first));assert(text.includes('Other source'));assert(text.includes(second));assert(text.includes('From memory'));assert(text.includes('pair-1'));assert(!text.includes('Do not render this unrecognized field'));
const target=renderTarget({memory_ids:[first,second],available_ids:Array.from({length:20},(_,i)=>'choice-'+i),target_id:'x'.repeat(500),metric:'relevance'});text=rendered(target);assert(text.includes(first));assert(text.includes(second));assert(text.includes('choice-11'));assert(!text.includes('choice-12'));assert(!text.includes('x'.repeat(201)));assert.equal(renderTarget({extra:{html:hostile}}),null);
''')

    def test_scope_changes_keep_active_decision_count_until_old_request_settles(self):
        self.run_js(r'''
choose('memory_support');selectEvidence();$('field-claim').value='Original claim';let finish,calls=0;api=()=>{calls++;return new Promise(resolve=>finish=resolve);};
const first=ask({preventDefault(){}});assert.equal(state.activeDecisions,1);syncScope('beta','next-run');
state.workflows=CATALOGUE;state.configuration={enabled:true,purposes:CATALOGUE.map(row=>row.workflow)};choose('skills');$('field-task').value='Prepare new request';$('field-catalogue').value='review | Review source';
assert.equal(state.pending,false);assert.equal($('ask').disabled,true);assert.equal($('load').disabled,false);assert.equal($('request-fields').disabled,false);assert($('working').textContent.includes('does not cancel provider work'));
await ask({preventDefault(){}});assert.equal(calls,1);assert.equal(state.activeDecisions,1);
finish({project_id:'alpha',workflow:'memory_support',status:'ok',reason:'Old scoped result'});await first;
assert.equal(state.activeDecisions,0);assert.equal($('ask').disabled,false);assert.equal($('result').hidden,true);assert.equal($('field-task').value,'Prepare new request');
''')

    def test_workflow_change_blocks_overlapping_decision_and_keeps_stale_result_hidden(self):
        self.run_js(r'''
choose('memory_support');selectEvidence();$('field-claim').value='Original claim';let finish,calls=0;api=()=>{calls++;return new Promise(resolve=>finish=resolve);};
const first=ask({preventDefault(){}});$('workflow').value='skills';$('workflow').listeners.change();$('field-task').value='Review';$('field-catalogue').value='review | Inspect source';
assert.equal(state.activeDecisions,1);assert.equal(state.pending,false);await ask({preventDefault(){}});assert.equal(calls,1);
finish({project_id:'alpha',workflow:'memory_support',status:'ok',reason:'Old workflow result'});await first;assert.equal(state.activeDecisions,0);assert.equal($('result').hidden,true);assert.equal($('ask').disabled,false);
''')

    def test_catalogue_failure_clears_old_choices_and_direct_submit_is_blocked(self):
        self.run_js(r'''
assert(state.workflows.length);syncScope('beta','next-run');assert.equal(state.workflows.length,0);assert.equal($('workflow').children.length,0);let calls=0;api=async()=>{calls++;throw new Error('Catalogue unavailable');};
await loadCatalog();assert.equal(state.workflows.length,0);assert.equal($('workflow').children.length,0);assert.equal($('ask').disabled,true);assert.equal($('fields').children.length,0);assert.equal($('evidence-hint').textContent,'');
await ask({preventDefault(){}});assert.equal(calls,1);assert($('notice').textContent.includes('not available'));
''')

    def test_non_json_errors_report_status_without_echoing_response_body(self):
        self.run_js(r'''
for(const status of [503,504,200]){fetch=async()=>({ok:status===200,status,json:async()=>{throw new SyntaxError('<html>private error body</html>');}});await assert.rejects(()=>api('/api/jev/workflow',{}),error=>error.message.includes('HTTP '+status)&&!error.message.includes('private error body'));
}
fetch=async()=>{const error=new Error('Aborted');error.name='AbortError';throw error;};await assert.rejects(()=>api('/api/jev/workflow',{}),/Provider work may still finish/);
''')

    def test_memory_load_reentry_is_guarded_and_workflow_change_keeps_local_result(self):
        self.run_js(r'''
let finish,calls=0;api=()=>{calls++;return new Promise(resolve=>finish=resolve);};const first=loadMemories();await loadMemories();assert.equal(calls,1);assert.equal(state.loading,true);assert.equal($('load').disabled,true);
const scopeGeneration=state.scopeGeneration;$('workflow').value='skills';$('workflow').listeners.change();assert.equal(state.scopeGeneration,scopeGeneration);assert.equal(state.loading,true);await loadMemories();assert.equal(calls,1);
finish([memory('a'.repeat(32))]);await first;assert.equal(state.memories.length,1);assert.equal(state.loading,false);assert.equal($('workflow').value,'skills');assert.equal($('load').disabled,false);
''')

    def test_render_fields_already_clears_recorded_versions_on_workflow_change(self):
        self.run_js(r'''
choose('recover');selectEvidence();state.versionFields.values().next().value.value='python | 3.12';assert.equal(state.versionFields.size,1);
$('workflow').value='memory_support';$('workflow').listeners.change();assert.equal(state.versionFields.size,0);$('field-claim').value='Only a claim';const payload=buildPayload();assert(!('memory_versions' in payload));assert(!('current_versions' in payload));
''')

    def test_later_catalogue_refresh_wins_within_same_scope(self):
        self.run_js(r'''
const completions=[];api=()=>new Promise(resolve=>completions.push(resolve));const first=loadCatalog(),second=loadCatalog();
completions[1]({workflows:CATALOGUE,configuration:{enabled:true,purposes:['memory_support'],cache_enabled:true}});await second;
completions[0]({workflows:[],configuration:{enabled:false,purposes:[],cache_enabled:false}});await first;
assert.equal(state.workflows.length,14);assert.equal(state.configuration.enabled,true);assert.equal(state.configuration.cache_enabled,true);
''')


    def test_zero_score_explains_the_scale_without_endorsing_a_candidate(self):
        self.run_js(r'''
renderResult({status:'ok',judgments:[{question_id:'recovery',type:'score',status:'review_required',score:0,confidence:.99,criteria:['Irrelevant to this failure','Directly useful fix'],direction:'lower_endpoint',is_endorsement:false}],decision:{provider_calls:0}});
const text=rendered($('result-body'));assert(text.includes('0 = Irrelevant to this failure'));assert(text.includes('1 = Directly useful fix'));assert(text.includes('not an endorsement'));
''')


if __name__ == '__main__':
    unittest.main()
