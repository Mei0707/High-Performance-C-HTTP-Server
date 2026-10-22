#!/usr/bin/env python3
"""Standard-library loopback load generator; writes raw latencies and Linux process telemetry.

This client shares CPU with the server and can bottleneck. Use wrk/hey on a second
machine for capacity claims. Never compare runs across different environments.
"""
import argparse
import asyncio
from datetime import datetime, timezone
import hashlib
import gzip
import json
import os
from pathlib import Path
import platform
import resource
import select
import signal
import subprocess
import time


def process_sample(pid):
    try:
        status = dict(line.split(":", 1) for line in Path(f"/proc/{pid}/status").read_text().splitlines())
        ticks = os.sysconf("SC_CLK_TCK")
        # stat's comm field can contain spaces; skip through the final ')'.
        fields = Path(f"/proc/{pid}/stat").read_text().rsplit(")", 1)[1].split()
        switches = 0
        for task in Path(f"/proc/{pid}/task").iterdir():
            try:
                for line in (task / "status").read_text().splitlines():
                    if line.startswith(("voluntary_ctxt_switches:", "nonvoluntary_ctxt_switches:")):
                        switches += int(line.split()[1])
            except FileNotFoundError:
                pass
        return {"time": time.monotonic(), "rss_kib": int(status["VmRSS"].split()[0]),
                "threads": int(status["Threads"]), "fds": len(list(Path(f"/proc/{pid}/fd").iterdir())),
                "cpu_seconds": (int(fields[11]) + int(fields[12])) / ticks, "context_switches": switches}
    except (FileNotFoundError, ProcessLookupError):
        return {}


async def load(port, args, duration):
    deadline = time.monotonic() + duration
    successes, failures = [], []
    statuses = {}
    payload = (f"GET {args.path} HTTP/1.1\r\nHost: localhost\r\n" +
               ("Connection: close\r\n" if args.close else "") + "\r\n").encode()

    async def client():
        reader = writer = None
        try:
            while time.monotonic() < deadline:
                start = time.perf_counter()
                try:
                    if writer is None:
                        reader, writer = await asyncio.wait_for(asyncio.open_connection("127.0.0.1", port), 3)
                    writer.write(payload)
                    await writer.drain()
                    header = await asyncio.wait_for(reader.readuntil(b"\r\n\r\n"), 3)
                    lines = header.decode("ascii").split("\r\n")
                    code = int(lines[0].split()[1])
                    headers = dict(line.split(": ", 1) for line in lines[1:] if line)
                    body = await asyncio.wait_for(reader.readexactly(int(headers.get("Content-Length", 0))), 3)
                    if code == 200 and args.path == "/hello" and body != b"Hello from PulseHTTP!\n":
                        raise ValueError("corrupted response")
                    ms = (time.perf_counter() - start) * 1000
                    statuses[str(code)] = statuses.get(str(code), 0) + 1
                    (successes if code == 200 else failures).append(ms)
                    if headers.get("Connection") == "close":
                        writer.close()
                        await writer.wait_closed()
                        reader = writer = None
                except (OSError, asyncio.TimeoutError, asyncio.IncompleteReadError, ValueError) as error:
                    label = type(error).__name__
                    statuses[label] = statuses.get(label, 0) + 1
                    failures.append((time.perf_counter() - start) * 1000)
                    if writer:
                        writer.close()
                    reader = writer = None
                    await asyncio.sleep(.001)
        finally:
            if writer:
                writer.close()
                try:
                    await writer.wait_closed()
                except OSError:
                    pass

    start = time.monotonic()
    await asyncio.gather(*(client() for _ in range(args.concurrency)))
    elapsed = time.monotonic() - start
    return {"elapsed_seconds": elapsed, "successes": len(successes), "errors": len(failures),
            "statuses": statuses, "latencies_ms": successes, "error_latencies_ms": failures,
            "successful_rps": len(successes) / elapsed,
            **{f"p{p}_ms": percentile(successes, p) for p in (50, 95, 99)}}


def percentile(values, p):
    values = sorted(values)
    return values[min(len(values) - 1, int((len(values) - 1) * p / 100))] if values else None


async def measured_run(port, pid, args):
    samples = [process_sample(pid)]
    work = asyncio.create_task(load(port, args, args.duration))
    while not work.done():
        await asyncio.sleep(.1)
        samples.append(process_sample(pid))
    result = await work
    result["process_samples"] = [s for s in samples if s]
    if samples[0] and samples[-1]:
        result["cpu_seconds"] = samples[-1]["cpu_seconds"] - samples[0]["cpu_seconds"]
        result["cpu_percent"] = result["cpu_seconds"] / result["elapsed_seconds"] * 100
        result["peak_rss_kib"] = max(s.get("rss_kib", 0) for s in samples)
        # For threads baseline, exited task counters disappear; use only as a diagnostic.
        result["context_switch_delta_live_tasks"] = samples[-1]["context_switches"] - samples[0]["context_switches"]
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--server", default="build/pulsehttp_server")
    parser.add_argument("--output", required=True)
    parser.add_argument("--path", default="/hello")
    parser.add_argument("--concurrency", type=int, default=32)
    parser.add_argument("--duration", type=float, default=3)
    parser.add_argument("--warmup", type=float, default=1)
    parser.add_argument("--repetitions", type=int, default=3)
    parser.add_argument("--close", action="store_true")
    parser.add_argument("server_args", nargs=argparse.REMAINDER)
    args = parser.parse_args()
    if min(args.concurrency, args.duration, args.warmup, args.repetitions) <= 0:
        parser.error("concurrency, duration, warmup and repetitions must be positive")
    extra = args.server_args[1:] if args.server_args[:1] == ["--"] else args.server_args
    command = [str(Path(args.server).resolve()), "--port", "0", *extra]
    try:
        sha = subprocess.check_output(["git", "rev-parse", "HEAD"], stderr=subprocess.DEVNULL, text=True).strip()
    except (subprocess.CalledProcessError, FileNotFoundError):
        sha = None
    metadata = {"schema": 1, "utc": datetime.now(timezone.utc).isoformat(),
                "platform": platform.platform(), "machine": platform.machine(), "logical_cpus": os.cpu_count(),
                "cpuinfo": Path("/proc/cpuinfo").read_text(), "meminfo": Path("/proc/meminfo").read_text(),
                "python": platform.python_version(), "compiler": subprocess.check_output(["c++", "--version"], text=True),
                "fd_limit": resource.getrlimit(resource.RLIMIT_NOFILE), "commit": sha,
                "binary_sha256": hashlib.sha256(Path(args.server).read_bytes()).hexdigest(),
                "source_sha256": source_hash(),
                "cmake_cache": (Path(args.server).parent / "CMakeCache.txt").read_text(),
                "somaxconn": Path("/proc/sys/net/core/somaxconn").read_text().strip(),
                "topology": "Linux container loopback; Python client and server share CPUs", "command": command,
                "load": vars(args), "runs": []}
    for repetition in range(args.repetitions):
        proc = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
        try:
            if not select.select([proc.stdout], [], [], 20)[0]:
                raise RuntimeError("startup timeout")
            port = json.loads(proc.stdout.readline())["port"]
            asyncio.run(load(port, args, args.warmup))
            result = asyncio.run(measured_run(port, proc.pid, args))
            metadata["runs"].append(result)
            print(json.dumps({k: v for k, v in result.items() if k not in ("latencies_ms", "error_latencies_ms", "process_samples")}))
        finally:
            proc.send_signal(signal.SIGTERM)
            try:
                code = proc.wait(timeout=15)
            except subprocess.TimeoutExpired:
                proc.kill(); proc.wait()
                raise RuntimeError("shutdown timeout")
            proc.stdout.close()
            if code:
                raise RuntimeError(f"server exited with {code}")
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    if output.suffix == ".gz":
        with gzip.open(output, "wt") as stream:
            json.dump(metadata, stream)
            stream.write("\n")
    else:
        output.write_text(json.dumps(metadata, indent=2) + "\n")


def source_hash():
    digest = hashlib.sha256()
    for path in sorted([Path("CMakeLists.txt"), *Path("include").rglob("*.h"), *Path("src").rglob("*.cpp"),
                        *Path("benchmarks/baselines").rglob("*.cpp")]):
        digest.update(str(path).encode() + b"\0" + path.read_bytes())
    return digest.hexdigest()


if __name__ == "__main__":
    main()
