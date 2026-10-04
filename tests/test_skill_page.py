import re
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'app'))
from skill_pack_page import PAGE


class SkillPageTests(unittest.TestCase):
    def test_token_nonce_safe_dom_and_explicit_actions(self):
        self.assertIn('content="__TOKEN__"', PAGE)
        self.assertIn('nonce="__NONCE__"', PAGE)
        for route in ('/api/skills/catalog', '/api/skills/recommend', '/api/skills/review',
                      '/api/skills/execute', '/api/skills/accept', '/api/skills/feedback'):
            self.assertIn(route, PAGE)
        for unsafe in ('innerHTML', 'outerHTML', 'insertAdjacentHTML', 'document.write', 'eval('):
            self.assertNotIn(unsafe, PAGE)
        self.assertIn('aria-live', PAGE)
        self.assertIn('supplied-text', PAGE)

    @unittest.skipUnless(shutil.which('node'), 'Node needed for actual JavaScript parse')
    def test_actual_page_script_parses(self):
        scripts = re.findall(r'<script nonce="__NONCE__">([\s\S]*?)</script>', PAGE)
        self.assertEqual(len(scripts), 1)
        with tempfile.TemporaryDirectory() as folder:
            script = Path(folder) / 'skills.js'
            script.write_text(scripts[0], encoding='utf-8')
            result = subprocess.run(['node', '--check', str(script)], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
