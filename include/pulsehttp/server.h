#pragma once
#include "pulsehttp/router.h"

namespace pulsehttp {
class Server {
public:
    explicit Server(Config config = {});
    void add_route(std::string method, std::string path, Handler handler);
    void run();
    void request_stop() { stop_ = true; }
    const Metrics& metrics() const { return metrics_; }
private:
    Config config_;
    Metrics metrics_;
    std::atomic<bool> ready_{false}, stop_{false}, started_{false};
    Router router_;
};
} // namespace pulsehttp
