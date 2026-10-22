#pragma once
#include <string>
#include <unordered_map>

namespace pulsehttp {
struct HttpRequest {
    std::string method;
    std::string target;
    std::string path;
    std::unordered_map<std::string, std::string> headers;
    std::string body;
    bool keep_alive = true;
};
} // namespace pulsehttp
