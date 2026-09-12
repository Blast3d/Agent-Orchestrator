"""Bounded worker output: memory, queues, previews, and hard overflow."""
import json
import os
from pathlib import Path
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'app'))
import output_limits
from worker_execution import invoke_cloud, WorkerInterrupted
from worker_progress import ClaudeProgress


class OutputLimitTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def run_child(self, code, stream=True, timeout=3, stdin=None):
        command = [sys.executable, '-u', '-c', code]
        if stream:
            command += ['--output-format', 'stream-json']
        return invoke_cloud(command, stdin, self.root, os.environ.copy(), timeout_seconds=timeout)

    def test_unterminated_stdout_hits_hard_line_cap(self):
        with patch.object(output_limits, 'EVENT_MAX', 2048), patch.object(output_limits, 'READ_CHUNK', 512):
            with self.assertRaises(WorkerInterrupted) as raised:
                self.run_child("import sys,time; sys.stdout.write('x'*80000); sys.stdout.flush(); time.sleep(8)", timeout=2)
        self.assertEqual(raised.exception.cause, 'output_limit')
        self.assertTrue(raised.exception.process_terminated)
        self.assertFalse(raised.exception.progress.get('terminal_received'))
        self.assertLessEqual(raised.exception.progress['stdout_retained_chars'], 2048 + 512)

    def test_unterminated_stderr_hits_hard_cap(self):
        with patch.object(output_limits, 'STDERR_MAX', 1024), patch.object(output_limits, 'READ_CHUNK', 256):
            with self.assertRaises(WorkerInterrupted) as raised:
                self.run_child("import sys,time; sys.stderr.write('e'*50000); sys.stderr.flush(); time.sleep(8)", timeout=2)
        self.assertEqual(raised.exception.cause, 'output_limit')
        self.assertTrue(raised.exception.process_terminated)
        stderr = (self.root / 'private-stderr.txt').read_text(encoding='utf-8')
        self.assertIn('truncated', stderr)
        self.assertLessEqual(raised.exception.progress['stderr_retained_chars'], 1024)
        self.assertGreater(raised.exception.progress['stderr_chars'], raised.exception.progress['stderr_retained_chars'])

    def test_preview_cap_then_valid_terminal_is_success(self):
        answer = 'partial-preview-then-more-answer-text'
        code = (
            "import json\n"
            "print(json.dumps({'type':'stream_event','event':{'delta':{'type':'text_delta','text':%r}}}) )\n"
            "print(json.dumps({'type':'result','is_error':False,'result':'final ok'}))\n"
        ) % answer
        with patch.object(output_limits, 'PARTIAL_PREVIEW_MAX', 12):
            result = self.run_child(code)
        self.assertEqual(json.loads(result.stdout)['result'], 'final ok')
        self.assertTrue(result.progress['terminal_received'])
        self.assertTrue(result.progress['preview_truncated'])
        self.assertEqual(result.progress['answer_chars'], 12)
        self.assertEqual(result.progress['observed_answer_chars'], len(answer))
        self.assertEqual((self.root / 'partial-response.txt').read_text(encoding='utf-8'), answer[:12])

    def test_oversized_terminal_is_not_success(self):
        code = (
            "import json\n"
            "print(json.dumps({'type':'result','is_error':False,'result':'Z'*8000}))\n"
        )
        with patch.object(output_limits, 'EVENT_MAX', 500), patch.object(output_limits, 'TERMINAL_MAX', 500):
            with self.assertRaises(WorkerInterrupted) as raised:
                self.run_child(code, timeout=2)
        self.assertIn(raised.exception.cause, ('output_limit', 'missing_terminal_result'))
        self.assertFalse(raised.exception.progress.get('terminal_received'))
        self.assertNotEqual(raised.exception.progress.get('state'), 'finished')

    def test_oversized_error_terminal_is_not_quota_confirmation(self):
        code = (
            "import json\n"
            "print(json.dumps({'type':'result','is_error':True,'errors':['quota_exhausted']*400}))\n"
        )
        with patch.object(output_limits, 'EVENT_MAX', 400), patch.object(output_limits, 'TERMINAL_MAX', 400):
            with self.assertRaises(WorkerInterrupted) as raised:
                self.run_child(code, timeout=2)
        self.assertIn(raised.exception.cause, ('output_limit', 'missing_terminal_result'))
        self.assertNotEqual(raised.exception.progress.get('state'), 'failed')
        stdout_path = self.root / 'partial-response.txt'
        self.assertNotIn('quota_exhausted', stdout_path.read_text(encoding='utf-8') if stdout_path.exists() else '')

    def test_burst_queue_respects_hard_timeout(self):
        code = (
            "import json,sys,time\n"
            "for i in range(400):\n"
            "    print(json.dumps({'type':'stream_event','event':{'delta':{'type':'text_delta','text':str(i)}}}))\n"
            "    sys.stdout.flush()\n"
            "time.sleep(12)\n"
        )
        started = time.monotonic()
        with patch.object(output_limits, 'QUEUE_MAX', 4):
            with self.assertRaises(WorkerInterrupted) as raised:
                self.run_child(code, timeout=0.8)
        elapsed = time.monotonic() - started
        self.assertLess(elapsed, 5.0)
        self.assertEqual(raised.exception.cause, 'timeout')
        self.assertTrue(raised.exception.process_terminated)

    def test_normal_stream_result_unchanged(self):
        result = self.run_child(
            "import json\n"
            "print(json.dumps({'type':'stream_event','event':{'delta':{'type':'text_delta','text':'draft'}}}))\n"
            "print(json.dumps({'type':'result','is_error':False,'result':'final answer'}))\n"
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout)['result'], 'final answer')
        self.assertTrue(result.progress['terminal_received'])
        self.assertFalse(result.progress['preview_truncated'])
        self.assertEqual((self.root / 'partial-response.txt').read_text(encoding='utf-8'), 'draft')

    def test_normal_buffered_result_unchanged(self):
        result = self.run_child(
            "import sys,json; data=sys.stdin.read(); sys.stderr.write('diag'); print(json.dumps({'response':data}))",
            stream=False, stdin='supplied brief',
        )
        self.assertEqual(json.loads(result.stdout)['response'], 'supplied brief')
        self.assertEqual(result.stderr, 'diag')
        self.assertEqual((self.root / 'partial-response.txt').read_text(encoding='utf-8'), '')

    def test_burst_is_drained_after_child_and_readers_finish(self):
        code = (
            "import json\n"
            "for i in range(100):\n"
            "    print(json.dumps({'type':'stream_event','event':{'delta':{'type':'text_delta','text':'x'}}}))\n"
            "print(json.dumps({'type':'result','is_error':False,'result':'burst complete'}))\n"
        )
        with patch.object(output_limits, 'QUEUE_MAX', 4096), patch.object(output_limits, 'READ_CHUNK', 16):
            result = self.run_child(code)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout)['result'], 'burst complete')
        self.assertEqual(result.progress['observed_answer_chars'], 100)

    def test_multibyte_truncation_preserves_prefix_across_deltas(self):
        with patch.object(output_limits, 'PARTIAL_PREVIEW_MAX', 1):
            p = ClaudeProgress()
            for delta in ('\u00e9', 'a'):
                p.observe({'type':'stream_event','event':{'delta':{'type':'text_delta','text':delta}}}, 0.1)
        self.assertEqual(p.partial_response, '')
        self.assertTrue(p.snapshot()['preview_truncated'])
        self.assertEqual(p.snapshot()['observed_answer_chars'], 2)

    def test_preview_newlines_do_not_expand_past_saved_byte_cap(self):
        answer = '\n' * 50
        code = (
            "import json\n"
            "print(json.dumps({'type':'stream_event','event':{'delta':{'type':'text_delta','text':%r}}}))\n"
            "print(json.dumps({'type':'result','is_error':False,'result':'done'}))\n"
        ) % answer
        with patch.object(output_limits, 'PARTIAL_PREVIEW_MAX', 12):
            result = self.run_child(code)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual((self.root / 'partial-response.txt').read_bytes(), b'\n' * 12)
        self.assertTrue(result.progress['preview_truncated'])

    def test_progress_preview_counters_without_thinking_leak(self):
        with patch.object(output_limits, 'PARTIAL_PREVIEW_MAX', 4):
            p = ClaudeProgress()
            p.observe({'type': 'stream_event', 'event': {'delta': {'type': 'thinking_delta', 'thinking': 'SECRET'}}},
                      0.1)
            p.observe({'type': 'stream_event', 'event': {'delta': {'type': 'text_delta', 'text': 'abcdef'}}},
                      0.2)
            snap = p.snapshot()
        self.assertEqual(p.partial_response, 'abcd')
        self.assertEqual(snap['answer_chars'], 4)
        self.assertEqual(snap['observed_answer_chars'], 6)
        self.assertTrue(snap['preview_truncated'])
        self.assertNotIn('SECRET', p.partial_response)
        self.assertNotIn('SECRET', str(snap))

    def test_terminal_above_terminal_max_below_event_max_is_output_limit(self):
        payload = 'W' * 400
        code = (
            "import json\n"
            "print(json.dumps({'type':'result','is_error':False,'result':%r}))\n"
        ) % payload
        with patch.object(output_limits, 'EVENT_MAX', 8192), patch.object(output_limits, 'TERMINAL_MAX', 200):
            with self.assertRaises(WorkerInterrupted) as raised:
                self.run_child(code, timeout=2)
        self.assertEqual(raised.exception.cause, 'output_limit')
        self.assertFalse(raised.exception.progress.get('terminal_received'))
        self.assertNotEqual(raised.exception.progress.get('state'), 'finished')

    def test_error_terminal_above_terminal_max_below_event_max_is_output_limit(self):
        code = (
            "import json\n"
            "print(json.dumps({'type':'result','is_error':True,'errors':['quota_exhausted']*40}))\n"
        )
        with patch.object(output_limits, 'EVENT_MAX', 8192), patch.object(output_limits, 'TERMINAL_MAX', 120):
            with self.assertRaises(WorkerInterrupted) as raised:
                self.run_child(code, timeout=2)
        self.assertEqual(raised.exception.cause, 'output_limit')
        self.assertNotEqual(raised.exception.progress.get('state'), 'failed')
        self.assertFalse(raised.exception.progress.get('terminal_received'))
        stdout_path = self.root / 'partial-response.txt'
        self.assertNotIn('quota_exhausted', stdout_path.read_text(encoding='utf-8') if stdout_path.exists() else '')

    def test_no_newline_terminal_after_prior_events(self):
        code = (
            "import json,sys\n"
            "print(json.dumps({'type':'stream_event','event':{'delta':{'type':'text_delta','text':'draft'}}}))\n"
            "sys.stdout.write(json.dumps({'type':'result','is_error':False,'result':'final no nl'}))\n"
            "sys.stdout.flush()\n"
        )
        result = self.run_child(code)
        self.assertEqual(json.loads(result.stdout)['result'], 'final no nl')
        self.assertTrue(result.progress['terminal_received'])
        self.assertEqual((self.root / 'partial-response.txt').read_text(encoding='utf-8'), 'draft')

    def test_multibyte_preview_bounds_encoded_bytes(self):
        text = 'é' * 8
        with patch.object(output_limits, 'PARTIAL_PREVIEW_MAX', 3):
            p = ClaudeProgress()
            p.observe({'type': 'stream_event', 'event': {'delta': {'type': 'text_delta', 'text': text}}}, 0.1)
            snap = p.snapshot()
        self.assertLessEqual(len(p.partial_response.encode('utf-8')), 3)
        self.assertEqual(snap['observed_answer_chars'], 8)
        self.assertEqual(snap['retained_answer_chars'], len(p.partial_response))
        self.assertEqual(snap['answer_chars'], snap['retained_answer_chars'])
        self.assertTrue(snap['preview_truncated'])
        self.assertNotEqual(p.partial_response, text)

    def test_multibyte_stream_line_cap_is_bytes(self):
        ch = 'é'
        code = (
            "import json,sys\n"
            "sys.stdout.write(json.dumps({'type':'stream_event','event':{'delta':{'type':'text_delta','text':%r}}}, ensure_ascii=False))\n"
            "sys.stdout.flush()\n"
            "import time; time.sleep(8)\n"
        ) % (ch * 2000)
        with patch.object(output_limits, 'EVENT_MAX', 100), patch.object(output_limits, 'READ_CHUNK', 32):
            with self.assertRaises(WorkerInterrupted) as raised:
                self.run_child(code, timeout=2)
        self.assertEqual(raised.exception.cause, 'output_limit')
        self.assertFalse(raised.exception.progress.get('terminal_received'))

    def test_stderr_file_including_marker_stays_within_byte_cap(self):
        with patch.object(output_limits, 'STDERR_MAX', 64), patch.object(output_limits, 'READ_CHUNK', 16):
            with self.assertRaises(WorkerInterrupted) as raised:
                self.run_child("import sys,time; sys.stderr.write('é'*5000); sys.stderr.flush(); time.sleep(8)", timeout=2)
        self.assertEqual(raised.exception.cause, 'output_limit')
        raw = (self.root / 'private-stderr.txt').read_bytes()
        self.assertLessEqual(len(raw), 64)
        self.assertIn(b'truncated', raw)
        self.assertGreater(raised.exception.progress['stderr_chars'], raised.exception.progress['stderr_retained_chars'])
        self.assertGreater(raised.exception.progress['stderr_observed_bytes'], raised.exception.progress['stderr_retained_bytes'])
        self.assertLessEqual(raised.exception.progress['stderr_retained_bytes'], 64)

    def test_oversized_json_line_is_not_parsed_as_success(self):
        code = (
            "import json\n"
            "print(json.dumps({'type':'result','is_error':False,'result':'ok','pad':'Y'*8000}))\n"
        )
        with patch.object(output_limits, 'EVENT_MAX', 200), patch.object(output_limits, 'TERMINAL_MAX', 50):
            with self.assertRaises(WorkerInterrupted) as raised:
                self.run_child(code, timeout=2)
        self.assertEqual(raised.exception.cause, 'output_limit')
        self.assertNotEqual(raised.exception.progress.get('state'), 'finished')
        self.assertFalse(raised.exception.progress.get('terminal_received'))


if __name__ == '__main__':
    unittest.main()
