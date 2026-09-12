"""Open or reuse the lightweight loopback dashboard; never launch a model."""
import argparse
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import time
import urllib.error
import urllib.request
import webbrowser

from paths import ROOT
from brain_store import safe_path
from usage_guard import file_lock

SERVICE = 'orchestrator-brain-dashboard'


def running_state(path, service=SERVICE):
    """Probe only a bounded loopback URL, and match this exact server instance."""
    try:
        if path.stat().st_size > 4096:
            return None
        state = json.loads(path.read_text(encoding='utf-8'))
        if (state.get('service') != service or state.get('version') != 1
                or type(state.get('pid')) is not int or state['pid'] <= 0
                or not re.fullmatch(r'http://127\.0\.0\.1:[1-9][0-9]{0,4}',state.get('origin',''))
                or not re.fullmatch(r'[a-f0-9]{32,128}',state.get('instance_id',''))):
            return None
        port=int(state['origin'].rsplit(':',1)[1])
        if port > 65535:
            return None
        # Bypass proxy environment variables; this request never leaves loopback.
        opener=urllib.request.build_opener(urllib.request.ProxyHandler({}),NoRedirect())
        with opener.open(state['origin']+'/health',timeout=0.6) as response:
            health=json.loads(response.read(4097))
        if (health.get('service')==service and health.get('version')==1
                and health.get('instance_id')==state['instance_id']):
            return state
    except (OSError,ValueError,TypeError,AttributeError,urllib.error.URLError):
        pass
    return None


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self,*args,**kwargs):
        return None


def open_dashboard(root=ROOT,open_browser=True,*,service=SERVICE,script='brain_dashboard.py',state_name='brain-dashboard'):
    root=Path(root).resolve()
    state_path=safe_path(root,root/('runtime/'+state_name+'.json'))
    lock_path=safe_path(root,root/('runtime/'+state_name+'-launch.lock'))
    state_path.parent.mkdir(parents=True,exist_ok=True)
    with file_lock(lock_path):
        state=running_state(state_path,service)
        reused=state is not None
        if state is None:
            state_path.unlink(missing_ok=True)
            kwargs={'stdin':subprocess.DEVNULL,'stdout':subprocess.DEVNULL,'stderr':subprocess.DEVNULL,
                    'cwd':str(root),'close_fds':True}
            if os.name=='nt':kwargs['creationflags']=subprocess.CREATE_NO_WINDOW
            else:kwargs['start_new_session']=True
            process=subprocess.Popen([sys.executable,str(Path(__file__).with_name(script)),
                '--root',str(root),'--port','0','--state-file',str(state_path)],**kwargs)
            deadline=time.monotonic()+12
            while time.monotonic()<deadline and process.poll() is None:
                state=running_state(state_path,service)
                if state:break
                time.sleep(.1)
            if state is None:
                if process.poll() is None:process.terminate();process.wait(timeout=5)
                raise RuntimeError('Dashboard did not start. Run python orchestrator.py brain dashboard for the diagnostic.')
    if open_browser:webbrowser.open(state['origin']+'/')
    return dict(state,reused=reused)


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root',type=Path,default=ROOT)
    parser.add_argument('--no-open',action='store_true')
    args=parser.parse_args(argv)
    try:
        print(json.dumps(open_dashboard(args.root,not args.no_open)));return 0
    except (OSError,ValueError,RuntimeError) as error:
        print(json.dumps({'status':'error','error':str(error)}));return 2


if __name__=='__main__':raise SystemExit(main())
