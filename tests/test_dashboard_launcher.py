"""Dashboard lifetime must be independent of temporary Windows command jobs."""
import ctypes
import io
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import textwrap
import time
from types import SimpleNamespace
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'app'))
import start_brain_dashboard as launcher
from coordinator_viewer import ViewerHandler


class LauncherTests(unittest.TestCase):
    def test_non_windows_starts_an_independent_session(self):
        with patch.object(launcher, 'os', SimpleNamespace(name='posix')), \
                patch.object(launcher.subprocess, 'Popen') as spawn:
            launcher._start_server(['python', 'dashboard.py'], '/workspace')
        self.assertTrue(spawn.call_args.kwargs['start_new_session'])
        self.assertNotIn('creationflags', spawn.call_args.kwargs)

    @unittest.skipUnless(os.name == 'nt', 'Windows process creation flags')
    def test_forbidden_breakaway_keeps_ordinary_launch_and_explains_lifetime(self):
        denied = OSError('Job forbids breakaway')
        denied.winerror = 5
        with patch.object(launcher.subprocess, 'Popen', side_effect=[denied, 'process']) as spawn, \
                patch.object(launcher.sys, 'stderr', new_callable=io.StringIO) as warning:
            self.assertEqual(launcher._start_server(['python', 'dashboard.py'], '.'), 'process')
        self.assertEqual(spawn.call_count, 2)
        self.assertEqual(spawn.call_args.kwargs['creationflags'], subprocess.CREATE_NO_WINDOW)
        self.assertIn('attached to this launcher', warning.getvalue())

    def test_unrelated_start_failure_is_not_retried(self):
        with patch.object(launcher.subprocess, 'Popen', side_effect=FileNotFoundError('missing')) as spawn:
            with self.assertRaises(FileNotFoundError):
                launcher._start_server(['missing'], '.')
        self.assertEqual(spawn.call_count, 1)

    def test_reusing_healthy_dashboard_never_starts_a_second_process(self):
        with tempfile.TemporaryDirectory() as folder, \
                patch.object(launcher, 'running_state', return_value={'origin': 'http://127.0.0.1:1234'}), \
                patch.object(launcher, '_start_server') as spawn:
            result = launcher.open_dashboard(folder, False)
        self.assertTrue(result['reused'])
        spawn.assert_not_called()

    def test_disconnected_viewer_client_does_not_receive_a_second_error_response(self):
        handler = object.__new__(ViewerHandler)
        for error in (BrokenPipeError(), ConnectionResetError(), ConnectionAbortedError()):
            handler.close_connection = False
            with patch.object(handler, '_error') as reply:
                handler._handle_error(error)
            reply.assert_not_called()
            self.assertTrue(handler.close_connection)

    @unittest.skipUnless(os.name == 'nt', 'Real Windows job lifetime regression')
    def test_server_survives_launcher_job_closing(self):
        # A fresh hidden launcher creates its own kill-on-close job. Its server
        # must still be alive after that launcher exits and Windows closes the job.
        from ctypes import wintypes
        program = textwrap.dedent('''
            import ctypes, json, os, sys, time
            from ctypes import wintypes as w
            from pathlib import Path
            sys.path.insert(0, sys.argv[1])
            from start_brain_dashboard import _start_server
            class Basic(ctypes.Structure):
                _fields_=[('process_time',ctypes.c_int64),('job_time',ctypes.c_int64),
                          ('flags',w.DWORD),('minimum',ctypes.c_size_t),('maximum',ctypes.c_size_t),
                          ('count',w.DWORD),('affinity',ctypes.c_size_t),('priority',w.DWORD),('scheduling',w.DWORD)]
            class Limits(ctypes.Structure):
                _fields_=[('basic',Basic),('io',ctypes.c_uint64*6),('memory',ctypes.c_size_t*4)]
            k=ctypes.WinDLL('kernel32',use_last_error=True)
            k.GetCurrentProcess.restype=w.HANDLE
            k.CreateJobObjectW.argtypes=[ctypes.c_void_p,w.LPCWSTR];k.CreateJobObjectW.restype=w.HANDLE
            k.SetInformationJobObject.argtypes=[w.HANDLE,ctypes.c_int,ctypes.c_void_p,w.DWORD]
            k.AssignProcessToJobObject.argtypes=[w.HANDLE,w.HANDLE]
            job=k.CreateJobObjectW(None,None);assert job
            limits=Limits();limits.basic.flags=0x2000|0x800 # KILL_ON_JOB_CLOSE | BREAKAWAY_OK
            assert k.SetInformationJobObject(job,9,ctypes.byref(limits),ctypes.sizeof(limits))
            assert k.AssignProcessToJobObject(job,k.GetCurrentProcess())
            folder=Path(sys.argv[2])
            child="from pathlib import Path; import time,sys; p=Path(sys.argv[1]); (p/'ready').touch(); deadline=time.monotonic()+30\\nwhile not (p/'stop').exists() and time.monotonic()<deadline: time.sleep(.02)"
            process=_start_server([sys.executable,'-c',child,str(folder)],folder)
            (folder/'pid').write_text(str(process.pid))
            deadline=time.monotonic()+5
            while not (folder/'ready').exists() and process.poll() is None and time.monotonic()<deadline: time.sleep(.02)
            assert (folder/'ready').exists()
            # Exit closes the sole job handle. Do not close it early and kill this launcher.
        ''')
        k = ctypes.WinDLL('kernel32', use_last_error=True)
        k.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        k.OpenProcess.restype = wintypes.HANDLE
        k.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
        k.CloseHandle.argtypes = [wintypes.HANDLE]
        k.TerminateProcess.argtypes = [wintypes.HANDLE, wintypes.UINT]
        with tempfile.TemporaryDirectory(prefix='dashboard-lifetime-') as folder:
            handle = None
            try:
                completed = subprocess.run([sys.executable, '-c', program,
                                            str(Path(launcher.__file__).parent), folder],
                    capture_output=True, text=True, timeout=10,
                    creationflags=subprocess.CREATE_NO_WINDOW | subprocess.CREATE_BREAKAWAY_FROM_JOB)
                self.assertEqual(completed.returncode, 0, completed.stderr)
                pid = int((Path(folder) / 'pid').read_text())
                handle = k.OpenProcess(0x100000 | 0x0001, False, pid) # SYNCHRONIZE | TERMINATE
                self.assertTrue(handle, 'Windows killed the server with its launcher job')
                self.assertEqual(k.WaitForSingleObject(handle, 250), 258, 'Server exited with launcher')
                (Path(folder) / 'stop').touch()
                self.assertEqual(k.WaitForSingleObject(handle, 5000), 0)
            finally:
                (Path(folder) / 'stop').touch()
                if handle:
                    if k.WaitForSingleObject(handle, 1000) == 258:
                        k.TerminateProcess(handle, 1)
                        k.WaitForSingleObject(handle, 2000)
                    k.CloseHandle(handle)


if __name__ == '__main__':
    unittest.main()
