#include "pulsehttp/metrics.h"
#include <sstream>

namespace pulsehttp {
void Metrics::observe(double seconds) {
    duration_sum.fetch_add(seconds, std::memory_order_relaxed);
    for (size_t i = 0; i < boundaries.size(); ++i)
        if (seconds <= boundaries[i]) buckets[i].fetch_add(1, std::memory_order_relaxed);
    requests.fetch_add(1, std::memory_order_relaxed);
}
std::string Metrics::render() const {
    std::ostringstream o;
    auto metric = [&](const char* name, const char* type, const auto& value) {
        o << "# TYPE " << name << ' ' << type << '\n' << name << ' ' << value.load(std::memory_order_relaxed) << '\n';
    };
    metric("http_requests_total", "counter", requests);
    metric("http_active_connections", "gauge", active);
    metric("http_connections_accepted_total", "counter", accepted);
    metric("http_connections_closed_total", "counter", closed);
    metric("http_worker_queue_depth", "gauge", queue_depth);
    metric("http_worker_queue_rejections_total", "counter", rejections);
    metric("http_bytes_received_total", "counter", bytes_received);
    metric("http_bytes_sent_total", "counter", bytes_sent);
    metric("http_parse_errors_total", "counter", parse_errors);
    metric("http_connection_timeouts_total", "counter", timeouts);
    metric("http_response_write_errors_total", "counter", write_errors);
    metric("http_connection_rejections_total", "counter", admission_rejections);
    metric("http_stale_completions_total", "counter", stale_completions);
    metric("http_log_drops_total", "counter", log_drops);
    metric("http_handler_errors_total", "counter", handler_errors);
    o << "# TYPE http_request_duration_seconds histogram\n";
    for (size_t i = 0; i < boundaries.size(); ++i)
        o << "http_request_duration_seconds_bucket{le=\"" << boundaries[i] << "\"} " << buckets[i].load() << '\n';
    o << "http_request_duration_seconds_bucket{le=\"+Inf\"} " << requests.load() << '\n';
    o << "http_request_duration_seconds_sum " << duration_sum.load() << '\n';
    o << "http_request_duration_seconds_count " << requests.load() << '\n';
    return o.str();
}
} // namespace pulsehttp
