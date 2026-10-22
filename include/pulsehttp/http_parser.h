#pragma once
#include "pulsehttp/http_request.h"
#include <cstddef>
#include <string_view>

namespace pulsehttp {
enum class ParseState { incomplete, complete, error };
struct ParseResult {
    ParseState state = ParseState::incomplete;
    int status = 0;
    size_t consumed = 0;
};
class HttpParser {
public:
    HttpParser(size_t header_limit = 16384, size_t body_limit = 1048576)
        : header_limit_(header_limit), body_limit_(body_limit) {}
    // The view starts at this message and grows between calls. Caller owns the bytes.
    ParseResult parse(std::string_view input);
    HttpRequest take_request();
    bool reading_body() const { return headers_done_; }
private:
    ParseResult parse_headers(std::string_view headers);
    size_t header_limit_, body_limit_;
    size_t scanned_ = 0, header_bytes_ = 0, body_bytes_ = 0;
    bool headers_done_ = false;
    int error_ = 0;
    HttpRequest request_;
};
std::string ascii_lower(std::string_view s);
bool http_token(std::string_view s);
bool header_value_valid(std::string_view s);
} // namespace pulsehttp
