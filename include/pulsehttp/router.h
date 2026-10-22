#pragma once
#include "pulsehttp/config.h"
#include "pulsehttp/http_request.h"
#include "pulsehttp/http_response.h"
#include "pulsehttp/metrics.h"
#include <atomic>
#include <functional>
#include <map>

namespace pulsehttp {
using Handler = std::function<HttpResponse(const HttpRequest&)>;
class Router {
public:
    Router(const Config& config, Metrics& metrics, const std::atomic<bool>& ready);
    void add_route(std::string method, std::string path, Handler handler);
    void freeze() { frozen_ = true; }
    HttpResponse dispatch(const HttpRequest& request) const;
    static std::string route_label(std::string_view path);
private:
    HttpResponse static_file(const HttpRequest& request) const;
    Config config_;
    UniqueFd root_;
    bool frozen_ = false;
    std::map<std::pair<std::string, std::string>, Handler> routes_;
};
} // namespace pulsehttp
