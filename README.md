# PulseHTTP

A C++20 HTTP/1.1 server built from the proposal in [High.pdf](High.pdf). Linux `epoll`
owns network I/O; a bounded worker pool runs handlers and wakes the reactor through `eventfd`.
There are no runtime framework dependencies. GoogleTest and Google Benchmark are development dependencies.

Implemented: incremental strict framing, GET/HEAD/POST, persistent connections and ordered
pipelining, partial I/O, LT/ET modes, bounded queues and buffers, connection identities,
timeouts, graceful shutdown, secure static files with buffered/sendfile modes, JSON access
logs, Prometheus metrics, a reusable server API, and a thread-per-connection comparison server.

## Build and run

On Linux (Ubuntu 24.04):

```sh
sudo apt-get update
sudo apt-get install -y build-essential cmake ninja-build python3 libgtest-dev
cmake -S . -B build -G Ninja -DCMAKE_BUILD_TYPE=Release
cmake --build build -j4
ctest --test-dir build --output-on-failure
./build/pulsehttp_server --port 8080 --document-root ./www
```

On macOS, use Linux containers. This tests the actual epoll backend:

```sh
docker build --target dev -t pulsehttp-dev .
docker run --rm -v "$PWD:/src" pulsehttp-dev sh -c \
  'cmake -S . -B build -G Ninja -DCMAKE_BUILD_TYPE=Release && cmake --build build -j4 && ctest --test-dir build --output-on-failure'
docker run --rm -p 8080:8080 -v "$PWD:/src" pulsehttp-dev \
  ./build/pulsehttp_server --address 0.0.0.0 --document-root ./www
```

Or build the non-root runtime image (its build runs the tests):

```sh
docker build -t pulsehttp .
docker run --rm --name pulsehttp -p 8080:8080 pulsehttp
curl http://localhost:8080/health
curl --data-binary 'hello' http://localhost:8080/api/echo
curl -I http://localhost:8080/static/index.html
docker stop --timeout 15 pulsehttp
```

## Routes and configuration

| Route | Method | Result |
|---|---|---|
| `/health` | GET, HEAD | JSON liveness |
| `/ready` | GET, HEAD | 200 while accepting; readiness false during drain |
| `/hello` | GET, HEAD | Small text response |
| `/api/echo` | POST | Binary-safe echo |
| `/api/compute?iterations=100000` | GET, HEAD | Bounded CPU workload; maximum 20,000,000 iterations |
| `/metrics` | GET, HEAD | Prometheus exposition |
| `/static/*` | GET, HEAD | Files under the configured root; symlinks rejected |

Use `--help` for all options. Bind defaults to `127.0.0.1:8080`. `--port 0` selects a
port and prints it as a JSON startup record. The document root must exist.

| Limit | Default | Option |
|---|---:|---|
| Workers / pending worker tasks | 4 / 256 | `--workers`, `--queue-capacity` |
| Active connections | 4096 | `--max-connections` |
| Header / body bytes | 16 KiB / 1 MiB | `--max-header-bytes`, `--max-body-bytes` |
| Input / output bytes per connection | 2 MiB / 2 MiB | `--max-input-bytes`, `--max-output-bytes` |
| Static file size | 16 MiB | `--max-file-bytes` |
| Requests per connection | 1000 | `--max-requests` |
| Idle / header / body timeout | 30 / 5 / 10 sec | `--idle-timeout-ms`, `--header-timeout-ms`, `--body-timeout-ms` |
| Processing / shutdown timeout | 10 / 5 sec | `--processing-timeout-ms`, `--shutdown-timeout-ms` |

Input must fit the configured header plus body maximum. A full worker queue produces
503 and closes the connection. Excess connections are closed immediately. File buffering
also obeys the output limit; `--sendfile` streams larger permitted files. Add
`--edge-triggered` to compare epoll modes and `--access-log` for bounded asynchronous JSON logs.

## Extend and validate

[examples/custom_routes.cpp](examples/custom_routes.cpp) shows registration before startup.
Construct `pulsehttp::Server`, call `add_route`, then `run`; `request_stop` is thread safe.
Handlers run concurrently and must finish in bounded time. See [concurrency](docs/concurrency.md)
for embedding and shutdown constraints.

```sh
cmake -S . -B build-asan -G Ninja -DCMAKE_BUILD_TYPE=RelWithDebInfo -DPULSE_SANITIZER=address,undefined
cmake --build build-asan -j4
ASAN_OPTIONS=detect_leaks=1:halt_on_error=1 UBSAN_OPTIONS=halt_on_error=1 ctest --test-dir build-asan --output-on-failure

cmake -S . -B build-tsan -G Ninja -DCMAKE_BUILD_TYPE=RelWithDebInfo -DPULSE_SANITIZER=thread
cmake --build build-tsan -j4
TSAN_OPTIONS=halt_on_error=1 ctest --test-dir build-tsan --output-on-failure

cmake -S . -B build-fuzz -G Ninja -DCMAKE_CXX_COMPILER=clang++ -DPULSE_BUILD_TESTS=OFF -DPULSE_FUZZ=ON -DPULSE_SANITIZER=address,undefined
cmake --build build-fuzz --target http_parser_fuzz -j4
mkdir -p /tmp/pulse-corpus
cp fuzz/corpus/* /tmp/pulse-corpus/
./build-fuzz/http_parser_fuzz /tmp/pulse-corpus -max_total_time=30 -max_len=131072

python3 tests/stress.py --connections 1000 --rounds 3 --requests 10
```

These commands also run inside `pulsehttp-dev` using the mount above. ASan and TSan use separate builds.

Run the reproducible comparison suite after a Release build:

```sh
python3 benchmarks/scripts/suite.py --duration 3 --repetitions 3
```

The suite preserves raw latency samples, errors, environment/build metadata, resource
samples, Markdown tables and an SVG chart. See [measured results](benchmarks/reports/summary.md),
[methodology](docs/benchmarks.md), and [analysis](docs/performance-analysis.md).
The included client shares the server's CPUs; results do not establish a production capacity.

Read the [proposal review](docs/proposal-review.md), [architecture](docs/architecture.md),
[protocol contract](docs/protocol.md), [validation record](docs/validation.md), and
[limitations](docs/limitations.md) for design decisions and evidence.
