"""The generated map remains offline, inert to data markup, and independently usable."""
import base64
import hashlib
from html.parser import HTMLParser
import json
from pathlib import Path
import re
import shutil
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'app'))
import system_map


class Resources(HTMLParser):
    def __init__(self):
        super().__init__()
        self.urls = []

    def handle_starttag(self, tag, attrs):
        self.urls.extend(value for key, value in attrs if key in ('src', 'href', 'action', 'poster'))


class SystemMapTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.assets = self.root / 'app' / 'assets'
        self.assets.mkdir(parents=True)
        shutil.copy2(Path(system_map.__file__).parent / 'assets' / 'system-map-template.html', self.assets)
        self.dataset = {'title': 'Verified system', 'nodes': [
            {'id': 'root', 'parent': None, 'label': 'System', 'status': 'implemented'},
            {'id': 'memory', 'parent': 'root', 'label': 'Memory', 'status': 'implemented'}],
            'edges': [{'from': 'root', 'to': 'memory', 'label': 'Uses context', 'kind': 'flow'}],
            'journeys': [{'id': 'example', 'node_ids': ['root', 'memory']}]}
        self.save()

    def save(self):
        (self.assets / 'system-map-data.json').write_text(json.dumps(self.dataset), encoding='utf-8')

    def test_markup_in_all_data_stays_inside_inert_json_and_round_trips(self):
        dangerous = '</script><script src="https://example.invalid/steal.js">&\u2028\u2029'
        self.dataset['title'] = dangerous
        self.dataset['nodes'][1]['summary'] = dangerous
        self.save()
        page = system_map.render_map(self.root)
        payload = re.search(r'<script id="system-map-data" type="application/json">(.*?)</script>', page, re.S).group(1)
        self.assertEqual(json.loads(payload)['title'], dangerous)
        self.assertNotIn('<', payload)
        self.assertNotIn('&', payload)
        self.assertNotIn('\u2028', payload)
        self.assertNotIn('\u2029', payload)
        self.assertEqual(page.count('</script>'), 2)

    def test_render_has_no_network_dependencies_or_writes(self):
        page = system_map.render_map(self.root)
        parsed = Resources()
        parsed.feed(page)
        self.assertTrue(all(url.startswith('data:') for url in parsed.urls), parsed.urls)
        self.assertNotRegex(page, r'\b(?:fetch|XMLHttpRequest|WebSocket|EventSource)\s*\(')
        self.assertNotRegex(page, r'@import\s|<iframe\b')
        self.assertFalse((self.root / 'runtime').exists())

    def test_only_implemented_nodes_and_valid_connections_are_accepted(self):
        for change in ('pending', 'missing_parent', 'missing_edge', 'missing_journey', 'cycle'):
            with self.subTest(change=change):
                original = json.loads(json.dumps(self.dataset))
                if change == 'pending': self.dataset['nodes'][1]['status'] = 'planned'
                elif change == 'missing_parent': self.dataset['nodes'][1]['parent'] = 'unknown'
                elif change == 'missing_edge': self.dataset['edges'][0]['to'] = 'unknown'
                elif change == 'missing_journey': self.dataset['journeys'][0]['node_ids'].append('unknown')
                else: self.dataset['nodes'][1]['parent'] = 'memory'
                self.save()
                with self.assertRaises(ValueError): system_map.render_map(self.root)
                self.dataset = original

    def test_bad_data_preserves_previous_generated_map(self):
        result = system_map.build_map(self.root)
        target = Path(result['output'])
        before = target.read_bytes()
        (self.assets / 'system-map-data.json').write_text('{broken', encoding='utf-8')
        with self.assertRaises(ValueError): system_map.build_map(self.root)
        self.assertEqual(target.read_bytes(), before)

    def test_missing_or_multiple_markers_are_rejected(self):
        template = self.assets / 'system-map-template.html'
        for text in ('no marker', system_map.MARKER * 2):
            template.write_text(text, encoding='utf-8')
            with self.assertRaises(ValueError): system_map.render_map(self.root)

    def test_custom_output_and_no_open_are_honored(self):
        target = self.root / 'export' / 'Shared system.html'
        with patch.object(system_map, 'ROOT', self.root), patch.object(system_map.webbrowser, 'open') as opener, patch('builtins.print'):
            self.assertEqual(system_map.main(['--output', str(target), '--no-open']), 0)
        opener.assert_not_called()
        self.assertIn('Verified system', target.read_text(encoding='utf-8'))
        self.assertFalse((self.root / 'runtime').exists())

    def test_default_cli_opens_generated_file_and_never_a_network_url(self):
        with patch.object(system_map, 'ROOT', self.root), patch.object(system_map.webbrowser, 'open', return_value=True) as opener, patch('builtins.print'):
            self.assertEqual(system_map.main([]), 0)
        opener.assert_called_once_with((self.root / 'runtime' / 'system-map.html').as_uri())
        self.assertTrue((self.root / 'runtime' / 'system-map.html').is_file())

    def test_csp_hashes_match_exact_inline_code_and_deny_connections(self):
        page = system_map.render_map(self.root)
        policy = system_map.content_security_policy(page)
        scripts = re.findall(r'<script(?:\s[^>]*)?>(.*?)</script>', page, re.S)
        styles = re.findall(r'<style(?:\s[^>]*)?>(.*?)</style>', page, re.S)
        for block in scripts + styles:
            digest = base64.b64encode(hashlib.sha256(block.encode('utf-8')).digest()).decode('ascii')
            self.assertIn("'sha256-" + digest + "'", policy)
        self.assertIn("connect-src 'none'", policy)
        self.assertIn("frame-ancestors 'none'", policy)
        self.assertNotIn("script-src 'unsafe-inline'", policy)


if __name__ == '__main__':
    unittest.main()
