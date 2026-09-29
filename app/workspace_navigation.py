"""One shared top navigation for every workspace page, served or saved.

Pages: Orchestrator · Usage · Tasks · Memory · Contributions · Experiments · System map,
with a read-only "Lead for new runs" chip. Usage, Tasks, Contributions and the
System map are routes on both loopback servers (and saved sibling files for
file:// reports); Orchestrator and Experiments live on the viewer server and
Memory on the Memory server.

The sidecar contains page locations and the lead label only. Discovery
health-checks the registered loopback services; it neither starts an app nor
reads account or session tokens.
"""
from html import escape
import json
from pathlib import Path
import re

from contributions import _atomic_text


# Static snapshots served as-is by both servers.
REPORT_ROUTES = {'/usage': 'usage-dashboard.html', '/contributions': 'project-map.html'}
# Rendered live from the task store by both loopback servers; the saved file is for file:// use.
LIVE_ROUTES = {'/tasks': 'task-inbox.html'}
SCRIPT_NAME = 'workspace-navigation.js'
PAGES = (
    ('viewer', 'Orchestrator'), ('usage', 'Usage'), ('tasks', 'Tasks'), ('brain', 'Memory'),
    ('contributions', 'Contributions'), ('experiments', 'Experiments'), ('system-map', 'System map'),
)
ROUTES = {'usage': '/usage', 'tasks': '/tasks', 'contributions': '/contributions', 'system-map': '/system-map'}
FILES = {'usage': 'usage-dashboard.html', 'tasks': 'task-inbox.html', 'contributions': 'project-map.html',
         'system-map': 'system-map.html'}
# Pages that live on exactly one server: page -> (service, path).
SERVICE_PAGES = {'viewer': ('viewer', '/'), 'brain': ('brain', '/'), 'experiments': ('viewer', '/experiments')}
LAUNCHERS = {'viewer': 'Open Orchestrator Viewer.cmd', 'brain': 'Open Brain Dashboard.cmd'}
SERVICE_TITLES = {'viewer': 'Orchestrator', 'brain': 'Memory'}
# Shared design tokens. Every page maps its own colors to these, so all pages
# follow the system light or dark preference together. Text pairs meet WCAG AA.
THEME = '''
:root{color-scheme:light dark;--ws-font:system-ui,-apple-system,"Segoe UI",sans-serif;
--ws-bg:#f5f4ef;--ws-surface:#fffefa;--ws-surface-2:#f1f0e9;--ws-raised:#ffffff;--ws-line:#d6dbd3;--ws-line-strong:#aeb9b0;
--ws-text:#1a2b33;--ws-muted:#56656c;--ws-faint:#66747a;--ws-accent:#1b6b5d;--ws-accent-ink:#ffffff;--ws-accent-soft:#dcefe8;
--ws-focus:#b3541e;--ws-good:#23694a;--ws-good-bg:#dcefe2;--ws-good-line:#a9d2b7;--ws-warn:#80520f;--ws-warn-bg:#f7ead0;
--ws-warn-line:#dfc28c;--ws-bad:#a0372d;--ws-bad-bg:#f8e1dd;--ws-bad-line:#e2aea6;--ws-aging:#5f5436;--ws-aging-bg:#ece7da;
--ws-info:#2f5a8c;--ws-info-bg:#e2eaf5;--ws-info-line:#b7c9e2;--ws-violet:#6b4fa8;--ws-chip:#e8ebe6;--ws-track:#dfe3dc;
--ws-shadow:0 10px 30px #1a2b3314;--ws-overlay:#1a2b3399;--ws-nav-bg:#ffffff;--ws-nav-current:#e4f1ec;
--ws-graph-bg:#f7f7f2;--ws-graph-edge:rgba(26,43,51,.16);--ws-graph-edge-strong:rgba(26,43,51,.6);
--ws-kind-fact:#3d62b8;--ws-kind-preference:#9a6a12;--ws-kind-episode:#1f7a63;--ws-kind-procedure:#7a4fb8}
@media(prefers-color-scheme:dark){:root{
--ws-bg:#10151d;--ws-surface:#18212d;--ws-surface-2:#131b25;--ws-raised:#1e2a38;--ws-line:#303d4e;--ws-line-strong:#4a5d75;
--ws-text:#edf3fb;--ws-muted:#a7b5c7;--ws-faint:#93a2b5;--ws-accent:#9edbc7;--ws-accent-ink:#10271f;--ws-accent-soft:#173c34;
--ws-focus:#e6ba77;--ws-good:#7ce0b4;--ws-good-bg:#173c34;--ws-good-line:#2f6b5b;--ws-warn:#ffd792;--ws-warn-bg:#3d321f;
--ws-warn-line:#786244;--ws-bad:#ffb5ae;--ws-bad-bg:#3d2226;--ws-bad-line:#78514d;--ws-aging:#e3d3ae;--ws-aging-bg:#2f3136;
--ws-info:#a3bdff;--ws-info-bg:#1b2e3a;--ws-info-line:#36535a;--ws-violet:#d3abff;--ws-chip:#243041;--ws-track:#293447;
--ws-shadow:0 12px 40px #0006;--ws-overlay:#040912c9;--ws-nav-bg:#131c27;--ws-nav-current:#23364a;
--ws-graph-bg:#101722;--ws-graph-edge:rgba(237,243,250,.12);--ws-graph-edge-strong:rgba(237,243,250,.55);
--ws-kind-fact:#94b5ff;--ws-kind-preference:#f4c878;--ws-kind-episode:#79dfc5;--ws-kind-procedure:#c6a0ed}}
'''
STYLE = '''
.workspace-navigation{--wn-bg:var(--ws-nav-bg,#131c27);--wn-line:var(--ws-line,#2f3d4f);--wn-text:var(--ws-text,#dce6f1);--wn-muted:var(--ws-muted,#a9b7c6);--wn-current-bg:var(--ws-nav-current,#23364a);--wn-current:var(--ws-text,#ffffff);--wn-accent:var(--ws-accent,#9edbc7);--wn-focus:var(--ws-focus,#e6ba77);box-sizing:border-box;display:flex;flex-wrap:wrap;align-items:center;justify-content:space-between;gap:8px 16px;background:var(--wn-bg);color:var(--wn-text);border:1px solid var(--wn-line);border-radius:12px;padding:8px 10px;margin:0 0 24px;font:14px/1.4 system-ui,-apple-system,"Segoe UI",sans-serif;text-align:left}
.workspace-navigation ol{display:flex;flex-wrap:wrap;align-items:center;gap:4px;list-style:none;margin:0;padding:0}
.workspace-navigation li{display:flex;margin:0;padding:0}
.workspace-navigation a{display:inline-block;color:var(--wn-text);text-decoration:none;border-radius:8px;padding:6px 10px;cursor:pointer;white-space:nowrap}
.workspace-navigation a:hover{background:var(--wn-current-bg);color:var(--wn-current);text-decoration:underline;text-underline-offset:3px}
.workspace-navigation a[aria-current="page"]{background:var(--wn-current-bg);color:var(--wn-current);font-weight:700;box-shadow:inset 0 -2px 0 var(--wn-accent)}
.workspace-navigation a[aria-disabled="true"]{color:var(--wn-muted);text-decoration:underline dotted;text-underline-offset:3px}
.workspace-navigation a:focus-visible{outline:3px solid var(--wn-focus);outline-offset:2px}
.workspace-navigation .workspace-lead{border:1px solid var(--wn-line);border-radius:20px;padding:4px 12px;font-size:13px;color:var(--wn-muted)}
.workspace-navigation .workspace-lead strong{color:var(--wn-text);font-weight:650}
.workspace-navigation .workspace-navigation-status{flex-basis:100%;color:var(--wn-muted);font:13px/1.5 system-ui,sans-serif;margin:2px 4px 4px}
body>.workspace-navigation{margin:12px 16px 20px}
@media(max-width:600px){.workspace-navigation{padding:6px}.workspace-navigation a{padding:6px 8px}.workspace-navigation .workspace-lead{padding:4px 10px}}
@media print{.workspace-navigation{display:none}}
'''


def _valid_origin(value):
    if not isinstance(value, str) or not re.fullmatch(r'http://127\.0\.0\.1:[1-9][0-9]{0,4}', value):
        return None
    return value if int(value.rsplit(':', 1)[1]) <= 65535 else None


def _origins(root, current=None, origin=None):
    from local_services import links
    origins = {'viewer': None, 'brain': None}
    for row in links(root, current):
        if row['id'] in origins:
            origins[row['id']] = _valid_origin(row.get('origin'))
    # The caller can supply its own newly listening server, avoiding a request
    # back into the same single-startup path before serve_forever begins.
    if current in origins:
        origins[current] = _valid_origin(origin)
    return origins


def lead_label():
    """'ASTRA' or 'Claude · Opus 5.5' for new runs; a neutral label when settings are unreadable."""
    try:
        from lead_selection import LEADS, describe
        value = describe()
        option = next((row for row in value.get('options', []) if row.get('id') == value['lead']), {})
        label = LEADS[value['lead']]['label']
        model = option.get('model_label')
        return label + (' · ' + model if model and model != 'unavailable' and value['lead'] == 'claude' else '')
    except Exception:  # A navigation chip must never break a page.
        return ''


def _lead_chip(label, href, *, identifier=False):
    """Read-only chip linking to the lead switch; disabled like other links when the viewer is not running."""
    text = ('<span>Lead for new runs:</span> <strong' + (' id="crumb-lead-name"' if identifier else '')
            + ' data-workspace-lead>' + escape(label or 'see Orchestrator') + '</strong>')
    attrs = ' id="crumb-lead"' if identifier else ''
    attrs += ' class="workspace-lead" data-workspace-page="viewer" data-workspace-hash="lead"'
    if href:
        attrs += ' href="' + escape(href, quote=True) + '" title="Change who leads new runs on the Orchestrator page"'
    else:
        attrs += (' role="link" tabindex="0" aria-disabled="true" title="'
                  + escape(_unavailable_title('viewer'), quote=True) + '"')
    return '<a' + attrs + '>' + text + '</a>'


def _items(page_id, hrefs, ids, disabled_titles):
    items = []
    for identifier, title in PAGES:
        attrs = (' id="crumb-' + identifier + '"' if ids else '') + ' data-workspace-page="' + identifier + '"'
        if identifier in FILES:
            attrs += ' data-workspace-file="' + escape(hrefs.get(identifier + ':file', FILES[identifier]), quote=True) + '"'
        href = hrefs.get(identifier)
        if href:
            attrs += ' href="' + escape(href, quote=True) + '"'
        else:
            attrs += (' role="link" tabindex="0" aria-disabled="true" title="'
                      + escape(disabled_titles[identifier], quote=True) + '"')
        if identifier == page_id:
            attrs += ' aria-current="page"'
        items.append('<li><a' + attrs + '>' + title + '</a></li>')
    return items


def _unavailable_title(identifier):
    service = SERVICE_PAGES[identifier][0]
    return (SERVICE_TITLES[service] + ' is not running. Open ' + LAUNCHERS[service]
            + ' in the Orchestrator folder, then reload this page.')


def navigation_markup(root, page_id, *, report_directory=None, served_by=None, origin=None, lead=None):
    """Accessible navigation with useful links before JavaScript loads.

    `served_by` names the loopback server rendering the page ('viewer' or
    'brain'): route pages then use their routes, and that server is not probed.
    Without it the page is a saved report and route pages use sibling files.
    """
    if page_id not in dict(PAGES):
        raise ValueError('Unknown workspace page')
    origins = _origins(root, served_by, origin)
    hrefs = {}
    for identifier, name in FILES.items():
        if report_directory is not None:
            hrefs[identifier + ':file'] = (Path(report_directory).resolve() / name).as_uri()
        hrefs[identifier] = ROUTES[identifier] if served_by else hrefs.get(identifier + ':file', name)
    for identifier, (service, path) in SERVICE_PAGES.items():
        if served_by == service:
            hrefs[identifier] = path
        elif origins.get(service):
            hrefs[identifier] = origins[service] + path
    titles = {identifier: _unavailable_title(identifier) for identifier in SERVICE_PAGES}
    viewer = hrefs.get('viewer')
    chip = _lead_chip(lead_label() if lead is None else lead, viewer + '#lead' if viewer else None)
    return ('<nav class="workspace-navigation" aria-label="Workspace pages" data-workspace-current="' + page_id + '">'
            '<ol>' + ''.join(_items(page_id, hrefs, False, titles)) + '</ol>' + chip +
            '<p class="workspace-navigation-status" data-workspace-status role="status" hidden></p></nav>')


def static_markup(page_id, served_by, lead=''):
    """Deterministic navigation for pages rendered by a loopback server (no probing, no I/O).

    Links on the other server start as `#`; the page script fills in its origin.
    Links carry `crumb-<page>` ids so page scripts can keep project and run scope.
    """
    if page_id not in dict(PAGES) or served_by not in SERVICE_TITLES:
        raise ValueError('Unknown workspace page')
    hrefs = {identifier: route for identifier, route in ROUTES.items()}
    for identifier, (service, path) in SERVICE_PAGES.items():
        hrefs[identifier] = path if service == served_by else '#'
    chip = _lead_chip(lead, '/#lead' if served_by == 'viewer' else '#', identifier=True)
    return ('<nav class="workspace-navigation" aria-label="Workspace pages" data-workspace-current="' + page_id + '">'
            '<ol>' + ''.join(_items(page_id, hrefs, True, {})) + '</ol>' + chip +
            '<p class="workspace-navigation-status" data-workspace-status role="status" hidden></p></nav>')


def decorate_report(page, root, page_id, *, report_directory=None, served_by=None, origin=None):
    """Add shared navigation without touching report data or the report's code."""
    if 'data-workspace-current=' in page:
        return page
    markup = navigation_markup(root, page_id, report_directory=report_directory, served_by=served_by, origin=origin)
    style = '<style>' + THEME + STYLE + '</style>'
    head_end = re.search(r'</head\s*>', page, re.IGNORECASE)
    if head_end:
        page = page[:head_end.start()] + style + page[head_end.start():]
    else:
        first_style = re.search(r'<style\b', page, re.IGNORECASE)
        position = first_style.start() if first_style else 0
        page = page[:position] + style + page[position:]
    body = re.search(r'<body\b[^>]*>', page, re.IGNORECASE)
    if body:
        position = body.end()
    else:
        heading = re.search(r'<h1\b', page, re.IGNORECASE)
        if not heading:
            raise ValueError('A report page needs a body or main heading')
        position = heading.start()
    page = page[:position] + markup + page[position:]
    script = '<script src="' + ('/' if served_by else '') + SCRIPT_NAME + '" defer></script>'
    end = re.search(r'</body\s*>|</html\s*>', page, re.IGNORECASE)
    position = end.start() if end else len(page)
    return page[:position] + script + page[position:]


_SCRIPT = r'''(function(){
"use strict";
const origins=__ORIGINS__;
const launchers=__LAUNCHERS__;
const lead=__LEAD__;
const routes={usage:"/usage",tasks:"/tasks",contributions:"/contributions","system-map":"/system-map"};
const files={usage:"usage-dashboard.html",tasks:"task-inbox.html",contributions:"project-map.html","system-map":"system-map.html"};
const services={viewer:["viewer","/"],brain:["brain","/"],experiments:["viewer","/experiments"]};
const titles={viewer:"Orchestrator",brain:"Memory"};
// Only these pages understand a project or run scope.
const scoped={viewer:true,brain:true,usage:true,tasks:true,contributions:true};
function cleanScope(value){
  const result={};
  if(value&&typeof value.project==="string"&&value.project.trim()&&value.project.length<=160)result.project=value.project;
  if(value&&typeof value.run==="string"&&/^[A-Za-z0-9][A-Za-z0-9._-]{0,199}$/.test(value.run))result.run=value.run;
  return result;
}
function locationScope(){
  const query=new URLSearchParams(location.search);
  // Memory pages keep their scope in the hash; saved reports use query.
  const hash=location.pathname==="/"||location.pathname==="/jev"?new URLSearchParams(location.hash.slice(1)):null;
  return cleanScope({project:query.get("project")||(hash&&hash.get("project")),run:query.get("run")||(hash&&hash.get("run"))});
}
let scope=locationScope();
function target(page,fileTarget,link){
  // A link can pin its own run (a contribution report) and fragment (one task, the lead panel).
  const run=link&&link.getAttribute("data-workspace-run");
  const pinned=run&&/^[A-Za-z0-9][A-Za-z0-9._-]{0,199}$/.test(run)?{run}:null;
  const fragment=link&&link.getAttribute("data-workspace-hash");
  const suffix=fragment&&/^[A-Za-z0-9=&._-]{1,200}$/.test(fragment)?"#"+fragment:"";
  const params=scoped[page]?new URLSearchParams(pinned||scope).toString():"";
  if(routes[page]){
    if(location.protocol!=="file:")return routes[page]+(params?"?"+params:"")+suffix;
    // A saved system map is optional; prefer a running server's live map.
    const live=page==="system-map"&&(origins.viewer||origins.brain);
    if(live)return live+routes[page];
    return (fileTarget||files[page])+(params?"?"+params:"")+suffix;
  }
  const service=services[page];
  const origin=service&&origins[service[0]];
  if(!origin)return null;
  if(page==="brain")return origin+"/"+(params?"#"+params:"");
  return origin+service[1]+(params?"?"+params:"")+suffix;
}
function unavailable(link){
  const service=(services[link.dataset.workspacePage]||["viewer"])[0];
  const message=titles[service]+" is not running. Open "+launchers[service]+" in the Orchestrator folder, then reload this page.";
  const status=link.closest("[data-workspace-current]")?.querySelector("[data-workspace-status]");
  if(status){status.textContent=message;status.hidden=false;}
}
function refresh(value){
  if(value!==undefined)scope=cleanScope(value);
  if(lead)for(const node of document.querySelectorAll("[data-workspace-lead]"))node.textContent=lead;
  for(const link of document.querySelectorAll("a[data-workspace-page]")){
    const page=link.dataset.workspacePage;
    if(!routes[page]&&!services[page])continue;
    // Keep a link's own tooltip (the lead chip); only "not running" tooltips come and go.
    if(link.dataset.workspaceTitle===undefined)link.dataset.workspaceTitle=link.getAttribute("aria-disabled")==="true"?"":(link.getAttribute("title")||"");
    const href=target(page,link.getAttribute("data-workspace-file"),link);
    if(href){link.setAttribute("href",href);link.removeAttribute("aria-disabled");link.removeAttribute("tabindex");link.removeAttribute("role");if(link.dataset.workspaceTitle)link.setAttribute("title",link.dataset.workspaceTitle);else link.removeAttribute("title");}
    else{const service=services[page][0];link.removeAttribute("href");link.setAttribute("aria-disabled","true");link.setAttribute("tabindex","0");link.setAttribute("role","link");link.title="Open "+launchers[service]+" in the Orchestrator folder, then reload this page.";}
    if(link.dataset.workspaceBound!=="true"){
      link.dataset.workspaceBound="true";
      link.addEventListener("click",event=>{if(link.getAttribute("aria-disabled")==="true"){event.preventDefault();unavailable(link);}});
      link.addEventListener("keydown",event=>{if((event.key==="Enter"||event.key===" ")&&link.getAttribute("aria-disabled")==="true"){event.preventDefault();unavailable(link);}});
    }
  }
}
window.WorkspaceNavigation={refresh};
if(document.readyState==="loading")document.addEventListener("DOMContentLoaded",()=>refresh());else refresh();
window.addEventListener("popstate",()=>refresh(locationScope()));
window.addEventListener("hashchange",()=>refresh(locationScope()));
})();
'''


def _json(value):
    return json.dumps(value, separators=(',', ':')).replace('<', '\\u003c')


def navigation_script(root, current=None, origin=None):
    """Return navigation JavaScript with only verified loopback page origins and the lead label."""
    return (_SCRIPT.replace('__ORIGINS__', _json(_origins(root, current, origin)))
            .replace('__LAUNCHERS__', _json(LAUNCHERS)).replace('__LEAD__', _json(lead_label())))


def write_navigation_script(root, current=None, origin=None, *, directory=None):
    """Refresh the sidecar after service startup or standard report generation.

    An explicit export directory receives its own sidecar; normal runtime copies
    are updated by server startup so reopening a saved report finds current ports.
    """
    destination = Path(directory) if directory is not None else Path(root) / 'runtime'
    destination.mkdir(parents=True, exist_ok=True)
    path = destination / SCRIPT_NAME
    _atomic_text(path, navigation_script(root, current, origin))
    return path
