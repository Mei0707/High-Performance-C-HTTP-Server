#pragma once
#include "pulsehttp/config.h"
#include "pulsehttp/http_parser.h"
#include "pulsehttp/http_response.h"
#include <chrono>

namespace pulsehttp {
using Clock = std::chrono::steady_clock;
enum class ConnectionState { reading, processing, writing };
struct Connection {
    uint64_t id;
    UniqueFd fd;
    HttpParser parser;
    std::string input, output;
    size_t input_offset = 0, write_offset = 0, requests = 0;
    off_t file_offset = 0;
    std::shared_ptr<FileBody> file;
    ConnectionState state = ConnectionState::reading;
    bool close_after = false, head = false, peer_eof = false;
    std::string method, route;
    Clock::time_point last_activity = Clock::now(), phase_start = last_activity, request_start = last_activity;
    bool message_started = false;
    Connection(uint64_t token, UniqueFd socket, const Config& c)
        : id(token), fd(std::move(socket)), parser(c.max_header_bytes, c.max_body_bytes) {}
    std::string_view unread() const { return std::string_view(input).substr(input_offset); }
    void consume(size_t n) {
        input_offset += n;
        if (input_offset >= input.size() / 2) { input.erase(0, input_offset); input_offset = 0; }
    }
};
} // namespace pulsehttp
