"""Run the viewer's actual lead interaction controls with bounded fake requests."""
from pathlib import Path
import shutil
import subprocess
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'app'))
from coordinator_viewer_page import PAGE


@unittest.skipUnless(shutil.which('node'), 'Node is needed for browser control regressions')
class InteractionFlowTests(unittest.TestCase):
    def run_js(self, scenario):
        setup = r'''
const assert = require('node:assert/strict');
const nodes = new Map();
function $(id) {if (!nodes.has(id)) nodes.set(id, {disabled:false,textContent:'',title:'',listeners:{},addEventListener(name, fn){this.listeners[name]=fn;}}); return nodes.get(id);}
let selected='exact-run', epoch=1, attaching=false;
let detail={session:'logical-session',generation:7,provider:{interaction:{kind:'codex_conversation',label:'Open Codex conversation',available:true,reason:'Open this saved conversation in VS Code.',session_id:'exact-conversation',background_id:null}}};
let api=async()=>({ok:true,status:'opened'});
function showError(error){$('error').textContent=error.message;}
'''
        begin = PAGE.index('  function interaction()')
        end = PAGE.index('  function showDetail(', begin)
        setup += PAGE[begin:end]
        begin = PAGE.index("  $('attach').addEventListener('click'")
        end = PAGE.index("  document.addEventListener('visibilitychange'", begin)
        setup += PAGE[begin:end]
        setup += '\n(async()=>{\n' + scenario + '\n})().catch(e=>{console.error(e);process.exitCode=1;});'
        result = subprocess.run([shutil.which('node')], input=setup, text=True,
                                encoding='utf-8', capture_output=True, timeout=10)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_codex_control_opens_exact_bound_conversation_route(self):
        self.run_js(r'''
const requests=[];api=async(path,body)=>{requests.push({path,body});return {ok:true,status:'opened'};};
attachEnabled();assert.equal($('attach').disabled,false);assert.equal($('attach').textContent,'Open Codex conversation');
await $('attach').listeners.click();assert.equal(requests.length,1);
assert.deepEqual(requests[0],{path:'/api/interact',body:{run:'exact-run',session:'logical-session',generation:7,kind:'codex_conversation',session_id:'exact-conversation',background_id:null}});
assert.match($('attach-status').textContent,/VS Code/);assert.equal(attaching,false);
''')

    def test_disabled_action_explains_cause_without_request(self):
        self.run_js(r'''
detail.provider.interaction={kind:'unavailable',label:'Open Claude console',available:false,reason:'The selected Claude session has ended.'};
api=async()=>{throw Error('No request permitted');};attachEnabled();
assert.equal($('attach').disabled,true);assert.match($('attach-help').textContent,/session has ended/);
await $('attach').listeners.click();assert.equal(attaching,false);
''')

    def test_claude_control_uses_existing_background_identity(self):
        self.run_js(r'''
detail.provider.interaction={kind:'claude_console',label:'Open Claude console',available:true,reason:'Existing session',session_id:'saved-session',background_id:'1234abcd'};
let body;api=async(path,payload)=>{assert.equal(path,'/api/interact');body=payload;return {ok:true,status:'opened'};};
await $('attach').listeners.click();assert.equal(body.background_id,'1234abcd');assert.equal(body.kind,'claude_console');
assert.match($('attach-status').textContent,/Console opened/);
''')

    def test_pending_open_cannot_double_launch_and_old_run_cannot_claim_success(self):
        self.run_js(r'''
let finish,calls=0;api=()=>{calls++;return new Promise(resolve=>{finish=resolve;});};
const first=$('attach').listeners.click();await $('attach').listeners.click();assert.equal(calls,1);
epoch++;$('attach-status').textContent='Newly selected run';finish({ok:true,status:'opened'});await first;
assert.equal($('attach-status').textContent,'Newly selected run');assert.equal(attaching,false);
''')

    def test_launch_failure_is_visible_and_can_retry(self):
        self.run_js(r'''
api=async()=>{throw Error('VS Code could not be opened');};await $('attach').listeners.click();
assert.match($('attach-status').textContent,/could not be opened/);assert.equal(attaching,false);assert.equal($('attach').disabled,false);
api=async()=>({ok:true,status:'opened'});await $('attach').listeners.click();assert.match($('attach-status').textContent,/VS Code/);
''')


if __name__ == '__main__':
    unittest.main()
