#include "pulsehttp/http_parser.h"
#include <charconv>
#include <utility>

namespace pulsehttp {
std::string ascii_lower(std::string_view s) {
    std::string out(s);
    for (char& c : out) if (c >= 'A' && c <= 'Z') c = static_cast<char>(c + ('a' - 'A'));
    return out;
}
bool http_token(std::string_view s) {
    if (s.empty()) return false;
    for (unsigned char c : s) {
        if ((c >= 'a' && c <= 'z') || (c >= 'A' && c <= 'Z') || (c >= '0' && c <= '9')) continue;
        if (std::string_view("!#$%&'*+-.^_`|~").find(static_cast<char>(c)) == std::string_view::npos) return false;
    }
    return true;
}
bool header_value_valid(std::string_view s) {
    for (unsigned char c : s) if ((c < 32 && c != '\t') || c == 127) return false;
    return true;
}
static std::string_view trim(std::string_view s) {
    while (!s.empty() && (s.front() == ' ' || s.front() == '\t')) s.remove_prefix(1);
    while (!s.empty() && (s.back() == ' ' || s.back() == '\t')) s.remove_suffix(1);
    return s;
}
ParseResult HttpParser::parse_headers(std::string_view h) {
    auto bad = [](int status = 400) { return ParseResult{ParseState::error, status, 0}; };
    const auto line_end = h.find("\r\n");
    if (line_end == std::string_view::npos) return bad();
    auto line = h.substr(0, line_end);
    const auto first = line.find(' '), last = line.rfind(' ');
    if (first == std::string_view::npos || first == last || !http_token(line.substr(0, first))) return bad();
    auto target = line.substr(first + 1, last - first - 1);
    if (target.empty() || target.front() != '/') return bad();
    for (unsigned char c : target) if (c <= 32 || c >= 127 || c == '#') return bad();
    auto version = line.substr(last + 1);
    if (version != "HTTP/1.1") return bad(version == "HTTP/1.0" || version == "HTTP/2.0" ? 505 : 400);
    request_.method = line.substr(0, first);
    request_.target = target;
    request_.path = target.substr(0, target.find('?'));
    bool content_length = false, transfer_encoding = false, host = false, expect = false;
    size_t pos = line_end + 2;
    while (pos < h.size()) {
        auto end = h.find("\r\n", pos);
        if (end == std::string_view::npos) return bad();
        line = h.substr(pos, end - pos);
        pos = end + 2;
        if (line.empty()) break;
        auto colon = line.find(':');
        if (colon == std::string_view::npos || !http_token(line.substr(0, colon))) return bad();
        auto name = ascii_lower(line.substr(0, colon));
        auto value = trim(line.substr(colon + 1));
        if (!header_value_valid(value)) return bad();
        if (name == "host") {
            if (host || value.empty()) return bad();
            for (unsigned char c : value) if (c <= 32 || c >= 127 || c == ',' || c == '/' || c == '@' || c == '\\') return bad();
            host = true;
        } else if (name == "content-length") {
            if (content_length || value.empty()) return bad();
            content_length = true;
            auto [ptr, ec] = std::from_chars(value.data(), value.data() + value.size(), body_bytes_);
            if (ec != std::errc{} || ptr != value.data() + value.size()) return bad();
        } else if (name == "transfer-encoding") transfer_encoding = true;
        else if (name == "expect") expect = true;
        auto [it, inserted] = request_.headers.emplace(name, value);
        if (!inserted) { it->second += ','; it->second += value; }
    }
    if (!host || (content_length && transfer_encoding)) return bad();
    if (transfer_encoding) return bad(501);
    if (expect) return bad(417);
    if (body_bytes_ > body_limit_) return bad(413);
    auto it = request_.headers.find("connection");
    if (it != request_.headers.end()) {
        auto lower = ascii_lower(it->second);
        std::string_view tokens(lower);
        while (!tokens.empty()) {
            auto comma = tokens.find(',');
            auto token = trim(tokens.substr(0, comma));
            if (!http_token(token)) return bad();
            if (token == "close") request_.keep_alive = false;
            if (comma == std::string_view::npos) break;
            tokens.remove_prefix(comma + 1);
        }
    }
    return {};
}
ParseResult HttpParser::parse(std::string_view input) {
    if (error_) return {ParseState::error, error_, 0};
    if (!headers_done_) {
        auto end = input.find("\r\n\r\n", scanned_);
        if (end == std::string_view::npos) {
            scanned_ = input.size() > 3 ? input.size() - 3 : 0;
            if (input.size() >= header_limit_) { error_ = 431; return {ParseState::error, error_, 0}; }
            return {};
        }
        header_bytes_ = end + 4;
        if (header_bytes_ > header_limit_) { error_ = 431; return {ParseState::error, error_, 0}; }
        auto result = parse_headers(input.substr(0, header_bytes_));
        if (result.state == ParseState::error) { error_ = result.status; return result; }
        headers_done_ = true;
    }
    if (input.size() - header_bytes_ < body_bytes_) return {};
    request_.body.assign(input.substr(header_bytes_, body_bytes_));
    return {ParseState::complete, 0, header_bytes_ + body_bytes_};
}
HttpRequest HttpParser::take_request() {
    auto out = std::move(request_);
    *this = HttpParser(header_limit_, body_limit_);
    return out;
}
} // namespace pulsehttp
