import json
from pathlib import Path
import subprocess
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'app'))


class LoadedTagFilterTests(unittest.TestCase):
    def test_tag_all_any_status_and_empty_selection(self):
        from brain_feature_ui import SCRIPT
        scenario = r'''
const assert=require('node:assert/strict');
const rows=[{id:'a',status:'active',tags:['Road','Survey']},{id:'b',status:'active',tags:['road']},{id:'c',status:'pending',tags:['Survey']}];
const ids=(status,tags,mode)=>filterLoadedLibrary(rows,status,tags,mode).map(row=>row.id);
assert.deepEqual(ids('active',['road','survey'],'all'),['a']);
assert.deepEqual(ids('active',['road','survey'],'any'),['a','b']);
assert.deepEqual(ids('pending',['survey'],'all'),['c']);
assert.deepEqual(ids('all',[],'all'),['a','b','c']);
assert.deepEqual(ids('active',['missing'],'any'),[]);
'''
        result = subprocess.run(['node', '-e', SCRIPT+'\n'+scenario], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)


if __name__ == '__main__': unittest.main()
