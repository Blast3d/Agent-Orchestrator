"""Exercise the viewer's actual automatic-startup control without providers."""
from pathlib import Path
import shutil
import subprocess
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'app'))
from coordinator_viewer_page import PAGE


@unittest.skipUnless(shutil.which('node'), 'Node is needed for browser control regressions')
class AutomaticSwitchFlowTests(unittest.TestCase):
    def run_js(self, scenario):
        setup = r'''
const assert = require('node:assert/strict');
const nodes = new Map();
function $(id) {if (!nodes.has(id)) nodes.set(id, {disabled:false,textContent:'',attributes:{},listeners:{},setAttribute(k,v){this.attributes[k]=v;},addEventListener(k,fn){this.listeners[k]=fn;}});return nodes.get(id);}
let api=async()=>{throw Error('Unexpected request');};
'''
        begin = PAGE.index('  let automaticState = null')
        end = PAGE.index('  let leadState = null', begin)
        script = setup + PAGE[begin:end] + '\n(async()=>{\n' + scenario + '\n})().catch(e=>{console.error(e);process.exitCode=1;});'
        result = subprocess.run([shutil.which('node')], input=script, text=True,
                                encoding='utf-8', capture_output=True, timeout=10)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_saved_state_and_manual_scope_are_shown(self):
        self.run_js(r'''
api=async(path,body)=>{assert.equal(path,'/api/automatic-startup');assert.equal(body,undefined);return {enabled:false};};
await loadAutomatic();
assert.equal($('automatic-toggle').attributes['aria-checked'],'false');
assert.equal($('automatic-state').textContent,'Off');assert.equal($('automatic-toggle').disabled,false);
assert.match($('automatic-note').textContent,/Manual runs remain available/);
assert.match($('automatic-note').textContent,/Routine questions, Git operations and small tasks/);
''')

    def test_click_is_single_flight_and_failed_save_is_unavailable(self):
        self.run_js(r'''
let finish,calls=0;
api=async(path,body)=>{if(!body)return {enabled:false};calls++;assert.deepEqual(body,{enabled:true});return new Promise(resolve=>{finish=resolve;});};
await loadAutomatic();const pending=$('automatic-toggle').listeners.click();
await $('automatic-toggle').listeners.click();assert.equal(calls,1);
assert.equal($('automatic-toggle').disabled,true);
assert.equal($('automatic-toggle').attributes['aria-checked'],'false');
finish({enabled:true});await pending;
assert.equal($('automatic-state').textContent,'On');
api=async()=>{throw Error('Save outcome unknown');};await $('automatic-toggle').listeners.click();
assert.equal($('automatic-state').textContent,'Unavailable');assert.equal($('automatic-toggle').disabled,true);
assert.match($('automatic-note').textContent,/Save outcome unknown/);
''')

    def test_stale_status_read_cannot_overwrite_saved_toggle(self):
        self.run_js(r'''
api=async()=>({enabled:false});await loadAutomatic();
let finishRead;
api=async(path,body)=>body?{enabled:true}:new Promise(resolve=>{finishRead=resolve;});
const reading=loadAutomatic();await $('automatic-toggle').listeners.click();
finishRead({enabled:false});await reading;
assert.equal($('automatic-state').textContent,'On');
assert.equal($('automatic-toggle').attributes['aria-checked'],'true');
''')

    def test_page_has_accessible_switch_and_periodic_refresh(self):
        self.assertIn('id="automatic-toggle"', PAGE)
        self.assertIn('aria-labelledby="automatic-label automatic-state"', PAGE)
        self.assertIn('aria-describedby="automatic-note"', PAGE)
        self.assertIn('loadAutomatic(); loadMonitor(); refresh();', PAGE)


if __name__ == '__main__':
    unittest.main()
