"""Local report breadcrumbs preserve page scope without exporting service secrets."""
from html.parser import HTMLParser
import json
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'app'))
from project_visuals import ProjectLibrary
from usage_guard import Guard
from workspace_navigation import (REPORT_ROUTES, decorate_report, navigation_script,
                                  write_navigation_script)


class NavigationParser(HTMLParser):
    def __init__(self, page):
        super().__init__()
        self.links = {}
        self.scripts = []
        self.feed(page)

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == 'a' and attrs.get('data-workspace-page'):
            self.links[attrs['data-workspace-page']] = attrs
        if tag == 'script' and attrs.get('src'):
            self.scripts.append(attrs['src'])


class WorkspaceNavigationTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.source = Path(__file__).resolve().parents[1]

    def service_rows(self, viewer='http://127.0.0.1:4242', brain='http://127.0.0.1:4343'):
        return [{'id': 'viewer', 'origin': viewer}, {'id': 'brain', 'origin': brain}]

    def script(self, **kwargs):
        with patch('local_services.links', return_value=self.service_rows()):
            return navigation_script(self.root, **kwargs)

    def execute(self, script, *, url='http://127.0.0.1:4242/usage?project=shared-library&run=run-one', refresh=None):
        node = shutil.which('node')
        if not node:
            self.skipTest('Node is needed for the browser-independent navigation behavior check')
        # A minimal DOM records browser-facing URLs. No report internals or
        # selectors are derived from the implementation under test.
        harness = r'''
const vm=require('node:vm');
const payload=JSON.parse(require('node:fs').readFileSync(0,'utf8'));
const url=new URL(payload.url);
const links=['viewer','brain','usage','contributions'].map(page=>({
  dataset:{workspacePage:page},attrs:{},listeners:{},
  setAttribute(key,value){this.attrs[key]=String(value)},
  removeAttribute(key){delete this.attrs[key]},
  getAttribute(key){return this.attrs[key]??null},
  addEventListener(key,fn){this.listeners[key]=fn},
  closest(){return null}
}));
const sandbox={URLSearchParams,location:url,window:{addEventListener(){}},
  document:{readyState:'complete',querySelectorAll(){return links}}};
vm.runInNewContext(payload.script,sandbox);
if(payload.refresh!==null)sandbox.window.WorkspaceNavigation.refresh(payload.refresh);
process.stdout.write(JSON.stringify(Object.fromEntries(links.map(link=>[link.dataset.workspacePage,link.attrs]))));
'''
        result = subprocess.run([node, '-e', harness], input=json.dumps({'script': script, 'url': url, 'refresh': refresh}),
                                capture_output=True, text=True, encoding='utf-8', check=True, timeout=10)
        return json.loads(result.stdout)

    def test_report_routes_are_fixed_and_have_no_filesystem_selector(self):
        self.assertEqual(REPORT_ROUTES, {'/usage': 'usage-dashboard.html', '/contributions': 'project-map.html'})

    def test_markup_has_reciprocal_links_current_page_and_adjacent_sidecar(self):
        with patch('local_services.links', return_value=self.service_rows()):
            page = decorate_report('<html><head><title>Report</title></head><body><h1>Report</h1></body></html>', self.root, 'usage')
        parsed = NavigationParser(page)
        self.assertEqual(set(parsed.links), {'viewer', 'brain', 'usage', 'contributions'})
        self.assertEqual(parsed.links['usage']['aria-current'], 'page')
        self.assertNotIn('aria-current', parsed.links['brain'])
        self.assertEqual(parsed.links['viewer']['href'], 'http://127.0.0.1:4242/')
        self.assertEqual(parsed.links['contributions']['href'], 'project-map.html')
        self.assertEqual(parsed.scripts, ['workspace-navigation.js'])
        self.assertEqual(page, decorate_report(page, self.root, 'usage'))

    def test_missing_or_invalid_service_is_explained_instead_of_linked(self):
        with patch('local_services.links', return_value=self.service_rows(None, 'https://example.com')):
            page = decorate_report('<html><h1>Report</h1></html>', self.root, 'usage')
            script = navigation_script(self.root)
        links = NavigationParser(page).links
        for identifier in ('viewer', 'brain'):
            self.assertNotIn('href', links[identifier])
            self.assertEqual(links[identifier]['aria-disabled'], 'true')
            self.assertIn('.cmd', links[identifier]['title'])
        self.assertNotIn('https://example.com', script)
        self.assertIn('not running', script)

    def test_origin_override_only_accepts_known_page_and_bounded_loopback_port(self):
        for invalid in ('http://127.0.0.1:65536', 'http://127.0.0.1:80/path', 'http://localhost:80', 'https://evil.invalid',
                        'http://127.0.0.1:80@evil.invalid', 'http://127.0.0.1:80/</script>'):
            script = self.script(current='brain', origin=invalid)
            self.assertNotIn(invalid, script)
            self.assertIn('"brain":null', script)
        self.assertIn('"brain":"http://127.0.0.1:5252"', self.script(current='brain', origin='http://127.0.0.1:5252'))
        self.assertNotIn('5252', self.script(current='unknown', origin='http://127.0.0.1:5252'))

    def test_sidecar_atomic_refresh_changes_ports_without_including_paths_or_tokens(self):
        with patch('local_services.links', return_value=self.service_rows()):
            path = write_navigation_script(self.root, 'brain', 'http://127.0.0.1:5252')
            before = path.read_text(encoding='utf-8')
            self.assertEqual(path, self.root / 'runtime/workspace-navigation.js')
            write_navigation_script(self.root, 'brain', 'http://127.0.0.1:5353')
        after = path.read_text(encoding='utf-8')
        self.assertIn('5252', before)
        self.assertNotIn('5252', after)
        self.assertIn('5353', after)
        self.assertNotIn(str(self.root), after)
        self.assertNotIn('X-Brain-Token', after)
        self.assertEqual(list(path.parent.glob('*.tmp')), [])

    def test_http_links_preserve_scope_and_use_report_routes(self):
        links = self.execute(self.script())
        self.assertEqual(links['usage']['href'], '/usage?project=shared-library&run=run-one')
        self.assertEqual(links['contributions']['href'], '/contributions?project=shared-library&run=run-one')
        self.assertEqual(links['viewer']['href'], 'http://127.0.0.1:4242/?project=shared-library&run=run-one')
        self.assertEqual(links['brain']['href'], 'http://127.0.0.1:4343/#project=shared-library&run=run-one')

    def test_file_links_use_siblings_and_keep_project_and_run_distinct(self):
        links = self.execute(self.script(), url='file:///C:/workspace/runtime/project-map.html?project=shared-library&run=unrelated-report')
        self.assertEqual(links['usage']['href'], 'usage-dashboard.html?project=shared-library&run=unrelated-report')
        self.assertEqual(links['contributions']['href'], 'project-map.html?project=shared-library&run=unrelated-report')
        self.assertNotIn('project=unrelated-report', links['brain']['href'])

    def test_memory_hash_and_explicit_scope_refresh_are_encoded_and_bounded(self):
        links = self.execute(self.script(), url='http://127.0.0.1:4343/#project=two+words%26extra&run=run-one')
        self.assertEqual(links['usage']['href'], '/usage?project=two+words%26extra&run=run-one')
        links = self.execute(self.script(), refresh={'project': 'x' * 160, 'run': '../unsafe'})
        self.assertEqual(links['usage']['href'], '/usage?project=' + 'x' * 160)
        links = self.execute(self.script(), refresh={'project': 'x' * 161, 'run': 'run-two'})
        self.assertEqual(links['usage']['href'], '/usage?run=run-two')
        self.assertEqual(self.execute(self.script(), refresh={})['usage']['href'], '/usage')

    def test_usage_generator_writes_navigable_page_and_sidecar_without_quota_refresh(self):
        runtime = self.root / 'runtime'
        guard = Guard(runtime)
        with patch.object(guard, 'refresh', side_effect=AssertionError('No provider refresh expected')), \
                patch('local_services.links', return_value=self.service_rows()):
            guard.dashboard()
        page = (runtime / 'usage-dashboard.html').read_text(encoding='utf-8')
        self.assertEqual(NavigationParser(page).links['usage']['aria-current'], 'page')
        self.assertTrue((runtime / 'workspace-navigation.js').is_file())

    def test_standard_map_export_links_canonical_reports_while_custom_template_is_unchanged(self):
        assets = self.root / 'app/assets'
        assets.mkdir(parents=True)
        shutil.copy2(self.source / 'app/assets/project-map.html', assets / 'project-map.html')
        library = ProjectLibrary(self.root, self.root / 'runtime')
        output = self.root / 'exports/map.html'
        with patch('local_services.links', return_value=self.service_rows()):
            library.render(output=output)
        parsed = NavigationParser(output.read_text(encoding='utf-8'))
        expected = (self.root / 'runtime/usage-dashboard.html').resolve().as_uri()
        self.assertEqual(parsed.links['usage']['href'], expected)
        self.assertEqual(parsed.links['usage']['data-workspace-file'], expected)
        self.assertTrue((output.parent / 'workspace-navigation.js').is_file())
        template = self.root / 'fragment.html'
        template.write_text('<script type="application/json">__PROJECT_MAP_DATA__</script>', encoding='utf-8')
        library.render(output=output, template=template)
        fragment = output.read_text(encoding='utf-8')
        self.assertEqual(fragment.count('</script>'), 1)
        self.assertNotIn('workspace-navigation', fragment)

    def test_map_selects_only_an_exact_run_and_keeps_return_context_explicit(self):
        node = shutil.which('node')
        if not node:
            self.skipTest('Node is needed for the saved-map selection check')
        page = (self.source / 'app/assets/project-map.html').read_text(encoding='utf-8')
        selection = re.search(r'    function selectIncomingProject\(\) \{.*?\n    \}', page, re.DOTALL).group(0)
        harness = r'''
const vm=require('node:vm'), payload=JSON.parse(require('node:fs').readFileSync(0,'utf8'));
const content={};
const element=key=>content[key]||(content[key]={textContent:'',hidden:true,querySelector:selector=>element(key+' '+selector)});
const sandbox={state:{projects:[{id:'latest-run'},{id:'run-one'}],project:null},
  returnRun:payload.run,returnProject:payload.project,text:value=>typeof value==='string'?value:'',$:element};
vm.runInNewContext(payload.selection+'\nselectIncomingProject();',sandbox);
process.stdout.write(JSON.stringify({project:sandbox.state.project?.id||null,content}));
'''
        cases = [('run-one', 'shared-library', 'run-one'), ('missing-run', 'shared-library', None),
                 ('', 'run-one', 'latest-run'), ('', '', 'latest-run')]
        for run, project, selected in cases:
            with self.subTest(run=run, project=project):
                result = subprocess.run([node, '-e', harness], input=json.dumps({'selection': selection, 'run': run, 'project': project}),
                                        capture_output=True, text=True, encoding='utf-8', check=True, timeout=10)
                data = json.loads(result.stdout)
                self.assertEqual(data['project'], selected)
                if run or project:
                    note = data['content']['dashboard-return-context']
                    self.assertFalse(note['hidden'])
                    self.assertIn('Dashboard links return to', note['textContent'])
                    self.assertIn(project or run, note['textContent'])
                if run == 'missing-run':
                    self.assertEqual(data['content']['empty-state h2']['textContent'], 'No saved map for this run yet.')


if __name__ == '__main__':
    unittest.main()
