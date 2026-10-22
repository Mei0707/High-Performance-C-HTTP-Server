# Hosted CI and controlled experiments

The main CI workflow runs Release, ASan/UBSan, TSan, parser fuzzing and a clean Docker
build. It accepts push, pull_request and workflow_dispatch events and retains JUnit,
CTest logs, build configuration and fuzz artifacts. Use the repository-wide
`/repos/Mei0707/High-Performance-C-HTTP-Server/actions/runs` endpoint when checking hosted
history; commit-specific helpers can filter out push/manual events. Read the run's actual
conclusion, not just successful individual steps.

The manual experiments workflow is opt-in. It runs an idle comparison, a soak of up to
three hours, or a perf collection on a hosted Linux runner and uploads artifacts even
after a failure. It consumes runner minutes only when dispatched. Its optimized build
uses O2 with symbols and frame pointers; the local Release comparisons below use O3.

## Reproduce locally

These experiments require Linux containers and at least two Docker CPUs. On the ten-CPU
development VM, CPUs 0–3 belong to the server and 4–7 to the client. Choose valid disjoint
sets on another host. The harness rejects overlaps and records each process's actual
affinity. All container/network names are unique and cleaned up after the case.

```sh
docker build --target dev -t pulsehttp-dev .
docker build -f benchmarks/experiments/Dockerfile -t pulsehttp-experiments .
docker run --rm -v "$PWD:/src" pulsehttp-experiments sh -c \
  'cmake -S . -B build -G Ninja -DCMAKE_BUILD_TYPE=Release && cmake --build build -j4 && ctest --test-dir build --output-on-failure'

python3 benchmarks/experiments/run.py benchmark --duration 10 --repetitions 3 \
  --output benchmarks/reports/follow-up/dedicated

python3 benchmarks/experiments/run.py idle --connections 2000 --duration 30 \
  --output benchmarks/reports/follow-up/idle-2000

python3 benchmarks/experiments/run.py soak --duration 1800 --connections 8 \
  --workers 1 --queue-capacity 16 --overload-connections 128 \
  --output benchmarks/reports/follow-up/soak
```

Benchmark and idle modes compare the reactor and thread baseline sequentially. The client
is a separate container on a Docker bridge, with a read-only source mount and a writable
artifact directory. Both server architectures receive the same CPU, 512 MiB memory,
4096-task and 65536-descriptor limits. A pids limit counts threads too. The idle workload
requires a valid response on every admitted socket, pulses 1% every two seconds, holds the
rest idle, and probes every original connection before closing. Admission failures and
lost connections are retained; a capacity limit is an experimental outcome, not hidden
by reconnecting. The server idle timeout is five minutes; keep the hold plus setup below it.

The wrk client uses four load-generator threads, validates every /hello body through a Lua
callback, and records HTTP outcomes and socket-error counters separately. Raw tool output
and p50/p95/p99 are retained. Server CPU comes from independent /proc process samples;
client CPU comes from waited child resource usage. These are separate containers and CPU
sets on **one VM**, not independent physical machines: memory, cache, kernel, network and
the Docker hypervisor remain shared. The result can still be limited by the client.

Soak phases rotate steady keep-alive traffic, connection churn, compute overload and
recovery. Only HTTP 503 during the overload phase is expected; other errors make the run
fail after preserving data. Fixed-size histograms separate successful and failed latency.
The target is resolved to IPv4 once before measurement; repeated connections use that
address. This avoids turning connection churn into an embedded-DNS stress test. Failures
include their I/O stage (connect, write, headers, body, close). TCP namespace counters are
sampled with server telemetry and client windows; they can reveal retransmission or
listen-queue pressure without changing network sysctls.
Their percentile fields are bucket upper bounds, not exact latency quantiles. Every window
is streamed to JSONL; no list of per-request timings grows with duration. Window data,
phase totals and per-second telemetry permit memory/FD/recovery analysis. A descriptive
RSS slope after the first recovery cycle is not a proof that a process can never leak.

## Linux profiling

```sh
docker run --rm -v "$PWD:/src" pulsehttp-experiments sh -c \
  'cmake -S . -B build-profile -G Ninja -DCMAKE_BUILD_TYPE=RelWithDebInfo -DPULSE_BUILD_TESTS=OFF -DCMAKE_CXX_FLAGS_RELWITHDEBINFO="-O2 -g -fno-omit-frame-pointer" && cmake --build build-profile -j4'
python3 benchmarks/experiments/run.py perf --build-dir build-profile \
  --duration 15 --repetitions 1 --output benchmarks/reports/follow-up/perf
```

Only profiling containers receive CAP_PERFMON, CAP_SYSLOG and an unconfined seccomp filter,
allowing perf_event_open and kernel symbol resolution without privileged mode or changing
host sysctls. The harness records
software task-clock/context-switch/migration/fault counters and cpu-clock call stacks
with DWARF unwinding at 99 Hz. It stops collectors before the target exits. Raw traces and
the exact profiled executable are gzip compressed; reports, collector diagnostics, exit
codes, tool versions and binary hashes are preserved. Missing counters or permission
failures produce a failed experiment. Kernel symbol access can still be restricted.

To inspect a trace later, decompress perf.data.gz and the matching profiled-server.gz.
Put the executable at its recorded path below a temporary symbol root and pass
`perf report --symfs SYMBOL_ROOT -i perf.data`. Shared-library symbols also require the
recorded container libraries. The saved text report resolves symbols from the original run.
Profiling has overhead; do not compare its throughput to an unprofiled capacity run.

Summarize any completed cases:

```sh
python3 benchmarks/experiments/report.py --output /tmp/comparison.json \
  benchmarks/reports/follow-up/idle-2000/reactor \
  benchmarks/reports/follow-up/idle-2000/baseline
```

Telemetry includes RSS, reserved virtual memory, threads, FDs, process CPU and cgroup
memory. RSS excludes kernel stacks; cgroup memory also includes the Python monitor,
kernel allocations and charged file cache. CPU percentages use actual monotonic sample
intervals and can exceed 100 for multiple cores. At least two samples must fall inside
the workload window. For measurements across physical hosts, synchronize clocks or align
telemetry through an explicit remote orchestration protocol.

Tool references: [wrk scripting API](https://github.com/wg/wrk/blob/master/SCRIPTING)
and [Linux perf tutorial](https://perfwiki.github.io/main/tutorial/).
