#pragma once
#include "pulsehttp/socket.h"
#include <memory>
#include <string>
#include <utility>
#include <vector>

namespace pulsehttp {
struct FileBody {
    UniqueFd fd;
    size_t size;
    FileBody(UniqueFd f, size_t n) : fd(std::move(f)), size(n) {}
};
struct HttpResponse {
    int status = 200;
    std::string content_type = "text/plain; charset=utf-8";
    std::string body;
    std::vector<std::pair<std::string, std::string>> headers;
    std::shared_ptr<FileBody> file;
    static HttpResponse text(int status, std::string body);
    // Throws on application-supplied response splitting or reserved framing headers.
    std::string serialize(bool head, bool close) const;
};
std::string_view reason_phrase(int status);
} // namespace pulsehttp
