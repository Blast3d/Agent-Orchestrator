"""Real loopback regression for the total rejected-body drain deadline."""
from pathlib import Path
import socket
import sys
import tempfile
import threading
import time
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'app'))
from brain_dashboard import make_server


class RejectedBodyTimingTests(unittest.TestCase):
    def test_unauthorized_slow_body_has_total_deadline_and_never_opens_store(self):
        calls = []

        def forbidden_store(root):
            calls.append(root)
            raise AssertionError('Rejected requests must not open the memory store')

        with tempfile.TemporaryDirectory() as root:
            server = make_server(root, store_factory=forbidden_store)
            serving = threading.Thread(target=server.serve_forever, daemon=True)
            serving.start()
            client = socket.create_connection(server.server_address, timeout=2)
            stop = threading.Event()
            sender = None
            try:
                # The old per-read timeout accepts each 50 ms byte and waits over
                # three seconds. The drain must have one total 250 ms deadline.
                request = ('POST /api/memories HTTP/1.1\r\n'
                           'Host: ' + server.origin.removeprefix('http://') + '\r\n'
                           'Origin: https://not-the-dashboard.invalid\r\n'
                           'Content-Type: application/json\r\n'
                           'Content-Length: 64\r\nConnection: close\r\n\r\n')
                started = time.monotonic()
                client.sendall(request.encode('ascii'))

                def trickle():
                    for _ in range(64):
                        if stop.is_set():
                            return
                        try:
                            client.sendall(b'x')
                        except OSError:
                            return
                        if stop.wait(.05):
                            return

                sender = threading.Thread(target=trickle, daemon=True)
                sender.start()
                response = b''
                while b'\r\n' not in response:
                    chunk = client.recv(4096)
                    if not chunk:
                        break
                    response += chunk
                elapsed = time.monotonic() - started
                self.assertTrue(response.startswith(b'HTTP/1.0 403 '), response[:80])
                self.assertLess(elapsed, 1.5, 'Slow rejected body extended the total drain deadline')
                self.assertEqual(calls, [], 'Rejected request reached memory storage')
            finally:
                stop.set()
                client.close()
                if sender is not None:
                    sender.join(timeout=1)
                server.shutdown()
                server.server_close()
                serving.join(timeout=1)
            self.assertFalse(serving.is_alive())
            self.assertFalse(sender and sender.is_alive())
            self.assertEqual(calls, [])


if __name__ == '__main__':
    unittest.main()
