#pragma once
#include <cstddef>
#include <cstdint>
#include <string>

namespace pulsehttp {
struct Config {
    std::string address = "127.0.0.1";
    uint16_t port = 8080;
    size_t workers = 4;
    size_t queue_capacity = 256;
    size_t max_connections = 4096;
    size_t max_header_bytes = 16 * 1024;
    size_t max_body_bytes = 1024 * 1024;
    size_t max_input_bytes = 2 * 1024 * 1024;
    size_t max_output_bytes = 2 * 1024 * 1024;
    size_t max_file_bytes = 16 * 1024 * 1024;
    size_t max_requests = 1000;
    int idle_timeout_ms = 30000;
    int header_timeout_ms = 5000;
    int body_timeout_ms = 10000;
    int processing_timeout_ms = 10000;
    int shutdown_timeout_ms = 5000;
    int backlog = 1024;
    bool edge_triggered = false;
    bool sendfile = false;
    bool access_log = false;
    std::string document_root = "./www";
    void validate() const;
    static Config from_args(int argc, char** argv);
    static std::string usage();
};
} // namespace pulsehttp
