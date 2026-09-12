"""Fixed local-page navigation shared by saved reports and loopback dashboards.

The sidecar contains page locations only. Discovery health-checks the registered
loopback services; it neither starts an app nor reads account or session tokens.
"""
from html import escape
import json
from pathlib import Path
import re

from contributions import _atomic_text


REPORT_ROUTES = {'/usage': 'usage-dashboard.html', '/contributions': 'project-map.html'}
SCRIPT_NAME = 'workspace-navigation.js'
PAGES = (
    ('viewer', 'Orchestrator'), ('brain', 'Memory'),
    ('usage', 'Provider usage'), ('contributions', 'Contribution maps'),
)
LAUNCHERS = {'viewer': 'Open Orchestrator Viewer.cmd', 'brain': 'Open Brain Dashboard.cmd'}
STYLE = '''
.workspace-navigation{box-sizing:border-box;background:#14212d;color:#e8f0f7;border:1px solid #394959;border-radius:12px;padding:14px 18px;margin:0 0 24px;font:14px/1.6 system-ui,sans-serif}
.workspace-navigation ol{display:flex;flex-wrap:wrap;align-items:center;gap:6px 0;list-style:none;margin:0;padding:0}
.workspace-navigation li{display:flex;align-items:center;margin:0;padding:0}
.workspace-navigation li+li:before{content:"/";color:#8699aa;margin:0 12px}
.workspace-navigation a{color:#b9dafa;text-decoration:underline;text-underline-offset:4px;cursor:pointer}
.workspace-navigation a:hover{color:#fff}.workspace-navigation a[aria-current="page"]{color:#fff;font-weight:700;text-decoration:none}
.workspace-navigation a[aria-disabled="true"]{color:#a4b2bf;text-decoration-style:dotted}
.workspace-navigation a:focus-visible{outline:3px solid #e6ba77;outline-offset:5px;border-radius:2px}
.workspace-navigation .workspace-navigation-status{color:#ccd6df;font:13px/1.5 system-ui,sans-serif;margin:10px 0 0}
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


def navigation_markup(root, page_id, *, report_directory=None):
    """Accessible report navigation, with useful links before JavaScript loads."""
    if page_id not in dict(PAGES):
        raise ValueError('Unknown workspace page')
    origins = _origins(root)
    items = []
    files = dict(zip(('usage', 'contributions'), REPORT_ROUTES.values()))
    if report_directory is not None:
        files = {key: (Path(report_directory).resolve() / name).as_uri() for key, name in files.items()}
    for identifier, title in PAGES:
        target = files.get(identifier) or (origins.get(identifier) or '')
        if identifier in origins and target:
            target += '/'
        attrs = ' data-workspace-page="' + identifier + '"'
        if identifier in files:
            attrs += ' data-workspace-file="' + escape(files[identifier], quote=True) + '"'
        if target:
            attrs += ' href="' + escape(target, quote=True) + '"'
        else:
            message = title + ' is not running. Open ' + LAUNCHERS[identifier] + ' in the Orchestrator folder, then reload this page.'
            attrs += ' role="link" tabindex="0" aria-disabled="true" title="' + escape(message, quote=True) + '"'
        if identifier == page_id:
            attrs += ' aria-current="page"'
        items.append('<li><a' + attrs + '>' + title + '</a></li>')
    return ('<nav class="workspace-navigation" aria-label="Workspace pages" data-workspace-current="' + page_id + '">'
            '<ol>' + ''.join(items) + '</ol>'
            '<p class="workspace-navigation-status" data-workspace-status role="status" hidden></p></nav>')


def decorate_report(page, root, page_id, *, report_directory=None):
    """Add shared navigation without touching report data or the report's code."""
    if 'data-workspace-current=' in page:
        return page
    markup = navigation_markup(root, page_id, report_directory=report_directory)
    style = '<style>' + STYLE + '</style>'
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
    script = '<script src="' + SCRIPT_NAME + '" defer></script>'
    end = re.search(r'</body\s*>|</html\s*>', page, re.IGNORECASE)
    position = end.start() if end else len(page)
    return page[:position] + script + page[position:]


_SCRIPT = r'''(function(){
"use strict";
const origins=__ORIGINS__;
const launchers=__LAUNCHERS__;
const routes={usage:"/usage",contributions:"/contributions"};
const files={usage:"usage-dashboard.html",contributions:"project-map.html"};
function cleanScope(value){
  const result={};
  if(value&&typeof value.project==="string"&&value.project.trim()&&value.project.length<=160)result.project=value.project;
  if(value&&typeof value.run==="string"&&/^[A-Za-z0-9][A-Za-z0-9._-]{0,199}$/.test(value.run))result.run=value.run;
  return result;
}
function locationScope(){
  const query=new URLSearchParams(location.search);
  // The Memory dashboard keeps its scope in the hash; saved reports use query.
  const hash=location.pathname==="/"?new URLSearchParams(location.hash.slice(1)):null;
  return cleanScope({project:query.get("project")||(hash&&hash.get("project")),run:query.get("run")||(hash&&hash.get("run"))});
}
let scope=locationScope();
function target(page,fileTarget){
  const params=new URLSearchParams(scope).toString();
  if(routes[page])return (location.protocol==="file:"?(fileTarget||files[page]):routes[page])+(params?"?"+params:"");
  const origin=origins[page];
  if(!origin)return null;
  return origin+"/"+(params?(page==="brain"?"#":"?")+params:"");
}
function unavailable(link){
  const page=link.dataset.workspacePage;
  const title=page==="brain"?"Memory":"Orchestrator";
  const message=title+" is not running. Open "+launchers[page]+" in the Orchestrator folder, then reload this page.";
  const status=link.closest("[data-workspace-current]")?.querySelector("[data-workspace-status]");
  if(status){status.textContent=message;status.hidden=false;}
}
function refresh(value){
  if(value!==undefined)scope=cleanScope(value);
  for(const link of document.querySelectorAll("a[data-workspace-page]")){
    const page=link.dataset.workspacePage;
    if(!Object.hasOwn(routes,page)&&!Object.hasOwn(origins,page))continue;
    const href=target(page,link.getAttribute("data-workspace-file"));
    if(href){link.setAttribute("href",href);link.removeAttribute("aria-disabled");link.removeAttribute("tabindex");link.removeAttribute("role");link.removeAttribute("title");}
    else{link.removeAttribute("href");link.setAttribute("aria-disabled","true");link.setAttribute("tabindex","0");link.setAttribute("role","link");link.title="Open "+launchers[page]+" in the Orchestrator folder, then reload this page.";}
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


def navigation_script(root, current=None, origin=None):
    """Return navigation JavaScript with only verified loopback page origins."""
    return _SCRIPT.replace('__ORIGINS__', json.dumps(_origins(root, current, origin), separators=(',', ':'))).replace(
        '__LAUNCHERS__', json.dumps(LAUNCHERS, separators=(',', ':')))


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
