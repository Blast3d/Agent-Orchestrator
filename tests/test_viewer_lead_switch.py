"""Run the viewer's actual lead-switch script against fake requests (no server, no model)."""
from pathlib import Path
import shutil
import subprocess
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'app'))
from coordinator_viewer_page import PAGE

STATE = r'''{lead:'astra',runner_up:'claude',codex_lead:'astra',options:[
 {id:'claude',label:'Claude',model_label:'Opus 5.5',available:true},
 {id:'astra',label:'ASTRA',model_label:'GPT-6-Astra',available:true},
 {id:'sol',label:'Sol',model_label:'GPT-6-Sol',available:true}]}'''


@unittest.skipUnless(shutil.which('node'), 'Node is needed for browser control regressions')
class LeadSwitchFlowTests(unittest.TestCase):
    def run_js(self, scenario):
        setup = r'''
const assert = require('node:assert/strict');
const nodes = new Map();
function $(id) {if (!nodes.has(id)) nodes.set(id, {id,disabled:false,hidden:false,checked:false,textContent:'',listeners:{},addEventListener(name, fn){this.listeners[name]=fn;}}); return nodes.get(id);}
let api=async()=>{throw Error('No request expected');};
'''
        begin = PAGE.index('  let leadState = null')
        end = PAGE.index('  let services = [];', begin)
        setup += PAGE[begin:end]
        setup += '\n(async()=>{\n' + scenario + '\n})().catch(e=>{console.error(e);process.exitCode=1;});'
        result = subprocess.run([shutil.which('node')], input=setup, text=True,
                                encoding='utf-8', capture_output=True, timeout=10)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_loads_selection_and_explains_scope(self):
        self.run_js(r'''
api=async(path,body)=>{assert.equal(path,'/api/lead');assert.equal(body,undefined);return ''' + STATE + r''';};
await loadLead();
assert.equal($('lead-astra').checked,true);assert.equal($('lead-sol').checked,false);
assert.equal($('lead-sol-model').textContent,'GPT-6-Sol');assert.equal($('lead-claude').disabled,false);
assert.equal($('lead-save').hidden,true);assert.equal($('lead-save').disabled,true);
assert.match($('lead-note').textContent,/New runs start with ASTRA/);assert.match($('lead-note').textContent,/Runner-up.*Claude/);
assert.match($('lead-note').textContent,/underway keep their lead/);
''')

    def test_moving_through_options_saves_nothing_until_save(self):
        self.run_js(r'''
const requests=[];
api=async(path,body)=>{requests.push({path,body});return body?Object.assign(''' + STATE + r''',{lead:body.lead,runner_up:'claude'}):''' + STATE + r''';};
await loadLead();
// Arrow keys fire a change for every option passed over.
$('lead-claude').listeners.change();$('lead-sol').listeners.change();
assert.equal(requests.length,1);
assert.equal($('lead-sol').checked,true);assert.equal($('lead-save').hidden,false);assert.equal($('lead-save').disabled,false);
assert.match($('lead-note').textContent,/Save to start new runs with Sol/);assert.match($('lead-note').textContent,/runner-up at 5% usage: Claude/);
await $('lead-save').listeners.click();
assert.equal(requests.length,2);assert.deepEqual(requests[1],{path:'/api/lead',body:{lead:'sol'}});
assert.equal($('lead-save').hidden,true);assert.match($('lead-note').textContent,/New runs start with Sol/);
''')

    def test_staging_claude_names_the_codex_runner_up_and_save_is_single_flight(self):
        self.run_js(r'''
let finish,calls=0;
api=async(path,body)=>{if(!body)return Object.assign(''' + STATE + r''',{codex_lead:'sol'});calls++;return new Promise(r=>{finish=r;});};
await loadLead();$('lead-claude').listeners.change();
assert.match($('lead-note').textContent,/runner-up at 5% usage: Sol/);
const pending=$('lead-save').listeners.click();await $('lead-save').listeners.click();
assert.equal(calls,1);assert.equal($('lead-save').disabled,true);assert.equal($('lead-astra').disabled,true);
finish(Object.assign(''' + STATE + r''',{lead:'claude',runner_up:'sol'}));await pending;
assert.equal($('lead-claude').checked,true);assert.equal($('lead-astra').disabled,false);
''')

    def test_failed_save_keeps_the_pick_and_shows_reason(self):
        self.run_js(r'''
api=async(path,body)=>{if(!body)return ''' + STATE + r''';throw Error('Claude Fable is paused by the user');};
await loadLead();$('lead-claude').listeners.change();await $('lead-save').listeners.click();
assert.equal($('lead-claude').checked,true);assert.equal($('lead-save').disabled,false);
assert.match($('lead-note').textContent,/paused/);assert.match($('lead-note').textContent,/Save to start new runs with Claude/);
''')

    def test_unavailable_claude_option_is_disabled_with_reason(self):
        self.run_js(r'''
const state=''' + STATE + r''';state.options[0]=Object.assign(state.options[0],{available:false,model_label:'unavailable',reason:'Claude Fable is paused'});
api=async()=>state;await loadLead();
assert.equal($('lead-claude').disabled,true);assert.equal($('lead-claude-model').textContent,'unavailable');
assert.match($('lead-note').textContent,/Claude is unavailable: Claude Fable is paused/);
''')

    def test_page_has_labelled_radio_group_save_button_and_shared_allowance_note(self):
        for lead in ('claude', 'astra', 'sol'):
            self.assertIn(f'id="lead-{lead}" type="radio" name="lead"', PAGE)
        self.assertIn('<legend class="sr-only">Lead orchestrator</legend>', PAGE)
        self.assertIn('id="lead-save"', PAGE)
        self.assertIn('ASTRA and Sol share one Codex allowance', PAGE)
        self.assertIn("$('owner').textContent = data.lead_label || label(data.owner) || 'Unassigned';", PAGE)


if __name__ == '__main__':
    unittest.main()
