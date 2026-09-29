"""Every workspace page shares one navigation and one light/dark token set (no servers, no providers)."""
from pathlib import Path
import re
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'app'))
import brain_dashboard
import coordinator_viewer_page
import experiment_page
import jev_workflow_page
import system_map
import task_inbox
from usage_report import render_usage
from workspace_navigation import PAGES, THEME, decorate_report

NAMES = ['Orchestrator', 'Usage', 'Tasks', 'Memory', 'Contributions', 'Experiments', 'System map']


def nav_labels(page):
    nav = re.search(r'<nav class="workspace-navigation".*?</nav>', page, re.S)
    return re.findall(r'<a[^>]*data-workspace-page="[a-z-]+"[^>]*>([^<]+)</a>', nav.group(0).split('</ol>')[0]) if nav else None


def tokens(block):
    return dict(re.findall(r'(--ws-[a-z0-9-]+):(#[0-9a-f]{6})', block))


def contrast(a, b):
    def lum(value):
        r, g, bl = (int(value[i:i + 2], 16) / 255 for i in (1, 3, 5))
        f = lambda c: c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4
        return 0.2126 * f(r) + 0.7152 * f(g) + 0.0722 * f(bl)
    high, low = sorted((lum(a), lum(b)), reverse=True)
    return (high + .05) / (low + .05)


class SharedNavigationTests(unittest.TestCase):
    def test_one_name_list_in_one_order(self):
        self.assertEqual([title for _, title in PAGES], NAMES)

    def test_server_rendered_pages_use_the_shared_navigation_and_mark_their_page(self):
        for module, current in ((coordinator_viewer_page, 'Orchestrator'), (brain_dashboard, 'Memory'),
                                (experiment_page, 'Experiments'), (jev_workflow_page, 'Memory')):
            with self.subTest(page=module.__name__):
                self.assertEqual(nav_labels(module.PAGE), NAMES)
                marked = re.search(r'aria-current="page">([^<]+)</a>', module.PAGE)
                self.assertEqual(marked.group(1), current)
                self.assertIn('Lead for new runs:', module.PAGE)
                self.assertNotIn('__WORKSPACE_NAV', module.PAGE)
                for stale in ('Provider usage', 'Contribution maps', 'Allowances', 'Project Maps'):
                    self.assertNotIn(stale, module.PAGE)

    def test_saved_reports_and_templates_get_the_same_navigation(self):
        with tempfile.TemporaryDirectory() as directory, patch('local_services.links', return_value=[]):
            root = Path(directory)
            pages = {'usage': decorate_report(render_usage({'updated_at': '2026-09-28T20:00:00+00:00', 'workers': [],
                                                             'worker_start_threshold_pct': 20, 'active_reservations': 0}),
                                              root, 'usage'),
                     'tasks': decorate_report(task_inbox.render_page(dataset={'tasks': [], 'warnings': 0}), root, 'tasks'),
                     'system-map': decorate_report(system_map.render_map(ROOT), root, 'system-map')}
        for page_id, page in pages.items():
            with self.subTest(page=page_id):
                self.assertEqual(nav_labels(page), NAMES)
                self.assertIn('<script src="workspace-navigation.js" defer></script>', page)
        inbox = (ROOT / 'app/assets/task-inbox.html').read_text(encoding='utf-8')
        self.assertNotIn('Task Inbox</a>', inbox)
        self.assertNotIn('Allowances', inbox)


class ThemeTests(unittest.TestCase):
    def test_light_and_dark_define_the_same_tokens_with_readable_text(self):
        light_block, dark_block = THEME.split('@media(prefers-color-scheme:dark)')
        light, dark = tokens(light_block), tokens(dark_block)
        self.assertEqual(set(light), set(dark))
        self.assertIn('color-scheme:light dark', light_block)
        for palette in (light, dark):
            for fore, back in (('--ws-text', '--ws-bg'), ('--ws-text', '--ws-surface'), ('--ws-muted', '--ws-surface'),
                               ('--ws-muted', '--ws-bg'), ('--ws-faint', '--ws-surface'), ('--ws-accent', '--ws-surface'),
                               ('--ws-accent-ink', '--ws-accent'), ('--ws-good', '--ws-good-bg'),
                               ('--ws-warn', '--ws-warn-bg'), ('--ws-bad', '--ws-bad-bg'), ('--ws-info', '--ws-info-bg'),
                               ('--ws-aging', '--ws-aging-bg'), ('--ws-muted', '--ws-chip')):
                self.assertGreaterEqual(contrast(palette[fore], palette[back]), 4.5, (fore, back))

    def test_every_page_embeds_the_tokens_and_has_no_fixed_dark_scheme(self):
        with tempfile.TemporaryDirectory() as directory:
            from project_visuals import ProjectLibrary
            root = Path(directory)
            (root / 'app/assets').mkdir(parents=True)
            (root / 'app/assets/project-map.html').write_bytes((ROOT / 'app/assets/project-map.html').read_bytes())
            output = root / 'exports/map.html'
            with patch('local_services.links', return_value=[]):
                ProjectLibrary(root, root / 'runtime').render(output=output)
            contributions = output.read_text(encoding='utf-8')
        pages = {'viewer': coordinator_viewer_page.PAGE, 'memory': brain_dashboard.PAGE, 'experiments': experiment_page.PAGE,
                 'jev': jev_workflow_page.PAGE, 'system-map': system_map.render_map(ROOT),
                 'tasks': task_inbox.render_page(dataset={'tasks': [], 'warnings': 0}), 'contributions': contributions,
                 'usage': render_usage({'updated_at': '2026-09-28T20:00:00+00:00', 'workers': [],
                                        'worker_start_threshold_pct': 20, 'active_reservations': 0})}
        for name, page in pages.items():
            with self.subTest(page=name):
                self.assertIn(THEME.strip(), page)
                self.assertNotIn('__WORKSPACE_THEME__', page)
                self.assertNotRegex(page, r':root\s*\{[^}]*color-scheme:\s*dark')
                self.assertNotIn('content="light"', page)

    def test_memory_graph_canvas_reads_the_theme_and_redraws_on_a_scheme_change(self):
        from brain_graph_ui import SCRIPT
        self.assertIn('getPropertyValue(name)', SCRIPT)
        self.assertIn('"--ws-graph-bg"', SCRIPT)
        self.assertIn('matchMedia("(prefers-color-scheme: dark)")', SCRIPT)
        self.assertIn('schemeQuery.removeEventListener("change", onScheme)', SCRIPT)


if __name__ == '__main__':
    unittest.main()
