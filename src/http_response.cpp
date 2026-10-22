#include "pulsehttp/http_response.h"
#include "pulsehttp/http_parser.h"
#include <stdexcept>

namespace pulsehttp {
std::string_view reason_phrase(int s) {
    switch (s) {
    case 200: return "OK"; case 204: return "No Content";
    case 400: return "Bad Request"; case 403: return "Forbidden";
    case 404: return "Not Found"; case 405: return "Method Not Allowed";
    case 408: return "Request Timeout"; case 413: return "Content Too Large";
    case 417: return "Expectation Failed"; case 431: return "Request Header Fields Too Large";
    case 500: return "Internal Server Error"; case 501: return "Not Implemented";
    case 503: return "Service Unavailable"; case 504: return "Gateway Timeout";
    case 505: return "HTTP Version Not Supported";
    default: return "Unknown";
    }
}
HttpResponse HttpResponse::text(int status, std::string body) {
    HttpResponse r;
    r.status = status;
    r.body = std::move(body);
    return r;
}
std::string HttpResponse::serialize(bool head, bool close) const {
    if (status < 200 || status > 599 || !header_value_valid(content_type))
        throw std::invalid_argument("invalid response status or content type");
    const bool no_body = status == 204 || status == 304;
    std::string out = "HTTP/1.1 " + std::to_string(status) + " " + std::string(reason_phrase(status)) + "\r\n";
    if (!no_body) out += "Content-Type: " + content_type + "\r\nContent-Length: " + std::to_string(file ? file->size : body.size()) + "\r\n";
    out += close ? "Connection: close\r\n" : "Connection: keep-alive\r\n";
    for (const auto& [name, value] : headers) {
        const auto lower = ascii_lower(name);
        if (!http_token(name) || !header_value_valid(value) || lower == "content-length" ||
            lower == "transfer-encoding" || lower == "connection" || lower == "content-type")
            throw std::invalid_argument("invalid or reserved response header");
        out += name + ": " + value + "\r\n";
    }
    out += "\r\n";
    if (!head && !file && !no_body) out += body;
    return out;
}
} // namespace pulsehttp
