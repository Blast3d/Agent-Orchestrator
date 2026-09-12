"""Bounded process execution with durable, answer-only progress evidence."""
import codecs
import json
import os
from pathlib import Path
import queue
import subprocess
import threading
import time

import output_limits
from output_limits import OutputLimitExceeded
from execution_limits import timeout_for_task
from task_store import write_json
from worker_progress import ClaudeProgress
from antigravity_progress import AntigravityProgress, AntigravityProtocolError


class WorkerInterrupted(Exception):
    """Local termination does not prove remote inference has stopped."""
    def __init__(self, cause, process_terminated=False, process_pid=None, progress=None):
        self.cause = cause
        self.process_terminated = process_terminated
        self.process_pid = process_pid
        self.progress = progress
        super().__init__(cause)


def _utf8_bytes(text):
    return len(text.encode('utf-8'))


def _clip_utf8(text, max_bytes):
    if max_bytes <= 0:
        return ''
    data = text.encode('utf-8')
    if len(data) <= max_bytes:
        return text
    return data[:max_bytes].decode('utf-8', errors='ignore')


def _read_pipe_chunk(handle, size):
    read1 = getattr(handle, 'read1', None)
    if callable(read1):
        return read1(size)
    raw = getattr(handle, 'raw', None)
    if raw is not None:
        return raw.read(size)
    return os.read(handle.fileno(), size)


def invoke_cloud(command, stdin, work, env, *, timeout_seconds=None, protocol='claude', expected_model=None):
    """Read both pipes concurrently; progress never extends the hard deadline.

    Windows communicate() buffers stdout until EOF. Dedicated pipe readers
    provide live events there as well. Thinking bodies are discarded in memory;
    only safe counters and answer deltas are persisted before a terminal result.
    Public previews may truncate; oversized terminals are never success.
    """
    limit = timeout_for_task('small') if timeout_seconds is None else timeout_seconds
    if isinstance(limit, bool) or not isinstance(limit, (int, float)) or not 0 < limit <= 1800:
        raise ValueError('Execution deadline must be positive and no more than 1800 seconds')
    work = Path(work)
    stream = '--output-format' in command and command[command.index('--output-format') + 1] == 'stream-json'
    if protocol not in ('claude', 'antigravity'):
        raise ValueError('Unknown worker stream protocol')
    parser = (AntigravityProgress(expected_model=expected_model)
              if protocol == 'antigravity' else ClaudeProgress())
    started = time.monotonic()
    last_persist = 0.0
    output_chars = 0
    stdout_retained_chars = 0
    stdout_retained_bytes = 0
    stderr_observed_chars = 0
    stderr_observed_bytes = 0
    stderr_retained_chars = 0
    stderr_retained_bytes = 0
    stderr_truncated = False
    stdout_parts = []
    stderr_text = ''
    stdout_line = ''
    events = queue.Queue(maxsize=max(1, output_limits.QUEUE_MAX))
    readers_finished = set()
    readers_lock = threading.Lock()
    stop = threading.Event()

    def remaining():
        return limit - (time.monotonic() - started)

    def persist(status, force=False):
        nonlocal last_persist
        now = time.monotonic()
        snapshot = dict(parser.snapshot(), elapsed_s=round(now - started, 3),
                        timeout_seconds=limit, process_status=status,
                        output_mode='stream-json' if stream else 'buffered-json',
                        stdout_chars=output_chars, stdout_retained_chars=stdout_retained_chars,
                        stderr_chars=stderr_observed_chars, stderr_retained_chars=stderr_retained_chars,
                        stderr_observed_bytes=stderr_observed_bytes,
                        stderr_retained_bytes=stderr_retained_bytes,
                        stderr_truncated=stderr_truncated)
        if force or now - last_persist >= 2:
            write_json(work / 'execution-progress.json', snapshot)
            (work / 'partial-response.txt').write_bytes(parser.partial_response.encode('utf-8'))
            last_persist = now
        return snapshot

    def stderr_marker():
        # Numeric accounting lives in progress; keep this marker inside the file cap.
        return '\n[stderr truncated]\n'

    def write_stderr_file():
        nonlocal stderr_text, stderr_retained_chars, stderr_retained_bytes
        marker = stderr_marker() if stderr_truncated else ''
        cap = output_limits.STDERR_MAX
        marker_b = _utf8_bytes(marker)
        max_body = max(0, cap - marker_b)
        body = _clip_utf8(stderr_text, max_body)
        stderr_text = body
        stderr_retained_chars = len(body)
        stderr_retained_bytes = _utf8_bytes(body)
        payload = body + marker
        if _utf8_bytes(payload) > cap:
            payload = _clip_utf8(payload, cap)
        (work / 'private-stderr.txt').write_bytes(payload.encode('utf-8'))

    def enqueue(item):
        deadline_hit = remaining() <= 0 or stop.is_set()
        if deadline_hit:
            try:
                events.put_nowait(item)
                return True
            except queue.Full:
                return False
        while not stop.is_set():
            rem = remaining()
            if rem <= 0:
                try:
                    events.put_nowait(item)
                    return True
                except queue.Full:
                    return False
            try:
                events.put(item, timeout=min(0.2, rem))
                return True
            except queue.Full:
                continue
        try:
            events.put_nowait(item)
            return True
        except queue.Full:
            return False

    persist('starting', True)
    process = subprocess.Popen(command, cwd=work, env=env, stdin=subprocess.PIPE,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0)

    def mark_reader_finished(label):
        with readers_lock:
            readers_finished.add(label)

    def read_pipe(handle, label):
        decoder = codecs.getincrementaldecoder('utf-8')('replace')
        try:
            while not stop.is_set():
                chunk = _read_pipe_chunk(handle, output_limits.READ_CHUNK)
                if not chunk:
                    tail = decoder.decode(b'', final=True)
                    if tail:
                        enqueue((label, tail, time.monotonic() - started))
                    break
                text = decoder.decode(chunk)
                if text and not enqueue((label, text, time.monotonic() - started)):
                    break
        except Exception:
            enqueue(('error', label, 0))
        finally:
            enqueue(('end', label, 0))
            mark_reader_finished(label)

    def write_input():
        try:
            if stdin is not None:
                data = stdin.encode('utf-8') if isinstance(stdin, str) else stdin
                process.stdin.write(data)
                process.stdin.flush()
        except (OSError, ValueError):
            enqueue(('error', 'stdin', 0))
        finally:
            try:
                process.stdin.close()
            except OSError:
                pass
            enqueue(('end', 'stdin', 0))
            mark_reader_finished('stdin')

    threads = [threading.Thread(target=read_pipe, args=(process.stdout, 'stdout'), daemon=True),
               threading.Thread(target=read_pipe, args=(process.stderr, 'stderr'), daemon=True),
               threading.Thread(target=write_input, daemon=True)]

    def add_stderr(content):
        nonlocal stderr_observed_chars, stderr_observed_bytes
        nonlocal stderr_retained_chars, stderr_retained_bytes, stderr_truncated, stderr_text
        stderr_observed_chars += len(content)
        stderr_observed_bytes += _utf8_bytes(content)
        cap = output_limits.STDERR_MAX
        if stderr_retained_bytes >= cap:
            stderr_truncated = True
            raise OutputLimitExceeded('stderr')
        room = cap - stderr_retained_bytes
        piece = _clip_utf8(content, room)
        stderr_text += piece
        stderr_retained_chars += len(piece)
        stderr_retained_bytes += _utf8_bytes(piece)
        if _utf8_bytes(content) > room:
            stderr_truncated = True
            raise OutputLimitExceeded('stderr')

    def handle_stream_line(line, elapsed):
        if _utf8_bytes(line) > output_limits.EVENT_MAX:
            raise OutputLimitExceeded('stdout_line')
        if not line.strip():
            return
        try:
            parsed = json.loads(line)
        except ValueError:
            parsed = None
        if protocol == 'antigravity' and isinstance(parsed, dict) and parsed.get('event') == 'init':
            info = parsed.get('init')
            cwd = info.get('cwd') if isinstance(info, dict) else None
            if not isinstance(cwd, str) or Path(cwd).resolve() != work.resolve():
                raise AntigravityProtocolError('antigravity_workspace_mismatch')
        parser.observe(parsed, elapsed)

    def feed_stdout(content, elapsed, cleanup=False):
        nonlocal output_chars, stdout_retained_chars, stdout_retained_bytes, stdout_line
        output_chars += len(content)
        if not stream:
            if stdout_retained_bytes >= output_limits.BUFFERED_STDOUT_MAX:
                if not cleanup:
                    raise OutputLimitExceeded('stdout')
                return
            room = output_limits.BUFFERED_STDOUT_MAX - stdout_retained_bytes
            piece = _clip_utf8(content, room)
            stdout_parts.append(piece)
            stdout_retained_chars += len(piece)
            stdout_retained_bytes += _utf8_bytes(piece)
            if _utf8_bytes(content) > room and not cleanup:
                raise OutputLimitExceeded('stdout')
            return
        start = 0
        while True:
            nl = content.find('\n', start)
            if nl < 0:
                rest = content[start:]
                if _utf8_bytes(stdout_line) + _utf8_bytes(rest) > output_limits.EVENT_MAX:
                    stdout_line = ''
                    if not cleanup:
                        raise OutputLimitExceeded('stdout_line')
                    return
                stdout_line += rest
                stdout_retained_chars = len(stdout_line)
                stdout_retained_bytes = _utf8_bytes(stdout_line)
                return
            piece = content[start:nl]
            if _utf8_bytes(stdout_line) + _utf8_bytes(piece) > output_limits.EVENT_MAX:
                stdout_line = ''
                if not cleanup:
                    raise OutputLimitExceeded('stdout_line')
                start = nl + 1
                continue
            line = stdout_line + piece
            stdout_line = ''
            start = nl + 1
            stdout_retained_chars = 0
            stdout_retained_bytes = 0
            handle_stream_line(line, elapsed)

    def consume(event, cleanup=False):
        nonlocal stdout_line
        label, content, elapsed = event
        if label == 'end':
            if content == 'stdout' and stream and stdout_line:
                leftover = stdout_line
                stdout_line = ''
                event_time = time.monotonic() - started
                if event_time < 0:
                    event_time = 0.0
                try:
                    handle_stream_line(leftover, event_time)
                except (OutputLimitExceeded, AntigravityProtocolError):
                    if not cleanup:
                        raise
        elif label == 'error':
            if not cleanup:
                raise OSError('Worker pipe could not be read or written')
        elif label == 'stderr':
            if cleanup:
                try:
                    add_stderr(content)
                except OutputLimitExceeded:
                    pass
            else:
                add_stderr(content)
        elif label == 'stdout':
            feed_stdout(content, elapsed, cleanup=cleanup)

    try:
        for thread in threads:
            thread.start()
        while True:
            with readers_lock:
                pipes_done = len(readers_finished) >= 3
            if pipes_done and events.empty() and process.poll() is not None:
                break
            rem = remaining()
            if rem <= 0:
                raise subprocess.TimeoutExpired(command, limit)
            try:
                consume(events.get(timeout=min(0.25, rem)))
            except queue.Empty:
                pass
            persist('running')
        write_stderr_file()
        if stream and parser.terminal_result is None and (process.returncode == 0 or protocol == 'antigravity'):
            raise WorkerInterrupted('missing_terminal_result', True, process.pid)
        snapshot = persist('exited', True)
        stdout = json.dumps(parser.terminal_result or {}) if stream else ''.join(stdout_parts)
        completed = subprocess.CompletedProcess(command, process.returncode, stdout, stderr_text)
        completed.process_pid = process.pid
        completed.progress = snapshot
        return completed
    except (KeyboardInterrupt, Exception) as exc:
        stop.set()
        terminated = process.poll() is not None
        try:
            if not terminated:
                process.kill()
            process.wait(timeout=10)
            terminated = process.poll() is not None
        except (OSError, subprocess.TimeoutExpired):
            pass
        with readers_lock:
            already_done = len(readers_finished) >= 3
        drain_until = time.monotonic() + (0.05 if already_done else 2)
        while time.monotonic() < drain_until:
            with readers_lock:
                done = len(readers_finished) >= 3
            if done and events.empty():
                break
            try:
                consume(events.get(timeout=0.05), cleanup=True)
            except queue.Empty:
                if done:
                    break
            except (OutputLimitExceeded, AntigravityProtocolError):
                pass
        cause = (exc.cause if isinstance(exc, (WorkerInterrupted, AntigravityProtocolError)) else
                 'timeout' if isinstance(exc, subprocess.TimeoutExpired) else
                 'output_limit' if isinstance(exc, OutputLimitExceeded) else
                 'interrupted' if isinstance(exc, KeyboardInterrupt) else 'process_io_error')
        snapshot = dict(parser.snapshot(), process_status=cause, timeout_seconds=limit)
        try:
            write_stderr_file()
            snapshot = persist(cause, True)
        except OSError:
            snapshot['evidence_write_failed'] = True
        with readers_lock:
            snapshot['pipe_readers_stopped'] = len(readers_finished) == 3
        raise WorkerInterrupted(cause, terminated, process.pid, snapshot) from exc
    finally:
        stop.set()
        for thread, handle in zip(threads, (process.stdout, process.stderr, process.stdin)):
            if thread.ident is not None:
                thread.join(timeout=0.1)
            if not thread.is_alive():
                try:
                    handle.close()
                except OSError:
                    pass
