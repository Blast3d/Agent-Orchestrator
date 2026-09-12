"""A dependency-free loopback dashboard for shared orchestrator memory."""
from __future__ import annotations

import argparse
import hmac
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import secrets
from threading import BoundedSemaphore
import webbrowser
from urllib.parse import parse_qs, urlsplit

MAX_REQUEST_BYTES = 65536


def _store(root):
    from brain_store import BrainStore
    return BrainStore(root=root)


class DashboardServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True

    def __init__(self, root, port=0, store_factory=None):
        self.root = Path(root).resolve()
        self.store_factory = store_factory or _store
        self.brain_token = secrets.token_urlsafe(32)
        self.instance_id = secrets.token_hex(16)
        self.nonce = secrets.token_urlsafe(24)
        self.request_slots = BoundedSemaphore(8)
        super().__init__(('127.0.0.1', port), DashboardHandler)
        self.origin = 'http://127.0.0.1:' + str(self.server_address[1])
        self.page_id = 'brain'
        self.service_cache = {}

    def process_request(self, request, client_address):
        if not self.request_slots.acquire(blocking=False):
            try:
                # Drain the unread request first: closing a socket with inbound bytes
                # pending makes Windows reset it, and the client never sees the 503.
                request.settimeout(0.5)
                try:
                    request.recv(MAX_REQUEST_BYTES)
                except OSError:
                    pass
                body=b'{"error":"The dashboard is busy. Wait a moment and retry."}'
                request.sendall(b'HTTP/1.0 503 Service Unavailable\r\nContent-Type: application/json\r\nCache-Control: no-store\r\nConnection: close\r\nContent-Length: '+str(len(body)).encode()+b'\r\n\r\n'+body)
            finally:
                self.shutdown_request(request)
            return
        try:
            super().process_request(request, client_address)
        except Exception:
            self.request_slots.release()
            raise

    def process_request_thread(self, request, client_address):
        try:
            super().process_request_thread(request, client_address)
        finally:
            self.request_slots.release()


class DashboardHandler(BaseHTTPRequestHandler):
    server_version = 'MemoryDashboard/1'
    sys_version = ''

    def setup(self):
        super().setup()
        self.connection.settimeout(10)

    def log_message(self, format, *args):
        # Never retain request addresses, search text or tokens in access logs.
        pass

    def _reply(self, status, payload, content_type='application/json; charset=utf-8', *, csp=None):
        body = (json.dumps(payload, ensure_ascii=False, allow_nan=False).encode('utf-8')
                if content_type.startswith('application/json') else payload.encode('utf-8'))
        self.send_response(status)
        self.send_header('Content-Type', content_type)
        self.send_header('Content-Length', str(len(body)))
        self.send_header('Cache-Control', 'no-store')
        self.send_header('Referrer-Policy', 'no-referrer')
        self.send_header('X-Content-Type-Options', 'nosniff')
        self.send_header('X-Frame-Options', 'DENY')
        self.send_header('Cross-Origin-Resource-Policy', 'same-origin')
        self.send_header('Content-Security-Policy', csp or
                         "default-src 'none'; script-src 'nonce-" + self.server.nonce +
                         "'; style-src 'nonce-" + self.server.nonce +
                         "'; connect-src 'self'; img-src data:; base-uri 'none'; "
                         "frame-ancestors 'none'; form-action 'none'; object-src 'none'")
        self.end_headers()
        self.wfile.write(body)

    def _system_map(self):
        from system_map import render_map, content_security_policy
        page = render_map(self.server.root)
        return self._reply(200,page,'text/html; charset=utf-8',csp=content_security_policy(page))

    def _workspace_page(self, address):
        """Serve only the two fixed report snapshots and their local navigation."""
        from workspace_navigation import REPORT_ROUTES, navigation_script
        if address.path == '/workspace-navigation.js' and not address.query:
            self._reply(200, navigation_script(self.server.root, self.server.page_id,
                                               self.server.origin), 'text/javascript; charset=utf-8')
            return True
        if address.path not in REPORT_ROUTES:
            return False
        params = parse_qs(address.query, max_num_fields=2, keep_blank_values=True)
        from local_services import RUN_NAME
        if (set(params) - {'project', 'run'} or any(len(v) != 1 for v in params.values())
                or len(params.get('project', [''])[0]) > 160
                or (params.get('run', [''])[0] and not RUN_NAME.fullmatch(params['run'][0]))):
            raise ValueError('Invalid project or run selection.')
        runtime = (self.server.root / 'runtime').resolve()
        path = runtime / REPORT_ROUTES[address.path]
        if path.resolve().parent != runtime:
            raise ValueError('This report is not available in this workspace.')
        try:
            with path.open('rb') as stream:
                raw = stream.read(8 * 1024 * 1024 + 1)
            if len(raw) > 8 * 1024 * 1024:
                raise ValueError('This report exceeds the page size limit.')
            # HTML parsing normalizes line endings before CSP hashes are checked.
            page = raw.decode('utf-8').replace('\r\n', '\n').replace('\r', '\n')
        except FileNotFoundError:
            self._reply(404, '<!doctype html><title>Report unavailable</title>'
                        '<h1>This report has not been generated yet.</h1>'
                        '<p>Use the Provider usage or Contribution maps launcher in AI-Workspace, then retry.</p>'
                        '<p><a href="/">Return to the dashboard</a></p>', 'text/html; charset=utf-8')
            return True
        from system_map import content_security_policy
        csp = content_security_policy(page).replace('script-src ', "script-src 'self' ", 1)
        self._reply(200, page, 'text/html; charset=utf-8', csp=csp)
        return True

    def _error(self, status, message):
        self._reply(status, {'error': message})

    def _same_site_page_navigation(self):
        # Sibling ports and standalone file reports may navigate to a UI page.
        # SOP protects the token-bearing document. API reads, frames, scripts
        # and writes never receive this top-level-navigation exception.
        return (self.command == 'GET' and urlsplit(self.path).path in
                ('/', '/usage', '/contributions', '/system-map')
                and self.headers.get_all('Sec-Fetch-Site', []) in (['same-site'], ['cross-site'])
                and self.headers.get_all('Sec-Fetch-Mode', []) == ['navigate']
                and self.headers.get_all('Sec-Fetch-Dest', []) == ['document'])

    def _gate(self, mutation=False):
        if len(self.path) > 2048:
            self._error(414, 'Request address is too long.')
            return False
        if not self.path.startswith('/') or self.path.startswith('//') or '#' in self.path:
            self._error(400, 'Invalid request address.')
            return False
        hosts = self.headers.get_all('Host', [])
        if hosts != [self.server.origin.removeprefix('http://')]:
            self._error(403, 'Open the dashboard using the 127.0.0.1 address from its launcher.')
            return False
        origins = self.headers.get_all('Origin', [])
        if (origins and origins != [self.server.origin]) or (mutation and origins != [self.server.origin]):
            self._error(403, 'This request must come from the dashboard.')
            return False
        if (self.headers.get('Sec-Fetch-Site') not in (None, 'none', 'same-origin')
                and not self._same_site_page_navigation()):
            self._error(403, 'This request must come from the dashboard.')
            return False
        if self.path.startswith('/api/'):
            tokens = self.headers.get_all('X-Brain-Token', [])
            if len(tokens) != 1 or not hmac.compare_digest(tokens[0].encode('utf-8'), self.server.brain_token.encode('ascii')):
                self._error(403, 'Reload the dashboard to reconnect securely.')
                return False
        return True

    @staticmethod
    def _project(value):
        if not isinstance(value, str) or not value.strip() or len(value) > 160:
            raise ValueError('Choose a project first.')
        return value.strip()

    def _services(self, project=None, run=None):
        from local_services import links
        return {'current': self.server.page_id,
                'services': links(self.server.root, self.server.page_id, project, run, self.server.service_cache)}

    def _open_service(self, data):
        from local_services import ensure
        return ensure(self.server.root, self._text(data.get('target'), 'Page', 40))

    @staticmethod
    def _cursor(value):
        if not (value.isascii() and value.isdigit() and len(value) <= 12):
            raise ValueError('Invalid change cursor.')
        return int(value)

    @staticmethod
    def _text(value, name, maximum=500):
        if not isinstance(value, str) or not value.strip() or len(value) > maximum:
            raise ValueError(name + ' is required and must fit its character limit.')
        return value.strip()

    def _memory(self, store, memory_id, project):
        item = store.get(self._text(memory_id, 'Memory', 128))
        if item.get('user_id', 'local') != 'local' or item.get('project_id') != project:
            raise ValueError('That memory is not available in this project.')
        return item

    def _handle_error(self, error):
        if isinstance(error, (ValueError, KeyError, TypeError)):
            self._error(400, str(error) if isinstance(error, ValueError) else 'The request has missing or invalid fields.')
        elif error.__class__.__name__ == 'StorageLimitError':
            self._error(507, 'Storage is at its limit. Free eligible data before adding more memories.')
        else:
            self._error(500, 'The memory service could not complete this request. Try again or check its local status.')

    def do_GET(self):
        if not self._gate():
            return
        address = urlsplit(self.path)
        try:
            if self._workspace_page(address):
                return
            if address.path == '/system-map' and not address.query:
                return self._system_map()
            if address.path == '/' and not address.query:
                return self._reply(200, PAGE.replace('__NONCE__', self.server.nonce)
                                   .replace('__TOKEN__', self.server.brain_token), 'text/html; charset=utf-8')
            if address.path == '/health' and not address.query:
                return self._reply(200, {'service': 'orchestrator-brain-dashboard', 'version': 1,
                                         'instance_id': self.server.instance_id})
            if address.path == '/favicon.ico':
                return self._reply(204, '', 'image/svg+xml')
            if address.path not in ('/api/status', '/api/snapshot', '/api/memories', '/api/changes', '/api/services', '/api/usage', '/api/use') and not address.path.startswith('/api/memories/'):
                return self._error(404, 'This page is not available.')
            params = parse_qs(address.query, max_num_fields=5, keep_blank_values=True)
            allowed_params = {'/api/memories': {'project_id', 'status'}, '/api/changes': {'project_id', 'after'},
                              '/api/services': {'project_id', 'run'},
                              '/api/use': {'project_id', 'job_id'}}.get(address.path, {'project_id'})
            if set(params) - allowed_params or any(len(v) != 1 for v in params.values()):
                raise ValueError('Invalid project selection.')
            if address.path == '/api/services':
                project = params.get('project_id', [None])[0]
                run = params.get('run', [None])[0]
                from local_services import RUN_NAME
                if run and not RUN_NAME.fullmatch(run):
                    raise ValueError('Invalid run selection.')
                return self._reply(200, self._services(self._project(project) if project else None, run))
            if address.path == '/api/usage':
                from project_memory_usage import project_summary
                project = self._project(params.get('project_id', [''])[0])
                return self._reply(200, project_summary(self.server.root, project))
            if address.path == '/api/use':
                from memory_usage import review_evidence
                project = self._project(params.get('project_id', [''])[0])
                return self._reply(200, review_evidence(self.server.root, project,
                    params.get('job_id', [''])[0]))
            store = self.server.store_factory(self.server.root)
            if address.path == '/api/status':
                return self._reply(200, store.status())
            project = self._project(params.get('project_id', [''])[0])
            if address.path == '/api/memories':
                return self._reply(200, store.list_memories(project_id=project, user_id='local',
                    status=params.get('status', [None])[0], limit=200))
            if address.path == '/api/snapshot':
                return self._reply(200, store.snapshot(project_id=project, user_id='local'))
            if address.path == '/api/changes':
                # The page polls this; `head` lets it start from "now" and badge only later writes.
                return self._reply(200, store.changes(project_id=project, user_id='local',
                                                      after=self._cursor(params.get('after', ['0'])[0]), limit=50))
            return self._reply(200, self._memory(store, address.path.removeprefix('/api/memories/'), project))
        except Exception as error:
            self._handle_error(error)

    def _body(self):
        if self.headers.get('Transfer-Encoding'):
            raise ValueError('Chunked requests are not supported.')
        lengths = self.headers.get_all('Content-Length', [])
        if len(lengths) != 1:
            raise ValueError('Provide one request length.')
        try:
            length = int(lengths[0])
        except ValueError:
            raise ValueError('Invalid request length.') from None
        if length < 0 or length > MAX_REQUEST_BYTES:
            self._error(413, 'This request is too large. Keep memories concise.')
            return None
        if self.headers.get_content_type() != 'application/json':
            self._error(415, 'Send this request as JSON.')
            return None
        body = self.rfile.read(length)
        if len(body) != length:
            raise ValueError('The request was incomplete.')
        try:
            def bad_constant(value):
                raise ValueError('Non-finite numbers are not supported.')
            data = json.loads(body.decode('utf-8'), parse_constant=bad_constant)
        except (UnicodeError, json.JSONDecodeError):
            raise ValueError('The request is not valid JSON.') from None
        if not isinstance(data, dict):
            raise ValueError('The request must be a JSON object.')
        if data.get('user_id', 'local') != 'local':
            raise ValueError('This dashboard only manages the local user.')
        return data

    def do_POST(self):
        if not self._gate(mutation=True):
            return
        if self.path not in ('/api/search', '/api/memories', '/api/approve', '/api/forget', '/api/supersede', '/api/relate', '/api/open', '/api/feedback'):
            return self._error(404, 'This action is not available.')
        try:
            data = self._body()
            if data is None:
                return
            if self.path == '/api/open':
                return self._reply(200, self._open_service(data))
            project = self._project(data.get('project_id'))
            if self.path == '/api/feedback':
                allowed = {'project_id', 'job_id', 'rating', 'reviewer', 'note', 'user_id',
                           'expected_source_sha256', 'expected_context_binding_sha256'}
                if set(data) - allowed:
                    raise ValueError('The feedback request contains unsupported fields.')
                from memory_usage import record_feedback
                from project_memory_usage import invalidate
                from task_store import TaskStore
                result = record_feedback(TaskStore(self.server.root / 'runs/tasks'),
                    self._text(data.get('job_id'), 'Task', 32), data.get('rating'),
                    data.get('reviewer'), data.get('note'),
                    expected_project_id=project, expected_user_id='local',
                    expected_source_sha256=data.get('expected_source_sha256'),
                    expected_context_binding_sha256=data.get('expected_context_binding_sha256'))
                invalidate(self.server.root, project)
                return self._reply(200, result)
            store = self.server.store_factory(self.server.root)
            if self.path == '/api/search':
                result = store.search(query=self._text(data.get('query'), 'Search', 500),
                                      project_id=project, user_id='local', limit=6, max_chars=8000, hops=1)
            elif self.path == '/api/memories':
                allowed = {'kind', 'title', 'content', 'source', 'tags', 'importance', 'episode', 'valid_from', 'valid_to'}
                payload = {key: value for key, value in data.items() if key in allowed}
                payload.update(project_id=project, user_id='local')
                result = store.propose(payload)
            else:
                item = self._memory(store, data.get('memory_id'), project)
                if self.path == '/api/approve':
                    result = store.approve(item['id'], self._text(data.get('reviewer'), 'Reviewer', 120),
                                           self._text(data.get('note'), 'Review note', 2000))
                elif self.path == '/api/forget':
                    result = store.forget(item['id'], self._text(data.get('actor'), 'Name', 120),
                                          self._text(data.get('reason'), 'Reason', 2000))
                elif self.path == '/api/supersede':
                    new = self._memory(store, data.get('new_id'), project)
                    result = store.supersede(item['id'], new['id'], self._text(data.get('actor'), 'Name', 120),
                                             self._text(data.get('reason'), 'Reason', 2000))
                else:
                    target = self._memory(store, data.get('target_id'), project)
                    result = store.relate(item['id'], target['id'], self._text(data.get('relation'), 'Relationship', 50),
                                          self._text(data.get('actor'), 'Name', 120),
                                          valid_from=data.get('valid_from'), valid_to=data.get('valid_to'))
            self._reply(201 if self.path == '/api/memories' else 200, result)
        except Exception as error:
            self._handle_error(error)

    def _unsupported(self):
        if self._gate():
            self._error(405, 'This request method is not supported.')

    do_PUT = do_DELETE = do_PATCH = do_OPTIONS = do_HEAD = do_TRACE = _unsupported


def make_server(root, port=0, store_factory=None):
    """Create a loopback-only server without opening a browser or starting a thread."""
    return DashboardServer(root=root, port=port, store_factory=store_factory)


def main(argv=None):
    parser = argparse.ArgumentParser(description='Open the local orchestrator memory dashboard.')
    parser.add_argument('--root', default=str(Path(__file__).resolve().parents[1]))
    parser.add_argument('--port', type=int, default=0)
    parser.add_argument('--open', action='store_true', dest='open_browser')
    parser.add_argument('--state-file', help='Local launcher state file; contains no authentication token.')
    args = parser.parse_args(argv)
    with make_server(args.root, port=args.port) as server:
        state_file = Path(args.state_file).resolve() if args.state_file else None
        state = {'pid': os.getpid(), 'origin': server.origin,
                 'service': 'orchestrator-brain-dashboard', 'version': 1, 'instance_id': server.instance_id}
        if state_file:
            state_file.parent.mkdir(parents=True, exist_ok=True)
            temporary = state_file.with_name(state_file.name + '.' + secrets.token_hex(8) + '.tmp')
            try:
                temporary.write_text(json.dumps(state) + '\n', encoding='utf-8')
                os.replace(temporary, state_file)
            finally:
                temporary.unlink(missing_ok=True)
        from workspace_navigation import write_navigation_script
        try:
            write_navigation_script(server.root, server.page_id, server.origin)
        except OSError:
            print('Saved report links could not be refreshed; live dashboard navigation remains available.', flush=True)
        print('Memory dashboard: ' + server.origin + '/', flush=True)
        try:
            if args.open_browser:
                webbrowser.open(server.origin + '/')
            server.serve_forever(poll_interval=0.2)
        except KeyboardInterrupt:
            pass
        finally:
            if state_file:
                try:
                    current = json.loads(state_file.read_text(encoding='utf-8'))
                    if (current.get('pid') == state['pid'] and current.get('origin') == state['origin']
                            and current.get('instance_id') == state['instance_id']):
                        state_file.unlink(missing_ok=True)
                except (OSError, ValueError, AttributeError):
                    pass
    return 0


PAGE = r'''<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<meta name="brain-token" content="__TOKEN__"><title>Memory · Orchestrator</title>
<style nonce="__NONCE__">
:root{color-scheme:dark;--bg:#0c111a;--panel:#131b27;--line:#293648;--text:#edf3fa;--muted:#9faec2;--blue:#a3bdff;--mint:#79dfc5;--amber:#f4c878;--red:#ff9eac;font:15px/1.5 system-ui,-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif}*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--text)}button,input,textarea,select{font:inherit;border:1px solid var(--line);border-radius:9px;background:#111a27;color:var(--text)}button{cursor:pointer;padding:9px 14px;transition:background .15s,border-color .15s}button:hover{background:#26364c;border-color:#60779a}button:disabled{opacity:.45;cursor:not-allowed}button:focus-visible,input:focus-visible,textarea:focus-visible,select:focus-visible,[tabindex]:focus-visible{outline:3px solid var(--blue);outline-offset:3px}button.primary{color:#101c1b;background:var(--mint);border-color:var(--mint);font-weight:700}button.danger{color:var(--red)}input,select,textarea{padding:10px 12px;width:100%}textarea{resize:vertical;min-height:110px}h1,h2,h3,p{margin-top:0}h1{font-size:30px;line-height:1.2;letter-spacing:-1px;margin-bottom:9px}h2{font-size:19px;margin-bottom:7px}h3{font-size:15px}small,.muted{color:var(--muted)}.app{display:grid;grid-template-columns:242px 1fr;min-height:100vh}.sidebar{padding:29px 21px;border-right:1px solid var(--line);background:#101722;display:flex;flex-direction:column;gap:28px}.brand{display:flex;align-items:center;gap:12px;font-weight:750;font-size:18px}.brand small{font-size:11px;font-weight:400}.logo{width:35px;height:35px;border:1px solid #50678a;border-radius:11px;display:grid;place-items:center;color:var(--mint);background:linear-gradient(140deg,#233b49,#172132);font-size:23px}.sidebar label{display:block;font-size:11px;color:var(--muted);letter-spacing:1px;margin-bottom:7px}.nav{display:grid;gap:7px}.nav button{display:flex;gap:12px;align-items:center;width:100%;text-align:left;border-color:transparent;background:transparent;color:var(--muted)}.nav button[aria-current=page]{background:#233047;color:var(--text)}.nav-symbol{width:22px;text-align:center;color:var(--blue)}.side-note{margin-top:auto;padding:16px 0 0;border-top:1px solid var(--line);font-size:12px;color:var(--muted)}.local-dot{display:inline-block;width:7px;height:7px;background:var(--mint);border-radius:50%;margin-right:7px}.main{min-width:0;padding:28px 38px 42px;max-width:1570px;width:100%;margin:0 auto}.topbar{display:flex;justify-content:space-between;align-items:center;gap:20px;margin-bottom:32px}.breadcrumb{font-size:12px;color:var(--muted)}.breadcrumb a{color:var(--blue);text-decoration:none}.breadcrumb a:hover{text-decoration:underline}.top-actions{display:flex;gap:9px}.heading{display:flex;justify-content:space-between;align-items:start;gap:20px}.heading p{color:var(--muted);max-width:640px}.pill{font-size:11px;line-height:1.4;padding:5px 9px;border-radius:30px;background:#253349;color:#bdd0ef;display:inline-flex;align-items:center;gap:5px;white-space:nowrap}.pill.active{background:#173c34;color:var(--mint)}.pill.pending{background:#3d321f;color:var(--amber)}.pill.superseded,.pill.expired{background:#30313d;color:#b6b8cd}.pill.deleted{background:#36242d;color:var(--red)}.stats{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:15px;margin:22px 0}.stat{background:var(--panel);border:1px solid var(--line);border-radius:16px;padding:19px 20px;min-height:129px}.stat-label{font-size:12px;color:var(--muted)}.stat-number{font-size:27px;line-height:1.3;letter-spacing:-.7px;font-weight:650;margin:8px 0 4px}.stat-caption{font-size:11px;color:var(--muted)}.meter{height:4px;background:#28374a;border-radius:5px;margin-top:12px;overflow:hidden}.meter-fill{height:100%;background:var(--mint);width:0}.workspace{display:grid;grid-template-columns:minmax(0,1.25fr) minmax(300px,1fr);gap:20px}.panel{background:var(--panel);border:1px solid var(--line);border-radius:16px;overflow:hidden;min-width:0}.panel-head{display:flex;justify-content:space-between;align-items:center;gap:16px;padding:21px 22px 17px;border-bottom:1px solid var(--line)}.panel-head h2{font-size:16px;margin-bottom:3px}.panel-head p{font-size:12px;color:var(--muted);margin-bottom:0}.panel-body{padding:20px 22px}.searchbar{display:flex;gap:9px;margin-bottom:16px}.searchbar input{min-width:0}.filters{display:flex;gap:7px;flex-wrap:wrap;margin-bottom:14px}.filters button{font-size:12px;padding:5px 10px;border-radius:30px;background:transparent}.filters button.active{background:#2b3a52;border-color:#657da2}.memory-list{display:grid;gap:10px}.memory-card{display:block;text-align:left;width:100%;background:#172130;border:1px solid var(--line);border-radius:11px;padding:15px;min-width:0}.memory-card:hover{background:#1e2b3e;border-color:#58718f}.card-top{display:flex;align-items:center;justify-content:space-between;gap:10px;margin-bottom:8px}.kind{font-size:10px;letter-spacing:1px;text-transform:uppercase;color:var(--blue);font-weight:700}.memory-card h3{font-size:14px;margin-bottom:5px;overflow-wrap:anywhere}.memory-card p{font-size:12px;color:var(--muted);display:-webkit-box;-webkit-line-clamp:2;-webkit-box-orient:vertical;overflow:hidden;margin:0 0 9px;overflow-wrap:anywhere}.card-bottom{display:flex;justify-content:space-between;gap:10px;font-size:10px;color:#aebed2}.tag{font-size:10px;background:#29364b;color:#c0cee0;border-radius:5px;padding:2px 6px;display:inline-block;margin:0 5px 3px 0}.empty{text-align:center;padding:34px 18px;color:var(--muted);font-size:13px}.empty-icon{font-size:26px;color:var(--blue);margin-bottom:10px}.empty strong{display:block;font-size:15px;color:var(--text);margin-bottom:6px}.graph-wrap{background:radial-gradient(ellipse at center,#1d2a3a 0,#121b28 72%);position:relative;min-height:350px;overflow:hidden}.graph{display:block;width:100%;height:350px;touch-action:none}.graph-tools{position:absolute;top:12px;right:12px;display:flex;gap:4px;z-index:1}.graph-tools button{font-size:13px;padding:4px 9px;background:#142033}.graph-empty{position:absolute;inset:45px 18px;pointer-events:none;display:flex;align-items:center;justify-content:center}.graph-caption{font-size:11px;color:var(--muted);padding:10px 20px 15px}.graph line{stroke:#465b77;stroke-width:1.3}.graph .node{cursor:pointer}.graph .node circle{stroke:#172333;stroke-width:6}.graph .node:hover circle,.graph .node:focus circle{stroke:#e7efff;stroke-width:3}.graph text{fill:#dbe7f7;font-size:11px;text-anchor:middle;pointer-events:none;paint-order:stroke;stroke:#15202f;stroke-width:4px;stroke-linejoin:round}.legend{display:flex;gap:13px;flex-wrap:wrap;font-size:10px;color:var(--muted)}.legend span:before{content:'●';margin-right:5px}.legend .fact{color:#a3bdff}.legend .preference{color:#79dfc5}.legend .episode{color:#efc67e}.legend .procedure{color:#d3abff}.wide{grid-column:1/-1}.activity-table{width:100%;border-collapse:collapse;text-align:left;font-size:12px}.activity-table th{font-size:10px;text-transform:uppercase;letter-spacing:.8px;color:var(--muted);font-weight:600}.activity-table td,.activity-table th{padding:12px 15px;border-bottom:1px solid var(--line)}.activity-table tr:last-child td{border-bottom:none}.notice{background:#1e3440;border:1px solid #315866;border-radius:10px;padding:12px 16px;font-size:12px;color:#c9e9e2;margin-bottom:20px}.notice.error{background:#39232a;border-color:#75404d;color:#ffd3dc}[hidden]{display:none!important}.footer{font-size:10px;color:var(--muted);margin-top:25px;display:flex;gap:20px;justify-content:space-between}.review-info{border-left:3px solid var(--amber);background:#29291f;padding:13px 16px;font-size:12px;color:#e8d9b6;margin-bottom:16px}.modal{border:1px solid #4a5f7c;color:var(--text);background:#121c2a;border-radius:18px;width:min(650px,calc(100vw - 30px));max-height:90vh;padding:0;box-shadow:0 24px 100px #0009}.modal::backdrop{background:#040912c9;backdrop-filter:blur(4px)}.modal-header{padding:22px 25px;border-bottom:1px solid var(--line);display:flex;justify-content:space-between;align-items:center;gap:20px}.modal-header h2{margin:0;font-size:19px;overflow-wrap:anywhere}.close{border:0;background:transparent;font-size:22px;padding:0 7px;color:var(--muted)}.modal-body{padding:23px 25px;overflow-wrap:anywhere}.modal-actions{display:flex;gap:8px;flex-wrap:wrap;padding-top:20px;border-top:1px solid var(--line);margin-top:22px}.form-grid{display:grid;grid-template-columns:1fr 1fr;gap:15px}.field{display:grid;gap:6px;font-size:12px;color:#c2cede}.field.full{grid-column:1/-1}.field small{font-size:10px}.form-actions{margin-top:22px;display:flex;gap:8px;justify-content:flex-end}.detail-content{white-space:pre-wrap;line-height:1.75;font-size:14px;margin:18px 0 20px;color:#e0e9f5}.metadata{display:grid;grid-template-columns:110px 1fr;gap:9px;font-size:12px;margin:18px 0}.metadata dt{color:var(--muted)}.metadata dd{margin:0;white-space:pre-wrap}.detail-section{border-top:1px solid var(--line);padding-top:17px;margin-top:19px}.detail-section h3{font-size:12px;text-transform:uppercase;letter-spacing:1px;color:var(--muted)}.timeline{border-left:1px solid #435870;margin-left:5px;padding-left:18px;display:grid;gap:15px}.timeline-item{position:relative;font-size:12px}.timeline-item:before{content:'';position:absolute;left:-23px;top:6px;width:8px;height:8px;border-radius:50%;background:var(--blue)}.timeline-item small{display:block;font-size:10px;margin-top:3px}.related-button{display:block;width:100%;text-align:left;margin:7px 0;font-size:12px}.action-help{font-size:12px;color:var(--muted);margin-bottom:17px}.inline-error{color:var(--red);font-size:12px;margin:12px 0}.pending-count{margin-left:auto;background:#55432c;color:var(--amber);border-radius:5px;padding:0 5px;font-size:10px}.view:focus{outline:none}
.map-link{color:var(--blue);align-self:center}.evidence-preview{white-space:pre-wrap;max-height:260px;overflow:auto}
@media(min-width:1450px){.graph,.graph-wrap{min-height:405px}.graph{height:405px}.main{padding-left:45px;padding-right:45px}.memory-list{gap:12px}}
@media(max-width:1100px){.app{grid-template-columns:210px 1fr}.main{padding:24px}.sidebar{padding:26px 15px}.stats{grid-template-columns:repeat(2,minmax(0,1fr))}.workspace{grid-template-columns:1fr}.graph,.graph-wrap{min-height:340px}.topbar{margin-bottom:23px}}
@media(max-width:720px){.app{display:block}.sidebar{padding:14px 17px;border-right:0;border-bottom:1px solid var(--line);gap:15px}.brand{font-size:16px}.sidebar .project-block{display:grid;grid-template-columns:65px 1fr;align-items:center;gap:8px}.sidebar label{margin:0}.nav{display:flex;overflow-x:auto;gap:4px}.nav button{width:auto;white-space:nowrap;font-size:12px;padding:8px}.nav-symbol{display:none}.side-note{display:none}.main{padding:20px 16px}.topbar{margin-bottom:22px}.breadcrumb{font-size:11px}.top-actions button{padding:7px 10px;font-size:12px}h1{font-size:26px}.heading p{font-size:13px}.heading>.pill{display:none}.stats{gap:10px;margin:18px 0}.stat{padding:14px;min-height:113px}.stat-number{font-size:25px}.stat-label{font-size:11px}.panel-head,.panel-body{padding:16px}.legend{gap:8px}.footer{display:block;line-height:2}.activity-table th,.activity-table td{padding:10px 8px;font-size:11px}.form-grid{grid-template-columns:1fr}.field.full{grid-column:auto}.modal-body,.modal-header{padding:20px}.graph,.graph-wrap{min-height:300px}.graph{height:300px}.card-bottom{flex-wrap:wrap}}
@media(prefers-reduced-motion:reduce){*{transition:none!important;scroll-behavior:auto!important}}
.workspace-crumbs{display:flex;align-items:center;flex-wrap:wrap;gap:8px 10px;line-height:1.8}.workspace-crumbs [aria-current=page]{color:var(--mint);font-weight:700}.workspace-crumbs a:focus-visible{outline:3px solid var(--blue);outline-offset:3px}.section-crumb{margin-top:5px}.topbar{align-items:flex-start}.top-actions{flex-wrap:wrap}
</style></head><body>
<div class="app"><aside class="sidebar"><div class="brand"><span class="logo" aria-hidden="true">◎</span><span>Orchestrator<br><small>Memory workspace</small></span></div>
<div class="project-block"><label for="project">PROJECT</label><select id="project" aria-label="Current project"><option value="general">General</option></select></div>
<nav class="nav" aria-label="Memory navigation"><button data-view="overview" aria-current="page"><span class="nav-symbol">◈</span>Overview</button><button data-view="library"><span class="nav-symbol">▤</span>Memory library</button><button data-view="graph"><span class="nav-symbol">⌘</span>Relationships</button><button data-view="review"><span class="nav-symbol">◷</span>Review queue<span id="nav-pending" class="pending-count">0</span></button><button data-view="activity"><span class="nav-symbol">↗</span>Recall activity</button></nav>
<div class="side-note"><p><span class="local-dot"></span>Shared by ASTRA &amp; Fable</p><p>Evidence stays attached.<br>Every update has a history.</p><span>Runs on this computer.</span></div></aside>
<main class="main"><div class="topbar"><div><nav class="breadcrumb workspace-crumbs" aria-label="Workspace pages"><a id="crumb-viewer" href="#" title="Open the Orchestrator viewer">Orchestrator</a><span aria-hidden="true">/</span><span aria-current="page">Memory</span><span aria-hidden="true">/</span><a id="crumb-usage" href="/usage">Provider usage</a><span aria-hidden="true">/</span><a id="crumb-contributions" href="/contributions">Contribution maps</a></nav><div class="breadcrumb section-crumb" id="crumb">Overview</div></div><div class="top-actions"><a href="/system-map" target="_blank" rel="noopener" class="map-link">System map</a><button id="refresh" title="Apply pending updates or retry a failed refresh" hidden>↻ &nbsp;Refresh</button><button id="add" class="primary">+ &nbsp;Add memory</button></div></div>
<div id="notice" class="notice" role="status" aria-live="polite" hidden></div><p id="memory-connection" class="muted" role="status">Connecting to memory…</p>
<div class="heading"><div><h1 id="page-title">Memory, with context.</h1><p id="page-description">Keep useful knowledge close, with the evidence and relationships that make it trustworthy.</p></div><span class="pill active"><span class="local-dot"></span>Local workspace</span></div>
<section class="stats" aria-label="Memory statistics"><div class="stat"><div class="stat-label">Reviewed memories</div><div class="stat-number" id="stat-active">—</div><div class="stat-caption">Approved memories in this project</div></div><div class="stat"><div class="stat-label">Awaiting your review</div><div class="stat-number" id="stat-pending">—</div><div class="stat-caption">Proposals stay out of recall</div></div><div class="stat"><div class="stat-label">Memory storage · all projects</div><div class="stat-number" id="stat-storage">—</div><div class="stat-caption" id="stat-storage-caption">Loading storage limits</div><div class="meter"><div class="meter-fill" id="storage-fill"></div></div></div><div class="stat"><div class="stat-label">Latest lookup</div><div class="stat-number" id="stat-speed">—</div><div class="stat-caption" id="stat-speed-caption">No searches yet in this project</div></div></section>
<section id="view" class="view" tabindex="-1"></section>
<footer class="footer"><span id="footer-storage">Checking application storage…</span><span>Approved knowledge. Bounded recall. No model needed for search.</span></footer>
</main></div>
<dialog id="detail" class="modal" aria-labelledby="detail-title"><div class="modal-header"><h2 id="detail-title">Memory</h2><button class="close" data-close="detail" aria-label="Close memory details">×</button></div><div id="detail-body" class="modal-body"></div></dialog>
<dialog id="editor" class="modal" aria-labelledby="editor-title"><div class="modal-header"><h2 id="editor-title">Add a memory</h2><button class="close" data-close="editor" aria-label="Close memory form">×</button></div><form id="memory-form" class="modal-body"><p class="action-help">Save a concise proposal. It becomes available to ASTRA and Fable after an explicit review.</p><div class="form-grid"><label class="field">Project<input id="new-project" required maxlength="100" autocomplete="off"></label><label class="field">Memory type<select id="new-kind"><option value="fact">Fact</option><option value="preference">Preference</option><option value="episode">Problem &amp; solution</option><option value="procedure">Procedure</option></select></label><label class="field full">Title<input id="new-title" required maxlength="160" placeholder="A useful, specific heading"></label><label class="field full">What should be remembered?<textarea id="new-content" required maxlength="3000" placeholder="Keep the lasting insight, including any conditions that matter."></textarea></label><div id="episode-fields" class="field full" hidden><label class="field">Problem<input id="episode-problem" maxlength="1000"></label><label class="field">Action<input id="episode-action" maxlength="1000"></label><label class="field">Outcome<input id="episode-outcome" maxlength="1000"></label></div><label class="field full">Evidence or source note<textarea id="new-source" required maxlength="1000" placeholder="Where did this knowledge come from? What supports it?"></textarea><small>For a reviewed worker result, use the orchestrator's task-memory command.</small></label><label class="field">Tags<input id="new-tags" maxlength="500" placeholder="Optional, separated by commas"></label><label class="field">Importance<select id="new-importance"><option value="0.5">Normal</option><option value="0.9">High · recurring or critical</option><option value="0.2">Low · occasional detail</option></select></label></div><div id="editor-error" class="inline-error" role="alert" hidden></div><div class="form-actions"><button type="button" data-close="editor">Cancel</button><button class="primary" type="submit">Save for review</button></div></form></dialog>
<dialog id="action" class="modal" aria-labelledby="action-title"><div class="modal-header"><h2 id="action-title">Update memory</h2><button class="close" data-close="action" aria-label="Close action">×</button></div><form id="action-form" class="modal-body"><div id="action-fields"></div><div id="action-error" class="inline-error" role="alert" hidden></div><div class="form-actions"><button type="button" data-close="action">Cancel</button><button id="action-submit" class="primary" type="submit">Save</button></div></form></dialog>
<dialog id="feedback" class="modal" aria-labelledby="feedback-title"><div class="modal-header"><h2 id="feedback-title">Review memory usefulness</h2><button class="close" data-close="feedback" aria-label="Close usefulness review">Close</button></div><form id="feedback-form" class="modal-body"><p class="action-help">Compare the supplied memories with the accepted answer. This records your judgment, not a measured causal effect.</p><p id="feedback-task"></p><details open><summary>Saved memory context</summary><pre id="feedback-context" class="evidence-preview"></pre></details><details><summary>Accepted answer</summary><pre id="feedback-answer" class="evidence-preview"></pre></details><p id="feedback-limits" class="muted"></p><div class="form-grid"><label class="field">Rating<select id="feedback-rating"><option value="helped">Helped</option><option value="neutral">Neutral</option><option value="harmful">Harmful</option></select></label><label class="field">Reviewer<input id="feedback-reviewer" required maxlength="100"></label><label class="field full">Evidence<textarea id="feedback-note" required minlength="20" maxlength="1000" placeholder="What in the answer shows that memory helped, was irrelevant, or caused a problem?"></textarea></label></div><p id="feedback-error" class="inline-error" role="alert"></p><div class="form-actions"><button type="button" data-close="feedback">Cancel</button><button type="submit" class="primary">Save review</button></div></form></dialog>
<script nonce="__NONCE__">
'use strict';
const $ = id => document.getElementById(id);
const token = document.querySelector('meta[name="brain-token"]').content;
const state = {view:'overview',project:'general',projectInitialized:false,status:null,snapshot:{memories:[],relations:[],traces:[]},filter:'active',searchResults:null,reviewMemories:null,query:'',detail:null,action:null,sequence:0,cursor:0,pending:0,recallRevision:null,usage:null,usageRevision:null,connectionError:false,services:[],run:new URLSearchParams(location.hash.slice(1)).get('run')||'',polling:false};
const kinds = {fact:'Fact',preference:'Preference',episode:'Problem & solution',procedure:'Procedure'};
const colors = {fact:'#a3bdff',preference:'#79dfc5',episode:'#efc67e',procedure:'#d3abff'};
const titles = {overview:['Memory, with context.','Keep useful knowledge close, with the evidence and relationships that make it trustworthy.'],library:['Your memory library.','Search approved knowledge or browse the recent history of this project.'],graph:['See the connections.','Follow the links between problems, decisions, solutions, and supporting evidence.'],review:['Review before recall.','Check the evidence, then decide what the orchestrator should remember.'],activity:['Recall, made visible.','See which memories searches returned. Compare lookup receipts, requested worker context, and explicit usefulness reviews.']};
function el(tag,cls,text){const node=document.createElement(tag);if(cls)node.className=cls;if(text!==undefined)node.textContent=String(text);return node;}
function button(text,fn,cls){const node=el('button',cls,text);node.type='button';node.addEventListener('click',fn);return node;}
function date(value){if(!value)return 'Not recorded';const d=new Date(value);return Number.isNaN(d.valueOf())?String(value):d.toLocaleString(undefined,{dateStyle:'medium',timeStyle:'short'});}
function bytes(value){const n=Number(value)||0;if(n>=1073741824)return (n/1073741824).toFixed(2)+' GiB';if(n>=1048576)return (n/1048576).toFixed(1)+' MiB';if(n>=1024)return (n/1024).toFixed(0)+' KiB';return n+' B';}
function notice(message,error=false){if(state.maintenanceWarning)message=(message?message+' ':'')+state.maintenanceWarning;$('notice').textContent=message;$('notice').classList.toggle('error',error);$('notice').hidden=!message;}
async function api(path,body){const controller=new AbortController(),timeout=setTimeout(()=>controller.abort(),15000);const options={headers:{'X-Brain-Token':token},cache:'no-store',credentials:'same-origin',signal:controller.signal};if(body!==undefined){options.method='POST';options.headers['Content-Type']='application/json';options.body=JSON.stringify(body);}try{let response;try{response=await fetch(path,options);}catch(e){throw new Error(e.name==='AbortError'?'The memory service took too long. It will retry.':'The dashboard is disconnected. Reopen it from the Orchestrator shortcut.');}let result;try{result=await response.json();}catch(e){throw new Error(response.status===503?'The dashboard is busy. Wait a moment and retry.':'The dashboard returned an unreadable response. Refresh or reopen it.');}if(result?.write_status?.cleanup_pending)state.maintenanceWarning='Saved, but storage reservation cleanup needs inspection.';if(!response.ok)throw new Error(result.error||'The request could not be completed.');return result;}finally{clearTimeout(timeout);}}
function projectQuery(){return '?project_id='+encodeURIComponent(state.project);}
function renderRefreshButton(){const n=state.pending;$('refresh').hidden=!n&&!state.connectionError;$('refresh').textContent=n?'Apply pending updates':'Retry now';$('refresh').title=n?'Changes are waiting while you read or edit. Apply them when ready.':'Retry the interrupted connection';}
function viewerRow(){return state.services.find(s=>s.id==='viewer')||null;}
function viewerTarget(){const viewer=viewerRow();if(!viewer||!viewer.origin)return '';const query=new URLSearchParams({project:state.project});if(viewer.run)query.set('run',viewer.run);return viewer.origin+'/?'+query;}
function renderCrumbs(){const scope=new URLSearchParams(),incoming=new URLSearchParams(location.hash.slice(1)).get('project');const project=state.projectInitialized?state.project:(incoming&&incoming.trim()&&incoming.length<=160?incoming.trim():'');if(project)scope.set('project',project);if(state.run)scope.set('run',state.run);for(const page of ['usage','contributions'])$('crumb-'+page).href='/'+page+(scope.size?'?'+scope:'');const target=viewerTarget(),viewer=viewerRow();$('crumb-viewer').href=target||'#';$('crumb-viewer').title=target?'Open the Orchestrator viewer'+(viewer&&viewer.run?' for this run':''):'Start the Orchestrator viewer';}
function safeToApply(){const selection=window.getSelection();return !$('detail').open&&!$('editor').open&&!$('action').open&&!$('feedback').open&&!state.searchResults&&state.view!=='graph'&&!document.activeElement?.matches('input,textarea')&&(!selection||selection.isCollapsed);}
function saveScope(){const hash=new URLSearchParams({project:state.project});if(state.run)hash.set('run',state.run);history.replaceState(null,'','#'+hash);}
async function loadServices(){const project=state.project,run=state.run;try{const data=await api('/api/services'+projectQuery()+(run?'&run='+encodeURIComponent(run):''));if(project!==state.project||run!==state.run)return;state.services=data.services||[];renderCrumbs();}catch(e){if(project===state.project&&run===state.run){state.services=[];renderCrumbs();}}}
function connection(message){state.connectionError=Boolean(message);renderRefreshButton();$('memory-connection').textContent=message|| (document.hidden?'Updates paused while this tab is hidden':'Connected · checks every 10 seconds');}
async function poll(){if(document.hidden||$('refresh').disabled||state.polling)return;state.polling=true;const project=state.project,cursor=state.cursor,recallRevision=state.recallRevision,usageRevision=state.usageRevision,retrying=state.connectionError;try{const [feed,usage]=await Promise.all([api('/api/changes'+projectQuery()+'&after='+cursor),api('/api/usage'+projectQuery())]);if(project!==state.project||cursor!==state.cursor||document.hidden)return;connection();let pending=(feed.changes||[]).length;if(retrying||usage.revision!==usageRevision||feed.resync_required||(typeof feed.recall_revision==='string'&&feed.recall_revision!==recallRevision))pending=Math.max(pending,1);if(pending<=0)return;if(safeToApply()){if(await refresh(true,true))notice('Applied changes made elsewhere.');}else{state.pending=pending;renderRefreshButton();}}catch(e){connection('Updates interrupted · '+e.message);}finally{state.polling=false;}}
async function refresh(silent=false,automatic=false){const seq=++state.sequence;$('refresh').disabled=true;try{const status=await api('/api/status');if(seq!==state.sequence)return false;state.status=status;if(!state.projectInitialized){const wanted=new URLSearchParams(location.hash.slice(1)).get('project');state.project=typeof wanted==='string'&&wanted.trim()&&wanted.length<=160?wanted.trim():(status.projects||[]).includes('agent-orchestrator')?'agent-orchestrator':((status.projects||[])[0]||'general');state.projectInitialized=true;saveScope();}const projects=[...new Set([...(status.projects||[]),state.project])].sort();$('project').replaceChildren(...projects.map(name=>{const option=el('option','',name);option.value=name;return option;}));$('project').value=state.project;loadServices();
// Capture the change head BEFORE the snapshot: a concurrent write then remains
// visible to the next poll instead of being skipped by a newer cursor.
const feed=await api('/api/changes'+projectQuery()+'&after='+state.cursor);if(seq!==state.sequence)return false;const [snapshot,pending,usage]=await Promise.all([api('/api/snapshot'+projectQuery()),api('/api/memories'+projectQuery()+'&status=pending'),api('/api/usage'+projectQuery())]);if(seq!==state.sequence)return false;if(automatic&&(document.hidden||!safeToApply())){state.pending=Math.max(1,(feed.changes||[]).length);renderRefreshButton();return false;}state.snapshot={memories:[],relations:[],traces:[],...snapshot};state.searchResults=null;state.reviewMemories=pending;state.usage=usage;state.usageRevision=usage.revision;state.cursor=Number(feed.head)||0;state.recallRevision=feed.recall_revision??null;state.pending=0;render();connection();if(!silent)notice('');return true;}catch(e){notice(e.message,true);connection('Updates interrupted · '+e.message);return false;}finally{if(seq===state.sequence)$('refresh').disabled=false;}}
function lookupTime(value){return typeof value==='number'&&Number.isFinite(value)&&value>=0?value.toFixed(1)+' ms':'Not recorded';}
function memories(){return state.snapshot.memories||[];}
function renderStats(){const status=state.snapshot.status||state.status||{};const storage=status.storage||{};const counts=status.project_counts?.[state.project];const active=counts?.active??memories().filter(m=>m.status==='active').length;const pending=counts?.pending??memories().filter(m=>m.status==='pending').length;$('stat-active').textContent=active;$('stat-pending').textContent=pending;$('nav-pending').textContent=pending;$('stat-storage').textContent=bytes(storage.brain_bytes);$('stat-storage-caption').textContent='of '+bytes(storage.brain_limit_bytes)+' memory limit';$('storage-fill').style.width=Math.min(100,100*(Number(storage.brain_bytes)||0)/(Number(storage.brain_limit_bytes)||1))+'%';const latest=[...(state.snapshot.traces||[])].sort((a,b)=>String(b.created_at).localeCompare(String(a.created_at)))[0];$('stat-speed').textContent=latest?lookupTime(latest.elapsed_ms):'—';$('stat-speed-caption').textContent=latest?((latest.memory_ids||[]).length+' memories · '+date(latest.created_at)):'No searches yet in this project';$('footer-storage').textContent='Application data: '+bytes(storage.total_bytes)+' / '+bytes(storage.total_limit_bytes)+' · '+(storage.status||'Checking');if(['blocked','full','over_limit'].includes(storage.status))notice('Storage has reached its limit. New writes may pause until eligible data is removed.',true);}
function setView(view){state.view=view;state.searchResults=null;state.query='';render();$('view').focus({preventScroll:true});}
function render(){renderStats();renderRefreshButton();renderCrumbs();document.querySelectorAll('[data-view]').forEach(n=>{if(n.dataset.view===state.view)n.setAttribute('aria-current','page');else n.removeAttribute('aria-current');});$('crumb').textContent={overview:'Overview',library:'Memory library',graph:'Relationships',review:'Review queue',activity:'Recall activity'}[state.view];$('page-title').textContent=titles[state.view][0];$('page-description').textContent=titles[state.view][1];const target=$('view');target.replaceChildren();if(state.view==='overview'){const layout=el('div','workspace');layout.append(libraryPanel(true),graphPanel());const activity=activityPanel();activity.classList.add('wide');layout.append(activity);const use=workerUsePanel();use.classList.add('wide');layout.append(use);target.append(layout);}else if(state.view==='library')target.append(libraryPanel(false));else if(state.view==='graph')target.append(graphPanel());else if(state.view==='review')target.append(reviewPanel());else{target.append(workerUsePanel(),activityPanel());}}
function panel(title,description){const node=el('section','panel');const header=el('div','panel-head');const group=el('div');group.append(el('h2','',title),el('p','',description));header.append(group);node.append(header);return {node,header};}
function empty(title,description,icon='◇'){const box=el('div','empty');box.append(el('div','empty-icon',icon),el('strong','',title),el('div','',description));return box;}
function recallReason(memory){const reason=String(memory.reason||'');const match=reason.match(/^Related by (\w+) to ([a-f0-9]+); hop (\d+)/);if(match){const source=memories().find(m=>m.id===match[2]);return source?'Connected to '+source.title+'.':'Found through an approved memory connection.';}return reason.startsWith('Keyword or alias')?'Matches your search wording or a saved alias.':reason;}
function memoryCard(memory){const card=button('',()=>showDetail(memory.id),'memory-card');const top=el('div','card-top');top.append(el('span','kind',kinds[memory.kind]||memory.kind),el('span','pill '+memory.status,memory.status==='active'&&memory.validity_state==='scheduled'?'scheduled':memory.status));card.append(top,el('h3','',memory.title||'Untitled memory'),el('p','',memory.content||'The content of this memory is no longer retained.'));const bottom=el('div','card-bottom');bottom.append(el('span','',memory.reviewer?'Reviewed by '+memory.reviewer:'Evidence awaiting review'),el('span','',date(memory.created_at)));card.append(bottom);if(memory.reason)card.append(el('small','',recallReason(memory)));return card;}
function libraryPanel(compact){const result=panel(compact?'Reviewed knowledge':'Memories',compact?'Approved and ready for the next task.':'Browse up to 200 recent records. Search finds older approved memories too.');if(compact)result.header.append(button('View all',()=>setView('library')));const body=el('div','panel-body');const search=el('form','searchbar');const input=el('input');input.type='search';input.maxLength=500;input.placeholder='Search what the orchestrator knows…';input.setAttribute('aria-label','Search project memories');input.value=state.query;const submit=el('button','','Search');submit.type='submit';search.append(input,submit);search.addEventListener('submit',async e=>{e.preventDefault();if(!input.value.trim())return;submit.disabled=true;try{state.query=input.value.trim();const project=state.project;const found=await api('/api/search',{query:state.query,project_id:project});if(project!==state.project)return;state.searchResults=found;if(found.trace_id){state.snapshot.traces.unshift({id:found.trace_id,created_at:new Date().toISOString(),elapsed_ms:found.lookup_ms??null,memory_ids:found.results.map(m=>m.id)});state.snapshot.traces=state.snapshot.traces.slice(0,30);}render();notice(found.results.length+' result'+(found.results.length===1?'':'s')+' in '+Number(found.elapsed_ms).toFixed(1)+' ms. Recall includes approved, currently valid memories.'+(found.recall_incomplete?' Source checks reached their limit; more evidence may exist.':'')+(!found.trace_id?' The search worked, but its recall record could not be saved.':''));}catch(error){notice(error.message,true);}finally{submit.disabled=false;}});body.append(search);if(!compact){const filters=el('div','filters');['active','pending','superseded','expired','all'].forEach(status=>filters.append(button(status==='all'?'All statuses':status[0].toUpperCase()+status.slice(1),()=>{state.filter=status;state.searchResults=null;render();},state.filter===status&&!state.searchResults?'active':'')));body.append(filters);}if(state.searchResults){const info=el('div','card-top');info.append(el('small','',state.searchResults.results.length+' matching memories'),button('Clear search',()=>{state.searchResults=null;state.query='';render();}));body.append(info);}let rows=state.searchResults?state.searchResults.results:(!compact&&state.filter==='pending'?(state.reviewMemories||[]):memories()).filter(m=>compact?m.status==='active':state.filter==='all'||m.status===state.filter);rows=[...rows].sort((a,b)=>state.searchResults?0:String(b.created_at).localeCompare(String(a.created_at)));rows=rows.slice(0,compact&&!state.searchResults?4:200);if(rows.length){const list=el('div','memory-list');list.append(...rows.map(memoryCard));body.append(list);}else body.append(empty(state.searchResults?'No matching knowledge':(state.filter==='pending'&&!compact?'Nothing awaiting review':'A little knowledge goes a long way'),state.searchResults?'Try a shorter phrase or a specific component name.':'Add a useful fact, preference, or a solution that worked. Review it to make it available for recall.'));result.node.append(body);return result.node;}
function reviewPanel(){const result=panel('Proposed memories','Up to 200 proposals shown. Reviewing them brings the next proposals into view.');const body=el('div','panel-body');body.append(el('div','review-info','Review the source and wording before approving. Old or unsupported claims should not become current knowledge.'));const pending=state.reviewMemories||memories().filter(m=>m.status==='pending');if(pending.length){const list=el('div','memory-list');list.append(...pending.map(memoryCard));body.append(list);}else body.append(empty('You are all caught up','New memory proposals will appear here.','✓'));result.node.append(body);return result.node;}
function activityPanel(){const result=panel('Recent recall','Search text is kept private; this history records timing and memory references.');const rows=[...(state.snapshot.traces||[])].sort((a,b)=>String(b.created_at).localeCompare(String(a.created_at))).slice(0,state.view==='overview'?5:50);if(!rows.length)result.node.append(empty('No recall activity yet','Search this project to see which memories match.','↗'));else{const table=el('table','activity-table');const head=el('thead'),headrow=el('tr');['When','Memories found','Lookup time'].forEach(t=>headrow.append(el('th','',t)));head.append(headrow);const body=el('tbody');rows.forEach(trace=>{const tr=el('tr');tr.append(el('td','',date(trace.created_at)));const refs=el('td');const ids=trace.memory_ids||[];if(!ids.length)refs.textContent='No matches';else ids.slice(0,6).forEach(id=>{const mem=memories().find(m=>m.id===id);refs.append(button(mem?mem.title:'View memory',()=>showDetail(id),'related-button'));});tr.append(refs,el('td','',lookupTime(trace.elapsed_ms)));body.append(tr);});table.append(head,body);result.node.append(table);}return result.node;}
function workerUsePanel(){
  const result=panel('Worker memory use','Task context and reviewed outcomes in this project. Provider reading is not observable.');
  const sample=state.usage||{tasks:[]},body=el('div','panel-body');
  body.append(el('p','muted',sample.tasks.length+' retained tasks; '+(sample.remembered||0)+' of '+(sample.eligible||0)+' accepted tasks have capture receipts. '+(sample.missing_receipt||0)+' missing receipts. '+(sample.truncated?'Partial sample. ':'')+'Capture receipts describe past writes; a memory can later be forgotten.'));
  const rows=sample.tasks.slice(0,state.view==='overview'?5:50);
  if(!rows.length)body.append(empty('No canonical tasks in this project yet','Native session activity and reviewed task artifacts are different records. Reviewed closeouts can be captured as project knowledge.'));
  rows.forEach(task=>{
    const memory=task.memory||{},feedback=memory.feedback||{},card=el('article','memory-card');
    card.append(el('h3','',task.task),el('p','',task.imported_artifact?'Reviewed closeout artifact; no worker execution.':task.worker+' / '+memory.label),el('div','muted',date(task.created_at)));
    if(!task.imported_artifact)card.append(el('div','',memory.reason||'No memory request receipt.'),el('small','',memory.memory_count+' memories / lookup '+lookupTime(memory.lookup_ms)));
    const refs=el('div');(memory.memory_ids||[]).slice(0,6).forEach(id=>{const known=memories().find(m=>m.id===id);refs.append(button(known?.title||'Inspect current memory',()=>showDetail(id),'related-button'));});card.append(refs);
    card.append(el('div','', 'Capture: '+(task.capture?.status||'missing')+' / '+(task.capture?.memory_count||0)+' memories'),el('div','', 'Usefulness: '+(feedback.status==='reviewed'?feedback.rating:feedback.status==='not_evaluated'?'Not evaluated':feedback.status||'Not evaluated')));
    if(task.review_status==='accepted'&&memory.stage==='execution_requested'&&memory.user_id==='local'&&memory.memory_count>0&&!['stale','invalid'].includes(feedback.status))card.append(button(feedback.status==='reviewed'?'Revise usefulness review':'Review usefulness',()=>openFeedback(task)));
    body.append(card);
  });
  result.node.append(body);return result.node;
}
async function openFeedback(task){
  const sequence=state.feedbackSequence=(state.feedbackSequence||0)+1;
  state.feedbackJob=task.job_id;state.feedbackProject=state.project;
  $('feedback-task').textContent=task.task;$('feedback-form').reset();$('feedback-error').textContent='';
  $('feedback-context').textContent='Loading saved evidence...';$('feedback-answer').textContent='';$('feedback-limits').textContent='';
  $('feedback-form').querySelector('[type=submit]').disabled=true;$('feedback').showModal();
  try{const evidence=await api('/api/use?project_id='+encodeURIComponent(state.feedbackProject)+'&job_id='+encodeURIComponent(task.job_id));
    if(sequence!==state.feedbackSequence||!$('feedback').open)return;
    state.feedbackSource=evidence.source_sha256;state.feedbackContext=evidence.context_binding_sha256;
    $('feedback-context').textContent=evidence.saved_context;$('feedback-answer').textContent=evidence.accepted_answer;
    $('feedback-limits').textContent=(evidence.saved_context_truncated||evidence.accepted_answer_truncated?'Evidence preview is truncated. Inspect the canonical task before rating. ':'')+'This is the saved request evidence; current library memories can later change or be forgotten.';
    $('feedback-form').querySelector('[type=submit]').disabled=Boolean(evidence.saved_context_truncated||evidence.accepted_answer_truncated);
  }catch(error){if(sequence===state.feedbackSequence&&$('feedback').open)$('feedback-error').textContent=error.message;}
}
$('feedback-form').addEventListener('submit',async event=>{
  event.preventDefault();const sequence=state.feedbackSequence,submit=event.target.querySelector('[type=submit]');submit.disabled=true;
  try{await api('/api/feedback',{project_id:state.feedbackProject,job_id:state.feedbackJob,expected_source_sha256:state.feedbackSource,expected_context_binding_sha256:state.feedbackContext,rating:$('feedback-rating').value,reviewer:$('feedback-reviewer').value,note:$('feedback-note').value});
    if(sequence!==state.feedbackSequence||!$('feedback').open)return;
    $('feedback').close();await refresh(true);notice('Usefulness review saved against the accepted answer and supplied context.');
  }catch(error){if(sequence===state.feedbackSequence&&$('feedback').open)$('feedback-error').textContent=error.message;}
  finally{if(sequence===state.feedbackSequence)submit.disabled=false;}
});
function svgEl(tag,attrs={}){const n=document.createElementNS('http://www.w3.org/2000/svg',tag);Object.entries(attrs).forEach(([k,v])=>n.setAttribute(k,String(v)));return n;}
function graphPanel(){const result=panel('Relationship map','Connections within the recent memory snapshot. Select a memory to explore.');const legend=el('div','legend');Object.keys(kinds).forEach(kind=>legend.append(el('span',kind,kind[0].toUpperCase()+kind.slice(1))));result.header.append(legend);const wrap=el('div','graph-wrap');const svg=svgEl('svg',{viewBox:'0 0 650 370',class:'graph',role:'group','aria-label':'Relationships between approved project memories'});const group=svgEl('g');svg.append(group);const tools=el('div','graph-tools');let zoom=1;const transform=()=>group.setAttribute('transform','translate(325 185) scale('+zoom+') translate(-325 -185)');tools.append(button('−',()=>{zoom=Math.max(.45,zoom-.2);transform();}),button('+',()=>{zoom=Math.min(2.5,zoom+.2);transform();}),button('Reset',()=>{zoom=1;transform();}));wrap.append(tools,svg);const nodes=memories().filter(m=>m.status==='active').slice(0,30);const ids=new Set(nodes.map(m=>m.id));const now=new Date();const edges=(state.snapshot.relations||[]).filter(r=>ids.has(r.source_id)&&ids.has(r.target_id)&&(!r.valid_to||new Date(r.valid_to)>now)&&(!r.valid_from||new Date(r.valid_from)<=now)).slice(0,60);const positions=new Map();nodes.forEach((m,i)=>{const angle=2*Math.PI*i/Math.max(nodes.length,1)-Math.PI/2;const ring=nodes.length>12&&i%2?.64:1;positions.set(m.id,{x:nodes.length===1?325:325+238*Math.cos(angle)*ring,y:nodes.length===1?178:178+117*Math.sin(angle)*ring});});const lines=edges.map(r=>{const line=svgEl('line');const title=svgEl('title');title.textContent=r.relation.replaceAll('_',' ');line.append(title);group.append(line);return {r,line};});const elements=new Map();nodes.forEach(m=>{const g=svgEl('g',{class:'node',tabindex:0,role:'button','aria-label':m.title});const circle=svgEl('circle',{r:13,fill:colors[m.kind]||colors.fact});const label=svgEl('text',{y:31});label.textContent=m.title.length>27?m.title.slice(0,25)+'…':m.title;const title=svgEl('title');title.textContent=m.title;g.append(circle,label,title);g.addEventListener('keydown',e=>{if(e.key==='Enter'||e.key===' '){e.preventDefault();showDetail(m.id);}});let drag=null;g.addEventListener('pointerdown',e=>{drag={x:e.clientX,y:e.clientY,moved:false};g.setPointerCapture(e.pointerId);});g.addEventListener('pointermove',e=>{if(!drag)return;if(Math.abs(e.clientX-drag.x)+Math.abs(e.clientY-drag.y)>4)drag.moved=true;if(!drag.moved)return;const point=new DOMPoint(e.clientX,e.clientY).matrixTransform(group.getScreenCTM().inverse());positions.set(m.id,{x:point.x,y:point.y});draw();});g.addEventListener('pointerup',()=>{if(drag&&!drag.moved)showDetail(m.id);drag=null;});g.addEventListener('pointercancel',()=>{drag=null;});elements.set(m.id,g);group.append(g);});function draw(){elements.forEach((g,id)=>{const p=positions.get(id);g.setAttribute('transform','translate('+p.x+' '+p.y+')');});lines.forEach(({r,line})=>{const a=positions.get(r.source_id),b=positions.get(r.target_id);Object.entries({x1:a.x,y1:a.y,x2:b.x,y2:b.y}).forEach(([k,v])=>line.setAttribute(k,v));});}draw();if(!nodes.length){const overlay=el('div','graph-empty');overlay.append(empty('Knowledge connects here','Approve your first memories, then link a problem to its solution.','⌘'));wrap.append(overlay);}result.node.append(wrap,el('div','graph-caption',nodes.length?nodes.length+' memories · '+edges.length+' connections · Drag to arrange, select to explore. Partial map: up to 30 nodes from 200 recent records.':'Connections are explicit and stay attached to their evidence.'));return result.node;}
function section(title){const node=el('section','detail-section');node.append(el('h3','',title));return node;}
async function showDetail(id){try{const project=state.project;const memory=await api('/api/memories/'+encodeURIComponent(id)+projectQuery());if(project!==state.project)return;state.detail=memory;$('detail-title').textContent=memory.title||'Forgotten memory';const body=$('detail-body');body.replaceChildren();const top=el('div','card-top');top.append(el('span','kind',kinds[memory.kind]||'Memory'),el('span','pill '+memory.status,memory.status==='active'&&memory.validity_state==='scheduled'?'scheduled':memory.status));body.append(top,el('div','detail-content',memory.content||'This memory’s content has been removed.'));const tags=el('div');(memory.tags||[]).forEach(tag=>tags.append(el('span','tag',tag)));body.append(tags);if(memory.episode&&Object.values(memory.episode).some(Boolean)){const episode=section('What happened');Object.entries(memory.episode).forEach(([key,value])=>{episode.append(el('strong','',key[0].toUpperCase()+key.slice(1)),el('p','detail-content',value));});body.append(episode);}const evidence=section('Evidence & review');const dl=el('dl','metadata');const fields=[['Source',memory.source?.type==='task'?'Reviewed task '+memory.source.job_id:'User-provided knowledge'],['Source note',memory.source?.note||'See the canonical reviewed task result.'],['Reviewed by',memory.reviewer||'Not reviewed'],['Review note',memory.review_note||'No approval recorded.'],['Importance',Math.round((memory.importance||0)*100)+' / 100']];fields.forEach(([key,value])=>dl.append(el('dt','',key),el('dd','',value)));evidence.append(dl);body.append(evidence);const history=section('Timeline');const timeline=el('div','timeline');[['Proposed',memory.created_at],['Reviewed',memory.reviewed_at],['Valid from',memory.valid_from],['Valid until',memory.valid_to]].filter(row=>row[1]).forEach(([label,value])=>{const item=el('div','timeline-item',label);item.append(el('small','',date(value)));timeline.append(item);});if(memory.superseded_by)timeline.append(el('div','timeline-item','Replaced by another approved memory.'));history.append(timeline);body.append(history);const relations=section('Connections');const links=memory.relations||[];if(!links.length)relations.append(el('p','muted','No connections recorded.'));else links.forEach(r=>{const other=r.source_id===memory.id?r.target_id:r.source_id;const target=memories().find(m=>m.id===other);relations.append(button(r.relation.replaceAll('_',' ')+' → '+(target?.title||'Related memory'),()=>showDetail(other),'related-button'));if(r.evidence_note)relations.append(el('p','smalltext muted',r.evidence_note));});body.append(relations);const actions=el('div','modal-actions');if(memory.status==='pending')actions.append(button('Approve memory',()=>openAction('approve'),'primary'));if(memory.status==='active'){actions.append(button('Add connection',()=>openAction('relate')),button('Replace with newer memory',()=>openAction('supersede')));}if(memory.status!=='deleted')actions.append(button('Forget memory',()=>openAction('forget'),'danger'));body.append(actions);if(!$('detail').open)$('detail').showModal();}catch(e){notice(e.message,true);}}
function field(label,id,type='input',value=''){const wrap=el('label','field full',label);const input=el(type);input.id=id;input.required=true;if(type!=='select'){input.maxLength=type==='textarea'?1000:100;input.value=value;}wrap.append(input);return {wrap,input};}
function openAction(action){state.action=action;const memory=state.detail;const names={approve:'Approve memory',forget:'Forget memory',supersede:'Replace this memory',relate:'Connect memories'};$('action-title').textContent=names[action];$('action-submit').textContent=names[action];$('action-submit').className=action==='forget'?'danger':'primary';$('action-error').hidden=true;const target=$('action-fields');target.replaceChildren();const descriptions={approve:'Confirm that the evidence supports this memory and that its wording is accurate. A review note of at least 20 characters is required.',forget:'Permanently remove this memory’s content and its derived search data, connections, and recall references. This cannot be undone.',supersede:'Choose an approved replacement in this project. The old memory stays in history and stops appearing as current knowledge.',relate:'Add an explicit relationship between two approved memories in this project.'};target.append(el('p','action-help',descriptions[action]));const fields=el('div','form-grid');if(action==='supersede'||action==='relate'){const choice=field(action==='supersede'?'Newer approved memory':'Connect to','action-target','select');const first=el('option','','Choose a memory');first.value='';choice.input.append(first,...memories().filter(m=>m.status==='active'&&m.id!==memory.id).map(m=>{const option=el('option','',m.title);option.value=m.id;return option;}));fields.append(choice.wrap);if(action==='relate'){const relation=field('Relationship','action-relation','select');[['related_to','Related to'],['solves','Solves'],['depends_on','Depends on'],['supports','Supports']].forEach(([value,label])=>{const option=el('option','',label);option.value=value;relation.input.append(option);});fields.append(relation.wrap);}}fields.append(field(action==='approve'?'Reviewer name':'Your name','action-actor','input','Local user').wrap);if(action!=='relate'){const note=field(action==='approve'?'Why is this memory supported?':'Reason','action-note','textarea');note.input.minLength=action==='approve'?20:10;fields.append(note.wrap);}target.append(fields);$('action').showModal();}
$('action-form').addEventListener('submit',async e=>{e.preventDefault();const action=state.action;const data={project_id:state.project,memory_id:state.detail.id,actor:$('action-actor').value};if(action==='approve'){data.reviewer=data.actor;data.note=$('action-note').value;}else if(action!=='relate')data.reason=$('action-note').value;if(action==='supersede')data.new_id=$('action-target').value;if(action==='relate'){data.target_id=$('action-target').value;data.relation=$('action-relation').value;}$('action-submit').disabled=true;try{await api('/api/'+action,data);$('action').close();$('detail').close();await refresh(true);notice({approve:'Memory approved and available for recall.',forget:'Memory content and its derived data have been removed.',supersede:'The newer memory is current. The previous version remains in history.',relate:'Connection saved.'}[action]);}catch(error){$('action-error').textContent=error.message;$('action-error').hidden=false;}finally{$('action-submit').disabled=false;}});
$('memory-form').addEventListener('submit',async e=>{e.preventDefault();const project=$('new-project').value.trim();const data={project_id:project,kind:$('new-kind').value,title:$('new-title').value.trim(),content:$('new-content').value.trim(),source:{type:'user',note:$('new-source').value.trim()},tags:$('new-tags').value.split(',').map(s=>s.trim()).filter(Boolean),importance:Number($('new-importance').value)};if(data.kind==='episode')data.episode={problem:$('episode-problem').value,action:$('episode-action').value,outcome:$('episode-outcome').value};const submit=e.target.querySelector('button[type=submit]');submit.disabled=true;try{await api('/api/memories',data);$('editor').close();e.target.reset();$('episode-fields').hidden=true;document.querySelectorAll('#episode-fields input').forEach(n=>n.required=false);if(state.project!==project){state.run='';state.cursor=0;state.pending=0;state.recallRevision=null;state.services=[];}state.project=project;saveScope();state.view='review';await refresh(true);notice('Memory saved for review. Open it to check the evidence and approve.');}catch(error){$('editor-error').textContent=error.message;$('editor-error').hidden=false;}finally{submit.disabled=false;}});
$('new-kind').addEventListener('change',()=>{const required=$('new-kind').value==='episode';$('episode-fields').hidden=!required;document.querySelectorAll('#episode-fields input').forEach(n=>n.required=required);});
$('add').addEventListener('click',()=>{$('new-project').value=state.project;$('editor-error').hidden=true;$('editor').showModal();$('new-title').focus();});
$('refresh').addEventListener('click',()=>refresh());
$('project').addEventListener('change',()=>{state.project=$('project').value;state.run='';state.searchResults=null;state.query='';state.cursor=0;state.pending=0;state.recallRevision=null;state.services=[];saveScope();renderCrumbs();$('detail').close();refresh();});
$('crumb-viewer').addEventListener('click',async e=>{if(e.ctrlKey||e.metaKey||e.shiftKey||e.altKey)return;e.preventDefault();notice('Opening the Orchestrator viewer…');try{const opened=await api('/api/open',{target:'viewer'});state.services=[Object.assign({id:'viewer'},viewerRow()||{},{origin:opened.origin})];window.location.href=viewerTarget();}catch(error){notice(error.message,true);}});
document.querySelectorAll('[data-view]').forEach(n=>n.addEventListener('click',()=>setView(n.dataset.view)));
document.querySelectorAll('[data-close]').forEach(n=>n.addEventListener('click',()=>$(n.dataset.close).close()));
document.addEventListener('visibilitychange',()=>{connection();if(!document.hidden)poll();});
renderCrumbs();
refresh();
setInterval(poll,10000);
</script></body></html>'''


if __name__ == '__main__':
    raise SystemExit(main())
