#!/usr/bin/env python3
"""Run experiments in two containers with disjoint CPU affinity and saved artifacts."""
import argparse
import gzip
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time
import uuid
from common import profile_valid

ROOT = Path(__file__).resolve().parents[2]


def cpu_set(value):
    cpus = set()
    for item in value.split(","):
        parts = item.split("-")
        if len(parts) not in (1, 2) or any(not p.isdecimal() for p in parts):
            raise ValueError("CPUs must be comma-separated integers or ascending ranges")
        low, high = int(parts[0]), int(parts[-1])
        if high < low or high > 65535:
            raise ValueError("invalid CPU range")
        cpus.update(range(low, high + 1))
    if not cpus:
        raise ValueError("CPU set is empty")
    return cpus


def docker(*args, **kwargs):
    return subprocess.run(["docker", *map(str, args)], check=True, text=True, **kwargs)


def container_logs(name):
    logs = docker("logs", name, capture_output=True)
    return logs.stdout + logs.stderr


def compress(path):
    if path.exists():
        with path.open("rb") as source, gzip.open(str(path) + ".gz", "wb") as target:
            shutil.copyfileobj(source, target)
        path.unlink()


def run_case(args, architecture):
    output = args.output / architecture
    if output.exists():
        raise ValueError(f"output already exists; choose a fresh output directory: {output}")
    server_output, client_output = output / "server", output / "client"
    server_output.mkdir(parents=True)
    client_output.mkdir()
    name = "pulse-experiment-" + uuid.uuid4().hex[:10]
    network, server = name + "-net", name + "-server"
    binary = args.build_dir / ("pulsehttp_server" if architecture == "reactor" else "thread_per_connection")
    if not (ROOT / binary).is_file():
        raise ValueError(f"build the Linux binary first: {binary}")
    command = [str(binary), "--address", "0.0.0.0", "--document-root", "www", "--workers", str(args.workers),
               "--queue-capacity", str(args.queue_capacity), "--max-connections", str(max(args.connections * 2, 4096)),
               "--max-requests", "1000000", "--idle-timeout-ms", "300000"]
    start = ["run", "-d", "--name", server, "--network", network, "--network-alias", "server",
             "--cpuset-cpus", args.server_cpus, "--pids-limit", str(args.pids_limit), "--memory", args.memory,
             "--ulimit", "nofile=65536:65536", "-v", f"{ROOT}:/src:ro", "-v", f"{server_output}:/results"]
    if args.mode == "perf":
        start += ["--cap-add", "PERFMON", "--cap-add", "SYSLOG", "--security-opt", "seccomp=unconfined"]
    start += [args.image, "python3", "benchmarks/experiments/server_monitor.py", "--output", "/results"]
    if args.mode == "perf":
        start += ["--perf"]
    start += ["--", *command]
    client = ["run", "--rm", "--name", name + "-client", "--network", network, "--cpuset-cpus", args.client_cpus,
              "--pids-limit", "256", "--memory", args.client_memory, "--ulimit", "nofile=65536:65536",
              "-v", f"{ROOT}:/src:ro", "-v", f"{client_output}:/results", args.image, "python3"]
    if args.mode in ("benchmark", "perf"):
        client += ["benchmarks/experiments/wrk_client.py", "--host", "server", "--output", "/results",
                   "--connections", str(args.connections), "--threads", str(args.client_threads),
                   "--duration", str(args.duration), "--warmup", str(args.warmup), "--repetitions", str(args.repetitions)]
    else:
        client += ["benchmarks/experiments/client.py", args.mode, "--host", "server", "--output", "/results",
                   "--connections", str(args.connections), "--duration", str(args.duration),
                   "--phase-seconds", str(args.phase_seconds), "--overload-connections", str(args.overload_connections)]
    plan = {"mode": args.mode, "architecture": architecture, "topology": "Separate containers and disjoint CPU sets on ONE Linux VM; shared kernel, memory, and network",
            "host_idle_sleep_assertion": args.host_idle_sleep_assertion,
            "server_cpus": sorted(cpu_set(args.server_cpus)), "client_cpus": sorted(cpu_set(args.client_cpus)),
            "image": args.image, "server_command": ["docker", *start], "client_command": ["docker", *client]}
    (output / "plan.json").write_text(json.dumps(plan, indent=2) + "\n")
    created_network = created_server = False
    client_code = None
    result = {}
    try:
        docker("network", "create", network, capture_output=True)
        created_network = True
        docker(*start, capture_output=True)
        created_server = True
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline:
            logs = container_logs(server)
            if '"event": "ready"' in logs:
                break
            running = docker("inspect", "--format", "{{.State.Running}}", server, capture_output=True).stdout.strip()
            if running != "true":
                raise RuntimeError("server monitor failed:\n" + logs)
            time.sleep(.2)
        else:
            raise RuntimeError("server monitor readiness timed out")
        print(f"{architecture}: {args.mode} started; output {output}", flush=True)
        with (output / "client-controller.log").open("w") as log:
            client_code = subprocess.run(["docker", *client], stdout=log, stderr=subprocess.STDOUT).returncode
        # Capture descriptor recovery after the client has closed every connection.
        time.sleep(3)
    finally:
        if created_server:
            docker("stop", "--timeout", "40", server, capture_output=True)
            state = docker("inspect", "--format", "{{json .State}}", server, capture_output=True).stdout
            result = {"client_exit_code": client_code, "monitor_state": json.loads(state)}
            (output / "monitor.log").write_text(container_logs(server))
            docker("rm", "-f", server, capture_output=True)
        # Remove only this invocation's uniquely named client if interrupted mid-run.
        subprocess.run(["docker", "rm", "-f", name + "-client"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        if created_network:
            docker("network", "rm", network, capture_output=True)
        (output / "result.json").write_text(json.dumps(result, indent=2) + "\n")
    if args.mode == "perf":
        # Retain both the raw trace and the exact executable for later symbol lookup.
        with (ROOT / binary).open("rb") as source, gzip.open(server_output / "profiled-server.gz", "wb") as target:
            shutil.copyfileobj(source, target)
        compress(server_output / "perf.data")
    good = client_code == 0 and result.get("monitor_state", {}).get("ExitCode") == 0
    if args.mode == "perf":
        profile = json.loads((server_output / "server-result.json").read_text())
        good = good and profile_valid(profile, (server_output / "perf-stat.csv").read_text())
    print(f"{architecture}: {'completed' if good else 'failed; inspect artifacts'}", flush=True)
    return good


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("mode", choices=["benchmark", "idle", "soak", "perf"])
    p.add_argument("--output", type=Path, required=True)
    p.add_argument("--server-cpus", default="0-3")
    p.add_argument("--client-cpus", default="4-7")
    p.add_argument("--image", default="pulsehttp-experiments")
    p.add_argument("--build-dir", type=Path, default=Path("build"))
    p.add_argument("--cases", nargs="+", choices=["reactor", "baseline"])
    p.add_argument("--connections", type=int)
    p.add_argument("--duration", type=int)
    p.add_argument("--warmup", type=int, default=2)
    p.add_argument("--repetitions", type=int, default=3)
    p.add_argument("--client-threads", type=int, default=4)
    p.add_argument("--workers", type=int, default=4)
    p.add_argument("--queue-capacity", type=int, default=256)
    p.add_argument("--pids-limit", type=int, default=4096)
    p.add_argument("--memory", default="512m")
    p.add_argument("--client-memory", default="512m")
    p.add_argument("--phase-seconds", type=int, default=30)
    p.add_argument("--overload-connections", type=int, default=256)
    args = p.parse_args()
    if args.duration is None:
        args.duration = {"benchmark":10, "idle":30, "soak":1800, "perf":15}[args.mode]
    if args.connections is None:
        args.connections = {"benchmark":128, "idle":2000, "soak":64, "perf":128}[args.mode]
    if min(args.duration, args.connections, args.warmup, args.repetitions, args.client_threads,
           args.workers, args.queue_capacity, args.pids_limit, args.phase_seconds, args.overload_connections) <= 0:
        p.error("all limits must be positive")
    try:
        server_set, client_set = cpu_set(args.server_cpus), cpu_set(args.client_cpus)
    except ValueError as e:
        p.error(str(e))
    if server_set & client_set:
        p.error("server/client CPU sets must not overlap")
    cores = int(docker("info", "--format", "{{.NCPU}}", capture_output=True).stdout)
    if max(server_set | client_set) >= cores:
        p.error(f"Docker exposes {cores} CPUs; choose CPU IDs below {cores}")
    args.output = args.output.resolve()
    cases = args.cases or (["reactor"] if args.mode == "soak" else ["reactor", "baseline"])
    failed = False
    awake = None
    if sys.platform == "darwin" and shutil.which("caffeinate"):
        awake = subprocess.Popen(["caffeinate","-i","-w",str(os.getpid())],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
    args.host_idle_sleep_assertion = awake is not None
    try:
        for architecture in cases:
            failed |= not run_case(args, architecture)
    finally:
        if awake:
            awake.terminate()
            awake.wait()
    if failed:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
