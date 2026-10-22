#include "pulsehttp/config.h"
#include <charconv>
#include <limits>
#include <stdexcept>
#include <string_view>

namespace pulsehttp {
void Config::validate() const {
    if (workers == 0 || workers > 1024 || queue_capacity == 0 || max_connections == 0 ||
        max_header_bytes < 64 || max_input_bytes < max_header_bytes ||
        max_body_bytes > max_input_bytes - max_header_bytes || max_output_bytes < 1024 ||
        max_requests == 0 || max_file_bytes == 0 || queue_capacity > std::numeric_limits<size_t>::max() - workers ||
        backlog <= 0 || idle_timeout_ms <= 0 ||
        header_timeout_ms <= 0 || body_timeout_ms <= 0 || processing_timeout_ms <= 0 ||
        shutdown_timeout_ms <= 0)
        throw std::invalid_argument("invalid configuration: positive limits required; input must fit header + body; output >= 1024");
}
static size_t number(std::string_view value) {
    size_t n = 0;
    auto [end, ec] = std::from_chars(value.data(), value.data() + value.size(), n);
    if (ec != std::errc{} || end != value.data() + value.size() || value.empty())
        throw std::invalid_argument("invalid unsigned integer: " + std::string(value));
    return n;
}
Config Config::from_args(int argc, char** argv) {
    Config c;
    for (int i = 1; i < argc; ++i) {
        std::string arg = argv[i];
        if (arg == "--edge-triggered") { c.edge_triggered = true; continue; }
        if (arg == "--sendfile") { c.sendfile = true; continue; }
        if (arg == "--access-log") { c.access_log = true; continue; }
        if (i + 1 == argc) throw std::invalid_argument("missing value: " + arg);
        std::string value = argv[++i];
        if (arg == "--address") c.address = value;
        else if (arg == "--document-root") c.document_root = value;
        else {
            auto n = number(value);
            if (arg == "--port") {
                if (n > 65535) throw std::invalid_argument("port out of range");
                c.port = static_cast<uint16_t>(n);
            } else if (arg == "--workers") c.workers = n;
            else if (arg == "--queue-capacity") c.queue_capacity = n;
            else if (arg == "--max-connections") c.max_connections = n;
            else if (arg == "--max-header-bytes") c.max_header_bytes = n;
            else if (arg == "--max-body-bytes") c.max_body_bytes = n;
            else if (arg == "--max-input-bytes") c.max_input_bytes = n;
            else if (arg == "--max-output-bytes") c.max_output_bytes = n;
            else if (arg == "--max-file-bytes") c.max_file_bytes = n;
            else if (arg == "--max-requests") c.max_requests = n;
            else {
                if (n > static_cast<size_t>(std::numeric_limits<int>::max()))
                    throw std::invalid_argument("integer out of range: " + arg);
                auto ms = static_cast<int>(n);
                if (arg == "--idle-timeout-ms") c.idle_timeout_ms = ms;
                else if (arg == "--header-timeout-ms") c.header_timeout_ms = ms;
                else if (arg == "--body-timeout-ms") c.body_timeout_ms = ms;
                else if (arg == "--processing-timeout-ms") c.processing_timeout_ms = ms;
                else if (arg == "--shutdown-timeout-ms") c.shutdown_timeout_ms = ms;
                else if (arg == "--backlog") c.backlog = ms;
                else throw std::invalid_argument("unknown option: " + arg);
            }
        }
    }
    c.validate();
    return c;
}
std::string Config::usage() {
    return "PulseHTTP --address IP --port PORT --workers N --queue-capacity N\n"
           "  --document-root DIR --edge-triggered --sendfile --access-log\n"
           "  --max-connections N --max-requests N --backlog N\n"
           "  --max-{header,body,input,output,file}-bytes N\n"
           "  --{idle,header,body,processing,shutdown}-timeout-ms N\n"
           "Use --port 0 for an ephemeral port. SIGINT/SIGTERM drains active requests.\n";
}
} // namespace pulsehttp
