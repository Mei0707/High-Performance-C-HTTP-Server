# Measured tradeoffs

These measurements describe one development environment, rather than a general server
ranking. The Linux 6.10.14 Docker VM exposed 10 logical CPUs and about 7.65 GiB RAM.
The server was built with GCC 13.3 in CMake Release mode. The Python asyncio client ran
in the same container at concurrency 32. Static files lived on the macOS workspace bind
mount. Each retained configuration has three two-second measured runs after a one-second
warm-up. See the [full table and raw data](../benchmarks/reports/summary.md).

The application source digest was
`473e33a23862f032d492641583e81ab841303e0f479485267a3cb04e8a224c2f`.
Every raw record includes its binary digest, compiler, full CMake cache, arguments and
environment metadata. The workspace had no Git history. A two-worker batch overlapped
an auxiliary compilation and was rerun without competing build work; the
[excluded batch](../benchmarks/reports/excluded/compute-workers-2-build-contention.json.gz)
is retained separately. It is absent from the summary.

## Lightweight routing and concurrency architecture

`/hello` sustained median successful throughput of 39,118 req/s with epoll LT, 41,556 with
ET, and 40,123 with the thread-per-connection baseline. Their observed min/max ranges
were 38,902–39,495, 39,676–41,665 and 39,412–41,377 respectively. No HTTP or transport
errors occurred in these batches. The ET result is an observation worth further study;
short adjacent runs and a shared load generator do not establish a general ET advantage.

The thread baseline was competitive at 32 connections and used about 0.89 CPU cores,
versus about 1.29 for LT. A plausible explanation is the reactor's queue and notification
handoff overhead for very small handlers; this is an inference, not a perf-derived finding.
This experiment does not establish which architecture handles thousands of idle connections
more economically. The separate stress test demonstrates correctness at 1,000 sessions,
not a matched resource comparison at that concurrency.

## Worker scaling and keep-alive

For `/api/compute?iterations=100000`, median throughput was 3,691 / 7,468 / 14,829 /
26,171 / 21,379 req/s with 1 / 2 / 4 / 8 / 16 workers. Eight workers performed best among
these tested choices; 16 workers reduced throughput by about 18% and increased median
p99 from 2.762 to 4.168 ms. On a VM with ten CPUs shared by client, reactor and workers,
oversubscription is a plausible explanation. CPU scheduling profiles and a dedicated
client would be needed to establish the cause.

New-connection `/hello` runs reached 9,764 req/s versus 39,118 with persistent connections,
with p99 rising from 1.780 to 4.904 ms. This is the cost of client and server connection
creation together. It cannot isolate server-side TCP overhead by itself.

## Static-file optimization case study

The buffered path performs bounded file reads on workers and serializes the bytes into
connection output. The sendfile path returns an owned file descriptor from the worker;
the reactor sends only the advertised file range using a tracked offset.

For 1 MiB files, median sampled peak RSS fell from **49,244 KiB to 3,384 KiB** with sendfile,
and measured server CPU fell from about **1.75 to 0.70 cores**. Median throughput was
2,360 versus 2,328 successful req/s, with overlapping variation; sendfile did not establish
a throughput improvement. The useful measured tradeoff is much lower userspace memory
and CPU, while delivery rate remained similar in this environment. Median p99 was 23.863
versus 27.620 ms, so these results do not show a latency improvement either.

At 4 KiB, buffered/sendfile medians were 6,179/6,001 req/s. At 64 KiB they were 5,350/5,782.
The workspace bind mount adds file-open and metadata overhead, and the client consumes
every byte on the same host. Neither these rates nor their ordering represent native-disk
or network capacity. Buffered mode remains the default: it keeps disk reads on workers;
sendfile on the reactor can fault on storage pages. Users can select the measured memory
tradeoff explicitly.

## Overload behavior

With one worker, queue capacity one and 20,000,000-iteration requests, all three runs
completed 40 successful responses and returned 19,289 / 20,604 / 20,947 HTTP 503 responses.
There were no transport-error categories. Successful throughput remained about 19.2 req/s;
median p99 success latency was 109.567 ms. Rejection p99 was 4.18–4.84 ms, including
connection setup. Median sampled peak RSS was 3,256 KiB.

This is deliberate load shedding, not a 10,000-req/s successful capacity result. The queue
and outstanding completion budget remain bounded by construction and tests. The
integration overload test also verifies subsequent health recovery and a nonzero rejection
counter. A longer open-loop load experiment is needed to characterize recovery curves,
queue residence times and memory over extended overload. Metrics requests use the same
worker pool and can themselves receive 503 during saturation.

The [follow-up report](../benchmarks/reports/follow-up/README.md) adds wrk in a separate
container on disjoint CPUs, a 2,000-connection mostly idle comparison, and Linux perf
profiles. It also preserves both soak results and their timing/error qualifications.
Native Linux storage, independent physical client/server hosts and longer randomized
repetitions remain further work. No profiler-derived optimization claim is made here.
