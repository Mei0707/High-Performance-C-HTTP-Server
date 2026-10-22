#!/usr/bin/env python3
"""Remote idle and soak workloads. Long runs use bounded in-memory histograms."""
import argparse
import asyncio
from collections import Counter
import json
from pathlib import Path
import resource
import time
from common import environment, write_json

HELLO = b"Hello from PulseHTTP!\n"
BUCKETS = [.1, .25, .5, 1, 2, 3, 5, 10, 20, 30, 50, 75, 100, 200, 300, 500, 1000, 2000, 3000, 5000, None]


class Session:
    def __init__(self, args):
        self.args = args
        self.reader = self.writer = None

    async def get(self, path="/hello", close=False):
        try:
            return await asyncio.wait_for(self._get(path, close), self.args.timeout)
        except BaseException:
            await self.close()
            raise

    async def _get(self, path, close):
        if self.writer is None:
            self.reader, self.writer = await asyncio.open_connection(self.args.host, self.args.port)
        wire = (f"GET {path} HTTP/1.1\r\nHost: pulse-experiment\r\n" +
                ("Connection: close\r\n" if close else "") + "\r\n").encode()
        self.writer.write(wire)
        await self.writer.drain()
        header = await self.reader.readuntil(b"\r\n\r\n")
        lines = header.decode("ascii").split("\r\n")
        status = int(lines[0].split()[1])
        headers = dict(line.split(": ", 1) for line in lines[1:] if line)
        length = int(headers["Content-Length"])
        if length < 0 or length > 2 * 1024 * 1024:
            raise ValueError("unexpected response length")
        body = await self.reader.readexactly(length)
        if status == 200:
            if path == "/hello" and body != HELLO:
                raise ValueError("corrupt hello response")
            if path.startswith("/api/compute") and not body.rstrip(b"\n").isdigit():
                raise ValueError("corrupt compute response")
        if headers.get("Connection") == "close":
            await self.close()
        return status

    async def close(self):
        writer, self.writer = self.writer, None
        self.reader = None
        if writer:
            writer.close()
            try:
                await asyncio.wait_for(writer.wait_closed(), 1)
            except (OSError, asyncio.TimeoutError):
                pass


class Histogram:
    def __init__(self):
        self.counts = [0] * len(BUCKETS)
        self.total_ms = self.maximum_ms = 0.0
        self.count = 0

    def add(self, ms):
        self.count += 1
        self.total_ms += ms
        self.maximum_ms = max(self.maximum_ms, ms)
        for i, upper in enumerate(BUCKETS):
            if upper is None or ms <= upper:
                self.counts[i] += 1
                return

    def result(self):
        result = {"count": self.count, "counts": self.counts.copy(), "max_ms": self.maximum_ms,
                  "mean_ms": self.total_ms / self.count if self.count else None}
        for percentile in (50, 95, 99):
            cumulative = 0
            result[f"p{percentile}_upper_ms"] = None
            if not self.count:
                continue
            for i, n in enumerate(self.counts):
                cumulative += n
                if cumulative * 100 >= self.count * percentile:
                    result[f"p{percentile}_upper_ms"] = BUCKETS[i]
                    break
        return result


class Window:
    def __init__(self):
        self.statuses = Counter()
        self.success = Histogram()
        self.failure = Histogram()

    def add(self, status, ms):
        self.statuses[str(status)] += 1
        (self.success if status == 200 else self.failure).add(ms)

    def result(self):
        return {"statuses": dict(self.statuses), "success_latency": self.success.result(),
                "failure_latency": self.failure.result()}


async def idle(args):
    failures = Counter()
    sessions = []
    gate = asyncio.Semaphore(args.dial_parallel)
    async def establish():
        async with gate:
            session = Session(args)
            try:
                if await session.get() != 200:
                    raise ValueError("non-200 admission probe")
                sessions.append(session)
            except (OSError, ValueError, asyncio.TimeoutError, asyncio.IncompleteReadError) as error:
                failures[type(error).__name__] += 1
                await session.close()
    report = {"environment": environment(), "arguments": vars(args), "requested": args.connections}
    try:
        setup_start = time.monotonic()
        await asyncio.gather(*(establish() for _ in range(args.connections)))
        report.update({"setup_seconds": time.monotonic() - setup_start, "established": len(sessions),
                       "hold_start_unix_ns": time.time_ns(), "hold_seconds": args.duration})
        end = time.monotonic() + args.duration
        active = sessions[:max(1, int(len(sessions) * .01))]
        report["sparse_active_connections"] = len(active)
        while time.monotonic() < end:
            for session in active:
                if session.writer is None:
                    continue
                try:
                    if await session.get() != 200:
                        failures["non_200_during_hold"] += 1
                except (OSError, ValueError, asyncio.TimeoutError, asyncio.IncompleteReadError) as error:
                    failures[type(error).__name__] += 1
            await asyncio.sleep(min(2, max(0, end - time.monotonic())))
        report["hold_end_unix_ns"] = time.time_ns()
        survived = 0
        async def verify(session):
            nonlocal survived
            async with gate:
                if session.writer is None:
                    failures["lost_original_connection"] += 1
                    return
                try:
                    if await session.get() == 200:
                        survived += 1
                except (OSError, ValueError, asyncio.TimeoutError, asyncio.IncompleteReadError) as error:
                    failures[type(error).__name__] += 1
        await asyncio.gather(*(verify(s) for s in sessions))
        report.update({"survived": survived, "failures": dict(failures),
                       "target_reached": survived == args.connections and not failures})
    finally:
        await asyncio.gather(*(session.close() for session in sessions))
        write_json(Path(args.output) / "client.json", report)
    print(json.dumps({k:v for k,v in report.items() if k != "environment"}), flush=True)


async def soak(args):
    output = Path(args.output)
    output.mkdir(parents=True, exist_ok=True)
    start = time.monotonic()
    end = start + args.duration
    phases = [("steady", "/hello", args.connections, False),
              ("churn", "/hello", args.connections, True),
              ("overload", "/api/compute?iterations=20000000", args.overload_connections, False),
              ("recovery", "/hello", args.connections, False)]
    report = {"environment": environment(), "arguments": vars(args), "bucket_upper_bounds_ms": BUCKETS,
              "start_unix_ns": time.time_ns(), "phase_totals": {}, "unexpected_errors": 0}
    index = 0
    with (output / "client-windows.jsonl").open("w", buffering=1) as sink:
        while time.monotonic() < end:
            name, path, connections, close = phases[index % len(phases)]
            phase_end = min(end, time.monotonic() + args.phase_seconds)
            window = Window()
            totals = report["phase_totals"].setdefault(name, Counter())
            async def worker():
                session = Session(args)
                try:
                    while time.monotonic() < phase_end:
                        before = time.perf_counter()
                        try:
                            status = await session.get(path, close)
                        except (OSError, ValueError, asyncio.TimeoutError, asyncio.IncompleteReadError) as error:
                            status = type(error).__name__
                            await asyncio.sleep(.01)
                        window.add(status, (time.perf_counter() - before) * 1000)
                        totals[str(status)] += 1
                        if status != 200 and not (name == "overload" and status == 503):
                            report["unexpected_errors"] += 1
                finally:
                    await session.close()
            work = asyncio.gather(*(worker() for _ in range(connections)))
            window_start = time.time_ns()
            while not work.done():
                await asyncio.sleep(min(args.window_seconds, max(.01, phase_end - time.monotonic())))
                window_end = time.time_ns()
                sink.write(json.dumps({"phase": name, "cycle": index // 4,
                                       "start_unix_ns": window_start, "end_unix_ns": window_end,
                                       **window.result()}) + "\n")
                window = Window()
                window_start = window_end
                # Once admission stops, wait for the last in-flight responses instead of spinning.
                if time.monotonic() >= phase_end:
                    await work
                    if window.success.count or window.failure.count:
                        sink.write(json.dumps({"phase": name, "cycle": index // 4,
                                               "start_unix_ns": window_start, "end_unix_ns": time.time_ns(),
                                               **window.result()}) + "\n")
                    break
            await work
            index += 1
            report["elapsed_seconds"] = time.monotonic() - start
            report["client_peak_rss_kib"] = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
            write_json(output / "client.json", report)
            print(json.dumps({"phase_finished": name, "elapsed_seconds": report["elapsed_seconds"],
                              "unexpected_errors": report["unexpected_errors"]}), flush=True)
    report["end_unix_ns"] = time.time_ns()
    report["completed"] = True
    write_json(output / "client.json", report)
    if report["unexpected_errors"]:
        raise SystemExit("soak recorded unexpected errors; artifacts retained")


if __name__ == "__main__":
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("mode", choices=["idle", "soak"])
    p.add_argument("--host", required=True)
    p.add_argument("--port", type=int, default=8080)
    p.add_argument("--output", required=True)
    p.add_argument("--connections", type=int, default=2000)
    p.add_argument("--duration", type=float, default=30)
    p.add_argument("--dial-parallel", type=int, default=32)
    p.add_argument("--timeout", type=float, default=5)
    p.add_argument("--phase-seconds", type=float, default=30)
    p.add_argument("--window-seconds", type=float, default=5)
    p.add_argument("--overload-connections", type=int, default=256)
    args = p.parse_args()
    if min(args.connections, args.duration, args.dial_parallel, args.timeout,
           args.phase_seconds, args.window_seconds, args.overload_connections) <= 0:
        p.error("limits and durations must be positive")
    asyncio.run(idle(args) if args.mode == "idle" else soak(args))
