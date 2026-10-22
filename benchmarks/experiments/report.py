#!/usr/bin/env python3
"""Summarize saved client/server artifacts without treating failed admission as success."""
import argparse
import json
from pathlib import Path
from statistics import median


def resources(samples, start, end, time_key="unix_ns"):
    points = [s for s in samples if start <= s[time_key] <= end]
    if len(points) < 2:
        return None
    first, last = points[0], points[-1]
    seconds = (last["monotonic_ns"] - first["monotonic_ns"]) / 1e9
    return {"sampled_seconds": seconds, "cpu_percent": (last["cpu_seconds"]-first["cpu_seconds"]) / seconds * 100,
            "peak_rss_kib": max(s["rss_kib"] for s in points), "peak_vms_kib": max(s["vms_kib"] for s in points),
            "peak_threads": max(s["threads"] for s in points), "peak_fds": max(s["fds"] for s in points),
            "peak_cgroup_mib": max(s.get("cgroup_memory_bytes", 0) for s in points) / 1048576}


def summarize(case):
    plan = json.loads((case / "plan.json").read_text())
    client = json.loads((case / "client/client.json").read_text())
    server = json.loads((case / "server/server-metadata.json").read_text())
    samples = [json.loads(line) for line in (case / "server/server-samples.jsonl").read_text().splitlines()]
    result = {"case": str(case), "mode": plan["mode"], "architecture": plan["architecture"],
              "initial_fds": server["initial_sample"]["fds"], "final_fds": samples[-1]["fds"] if samples else None,
              "server_affinity": server["environment"]["affinity"], "client_affinity": client["environment"]["affinity"]}
    result["clock_discontinuities"] = []
    for a,b in zip(samples,samples[1:]):
        difference = ((b["unix_ns"]-a["unix_ns"]) - (b["monotonic_ns"]-a["monotonic_ns"])) / 1e9
        if abs(difference) > 1:
            result["clock_discontinuities"].append({"at_monotonic_ns":b["monotonic_ns"],"wall_minus_monotonic_seconds":difference})
    if set(result["server_affinity"]) & set(result["client_affinity"]):
        raise ValueError(f"CPU overlap in measured data: {case}")
    result["descriptor_recovery"] = result["final_fds"] is not None and result["final_fds"] <= result["initial_fds"] + 2
    if plan["mode"] in ("benchmark", "perf"):
        runs = client["runs"]
        result["runs"] = [{**r, "server_resources": resources(samples,
                           r.get("start_monotonic_ns",r["start_unix_ns"]),r.get("end_monotonic_ns",r["end_unix_ns"]),
                           "monotonic_ns" if "start_monotonic_ns" in r else "unix_ns")} for r in runs]
        result["median_rps"] = median(r["successful_rps"] for r in runs)
        result["min_rps"] = min(r["successful_rps"] for r in runs)
        result["max_rps"] = max(r["successful_rps"] for r in runs)
        result["median_p99_ms"] = median(r["p99_ms"] for r in runs)
        result["median_client_cpu_percent"] = median(r["client_cpu_percent"] for r in runs)
        measured = [r["server_resources"] for r in result["runs"] if r["server_resources"]]
        result["median_server_cpu_percent"] = median(r["cpu_percent"] for r in measured) if measured else None
        result["all_runs_valid"] = all(r["valid"] for r in runs)
    elif plan["mode"] == "idle":
        result.update({k:client[k] for k in ("requested", "established", "survived", "failures", "target_reached")})
        result["hold_resources"] = resources(samples, client.get("hold_start_monotonic_ns",client["hold_start_unix_ns"]),
                                               client.get("hold_end_monotonic_ns",client["hold_end_unix_ns"]),
                                               "monotonic_ns" if "hold_start_monotonic_ns" in client else "unix_ns")
    else:
        result.update({k:client.get(k) for k in ("elapsed_seconds", "completed", "phase_totals", "unexpected_errors", "client_peak_rss_kib")})
        windows = [json.loads(line) for line in (case / "client/client-windows.jsonl").read_text().splitlines()]
        recovery = []
        for w in windows:
            if w["phase"] == "recovery":
                key = "monotonic_ns" if "start_monotonic_ns" in w else "unix_ns"
                points = [s for s in samples if w["start_"+key] <= s[key] <= w["end_"+key]]
                if points:
                    recovery.append({"unix_ns": points[-1]["unix_ns"],"monotonic_ns":points[-1]["monotonic_ns"],
                                     "rss_kib": median(s["rss_kib"] for s in points), "cycle": w["cycle"]})
        result["recovery_rss"] = recovery
        # Report a descriptive slope after the first recovery cycle, not a leak verdict.
        points = [r for r in recovery if r["cycle"] > 0]
        if len(points) >= 3:
            origin = points[0]["monotonic_ns"]
            x = [(r["monotonic_ns"] - origin) / 60e9 for r in points]
            y = [r["rss_kib"] for r in points]
            mean_x, mean_y = sum(x)/len(x), sum(y)/len(y)
            denominator = sum((n-mean_x)**2 for n in x)
            result["recovery_rss_slope_kib_per_minute"] = sum((a-mean_x)*(b-mean_y) for a,b in zip(x,y)) / denominator if denominator else None
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("cases", nargs="+", type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    rows = [summarize(case) for case in args.cases]
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(rows, indent=2) + "\n")
    for row in rows:
        print(json.dumps({k:v for k,v in row.items() if k not in ("runs", "recovery_rss")}))


if __name__ == "__main__":
    main()
