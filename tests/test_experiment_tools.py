#!/usr/bin/env python3
"""Check measurement integrity and prevent accidental overlap of client/server CPUs."""
import asyncio
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "benchmarks/experiments"))
from client import Histogram, Session, Window
from run import cpu_set
from common import profile_valid


class MeasurementTests(unittest.TestCase):
    def test_expected_perf_stop_requires_valid_artifacts(self):
        result = {"perf_exit_codes": [-2, 0], "perf_stop_requested": [True, True], "perf_report_exit_code": 0}
        self.assertTrue(profile_valid(result, "10,msec,task-clock"))
        result["perf_stop_requested"][0] = False
        self.assertFalse(profile_valid(result, "10,msec,task-clock"))
        result["perf_exit_codes"][0] = 1
        result["perf_stop_requested"][0] = True
        self.assertFalse(profile_valid(result, "10,msec,task-clock"))
        result["perf_exit_codes"][0] = 0
        self.assertFalse(profile_valid(result, "<not supported>,task-clock"))
        result["perf_report_exit_code"] = 1
        self.assertFalse(profile_valid(result, "10,msec,task-clock"))

    def test_quantiles_and_histogram_counts(self):
        histogram = Histogram()
        for _ in range(95):
            histogram.add(.1)
        for _ in range(5):
            histogram.add(999)
        result = histogram.result()
        self.assertEqual(sum(result["counts"]), 100)
        self.assertEqual(result["p95_upper_ms"], .1)
        self.assertEqual(result["p99_upper_ms"], 1000)
        self.assertEqual(result["max_ms"], 999)

    def test_errors_do_not_lower_success_latency(self):
        window = Window()
        window.add(200, 500)
        for _ in range(1000):
            window.add(503, .1)
        result = window.result()
        self.assertEqual(result["success_latency"]["p99_upper_ms"], 500)
        self.assertEqual(result["failure_latency"]["count"], 1000)

    def test_cpu_ranges(self):
        self.assertEqual(cpu_set("0-2,4,4"), {0,1,2,4})
        for value in ("", "2-1", "-1", "1,", "0-1-2", "all"):
            with self.assertRaises(ValueError):
                cpu_set(value)

    def test_rejects_overlap_before_accessing_docker(self):
        result = subprocess.run([sys.executable, str(ROOT / "benchmarks/experiments/run.py"),
                                 "benchmark", "--output", "/tmp/unused", "--server-cpus", "0-1", "--client-cpus", "1-2"],
                                capture_output=True, text=True)
        self.assertEqual(result.returncode, 2)
        self.assertIn("must not overlap", result.stderr)

    def test_rejects_zero_duration(self):
        result = subprocess.run([sys.executable, str(ROOT / "benchmarks/experiments/run.py"),
                                 "benchmark", "--output", "/tmp/unused", "--duration", "0"], capture_output=True, text=True)
        self.assertEqual(result.returncode, 2)
        self.assertIn("positive", result.stderr)


class ClientTests(unittest.IsolatedAsyncioTestCase):
    async def test_detects_corruption_and_closes_connection(self):
        closed = asyncio.Event()
        async def corrupt(reader, writer):
            try:
                await reader.readuntil(b"\r\n\r\n")
                writer.write(b"HTTP/1.1 200 OK\r\nContent-Length: 3\r\n\r\nbad")
                await writer.drain()
                await reader.read()
                closed.set()
            finally:
                writer.close()
                await writer.wait_closed()
        server = await asyncio.start_server(corrupt, "127.0.0.1", 0)
        async with server:
            session = Session(SimpleNamespace(host="127.0.0.1", port=server.sockets[0].getsockname()[1], timeout=1))
            with self.assertRaisesRegex(ValueError, "corrupt"):
                await session.get()
            self.assertIsNone(session.writer)
            await asyncio.wait_for(closed.wait(), 1)


if __name__ == "__main__":
    unittest.main(verbosity=2)
