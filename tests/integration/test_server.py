#!/usr/bin/env python3
"""Black-box tests of real Linux server processes and raw TCP streams."""
import concurrent.futures
import json
import os
from pathlib import Path
import select
import signal
import socket
import subprocess
import sys
import tempfile
import time
import unittest

BINARY = str(Path(sys.argv.pop(1)).resolve())
BASELINE = "--baseline" in sys.argv
if BASELINE:
    sys.argv.remove("--baseline")


class Client:
    def __init__(self, port):
        self.sock = socket.create_connection(("127.0.0.1", port), timeout=5)
        self.buffer = b""

    def send(self, path="/hello", method="GET", body=b"", close=False):
        wire = (f"{method} {path} HTTP/1.1\r\nHost: localhost\r\nContent-Length: {len(body)}\r\n" +
                ("Connection: close\r\n" if close else "") + "\r\n").encode() + body
        self.sock.sendall(wire)

    def response(self, head=False):
        while b"\r\n\r\n" not in self.buffer:
            chunk = self.sock.recv(65536)
            if not chunk:
                raise EOFError(self.buffer)
            self.buffer += chunk
        header, self.buffer = self.buffer.split(b"\r\n\r\n", 1)
        lines = header.split(b"\r\n")
        status = int(lines[0].split()[1])
        headers = dict(line.decode().split(": ", 1) for line in lines[1:])
        length = 0 if head else int(headers.get("Content-Length", 0))
        while len(self.buffer) < length:
            chunk = self.sock.recv(65536)
            if not chunk:
                raise EOFError("truncated body")
            self.buffer += chunk
        body, self.buffer = self.buffer[:length], self.buffer[length:]
        return status, headers, body

    def close(self):
        self.sock.close()


class RunningServer:
    def __init__(self, *args, binary=BINARY):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        (self.root / "hello.txt").write_bytes(b"static content\n")
        (self.root / "large.bin").write_bytes(bytes(range(256)) * 4096)
        (self.root / "escape").symlink_to("/etc/passwd")
        (self.root / "escape-dir").symlink_to("/etc")
        os.mkfifo(self.root / "fifo")
        self.err = tempfile.TemporaryFile(mode="w+b")
        self.proc = subprocess.Popen([binary, "--port", "0", "--document-root", str(self.root), *args],
                                     stdout=subprocess.PIPE, stderr=self.err)
        if not select.select([self.proc.stdout], [], [], 20)[0]:
            self.proc.kill()
            raise RuntimeError("server did not start")
        line = self.proc.stdout.readline()
        if not line:
            self.err.seek(0)
            raise RuntimeError(self.err.read().decode())
        self.port = json.loads(line)["port"]

    def stop(self):
        if self.proc.poll() is None:
            self.proc.send_signal(signal.SIGTERM)
        try:
            code = self.proc.wait(timeout=15)
        except subprocess.TimeoutExpired:
            self.proc.kill()
            self.proc.wait()
            raise AssertionError("server failed to shut down")
        self.err.seek(0)
        errors = self.err.read().decode()
        self.proc.stdout.close()
        self.err.close()
        self.tmp.cleanup()
        if code != 0 or "Sanitizer" in errors or "runtime error:" in errors:
            raise AssertionError(f"exit {code}: {errors}")


class ServerTests(unittest.TestCase):
    def start(self, *args, **kw):
        server = RunningServer(*args, **kw)
        self.addCleanup(server.stop)
        return server

    def client(self, server):
        c = Client(server.port)
        self.addCleanup(c.close)
        return c

    def test_get_head_post_pipeline_fragmentation(self):
        for mode in ((), ("--edge-triggered",)):
            with self.subTest(mode=mode):
                s = self.start(*mode)
                c = self.client(s)
                wire = b"POST /api/echo HTTP/1.1\r\nHost: x\r\nContent-Length: 4\r\n\r\na\x00bc"
                for byte in wire:
                    c.sock.sendall(bytes([byte]))
                c.send("/hello", "HEAD")
                c.send("/health", close=True)
                self.assertEqual(c.response()[2], b"a\x00bc")
                self.assertEqual(int(c.response(head=True)[1]["Content-Length"]), len(b"Hello from PulseHTTP!\n"))
                self.assertEqual(json.loads(c.response()[2]), {"status": "ok"})
                self.assertEqual(c.sock.recv(1), b"")

    def test_framing_and_limits(self):
        s = self.start("--max-header-bytes", "256", "--max-body-bytes", "1024")
        cases = [
            (b"GET / HTTP/1.1\r\nHost: x\r\nContent-Length: 0\r\nTransfer-Encoding: chunked\r\n\r\n", 400),
            (b"POST / HTTP/1.1\r\nHost: x\r\nContent-Length: 1025\r\n\r\n", 413),
            (b"GET / HTTP/1.1\r\nHost: x\r\nX: " + b"x" * 256 + b"\r\n\r\n", 431),
            (b"GET / HTTP/1.1\r\nHost: x\r\nTransfer-Encoding: chunked\r\n\r\n", 501),
        ]
        for wire, status in cases:
            c = self.client(s)
            c.sock.sendall(wire)
            r = c.response()
            self.assertEqual(r[0], status)
            self.assertEqual(r[1]["Connection"], "close")

    def test_static_partial_writes_and_secure_paths(self):
        for mode in ((), ("--sendfile",), ("--edge-triggered", "--sendfile")):
            s = self.start(*mode)
            c = self.client(s)
            c.sock.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, 16384)
            c.send("/static/large.bin")
            time.sleep(.05)
            status, _, body = c.response()
            self.assertEqual(status, 200)
            self.assertEqual(body, bytes(range(256)) * 4096)
            c.send("/static/hello.txt", "HEAD")
            self.assertEqual(c.response(head=True)[1]["Content-Length"], "15")
            for path in ("../etc/passwd", "%2e%2e/etc/passwd", "%2fetc/passwd", "escape", "escape-dir/passwd", "fifo", "a%00b"):
                c.send("/static/" + path)
                self.assertIn(c.response()[0], (400, 403, 404))
            c.send("/health")
            self.assertEqual(c.response()[0], 200)

    @unittest.skipIf(BASELINE, "baseline closes on its thread deadlines")
    def test_timeouts(self):
        s = self.start("--idle-timeout-ms", "600", "--header-timeout-ms", "150", "--body-timeout-ms", "150")
        c = self.client(s)
        c.sock.sendall(b"GET / HTTP/1.1\r\nHo")
        self.assertEqual(c.response()[0], 408)
        c = self.client(s)
        c.sock.sendall(b"POST /api/echo HTTP/1.1\r\nHost: x\r\nContent-Length: 10\r\n\r\na")
        self.assertEqual(c.response()[0], 408)
        c = self.client(s)
        time.sleep(.8)
        self.assertEqual(c.sock.recv(1), b"")

    def test_request_and_output_limits(self):
        s = self.start("--max-requests", "2", "--max-output-bytes", "1024")
        c = self.client(s)
        c.send(); self.assertEqual(c.response()[0], 200)
        c.send(); self.assertEqual(c.response()[1]["Connection"], "close")
        self.assertEqual(c.sock.recv(1), b"")
        c = self.client(s)
        c.send("/api/echo", "POST", b"x" * 2000)
        self.assertEqual(c.response()[0], 503)

    @unittest.skipIf(BASELINE, "baseline has no worker queue")
    def test_backpressure_and_recovery(self):
        s = self.start("--workers", "1", "--queue-capacity", "1")
        clients = [self.client(s) for _ in range(64)]
        for c in clients:
            c.send("/api/compute?iterations=20000000")
        statuses = [c.response()[0] for c in clients]
        self.assertIn(503, statuses)
        self.assertIn(200, statuses)
        c = self.client(s); c.send("/metrics")
        body = c.response()[2].decode()
        values = dict(line.split() for line in body.splitlines() if line and not line.startswith("#") and "{" not in line)
        self.assertGreater(int(values["http_worker_queue_rejections_total"]), 0)
        self.assertLessEqual(int(values["http_worker_queue_depth"]), 1)
        c.send("/health"); self.assertEqual(c.response()[0], 200)

    def test_connection_churn_and_fd_reuse(self):
        s = self.start("--workers", "2", "--queue-capacity", "16", "--edge-triggered")
        before = len(list(Path(f"/proc/{s.proc.pid}/fd").iterdir()))
        def work(i):
            c = Client(s.port)
            try:
                if i % 3 == 0:
                    c.send("/api/compute?iterations=500000")
                else:
                    body = f"unique-{i}".encode()
                    c.send("/api/echo", "POST", body, close=True)
                    status, _, echoed = c.response()
                    if status == 200:
                        self.assertEqual(echoed, body)
                    else:
                        self.assertEqual(status, 503)
            finally:
                c.close()
        with concurrent.futures.ThreadPoolExecutor(max_workers=24) as executor:
            list(executor.map(work, range(400)))
        time.sleep(.4)
        after = len(list(Path(f"/proc/{s.proc.pid}/fd").iterdir()))
        self.assertLessEqual(after, before + 2)
        c = self.client(s); c.send("/health")
        self.assertEqual(c.response()[0], 200)

    def test_graceful_shutdown_active_request(self):
        s = self.start()
        c = self.client(s)
        c.send("/api/compute?iterations=20000000")
        time.sleep(.005)
        s.proc.send_signal(signal.SIGTERM)
        self.assertEqual(c.response()[0], 200)
        self.assertEqual(c.sock.recv(1), b"")
        self.assertEqual(s.proc.wait(timeout=5), 0)

    @unittest.skipIf(BASELINE, "baseline has no worker completion queue")
    def test_processing_timeout_discards_stale_completion(self):
        s = self.start("--processing-timeout-ms", "1", "--workers", "1")
        # The 50 ms sweep must see a handler that is still processing. A long queue
        # guarantees this even on machines where one compute call is very fast.
        clients = [self.client(s) for _ in range(16)]
        for c in clients:
            c.send("/api/compute?iterations=20000000")
        statuses = [c.response()[0] for c in clients]
        self.assertIn(504, statuses)
        time.sleep(2)
        c = self.client(s); c.send("/health")
        self.assertEqual(c.response()[0], 200)

    def test_half_close_still_receives_response(self):
        s = self.start()
        c = self.client(s); c.send()
        c.sock.shutdown(socket.SHUT_WR)
        self.assertEqual(c.response()[0], 200)

    def test_large_binary_body_and_long_pipeline(self):
        s = self.start("--edge-triggered")
        c = self.client(s)
        body = bytes(range(256)) * 4096
        c.send("/api/echo", "POST", body)
        self.assertEqual(c.response()[2], body)
        for i in range(100):
            c.send("/api/echo", "POST", str(i).encode())
        for i in range(100):
            self.assertEqual(c.response()[2], str(i).encode())

    @unittest.skipIf(BASELINE, "baseline closes on its thread deadlines")
    def test_absolute_header_deadline_with_trickle(self):
        s = self.start("--header-timeout-ms", "200", "--idle-timeout-ms", "2000")
        c = self.client(s)
        for byte in b"GET":
            c.sock.sendall(bytes([byte]))
            time.sleep(.08)
        self.assertEqual(c.response()[0], 408)

    def test_connection_admission_and_file_limit(self):
        s = self.start("--max-connections", "1", "--max-file-bytes", "100")
        c = self.client(s)
        c.send("/health"); self.assertEqual(c.response()[0], 200)
        second = self.client(s)
        self.assertEqual(second.sock.recv(1), b"")
        c.send("/static/large.bin"); self.assertEqual(c.response()[0], 413)
        c.send("/static/missing.txt"); self.assertEqual(c.response()[0], 404)

    @unittest.skipIf(BASELINE, "custom_routes runs the reactor")
    def test_custom_handlers_exceptions_and_timeout_generation(self):
        binary = str(Path(BINARY).with_name("custom_routes"))
        s = self.start("--processing-timeout-ms", "100", binary=binary)
        c = self.client(s)
        c.send("/version"); self.assertEqual(c.response()[2], b"PulseHTTP 0.1.0\n")
        c.send("/throw"); self.assertEqual(c.response()[0], 500)
        c.send("/invalid-response"); self.assertEqual(c.response()[0], 500)
        c = self.client(s)
        c.send("/slow"); self.assertEqual(c.response()[0], 504)
        self.assertEqual(c.sock.recv(1), b"")
        # Reuse closed descriptor numbers while the timed-out worker still runs.
        for _ in range(10):
            another = self.client(s)
            another.send("/version", close=True)
            self.assertEqual(another.response()[2], b"PulseHTTP 0.1.0\n")
        time.sleep(.2)
        another = self.client(s)
        another.send("/metrics")
        metrics = another.response()[2].decode()
        values = dict(line.split() for line in metrics.splitlines() if line and not line.startswith("#") and "{" not in line)
        self.assertGreaterEqual(int(values["http_stale_completions_total"]), 1)
        self.assertGreaterEqual(int(values["http_handler_errors_total"]), 2)


if __name__ == "__main__":
    unittest.main(verbosity=2)
