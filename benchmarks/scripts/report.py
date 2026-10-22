#!/usr/bin/env python3
"""Summarize raw runs into Markdown and a standalone SVG chart (no dependencies)."""
import argparse
import html
import gzip
import json
from pathlib import Path
from statistics import median


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("files", nargs="+")
    p.add_argument("--output", default="benchmarks/reports/summary.md")
    args = p.parse_args()
    rows = []
    for name in args.files:
        if name.endswith(".gz"):
            with gzip.open(name, "rt") as stream:
                data = json.load(stream)
        else:
            data = json.loads(Path(name).read_text())
        runs = data["runs"]
        percentiles = [r["p99_ms"] for r in runs if r["p99_ms"] is not None]
        label = Path(name).name.removesuffix(".gz").removesuffix(".json")
        rows.append((label, median(r["successful_rps"] for r in runs),
                     min(r["successful_rps"] for r in runs), max(r["successful_rps"] for r in runs),
                     median(percentiles) if percentiles else None,
                     sum(r["errors"] for r in runs), median(r.get("peak_rss_kib", 0) for r in runs)))
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    lines = ["# Measured loopback results", "", "Each row summarizes repeated runs. Rates count HTTP 200 responses; errors are reported separately.",
             "The Python client shares CPU with the server; these are development measurements, not capacity claims.", "",
             "| Configuration | Median successful req/s | Min–max req/s | Median p99 ms | Errors (all runs) | Median peak RSS KiB |",
             "|---|---:|---:|---:|---:|---:|"]
    for label, rate, low, high, p99, errors, rss in rows:
        tail = f"{p99:.3f}" if p99 is not None else "n/a"
        lines.append(f"| {label} | {rate:.1f} | {low:.1f}–{high:.1f} | {tail} | {errors} | {rss:.0f} |")
    lines += ["", "![Measured successful throughput](throughput.svg)", "", "Raw inputs:", ""]
    lines += [f"- [{Path(f).name}]({Path(f).relative_to(output.parent)})" for f in args.files]
    output.write_text("\n".join(lines) + "\n")
    width, step = 1000, 42
    parts = [f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{len(rows)*step+80}" role="img" aria-label="Measured successful throughput in requests per second">',
             '<rect width="100%" height="100%" fill="white"/>',
             '<text x="20" y="28" font-family="sans-serif" font-size="18">Successful requests/sec — development loopback measurements</text>']
    scale = max(row[1] for row in rows) or 1
    for i, (label, rate, *_) in enumerate(rows):
        y = 50 + i * step
        parts += [f'<text x="15" y="{y+19}" font-family="sans-serif" font-size="13">{html.escape(label)}</text>',
                  f'<rect x="310" y="{y}" width="{rate/scale*560:.1f}" height="26" fill="#2463ad"/>',
                  f'<text x="{320+rate/scale*560:.1f}" y="{y+19}" font-family="sans-serif" font-size="13">{rate:.0f}</text>']
    parts.append("</svg>")
    output.with_name("throughput.svg").write_text("\n".join(parts) + "\n")


if __name__ == "__main__":
    main()
