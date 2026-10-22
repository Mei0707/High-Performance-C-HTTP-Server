#!/usr/bin/env python3
"""Run the server with bounded, streaming /proc telemetry and optional perf attachment."""
import argparse
import http.client
import json
from pathlib import Path
import signal
import subprocess
import time
from common import environment, sample, write_json


def main(args):
    output = Path(args.output)
    output.mkdir(parents=True, exist_ok=True)
    command = args.command[1:] if args.command[:1] == ["--"] else args.command
    stopping = False
    def stop(*_):
        nonlocal stopping
        stopping = True
    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    profilers = []
    profiler_logs = []
    profiler_stop_requested = []
    process = None
    try:
        with (output / "server.log").open("w") as log, (output / "server-samples.jsonl").open("w", buffering=1) as samples:
            process = subprocess.Popen(command, stdout=log, stderr=subprocess.STDOUT)
            start = time.monotonic()
            while True:
                if process.poll() is not None:
                    raise RuntimeError(f"server exited during startup ({process.returncode})")
                if stopping or time.monotonic() - start > 20:
                    raise RuntimeError("server startup interrupted or timed out")
                try:
                    connection = http.client.HTTPConnection("127.0.0.1", args.port, timeout=.5)
                    connection.request("GET", "/health")
                    response = connection.getresponse()
                    assert response.status == 200
                    response.read()
                    connection.close()
                    break
                except (OSError, http.client.HTTPException):
                    time.sleep(.05)
            # Let the readiness probe close so the baseline FD sample excludes it.
            time.sleep(.1)
            metadata = {"environment": environment(command[0]), "pid": process.pid,
                        "command": command, "initial_sample": sample(process.pid)}
            write_json(output / "server-metadata.json", metadata)
            if args.perf:
                commands = [
                    ["perf", "stat", "-p", str(process.pid), "-e", "task-clock,context-switches,cpu-migrations,page-faults", "-I", "1000", "-x,", "-o", str(output / "perf-stat.csv")],
                    ["perf", "record", "-p", str(process.pid), "-e", "cpu-clock", "-F", "99", "-g", "--call-graph", "dwarf,8192", "-o", str(output / "perf.data")],
                ]
                for index, cmd in enumerate(commands):
                    sink = (output / f"perf-{index}.log").open("w")
                    profiler_logs.append(sink)
                    profilers.append(subprocess.Popen(cmd, stdout=sink, stderr=subprocess.STDOUT))
                    profiler_stop_requested.append(False)
                metadata["perf_commands"] = commands
                metadata["perf_version"] = subprocess.check_output(["perf", "--version"], text=True).strip()
                write_json(output / "server-metadata.json", metadata)
            print(json.dumps({"event": "ready", "pid": process.pid}), flush=True)
            sent_stop = False
            deadline = None
            while process.poll() is None:
                point = sample(process.pid)
                if point:
                    samples.write(json.dumps(point) + "\n")
                if stopping and not sent_stop:
                    # Stop collectors before their target exits and retain a usable perf.data.
                    for index, profiler in enumerate(profilers):
                        if profiler.poll() is None:
                            profiler_stop_requested[index] = True
                            profiler.send_signal(signal.SIGINT)
                    for profiler in profilers:
                        profiler.wait(timeout=15)
                    process.send_signal(signal.SIGTERM)
                    sent_stop = True
                    deadline = time.monotonic() + 15
                if deadline and time.monotonic() > deadline:
                    raise RuntimeError("server did not finish shutdown")
                time.sleep(args.interval)
            result = {"exit_code": process.returncode, "stopped_by_controller": stopping}
            if args.perf:
                for index, p in enumerate(profilers):
                    if p.poll() is None:
                        profiler_stop_requested[index] = True
                        p.send_signal(signal.SIGINT)
                    p.wait(timeout=15)
                result["perf_exit_codes"] = [p.returncode for p in profilers]
                result["perf_stop_requested"] = profiler_stop_requested
                with (output / "perf-report.txt").open("w") as report:
                    r = subprocess.run(["perf", "report", "--stdio", "--no-children", "--call-graph", "none", "--percent-limit", ".5", "-i", str(output / "perf.data")], stdout=report, stderr=subprocess.STDOUT)
                result["perf_report_exit_code"] = r.returncode
            write_json(output / "server-result.json", result)
            if process.returncode != 0 or not stopping:
                raise RuntimeError(f"unexpected server exit: {result}")
    finally:
        for p in profilers:
            if p.poll() is None:
                p.kill()
            p.wait()
        for log in profiler_logs:
            log.close()
        if process and process.poll() is None:
            process.kill()
            process.wait()


if __name__ == "__main__":
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--output", required=True)
    p.add_argument("--port", type=int, default=8080)
    p.add_argument("--interval", type=float, default=1)
    p.add_argument("--perf", action="store_true")
    p.add_argument("command", nargs=argparse.REMAINDER)
    args = p.parse_args()
    if not args.command or args.interval <= 0:
        p.error("a command and a positive sampling interval are required")
    main(args)
