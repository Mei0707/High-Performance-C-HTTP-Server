#!/usr/bin/env python3
"""Observe TCP namespace counters for an existing experiment without changing sysctls."""
import argparse
import hashlib
import json
from pathlib import Path
import subprocess
import time


def parse_counters(text):
    lines = text.splitlines()
    counters = {}
    for header, data in zip(lines[::2], lines[1::2]):
        names, values = header.split(), data.split()
        if not names or names[0] != values[0] or len(names) != len(values):
            raise ValueError("mismatched network counter header/data")
        if names[0] in ("Tcp:", "TcpExt:"):
            counters.update({names[0] + key: int(value) for key,value in zip(names[1:],values[1:])})
    return counters


def snapshot():
    counters = {}
    for name in ("snmp", "netstat"):
        counters.update(parse_counters(Path(f"/proc/net/{name}").read_text()))
    return {"unix_ns": time.time_ns(), "counters": counters}


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("case", type=Path)
    p.add_argument("--interval", type=float, default=10)
    args = p.parse_args()
    if args.interval <= 0:
        p.error("interval must be positive")
    plan = json.loads((args.case / "plan.json").read_text())
    targets = {}
    for role in ("server", "client"):
        command = plan[role + "_command"]
        name = command[command.index("--name") + 1]
        if not name.startswith("pulse-experiment-"):
            raise ValueError("only this harness's named containers may be observed")
        targets[role] = name
    metadata = {"started_unix_ns": time.time_ns(), "interval_seconds": args.interval,
                "collector_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
                "targets": targets, "note": "Counters are cumulative within each container network namespace; observation may start after the workload."}
    (args.case / "network-observer.json").write_text(json.dumps(metadata,indent=2) + "\n")
    code = "import sys,json;sys.path.insert(0,'benchmarks/experiments');from network_watch import snapshot;print(json.dumps(snapshot()))"
    with (args.case / "network-samples.jsonl").open("w",buffering=1) as output:
        while True:
            alive = False
            for role,name in targets.items():
                result = subprocess.run(["docker","exec",name,"python3","-c",code], capture_output=True,text=True)
                if result.returncode == 0:
                    point = json.loads(result.stdout)
                    output.write(json.dumps({"role":role,**point}) + "\n")
                    alive = True
            if not alive:
                break
            time.sleep(args.interval)


if __name__ == "__main__":
    main()
