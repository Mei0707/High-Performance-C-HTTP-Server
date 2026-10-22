# Validation record

Validated on October 22, 2026, using Ubuntu 24.04 arm64 containers on Docker Desktop
on the macOS development host. GCC 13.3 compiled Release and sanitizer builds; Clang
18.1.3 compiled the fuzz harness. This exercises Linux epoll/eventfd/signalfd/sendfile
directly. No alternate networking backend was used.

| Configuration | Unit tests | Reactor integration | Baseline integration | Result |
|---|---:|---:|---:|---|
| Release | 35 | 14 | 9 applicable, 5 skipped | Passed |
| AddressSanitizer + UndefinedBehaviorSanitizer | 35 | 14 | 9 applicable, 5 skipped | Passed |
| ThreadSanitizer | 35 | 14 | 9 applicable, 5 skipped | Passed |

The baseline skips worker-queue, worker-completion and reactor-specific timeout cases;
it has no separate worker pool. It still runs fragmentation, framing, HEAD, binary bodies,
pipelining, static containment, slow reads, limits, churn, half-close and active shutdown.
Final CTest times were 44.21 s (Release), 45.39 s (ASan/UBSan), and 45.00 s (TSan).

Preserved test logs: [Release](validation/release.log), [ASan/UBSan](validation/asan-ubsan.log),
[TSan](validation/tsan.log). Sanitizer runs used halt-on-error, with leak detection enabled
for ASan. No sanitizer finding was reported.

The libFuzzer campaign executed **1,057,699 inputs in 31 seconds**, with ASan/UBSan enabled.
It checked that whole-buffer and fragmented parsing agree, exercised pipelines and seeded
valid binary bodies and ambiguous framing. The final corpus had 547 units / 178 KiB and
476 reported coverage points. No crash or sanitizer finding occurred. See [raw fuzz log](validation/fuzz.log).
This short campaign is a smoke check, not an extended fuzzing claim.

The separate stress run held up to **1,000 concurrent persistent sessions**, repeated
three rounds of ten binary echo requests per session, and validated **30,000 response
bodies**. The server descriptor count was **8 before and 8 after each round**. See
[stress measurements](validation/stress.json). Reproduce on Linux:

```sh
python3 tests/stress.py --connections 1000 --rounds 3 --requests 10 --output /tmp/pulse-stress.json
```

Load comparisons and resource samples are recorded separately in the
[benchmark report](../benchmarks/reports/summary.md). Their source and binary hashes identify
the measured build. Native hardware capacity tests, a long-duration soak and privileged
perf profiling were not performed in this validation.

The final multi-stage Docker image built successfully, including its clean CTest stage.
Its runtime user was verified as UID 10001. A client on its private container network
verified health, binary echo, metrics, GET/HEAD and sendfile of the packaged HTML file.
All five access records parsed as JSON with the expected fields and HTTP 200 statuses.
The temporary verification container was stopped and removed. See the
[image build log](validation/docker-build.log) and [runtime records](validation/runtime.log).

The optional Google Benchmark parser target also built and ran three repetitions;
its [raw output](validation/parser-microbenchmark.json) is retained as target validation.
The distribution's Google Benchmark library reported a DEBUG build, so its timings
are not used for a performance claim. The full fuzz configuration also built all of its
normal executable targets successfully.
