#!/usr/bin/env python3
"""Run controlled comparisons sequentially, then generate a report and SVG chart."""
import argparse
from pathlib import Path
import subprocess
import sys
import tempfile

p = argparse.ArgumentParser(description=__doc__)
p.add_argument("--duration", default="3")
p.add_argument("--repetitions", default="3")
p.add_argument("--output", default="benchmarks/reports")
args = p.parse_args()
out = Path(args.output)
files = []
samples = Path(tempfile.mkdtemp(prefix="pulse-benchmark-", dir="www"))
for size in (4096, 65536, 1048576):
    (samples / f"{size}.bin").write_bytes(bytes(range(256)) * (size // 256))

cases = [
    ("epoll-lt-c32", [], [], "/hello"),
    ("epoll-et-c32", ["--edge-triggered"], [], "/hello"),
    ("threads-c32", [], ["--server", "build/thread_per_connection"], "/hello"),
    ("new-connection-c32", [], ["--close"], "/hello"),
    ("compute-workers-1", ["--workers", "1"], [], "/api/compute?iterations=100000"),
    ("compute-workers-2", ["--workers", "2"], [], "/api/compute?iterations=100000"),
    ("compute-workers-4", ["--workers", "4"], [], "/api/compute?iterations=100000"),
    ("compute-workers-8", ["--workers", "8"], [], "/api/compute?iterations=100000"),
    ("compute-workers-16", ["--workers", "16"], [], "/api/compute?iterations=100000"),
    ("overload-q1", ["--workers", "1", "--queue-capacity", "1"], [], "/api/compute?iterations=20000000"),
]
for size in (4096, 65536, 1048576):
    for mode in ("buffered", "sendfile"):
        cases.append((f"{mode}-{size}", ["--sendfile"] if mode == "sendfile" else [], [], f"/static/{samples.name}/{size}.bin"))
try:
    for label, server_args, client_args, path in cases:
        filename = out / "raw" / f"{label}.json.gz"
        command = [sys.executable, "benchmarks/scripts/load.py", "--output", str(filename),
                   "--duration", args.duration, "--repetitions", args.repetitions,
                   "--warmup", "1", "--concurrency", "32", "--path", path,
                   *client_args, "--", *server_args]
        print(label, flush=True)
        subprocess.run(command, check=True)
        files.append(str(filename))
    subprocess.run([sys.executable, "benchmarks/scripts/report.py", "--output", str(out / "summary.md"), *files], check=True)
finally:
    for sample in samples.glob("*.bin"):
        sample.unlink()
    samples.rmdir()
