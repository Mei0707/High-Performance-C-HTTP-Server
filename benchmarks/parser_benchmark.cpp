#include "pulsehttp/http_parser.h"
#include <benchmark/benchmark.h>

static void ParseComplete(benchmark::State& state) {
    const std::string request = "GET /hello HTTP/1.1\r\nHost: localhost\r\nConnection: keep-alive\r\n\r\n";
    for (auto _ : state) {
        pulsehttp::HttpParser parser;
        benchmark::DoNotOptimize(parser.parse(request));
    }
    state.SetBytesProcessed(state.iterations() * static_cast<int64_t>(request.size()));
}
BENCHMARK(ParseComplete);
