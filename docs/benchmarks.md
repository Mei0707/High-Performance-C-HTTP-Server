# Benchmark methodology

Build Release with sanitizers disabled. Install `libbenchmark-dev` and configure
`-DPULSE_BUILD_BENCHMARKS=ON` for `build/parser_benchmark`. The runtime has no Google
Benchmark dependency.

`benchmarks/scripts/suite.py` runs sequential configurations on the same Linux environment:
epoll LT/ET, bounded thread-per-connection, keep-alive/new connections, 1/2/4/8/16 workers,
overload with a one-slot queue, and buffered/sendfile transfers at 4 KiB, 64 KiB and 1 MiB.
Each starts a fresh server, warms up, then records three repetitions by default. Temporary
file fixtures are generated identically in a temporary directory under www and removed
afterward. Do not run multiple suites concurrently.

Raw gzip-compressed JSON contains all successful and failed request latencies, response/error counts,
elapsed time, CPU time, RSS, thread/FD samples, live-task context-switch counters, kernel,
logical CPU count, memory information, compiler output, full CMake cache, socket backlog
limit, FD limit, arguments, binary hash and source hash. A commit SHA is included when a
Git checkout exists. This initial workspace was not a Git repository, so the source digest
is the reproducible identity. The measurement clock's UTC timestamp is retained verbatim.

Successful req/s is HTTP 200 count divided by actual elapsed time, including the final
in-flight responses. p50/p95/p99 describe successful client round trips, including connection
setup when reconnecting. Rejections have separate latency arrays. The summary reports median
and min/max throughput, median p99, total errors and peak sampled RSS. 100 ms sampling can
miss brief resource peaks. Live-thread context switches can decrease when threads exit and
are diagnostic only, especially for the thread baseline. These are closed-loop measurements;
they do not correct for coordinated omission.

The standard-library asyncio client and server share CPUs and container loopback. It validates
`/hello` response bytes and consumes full bodies. Use a dedicated client host with wrk/hey for
capacity work; recipes are in their benchmark directories. Record topology, CPU model,
physical/logical core counts, RAM, tool version, compiler flags, commit/digests, TCP and file
descriptor limits. Pin CPU affinity and alternate experiment order for stronger comparisons.

The Prometheus `http_requests_total` counts responses prepared, including HTTP errors, not
successfully transmitted requests. Its duration histogram spans the request's active parsing
phase through response preparation (including worker queue time); it excludes response
transmission. Buffered pipelined requests start their phase when promoted to active. End-to-end
latency comes from the client. Gauge snapshots and counters are approximate across threads;
queue depth is sampled on reactor ticks. No raw path or connection-ID metric labels are used.

For profiling on a compatible Linux host with perf permissions:

```sh
perf stat -p PID -e task-clock,context-switches,cycles,instructions,cache-misses -- sleep 30
perf record -F 99 -g -p PID -- sleep 30
perf report
```

Preserve perf output, workload, compiler and environment alongside results. Do not call a
tradeoff an improvement unless the magnitude exceeds run variation under matched conditions.
