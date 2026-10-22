"""Small Linux telemetry helpers shared by experiment processes."""
import hashlib
import json
import os
from pathlib import Path
import platform
import resource
import signal
import subprocess
import time


def sample(pid):
    try:
        values = dict(line.split(":", 1) for line in Path(f"/proc/{pid}/status").read_text().splitlines())
        stat = Path(f"/proc/{pid}/stat").read_text().rsplit(")", 1)[1].split()
        switches = 0
        for task in Path(f"/proc/{pid}/task").iterdir():
            try:
                for line in (task / "status").read_text().splitlines():
                    if line.startswith(("voluntary_ctxt_switches:", "nonvoluntary_ctxt_switches:")):
                        switches += int(line.split()[1])
            except FileNotFoundError:
                pass
        result = {"unix_ns": time.time_ns(), "monotonic_ns": time.monotonic_ns(),
                "rss_kib": int(values["VmRSS"].split()[0]), "vms_kib": int(values["VmSize"].split()[0]),
                "threads": int(values["Threads"]), "fds": len(list(Path(f"/proc/{pid}/fd").iterdir())),
                "cpu_seconds": (int(stat[11]) + int(stat[12])) / os.sysconf("SC_CLK_TCK"),
                "live_task_context_switches": switches}
        memory = Path("/sys/fs/cgroup/memory.current")
        if memory.exists():
            result["cgroup_memory_bytes"] = int(memory.read_text())
        return result
    except (FileNotFoundError, ProcessLookupError, KeyError):
        return None


def environment(binary=None):
    def read(path):
        try:
            return Path(path).read_text().strip()
        except OSError:
            return None
    repo = Path(__file__).resolve().parents[2]
    git = ["git", "-c", f"safe.directory={repo}"]
    commit = subprocess.run([*git, "rev-parse", "HEAD"], text=True, capture_output=True)
    result = {"unix_ns": time.time_ns(), "platform": platform.platform(), "python": platform.python_version(),
              "affinity": sorted(os.sched_getaffinity(0)), "logical_cpus": os.cpu_count(),
              "cpuinfo": read("/proc/cpuinfo"), "meminfo": read("/proc/meminfo"),
              "fd_limit": resource.getrlimit(resource.RLIMIT_NOFILE),
              "pids_max": read("/sys/fs/cgroup/pids.max"), "memory_max": read("/sys/fs/cgroup/memory.max"),
              "cpu_max": read("/sys/fs/cgroup/cpu.max"), "commit": commit.stdout.strip() or None}
    digest = hashlib.sha256()
    paths = [repo / "CMakeLists.txt", *repo.glob("include/**/*.h"), *repo.glob("src/*.cpp"),
             *repo.glob("benchmarks/baselines/*.cpp"), *repo.glob("benchmarks/experiments/*.py"),
             *repo.glob("benchmarks/wrk/*.lua")]
    for path in sorted(paths):
        digest.update(str(path.relative_to(repo)).encode() + b"\0" + path.read_bytes())
    result["experiment_source_sha256"] = digest.hexdigest()
    if binary:
        result["binary_sha256"] = hashlib.sha256(Path(binary).read_bytes()).hexdigest()
        result["cmake_cache"] = read(Path(binary).parent / "CMakeCache.txt")
    return result


def write_json(path, data):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2) + "\n")


def profile_valid(result, stat_text):
    codes = result.get("perf_exit_codes", [])
    stopped = result.get("perf_stop_requested", [])
    return (len(codes) == len(stopped) == 2 and
            all(code == 0 or (requested and code in (-signal.SIGINT, 128 + signal.SIGINT))
                for code, requested in zip(codes, stopped)) and
            result.get("perf_report_exit_code") == 0 and "task-clock" in stat_text and
            "<not supported>" not in stat_text and "<not counted>" not in stat_text)
