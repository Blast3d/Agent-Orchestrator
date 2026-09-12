"""Execute the dashboard's actual refresh functions against controlled async reads."""
import json
from pathlib import Path
import shutil
import subprocess
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'app'))
from brain_dashboard import PAGE


def function_source(name, next_name):
    begin = PAGE.index('function ' + name + '(')
    if PAGE[begin - 6:begin] == 'async ':
        begin -= 6
    end = PAGE.index('function ' + next_name + '(', begin + 10)
    if PAGE[end - 6:end] == 'async ':
        end -= 6
    return PAGE[begin:end]


@unittest.skipUnless(shutil.which('node'), 'Node is required to execute browser flow regressions')
class RefreshFlowTests(unittest.TestCase):
    def run_js(self, scenario):
        script = r'''
const assert = require('node:assert/strict');
const nodes = new Map();
function $(id) { if (!nodes.has(id)) nodes.set(id, {disabled:false,open:false,textContent:'',replaceChildren(){}}); return nodes.get(id); }
const state = {sequence:0, project:'alpha', projectInitialized:true, run:'exact-run', cursor:1, pending:0, services:[], snapshot:{memories:[]}, searchResults:null, view:'overview', polling:false};
const document = {hidden:false, activeElement:null};
const window = {getSelection:()=>({isCollapsed:true})};
const location = {hash:'#project=new-empty-project&run=exact-run'};
const history = {replaceState(){}};
function el() {return {};}
function projectQuery(){return '?project_id='+encodeURIComponent(state.project);}
function loadServices(){}
function saveScope(){}
function renderRefreshButton(){}
function render(){}
function notice(){}
function connection(value){$('connection').textContent=value||'connected';}
let api;
'''
        script += function_source('safeToApply', 'saveScope')
        script += function_source('poll', 'refresh')
        script += function_source('refresh', 'memories')
        script += '\n(async()=>{\n' + scenario + '\n})().catch(e=>{console.error(e);process.exitCode=1;});'
        result = subprocess.run([shutil.which('node')], input=script, text=True,
                                encoding='utf-8', capture_output=True, timeout=10)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_write_during_snapshot_is_seen_by_the_next_poll(self):
        self.run_js(r'''
let head=1, reads=0; const calls=[];
api=async path=>{calls.push(path); if(path==='/api/status')return {projects:['alpha']};
 if(path.startsWith('/api/changes'))return {head,changes:head>state.cursor?[{seq:head}]:[]};
 if(path.startsWith('/api/snapshot')){reads++;if(reads===1){head=2;return {memories:['before-write']};}return {memories:['after-write']};}
 return [];};
assert.equal(await refresh(),true);assert.equal(state.cursor,1);
assert(calls.findIndex(x=>x.startsWith('/api/changes'))<calls.findIndex(x=>x.startsWith('/api/snapshot')));
await poll();assert.equal(state.cursor,2);assert.deepEqual(state.snapshot.memories,['after-write']);
''')

    def test_explicit_empty_project_does_not_fall_back_to_another_project(self):
        self.run_js(r'''
state.projectInitialized=false;
api=async path=>path==='/api/status'?{projects:['agent-orchestrator']}:
 path.startsWith('/api/changes')?{head:0,changes:[]}:path.startsWith('/api/snapshot')?{memories:[]}:[];
await refresh();assert.equal(state.project,'new-empty-project');assert.equal(state.run,'exact-run');
''')

    def test_dialog_opened_during_poll_preserves_view_and_cursor(self):
        self.run_js(r'''
api=async path=>{if(path==='/api/status')return {projects:['alpha']};
 if(path.startsWith('/api/changes'))return {head:2,changes:[{seq:2}]};
 if(path.startsWith('/api/snapshot')){$('editor').open=true;return {memories:['new']};}return [];};
assert.equal(await refresh(true,true),false);assert.equal(state.cursor,1);
assert.deepEqual(state.snapshot.memories,[]);assert.equal(state.pending,1);
''')

    def test_hidden_tab_and_draft_input_prevent_background_replacement(self):
        self.run_js(r'''
let calls=0;api=async()=>{calls++;return {head:2,changes:[{seq:2}]};};
document.hidden=true;await poll();assert.equal(calls,0);
document.hidden=false;document.activeElement={matches:()=>true};await poll();
assert.equal(calls,2);assert.equal(state.cursor,1);assert.equal(state.pending,1);
''')

    def test_poll_disconnection_is_visible_and_can_retry(self):
        self.run_js(r'''
api=async()=>{throw Error('fixture disconnected');};await poll();
assert.match($('connection').textContent,/Updates interrupted/);assert.equal(state.polling,false);
api=async()=>({head:1,changes:[]});await poll();assert.equal($('connection').textContent,'connected');
''')

    def test_explicit_write_refresh_can_replace_previous_search_results(self):
        self.run_js(r'''
state.searchResults={results:['old']};
api=async path=>path==='/api/status'?{projects:['alpha']}:
 path.startsWith('/api/changes')?{head:2,changes:[{seq:2}]}:path.startsWith('/api/snapshot')?{memories:['updated']}:[];
assert.equal(await refresh(true),true);assert.equal(state.searchResults,null);
assert.deepEqual(state.snapshot.memories,['updated']);assert.equal(state.cursor,2);
''')

    def test_recall_only_change_refreshes_without_a_memory_write(self):
        self.run_js(r'''
state.recallRevision='before';
api=async path=>path==='/api/status'?{projects:['alpha']}:
 path.startsWith('/api/changes')?{head:1,changes:[],recall_revision:'after'}:
 path.startsWith('/api/snapshot')?{memories:[],traces:[{id:'new-search'}]}:[];
await poll();assert.equal(state.cursor,1);assert.equal(state.recallRevision,'after');
assert.equal(state.snapshot.traces[0].id,'new-search');
''')

    def test_recall_only_change_waits_for_open_dialog(self):
        self.run_js(r'''
state.recallRevision='before';$('detail').open=true;let calls=0;
api=async()=>{calls++;return {head:1,changes:[],recall_revision:'after'};};
await poll();assert.equal(calls,2);assert.equal(state.pending,1);
assert.equal(state.recallRevision,'before');assert.equal(state.cursor,1);
''')

    def test_unchanged_recall_does_not_reload_snapshot(self):
        self.run_js(r'''
state.recallRevision='same';let calls=0;
api=async()=>{calls++;return {head:1,changes:[],recall_revision:'same'};};
await poll();assert.equal(calls,2);assert.equal(state.pending,0);
''')

    def test_usage_only_change_waits_for_feedback_then_updates(self):
        self.run_js(r'''
state.usageRevision='before';state.recallRevision='same';$('feedback').open=true;
api=async path=>path==='/api/status'?{projects:['alpha']}:
 path.startsWith('/api/usage')?{revision:'after',tasks:[]}:
 path.startsWith('/api/changes')?{head:1,changes:[],recall_revision:'same'}:
 path.startsWith('/api/snapshot')?{memories:[]}:[];
await poll();assert.equal(state.pending,1);assert.equal(state.usageRevision,'before');
$('feedback').open=false;await poll();assert.equal(state.usageRevision,'after');
assert.equal(state.pending,0);assert.equal(state.cursor,1);
''')


if __name__ == '__main__':
    unittest.main()
