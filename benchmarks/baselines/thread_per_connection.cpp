// Deliberately simple baseline. Same parser/router/response code, one thread per socket.
#include "pulsehttp/connection.h"
#include "pulsehttp/router.h"
#include <arpa/inet.h>
#include <csignal>
#include <iostream>
#include <list>
#include <netinet/tcp.h>
#include <poll.h>
#include <pthread.h>
#include <sys/sendfile.h>
#include <sys/signalfd.h>
#include <sys/socket.h>
#include <thread>

using namespace pulsehttp;

struct Baseline {
    Config config;
    Metrics metrics;
    std::atomic<bool> ready{true};
    Router router;
    explicit Baseline(Config c) : config(std::move(c)), router(config, metrics, ready) { router.freeze(); }

    void serve(UniqueFd socket) {
        Connection c(0, std::move(socket), config);
        ++metrics.active; ++metrics.accepted;
        struct CloseCount { Metrics& m; ~CloseCount() { --m.active; ++m.closed; } } count{metrics};
        char bytes[16384];
        while (ready.load()) {
            const auto start = Clock::now();
            bool message_started = !c.unread().empty();
            auto deadline = start + std::chrono::milliseconds(message_started ? config.header_timeout_ms : config.idle_timeout_ms);
            ParseResult result;
            for (;;) {
                bool body = c.parser.reading_body();
                result = c.parser.parse(c.unread());
                if (result.state != ParseState::incomplete) break;
                if (!body && c.parser.reading_body()) deadline = Clock::now() + std::chrono::milliseconds(config.body_timeout_ms);
                if (!ready.load() || Clock::now() > deadline) return;
                pollfd p{c.fd.get(), POLLIN, 0};
                if (::poll(&p, 1, 50) <= 0) continue;
                if (c.input_offset) { c.input.erase(0, c.input_offset); c.input_offset = 0; }
                auto room = config.max_input_bytes - c.input.size();
                if (!room) { result = {ParseState::error, 413, 0}; break; }
                auto n = ::recv(c.fd.get(), bytes, std::min(room, sizeof(bytes)), 0);
                if (n < 0 && (errno == EINTR || errno == EAGAIN)) continue;
                if (n <= 0) return;
                if (!message_started) {
                    message_started = true;
                    deadline = Clock::now() + std::chrono::milliseconds(config.header_timeout_ms);
                }
                c.input.append(bytes, static_cast<size_t>(n)); metrics.bytes_received += static_cast<uint64_t>(n);
            }
            HttpRequest request;
            HttpResponse response;
            bool close_after = !ready.load();
            if (result.state == ParseState::error) {
                ++metrics.parse_errors; close_after = true;
                response = HttpResponse::text(result.status, std::string(reason_phrase(result.status)) + "\n");
            } else {
                request = c.parser.take_request(); c.consume(result.consumed);
                close_after = close_after || !request.keep_alive || ++c.requests >= config.max_requests;
                try { response = router.dispatch(request); }
                catch (...) { response = HttpResponse::text(500, "handler failed\n"); }
            }
            metrics.observe(std::chrono::duration<double>(Clock::now() - start).count());
            auto output = response.serialize(request.method == "HEAD", close_after);
            if (output.size() > config.max_output_bytes) {
                response = HttpResponse::text(503, "output buffer limit\n"); close_after = true;
                output = response.serialize(request.method == "HEAD", true);
            }
            size_t offset = 0;
            off_t file_offset = 0;
            auto write_deadline = Clock::now() + std::chrono::milliseconds(config.idle_timeout_ms);
            auto drain_deadline = Clock::time_point::max();
            auto file = request.method == "HEAD" ? nullptr : response.file;
            while (offset < output.size() || (file && static_cast<size_t>(file_offset) < file->size)) {
                if (!ready.load() && drain_deadline == Clock::time_point::max())
                    drain_deadline = Clock::now() + std::chrono::milliseconds(config.shutdown_timeout_ms);
                if (Clock::now() >= std::min(write_deadline, drain_deadline)) return;
                pollfd p{c.fd.get(), POLLOUT, 0};
                if (::poll(&p, 1, 50) <= 0) continue;
                ssize_t n;
                if (offset < output.size()) {
                    n = ::send(c.fd.get(), output.data() + offset, output.size() - offset, MSG_NOSIGNAL);
                    if (n > 0) offset += static_cast<size_t>(n);
                } else n = ::sendfile(c.fd.get(), file->fd.get(), &file_offset, file->size - static_cast<size_t>(file_offset));
                if (n < 0 && (errno == EINTR || errno == EAGAIN)) continue;
                if (n <= 0) return;
                metrics.bytes_sent += static_cast<uint64_t>(n);
                write_deadline = Clock::now() + std::chrono::milliseconds(config.idle_timeout_ms);
            }
            if (close_after) return;
        }
    }
    void run() {
        ::signal(SIGPIPE, SIG_IGN);
        sigset_t mask;
        sigemptyset(&mask); sigaddset(&mask, SIGINT); sigaddset(&mask, SIGTERM);
        pthread_sigmask(SIG_BLOCK, &mask, nullptr);
        UniqueFd signals(::signalfd(-1, &mask, SFD_CLOEXEC | SFD_NONBLOCK));
        UniqueFd listener(::socket(AF_INET, SOCK_STREAM | SOCK_NONBLOCK | SOCK_CLOEXEC, 0));
        int yes = 1;
        ::setsockopt(listener.get(), SOL_SOCKET, SO_REUSEADDR, &yes, sizeof(yes));
        sockaddr_in address{}; address.sin_family = AF_INET; address.sin_port = htons(config.port);
        if (::inet_pton(AF_INET, config.address.c_str(), &address.sin_addr) != 1 ||
            ::bind(listener.get(), reinterpret_cast<sockaddr*>(&address), sizeof(address)) != 0 ||
            ::listen(listener.get(), config.backlog) != 0) throw std::runtime_error("baseline listen failed");
        socklen_t len = sizeof(address);
        ::getsockname(listener.get(), reinterpret_cast<sockaddr*>(&address), &len);
        std::cout << "{\"event\":\"listening\",\"port\":" << ntohs(address.sin_port) << ",\"backend\":\"threads\"}" << std::endl;
        struct Worker { std::thread thread; std::shared_ptr<std::atomic<bool>> done; };
        std::list<Worker> workers;
        while (ready) {
            for (auto it = workers.begin(); it != workers.end();) {
                if (it->done->load()) { it->thread.join(); it = workers.erase(it); }
                else ++it;
            }
            pollfd events[]{{listener.get(), POLLIN, 0}, {signals.get(), POLLIN, 0}};
            if (::poll(events, 2, 20) <= 0) continue;
            if (events[1].revents) { ready = false; break; }
            if (!events[0].revents) continue;
            UniqueFd fd(::accept4(listener.get(), nullptr, nullptr, SOCK_NONBLOCK | SOCK_CLOEXEC));
            if (!fd) continue;
            if (workers.size() >= config.max_connections) { ++metrics.admission_rejections; continue; }
            ::setsockopt(fd.get(), IPPROTO_TCP, TCP_NODELAY, &yes, sizeof(yes));
            auto done = std::make_shared<std::atomic<bool>>(false);
            // Insert before constructing a joinable thread so allocation failure cannot terminate.
            workers.push_back({{}, done});
            try {
                workers.back().thread = std::thread([this, socket = std::move(fd), done]() mutable {
                    try { serve(std::move(socket)); } catch (...) { ++metrics.handler_errors; }
                    *done = true;
                });
            } catch (...) { workers.pop_back(); ++metrics.admission_rejections; }
        }
        listener.reset();
        for (auto& w : workers) w.thread.join();
    }
};

int main(int argc, char** argv) {
    try { Baseline baseline(Config::from_args(argc, argv)); baseline.run(); }
    catch (const std::exception& e) { std::cerr << e.what() << '\n'; return 1; }
}
