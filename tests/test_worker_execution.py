"""Exercise real child pipes/deadlines without accounts, inference, or network."""
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
import time
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'app'))
from worker_execution import invoke_cloud, WorkerInterrupted


class ExecutionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def run_child(self, code, stream=True, timeout=3, stdin=None):
        command = [sys.executable, '-u', '-c', code]
        if stream:
            command += ['--output-format', 'stream-json']
        return invoke_cloud(command, stdin, self.root, os.environ.copy(), timeout_seconds=timeout)

    def test_timeout_retains_partial_answer_and_safe_progress(self):
        code = '''import json,sys,time
print(json.dumps({'type':'stream_event','event':{'delta':{'type':'thinking_delta','thinking':'PRIVATE_THOUGHT'}}}))
print(json.dumps({'type':'stream_event','event':{'delta':{'type':'text_delta','text':'unfinished answer'}}}))
print('diagnostic stderr',file=sys.stderr)
time.sleep(10)
'''
        with self.assertRaises(WorkerInterrupted) as raised:
            self.run_child(code, timeout=0.8)
        self.assertTrue(raised.exception.process_terminated)
        self.assertFalse(raised.exception.progress['terminal_received'])
        self.assertEqual((self.root / 'partial-response.txt').read_text(), 'unfinished answer')
        self.assertIn('diagnostic stderr', (self.root / 'private-stderr.txt').read_text())
        for path in self.root.iterdir():
            self.assertNotIn('PRIVATE_THOUGHT', path.read_text())
        self.assertEqual(json.loads((self.root / 'execution-progress.json').read_text())['process_status'], 'timeout')

    def test_completed_stream_returns_terminal_not_draft(self):
        result = self.run_child('''import json
print('malformed line')
print(json.dumps({'type':'stream_event','event':{'delta':{'type':'text_delta','text':'draft'}}}))
print(json.dumps({'type':'result','is_error':False,'result':'final answer','usage':{'output_tokens':3}}))
''')
        self.assertEqual(json.loads(result.stdout)['result'], 'final answer')
        self.assertEqual(result.progress['malformed_events'], 1)
        self.assertTrue(result.progress['terminal_received'])

    def test_progress_is_visible_before_child_exits_on_windows(self):
        code = '''import json,time
print(json.dumps({'type':'stream_event','event':{'delta':{'type':'text_delta','text':'live progress'}}}))
time.sleep(3)
print(json.dumps({'type':'result','is_error':False,'result':'finished'}))
'''
        with ThreadPoolExecutor(max_workers=1) as pool:
            future = pool.submit(self.run_child, code, True, 6)
            until = time.monotonic() + 2.8
            seen = False
            while time.monotonic() < until:
                path = self.root / 'execution-progress.json'
                if path.exists():
                    try:
                        snapshot = json.loads(path.read_text())
                    except (OSError, ValueError):
                        snapshot = {}
                    if snapshot.get('answer_chars') == 13:
                        seen = True
                        self.assertFalse(future.done())
                        break
                time.sleep(0.04)
            result = future.result()
        self.assertTrue(seen, 'Progress must be persisted before EOF, not just after completion')
        self.assertLess(result.progress['first_answer_s'], 2.0)

    def test_exit_zero_without_terminal_is_uncertain(self):
        with self.assertRaises(WorkerInterrupted) as raised:
            self.run_child("print('{}')")
        self.assertEqual(raised.exception.cause, 'missing_terminal_result')

    def test_terminal_error_is_preserved_for_dispatcher(self):
        result = self.run_child("print('{\"type\":\"result\",\"is_error\":true,\"errors\":[\"quota_exhausted\"]}')")
        self.assertTrue(json.loads(result.stdout)['is_error'])
        self.assertEqual(result.progress['state'], 'failed')

    def test_terminal_does_not_cancel_hard_deadline(self):
        with self.assertRaises(WorkerInterrupted) as raised:
            self.run_child("import time; print('{\"type\":\"result\",\"is_error\":false,\"result\":\"done\"}'); time.sleep(10)", timeout=0.8)
        self.assertEqual(raised.exception.cause, 'timeout')
        self.assertTrue(raised.exception.progress['terminal_received'])

    def test_buffered_json_and_stderr_are_drained_together(self):
        result = self.run_child("import sys,json; data=sys.stdin.read(); sys.stderr.write('x'*200000); print(json.dumps({'response':data}))", stream=False, stdin='supplied brief')
        self.assertEqual(json.loads(result.stdout)['response'], 'supplied brief')
        self.assertEqual(len(result.stderr), 200000)
        self.assertEqual((self.root / 'partial-response.txt').read_text(), '')

    def test_split_utf8_is_decoded_once_complete(self):
        code = '''import sys,time,json
data=json.dumps({'type':'stream_event','event':{'delta':{'type':'text_delta','text':'caf\u00e9'}}},ensure_ascii=False).encode('utf-8')
cut=data.index(bytes([0xc3]))+1
sys.stdout.buffer.write(data[:cut]); sys.stdout.flush(); time.sleep(0.7)
sys.stdout.buffer.write(data[cut:]+b'\\n');sys.stdout.flush()
print(json.dumps({'type':'result','is_error':False,'result':'finished'}))
'''
        self.run_child(code)
        self.assertEqual((self.root / 'partial-response.txt').read_text(encoding='utf-8'), 'caf\u00e9')

    def test_progress_io_failure_kills_child(self):
        from task_store import write_json as real_write
        def fail_after_start(path, value):
            if value['process_status'] != 'starting':
                raise OSError('simulated disk failure')
            return real_write(path, value)
        with patch('worker_execution.write_json', side_effect=fail_after_start):
            with self.assertRaises(WorkerInterrupted) as raised:
                self.run_child('import time; time.sleep(10)', timeout=0.8)
        self.assertTrue(raised.exception.process_terminated)
        self.assertTrue(raised.exception.progress['evidence_write_failed'])


if __name__ == '__main__':
    unittest.main()
