#!/usr/bin/env python3
"""Drive an already-running server; preserve wrk output and client CPU consumption."""
import argparse
import json
from pathlib import Path
import resource
import subprocess
import time
from common import environment, write_json


def main(args):
    output = Path(args.output)
    output.mkdir(parents=True, exist_ok=True)
    command = ["wrk", f"-t{args.threads}", f"-c{args.connections}", "--latency", "-s", "benchmarks/wrk/validated.lua"]
    url = f"http://{args.host}:{args.port}{args.path}"
    validation = "hello" if args.path == "/hello" else "none"
    report = {"environment": environment(), "tool_version": subprocess.run(["wrk", "--version"], capture_output=True, text=True).stdout,
              "target": url, "runs": []}
    warm = subprocess.run([*command, f"-d{args.warmup}s", url, "--", validation], capture_output=True, text=True, check=True)
    (output / "wrk-warmup.txt").write_text(warm.stdout + warm.stderr)
    failed = False
    for repetition in range(args.repetitions):
        usage_before = resource.getrusage(resource.RUSAGE_CHILDREN)
        start = time.time_ns()
        start_mono = time.monotonic_ns()
        cmd = [*command, f"-d{args.duration}s", url, "--", validation]
        completed = subprocess.run(cmd, capture_output=True, text=True, check=True)
        end = time.time_ns()
        end_mono = time.monotonic_ns()
        usage_after = resource.getrusage(resource.RUSAGE_CHILDREN)
        (output / f"wrk-{repetition+1}.txt").write_text(completed.stdout + completed.stderr)
        lines = [line.removeprefix("PULSE_RESULT ") for line in completed.stdout.splitlines() if line.startswith("PULSE_RESULT ")]
        if len(lines) != 1:
            raise RuntimeError("missing wrk validation output")
        result = json.loads(lines[0])
        result.update({"command": cmd, "start_unix_ns": start, "end_unix_ns": end,
                       "start_monotonic_ns": start_mono, "end_monotonic_ns": end_mono,
                       "client_cpu_seconds": usage_after.ru_utime + usage_after.ru_stime - usage_before.ru_utime - usage_before.ru_stime})
        result["client_cpu_percent"] = result["client_cpu_seconds"] / ((end_mono-start_mono)/1e9) * 100
        result["clock_discontinuity_seconds"] = ((end-start) - (end_mono-start_mono)) / 1e9
        result["valid"] = abs(result["clock_discontinuity_seconds"]) < .1 and result["http_200"] > 0 and all(result[key] == 0 for key in ("http_other", "invalid_bodies", "connect_errors", "read_errors", "write_errors", "timeouts"))
        failed |= not result["valid"]
        report["runs"].append(result)
        write_json(output / "client.json", report)
        print(json.dumps(result), flush=True)
    if failed:
        raise SystemExit("wrk recorded unsuccessful/corrupted responses or transport errors; inspect the retained run")


if __name__ == "__main__":
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--host", required=True)
    p.add_argument("--port", type=int, default=8080)
    p.add_argument("--path", default="/hello")
    p.add_argument("--output", required=True)
    p.add_argument("--connections", type=int, default=128)
    p.add_argument("--threads", type=int, default=4)
    p.add_argument("--duration", type=int, default=10)
    p.add_argument("--warmup", type=int, default=2)
    p.add_argument("--repetitions", type=int, default=3)
    args = p.parse_args()
    if min(args.connections, args.threads, args.duration, args.warmup, args.repetitions) <= 0 or args.threads > args.connections:
        p.error("positive limits required, with connections >= threads")
    main(args)
