"""Build a private, offline visual project library from reviewed contribution reports."""
import argparse
from copy import deepcopy
from datetime import datetime, timezone
import json
from pathlib import Path
import webbrowser

from contributions import build_report, _atomic_text
from paths import ROOT, STATE
from usage_guard import file_lock


def stamp():
    return datetime.now(timezone.utc).isoformat()


def project_from_report(report, date, title=None):
    # Recompute from evidence instead of trusting cached percentages in a report.
    verified = build_report({'schema_version': report['schema_version'], 'scope_id': report['scope_id'],
        'title': report['title'], 'basis': report['basis'],
        'contributors': [{k: p[k] for k in ('id', 'name', 'provider', 'model')} for p in report['by_agent']],
        'work_items': report['work_items'], 'activity': report['activity']})
    people = []
    for agent in verified['by_agent']:
        kept = []
        for item in verified['work_items']:
            if item['status'] == 'accepted' and any(a['agent_id'] == agent['id'] and a['percent'] > 0 for a in item['allocations']):
                kept.append({'label': item['label'], 'category': item['category']})
        people.append({'id': agent['id'], 'name': agent['name'], 'provider': agent['provider'],
            'share_pct': None if agent['accepted_work_pct'] == 0 and verified['unattributed_pct'] else agent['accepted_work_pct'], 'roles': sorted({x['category'] for x in kept}),
            'kept': kept, 'attempts': agent['delegations']})
    return {'id': verified['scope_id'], 'title': title or verified['title'],
        'state': 'ready' if verified['attribution_complete'] else 'incomplete' if verified['accepted_weight'] else 'waiting',
        'date': date, 'people': people,
        'categories': [{'name': c['category'], 'people': [{'id': a['id'], 'share_pct': None if a['accepted_work_pct'] == 0 and c['unattributed_pct'] else a['accepted_work_pct']} for a in c['by_agent']],
                        'unassigned_pct': c['unattributed_pct']} for c in verified['by_category']],
        'unassigned_pct': verified['unattributed_pct'], 'attempts': verified['usage']['delegations']}


class ProjectLibrary:
    def __init__(self, root=None, state=None):
        self.root = Path(root or ROOT)
        self.state = Path(state or STATE)
        self.state.mkdir(parents=True, exist_ok=True)
        self.index = self.state / 'project-library.json'

    def _read_index(self):
        if not self.index.exists():
            return {'schema_version': 1, 'reports': {}}
        data = json.loads(self.index.read_text(encoding='utf-8'))
        if data.get('schema_version') != 1 or not isinstance(data.get('reports'), dict):
            raise ValueError('Project library index is invalid')
        return data

    def register(self, report_path):
        path = Path(report_path).resolve(strict=True)
        if not path.is_file():
            raise ValueError('Choose a saved contribution report')
        report = json.loads(path.read_text(encoding='utf-8'))
        project_from_report(report, stamp())  # Validate before registering a path.
        with file_lock(self.state / 'project-library.lock'):
            index = self._read_index()
            index['reports'].setdefault(str(path), {})
            _atomic_text(self.index, json.dumps(index, indent=2, ensure_ascii=False))

    def collect(self):
        with file_lock(self.state / 'project-library.lock'):
            index = self._read_index()
            for path in (self.root / '.orchestration').glob('*/contribution-audit.json'):
                index['reports'].setdefault(str(path.resolve()), {})
            projects = {}
            unavailable = 0
            for name, entry in index['reports'].items():
                path = Path(name)
                try:
                    report = json.loads(path.read_text(encoding='utf-8'))
                    title = None
                    manifest = path.parent / 'run.json'
                    if manifest.is_file():
                        metadata = json.loads(manifest.read_text(encoding='utf-8'))
                        if metadata.get('run_id') == report['scope_id'] and isinstance(metadata.get('display_name'), str):
                            title = metadata['display_name'].strip() or None
                    date = datetime.fromtimestamp(path.stat().st_mtime, timezone.utc).isoformat()
                    project = project_from_report(report, date, title)
                    entry['last_project'] = project
                except (OSError, ValueError, KeyError, TypeError):
                    unavailable += 1
                    if not entry.get('last_project'):
                        continue
                    project = deepcopy(entry['last_project'])
                    project['note'] = 'Saved view. The latest project results are unavailable right now.'
                previous = projects.get(project['id'])
                if (previous is None or project['date'] > previous['date']
                        or (previous.get('note') and not project.get('note') and project['date'] == previous['date'])):
                    projects[project['id']] = project
            _atomic_text(self.index, json.dumps(index, indent=2, ensure_ascii=False))
        return {'schema_version': 1, 'generated_at': stamp(),
                'projects': sorted(projects.values(), key=lambda p: (p['date'], p['id']), reverse=True),
                'unavailable_reports': unavailable}

    def render(self, output=None, template=None):
        dataset = self.collect()
        standard_template = template is None
        template = Path(template) if template else self.root / 'app/assets/project-map.html'
        page = template.read_text(encoding='utf-8')
        marker = '__PROJECT_MAP_DATA__'
        if page.count(marker) != 1:
            raise ValueError('Visual page must contain exactly one data placeholder')
        payload = json.dumps(dataset, ensure_ascii=False, allow_nan=False)
        # Report titles are untrusted text. Prevent closing the embedded data tag.
        for character, escaped in (('&', '\\u0026'), ('<', '\\u003c'), ('>', '\\u003e'), ('\u2028', '\\u2028'), ('\u2029', '\\u2029')):
            payload = payload.replace(character, escaped)
        target = Path(output) if output else self.state / 'project-map.html'
        target.parent.mkdir(parents=True, exist_ok=True)
        page = page.replace(marker, payload)
        if standard_template:
            from workspace_navigation import decorate_report, write_navigation_script
            # Exporting a copy elsewhere must not invent sibling report files.
            report_directory = self.state if target.parent.resolve() != self.state.resolve() else None
            page = decorate_report(page, self.root, 'contributions', report_directory=report_directory)
            write_navigation_script(self.root, directory=target.parent)
        _atomic_text(target, page)
        return {'output': str(target), 'projects': len(dataset['projects']), 'unavailable_reports': dataset['unavailable_reports']}


def refresh_views(report_path=None):
    library = ProjectLibrary()
    if report_path:
        library.register(report_path)
    return library.render()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--add-report', type=Path, help='Include a saved report from another project')
    parser.add_argument('--open', action='store_true', help='Open the visual project view')
    args = parser.parse_args()
    result = refresh_views(args.add_report)
    if args.open:
        webbrowser.open(Path(result['output']).resolve().as_uri())
    print(json.dumps(result))
    return 0

if __name__ == '__main__':
    raise SystemExit(main())
