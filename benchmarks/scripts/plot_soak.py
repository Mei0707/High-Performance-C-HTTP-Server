#!/usr/bin/env python3
"""Plot saved soak telemetry with matplotlib; no server or Docker access required."""
import argparse
import json
from pathlib import Path


def read_jsonl(path):
    # A live preview may end at an unfinished final write. Never ignore bad complete lines.
    return [json.loads(line) for line in path.read_text().splitlines(keepends=True) if line.endswith("\n")]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("case", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from matplotlib.patches import Patch

    client = json.loads((args.case / "client/client.json").read_text())
    samples = read_jsonl(args.case / "server/server-samples.jsonl")
    windows = read_jsonl(args.case / "client/client-windows.jsonl")
    start = client["start_unix_ns"]
    end = client.get("end_unix_ns", samples[-1]["unix_ns"])
    samples = [s for s in samples if start <= s["unix_ns"] <= end]
    if len(samples) < 2:
        parser.error("at least two server samples are required")
    x = [(s["unix_ns"] - start) / 60e9 for s in samples]
    rss = [s["rss_kib"] / 1024 for s in samples]
    fds = [s["fds"] for s in samples]
    cpu = [(b["cpu_seconds"] - a["cpu_seconds"]) / ((b["monotonic_ns"]-a["monotonic_ns"])/1e9)
           for a,b in zip(samples,samples[1:])]
    colors = {"steady":"#2f8f4e", "churn":"#386cb0", "overload":"#d54b3e", "recovery":"#e8b330"}
    with plt.rc_context({"font.size":10, "axes.spines.top":False, "axes.spines.right":False}):
        fig, axes = plt.subplots(3,1,sharex=True,figsize=(11,7),layout="constrained")
        for axis in axes:
            for w in windows:
                axis.axvspan((w["start_unix_ns"]-start)/60e9,(w["end_unix_ns"]-start)/60e9,
                             color=colors[w["phase"]],alpha=.10,linewidth=0)
            axis.grid(axis="y",alpha=.22)
        axes[0].plot(x,rss,color="#233b65",linewidth=1.3)
        axes[0].set_ylabel("Server RSS (MiB)")
        axes[0].set_ylim(0,max(rss)*1.3)
        axes[1].plot(x,fds,color="#233b65",linewidth=1.1)
        axes[1].set_ylabel("Open descriptors")
        axes[1].set_ylim(bottom=0)
        axes[2].plot(x[1:],cpu,color="#233b65",linewidth=1.1)
        axes[2].set_ylabel("Server CPU cores")
        axes[2].set_ylim(0,max(1.2,max(cpu)*1.1))
        axes[2].set_xlabel("Elapsed workload time (minutes)")
        axes[2].set_xlim(0,(end-start)/60e9)
        title = f"{(end-start)/60e9:.1f}-minute soak: server resource samples"
        if not client.get("completed"):
            title += " (partial observation)"
        axes[0].set_title(title,loc="left",pad=28)
        axes[0].legend(handles=[Patch(facecolor=c,alpha=.35,label=name.capitalize()) for name,c in colors.items()],
                       loc="lower left",bbox_to_anchor=(0,1.005),ncol=4,frameon=False)
        args.output.parent.mkdir(parents=True,exist_ok=True)
        fig.savefig(args.output.with_suffix(".svg"))
        fig.savefig(args.output.with_suffix(".png"),dpi=150)
        plt.close(fig)


if __name__ == "__main__":
    main()
