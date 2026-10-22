#include "pulsehttp/server.h"
#include <csignal>
#include <iostream>
#include <thread>

int main(int argc, char** argv) {
    ::signal(SIGPIPE, SIG_IGN);
    try {
        pulsehttp::Server server(pulsehttp::Config::from_args(argc, argv));
        // Handlers run concurrently: captured application state must be thread safe.
        server.add_route("GET", "/version", [](const auto&) {
            return pulsehttp::HttpResponse::text(200, "PulseHTTP 0.1.0\n");
        });
        server.add_route("GET", "/throw", [](const auto&) -> pulsehttp::HttpResponse {
            throw std::runtime_error("example handler error");
        });
        server.add_route("GET", "/slow", [](const auto&) {
            std::this_thread::sleep_for(std::chrono::milliseconds(250));
            return pulsehttp::HttpResponse::text(200, "finished\n");
        });
        server.add_route("GET", "/invalid-response", [](const auto&) {
            auto response = pulsehttp::HttpResponse::text(200, "invalid\n");
            response.headers.emplace_back("X-Test", "bad\r\nInjected: value");
            return response;
        });
        server.run();
    } catch (const std::exception& e) { std::cerr << e.what() << '\n'; return 1; }
}
