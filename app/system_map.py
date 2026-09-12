"""Build and open the verified, offline system map from maintained assets."""
import argparse
import base64
import hashlib
from html.parser import HTMLParser
import json
from pathlib import Path
import webbrowser

from contributions import _atomic_text
from paths import ROOT


MARKER = '__SYSTEM_MAP_DATA__'


def _validate(data):
    if not isinstance(data, dict) or not isinstance(data.get('nodes'), list) or not data['nodes']:
        raise ValueError('System map data must contain implemented nodes')
    nodes = {}
    for node in data['nodes']:
        if not isinstance(node, dict) or not isinstance(node.get('id'), str) or not node['id']:
            raise ValueError('Every system map node needs a string ID')
        if node['id'] in nodes or node.get('status') != 'implemented':
            raise ValueError('System map node IDs must be unique and all nodes must be implemented')
        nodes[node['id']] = node
    if sum(node.get('parent') is None for node in nodes.values()) != 1:
        raise ValueError('System map needs exactly one root')
    for node in nodes.values():
        seen = {node['id']}
        parent = node.get('parent')
        while parent is not None:
            if not isinstance(parent, str) or parent not in nodes or parent in seen:
                raise ValueError('System map hierarchy has an invalid parent or cycle')
            seen.add(parent)
            parent = nodes[parent].get('parent')
    for edge in data.get('edges', []):
        if (not isinstance(edge, dict) or not isinstance(edge.get('from'), str)
                or not isinstance(edge.get('to'), str) or edge['from'] not in nodes
                or edge['to'] not in nodes or edge.get('kind') not in ('flow', 'relationship')):
            raise ValueError('System map connections must reference implemented nodes')
    for journey in data.get('journeys', []):
        if (not isinstance(journey, dict) or not isinstance(journey.get('node_ids'), list)
                or not journey['node_ids'] or any(not isinstance(node_id, str) or node_id not in nodes
                                                 for node_id in journey['node_ids'])):
            raise ValueError('System map journeys must reference implemented nodes')


def render_map(root=ROOT):
    """Return HTML text from fixed maintained assets, without writes or launches."""
    assets = Path(root) / 'app' / 'assets'
    template = (assets / 'system-map-template.html').read_text(encoding='utf-8')
    if template.count(MARKER) != 1:
        raise ValueError('System map template needs exactly one data placeholder')
    data = json.loads((assets / 'system-map-data.json').read_text(encoding='utf-8'))
    _validate(data)
    payload = json.dumps(data, ensure_ascii=False, allow_nan=False, separators=(',', ':'))
    # JSON strings are still parsed by the HTML tokenizer inside application/json.
    # Escape every HTML-significant character before embedding, including titles.
    for character, escaped in (('&', '\\u0026'), ('<', '\\u003c'), ('>', '\\u003e'),
                               ('\u2028', '\\u2028'), ('\u2029', '\\u2029')):
        payload = payload.replace(character, escaped)
    return template.replace(MARKER, payload)


class _InlineBlocks(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=False)
        self.blocks = {'script': [], 'style': []}
        self.current = None
        self.parts = []

    def handle_starttag(self, tag, attrs):
        if tag in self.blocks:
            self.current = tag
            self.parts = []

    def handle_data(self, value):
        if self.current:
            self.parts.append(value)

    def handle_endtag(self, tag):
        if tag == self.current:
            self.blocks[tag].append(''.join(self.parts))
            self.current = None


def content_security_policy(page):
    """A fixed-route dashboard can serve this HTML with exact inline-block hashes."""
    parser = _InlineBlocks()
    parser.feed(page)
    def hashes(kind):
        return ' '.join("'sha256-" + base64.b64encode(hashlib.sha256(block.encode('utf-8')).digest()).decode('ascii') + "'"
                        for block in parser.blocks[kind]) or "'none'"
    return ("default-src 'none'; script-src " + hashes('script') + "; script-src-attr 'none'; "
            "style-src " + hashes('style') + "; style-src-attr 'unsafe-inline'; img-src data:; "
            "connect-src 'none'; object-src 'none'; base-uri 'none'; form-action 'none'; frame-ancestors 'none'")


def build_map(root=ROOT, output=None):
    """Atomically refresh the standalone artifact; preserve the prior file on error."""
    page = render_map(root)
    target = Path(output).expanduser() if output is not None else Path(root) / 'runtime' / 'system-map.html'
    target = target.resolve()
    target.parent.mkdir(parents=True, exist_ok=True)
    _atomic_text(target, page)
    return {'output': str(target), 'bytes': len(page.encode('utf-8')), 'offline': True}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, help='Write the standalone HTML to this path')
    parser.add_argument('--no-open', action='store_true', help='Generate the map without opening a browser')
    args = parser.parse_args(argv)
    try:
        result = build_map(ROOT, args.output)
    except (OSError, ValueError, TypeError) as error:
        parser.exit(1, 'System map could not be generated: ' + str(error) + '\n')
    result['opened'] = False if args.no_open else bool(webbrowser.open(Path(result['output']).as_uri()))
    print(json.dumps(result))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
