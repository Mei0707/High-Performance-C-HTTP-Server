#pragma once
#include <array>
#include <atomic>
#include <cstdint>
#include <string>

namespace pulsehttp {
struct Metrics {
    std::atomic<uint64_t> requests{0}, active{0}, accepted{0}, closed{0}, queue_depth{0};
    std::atomic<uint64_t> rejections{0}, bytes_received{0}, bytes_sent{0}, parse_errors{0};
    std::atomic<uint64_t> timeouts{0}, write_errors{0}, admission_rejections{0}, stale_completions{0};
    std::atomic<uint64_t> log_drops{0}, handler_errors{0};
    static constexpr std::array<double, 9> boundaries{.0001, .0005, .001, .005, .01, .05, .1, .5, 1};
    std::array<std::atomic<uint64_t>, 9> buckets{};
    std::atomic<double> duration_sum{0};
    void observe(double seconds);
    std::string render() const;
};
} // namespace pulsehttp
