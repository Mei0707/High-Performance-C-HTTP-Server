#include "pulsehttp/reactor.h"
#include <arpa/inet.h>
#include <cerrno>
#include <csignal>
#include <cstring>
#include <iostream>
#include <netinet/tcp.h>
#include <stdexcept>
#include <sys/epoll.h>
#include <sys/eventfd.h>
#include <sys/sendfile.h>
#include <sys/signalfd.h>
#include <sys/socket.h>

namespace pulsehttp {
static void check(bool ok, const char* what) {
    if (!ok) throw std::runtime_error(std::string(what) + ": " + std::strerror(errno));
}
Reactor::Reactor(const Config& c, const Router& router, Metrics& metrics,
                 std::atomic<bool>& ready, const std::atomic<bool>& stop)
    : config_(c), router_(router), metrics_(metrics), ready_(ready), stop_(stop),
      logger_(c.access_log, metrics), epoll_(::epoll_create1(EPOLL_CLOEXEC)),
      wake_(::eventfd(0, EFD_NONBLOCK | EFD_CLOEXEC)), pool_(c.workers, c.queue_capacity) {
    check(bool(epoll_) && bool(wake_), "epoll/eventfd");
    sigset_t mask;
    ::sigemptyset(&mask); ::sigaddset(&mask, SIGINT); ::sigaddset(&mask, SIGTERM);
    signal_.reset(::signalfd(-1, &mask, SFD_NONBLOCK | SFD_CLOEXEC));
    check(bool(signal_), "signalfd");
    listener_.reset(::socket(AF_INET, SOCK_STREAM | SOCK_NONBLOCK | SOCK_CLOEXEC, 0));
    check(bool(listener_), "socket");
    int yes = 1;
    check(::setsockopt(listener_.get(), SOL_SOCKET, SO_REUSEADDR, &yes, sizeof(yes)) == 0, "SO_REUSEADDR");
    sockaddr_in address{};
    address.sin_family = AF_INET; address.sin_port = htons(c.port);
    if (::inet_pton(AF_INET, c.address.c_str(), &address.sin_addr) != 1)
        throw std::invalid_argument("--address must be an IPv4 literal");
    check(::bind(listener_.get(), reinterpret_cast<sockaddr*>(&address), sizeof(address)) == 0, "bind");
    check(::listen(listener_.get(), c.backlog) == 0, "listen");
    watch(EPOLL_CTL_ADD, listener_.get(), 1, EPOLLIN);
    watch(EPOLL_CTL_ADD, wake_.get(), 2, EPOLLIN);
    watch(EPOLL_CTL_ADD, signal_.get(), 3, EPOLLIN);
    socklen_t len = sizeof(address);
    check(::getsockname(listener_.get(), reinterpret_cast<sockaddr*>(&address), &len) == 0, "getsockname");
    ready_ = true;
    std::cout << "{\"event\":\"listening\",\"port\":" << ntohs(address.sin_port)
              << ",\"backend\":\"epoll\",\"trigger\":\"" << (c.edge_triggered ? "edge" : "level") << "\"}" << std::endl;
}
Reactor::~Reactor() { ready_ = false; pool_.shutdown(); }
void Reactor::watch(int operation, int fd, uint64_t token, uint32_t events) {
    epoll_event event{};
    event.events = events; event.data.u64 = token;
    check(::epoll_ctl(epoll_.get(), operation, fd, &event) == 0, "epoll_ctl");
}
void Reactor::arm(Connection& c) {
    uint32_t events = config_.edge_triggered ? EPOLLET : 0U;
    // Suspend reads while a handler or response owns this connection. Kernel TCP backpressure
    // bounds pipelined input without rejecting an earlier, already accepted request.
    if (c.state == ConnectionState::reading) events |= EPOLLIN | EPOLLRDHUP;
    if (c.state == ConnectionState::writing) events |= EPOLLOUT;
    watch(EPOLL_CTL_MOD, c.fd.get(), c.id, events);
}
void Reactor::accept_connections() {
    // LT listener and a batch cap prevent an accept flood from starving existing clients.
    for (int i = 0; i < 128; ++i) {
        UniqueFd fd(::accept4(listener_.get(), nullptr, nullptr, SOCK_NONBLOCK | SOCK_CLOEXEC));
        if (!fd) {
            if (errno == EINTR) { --i; continue; }
            if (errno == EAGAIN || errno == EWOULDBLOCK) return;
            // EMFILE/ENFILE: avoid a readiness busy loop. Disable listener until next tick.
            if (errno == EMFILE || errno == ENFILE) {
                ::epoll_ctl(epoll_.get(), EPOLL_CTL_DEL, listener_.get(), nullptr);
                return;
            }
            return;
        }
        if (connections_.size() >= config_.max_connections) { ++metrics_.admission_rejections; continue; }
        int yes = 1;
        ::setsockopt(fd.get(), IPPROTO_TCP, TCP_NODELAY, &yes, sizeof(yes));
        auto id = next_id_++;
        if (next_id_ == 0) throw std::runtime_error("connection identity exhausted");
        auto c = std::make_unique<Connection>(id, std::move(fd), config_);
        watch(EPOLL_CTL_ADD, c->fd.get(), id, EPOLLIN | EPOLLRDHUP | (config_.edge_triggered ? EPOLLET : 0U));
        connections_.emplace(id, std::move(c));
        ++metrics_.active; ++metrics_.accepted;
    }
}
void Reactor::close(uint64_t id) {
    auto it = connections_.find(id);
    if (it == connections_.end()) return;
    ::epoll_ctl(epoll_.get(), EPOLL_CTL_DEL, it->second->fd.get(), nullptr);
    connections_.erase(it); --metrics_.active; ++metrics_.closed;
}
void Reactor::read(Connection& c) {
    char bytes[16384];
    size_t budget = 256 * 1024;
    while (budget > 0 && c.state == ConnectionState::reading) {
        if (c.input_offset) { c.input.erase(0, c.input_offset); c.input_offset = 0; }
        auto room = config_.max_input_bytes - c.input.size();
        if (!room) { respond(c, HttpResponse::text(413, "input buffer limit\n"), true); return; }
        auto n = ::recv(c.fd.get(), bytes, std::min({sizeof(bytes), room, budget}), 0);
        if (n > 0) {
            if (!c.message_started) {
                c.message_started = true; c.phase_start = c.request_start = Clock::now();
            }
            c.last_activity = Clock::now();
            c.input.append(bytes, static_cast<size_t>(n)); metrics_.bytes_received += static_cast<uint64_t>(n);
            budget -= static_cast<size_t>(n);
            parse(c);
        } else if (n == 0) {
            c.peer_eof = true;
            if (!c.unread().empty()) respond(c, HttpResponse::text(400, "incomplete request\n"), true);
            else close(c.id);
            return;
        } else {
            if (errno == EINTR) continue;
            if (errno == EAGAIN || errno == EWOULDBLOCK) break;
            close(c.id); return;
        }
    }
    // MOD rechecks readiness even in ET mode when a fairness budget stops before EAGAIN.
    arm(c);
}
void Reactor::parse(Connection& c) {
    auto was_body = c.parser.reading_body();
    auto result = c.parser.parse(c.unread());
    if (!was_body && c.parser.reading_body()) c.phase_start = Clock::now();
    if (result.state == ParseState::incomplete) return;
    if (result.state == ParseState::error) {
        ++metrics_.parse_errors;
        respond(c, HttpResponse::text(result.status, std::string(reason_phrase(result.status)) + "\n"), true);
        return;
    }
    auto request = c.parser.take_request();
    c.consume(result.consumed);
    c.method = request.method; c.head = request.method == "HEAD";
    c.route = Router::route_label(request.path);
    c.close_after = !request.keep_alive || ++c.requests >= config_.max_requests || draining_;
    c.state = ConnectionState::processing; c.phase_start = Clock::now();
    const auto id = c.id;
    // Count completions as outstanding until consumed: disconnect/reconnect churn cannot
    // grow the completion queue beyond the same global admission budget.
    if (outstanding_ >= config_.queue_capacity + config_.workers) {
        ++metrics_.rejections; respond(c, HttpResponse::text(503, "worker capacity exhausted\n"), true); return;
    }
    ++outstanding_;
    auto accepted = pool_.submit([this, id, q = std::move(request)] {
        HttpResponse response;
        try { response = router_.dispatch(q); }
        catch (...) { ++metrics_.handler_errors; response = HttpResponse::text(500, "handler failed\n"); }
        {
            std::lock_guard lock(completion_mutex_);
            completed_.push_back({id, std::move(response)});
        }
        uint64_t one = 1;
        while (::write(wake_.get(), &one, sizeof(one)) < 0 && errno == EINTR) {}
    });
    if (!accepted) {
        --outstanding_; ++metrics_.rejections;
        respond(c, HttpResponse::text(503, "worker queue full\n"), true);
    }
    metrics_.queue_depth = pool_.depth();
    arm(c);
}
void Reactor::respond(Connection& c, HttpResponse response, bool close_after) {
    c.close_after = c.close_after || close_after || draining_ || c.peer_eof;
    auto status = response.status;
    try { c.output = response.serialize(c.head, c.close_after); }
    catch (...) {
        ++metrics_.handler_errors; status = 500; response = HttpResponse::text(500, "invalid handler response\n");
        c.close_after = true; c.output = response.serialize(c.head, true);
    }
    if (c.output.size() > config_.max_output_bytes) {
        status = 503; c.close_after = true;
        response = HttpResponse::text(503, "output buffer limit\n"); c.output = response.serialize(c.head, true);
    }
    c.file = c.head || response.status == 204 || response.status == 304 ? nullptr : std::move(response.file);
    c.file_offset = 0; c.write_offset = 0;
    c.state = ConnectionState::writing; c.phase_start = c.last_activity = Clock::now();
    const double duration = std::chrono::duration<double>(Clock::now() - c.request_start).count();
    metrics_.observe(duration);
    if (config_.access_log) {
        auto timestamp = std::chrono::duration_cast<std::chrono::milliseconds>(std::chrono::system_clock::now().time_since_epoch()).count();
        // Methods are tokens, route labels are a fixed vocabulary; no raw untrusted JSON fields.
        logger_.write("{\"timestamp_ms\":" + std::to_string(timestamp) + ",\"level\":\"info\",\"connection_id\":" + std::to_string(c.id) +
            ",\"request_id\":" + std::to_string(c.requests) + ",\"method\":\"" + c.method + "\",\"route\":\"" + c.route +
            "\",\"status\":" + std::to_string(status) + ",\"duration_seconds\":" + std::to_string(duration) +
            ",\"error_category\":\"" + (status >= 400 ? "http_error" : "none") + "\"}");
    }
    arm(c);
}
void Reactor::completions() {
    uint64_t value;
    while (::read(wake_.get(), &value, sizeof(value)) < 0 && errno == EINTR) {}
    std::deque<Completion> batch;
    { std::lock_guard lock(completion_mutex_); batch.swap(completed_); }
    for (auto& completion : batch) {
        --outstanding_;
        auto it = connections_.find(completion.id);
        if (it == connections_.end() || it->second->state != ConnectionState::processing) {
            ++metrics_.stale_completions; continue;
        }
        respond(*it->second, std::move(completion.response));
    }
    metrics_.queue_depth = pool_.depth();
}
void Reactor::write(Connection& c) {
    size_t budget = 256 * 1024;
    while (budget > 0) {
        ssize_t n;
        if (c.write_offset < c.output.size()) {
            n = ::send(c.fd.get(), c.output.data() + c.write_offset, std::min(budget, c.output.size() - c.write_offset), MSG_NOSIGNAL);
            if (n > 0) c.write_offset += static_cast<size_t>(n);
        } else if (c.file && static_cast<size_t>(c.file_offset) < c.file->size) {
            // A file growing during transfer cannot exceed its originally advertised length.
            n = ::sendfile(c.fd.get(), c.file->fd.get(), &c.file_offset, std::min(budget, c.file->size - static_cast<size_t>(c.file_offset)));
            if (n == 0) { ++metrics_.write_errors; close(c.id); return; }
        } else {
            c.file.reset(); c.output.clear(); c.write_offset = 0;
            if (c.close_after || draining_) { close(c.id); return; }
            c.state = ConnectionState::reading; c.message_started = !c.unread().empty();
            c.phase_start = c.request_start = Clock::now();
            if (c.message_started) parse(c);
            arm(c);
            return;
        }
        if (n > 0) {
            budget -= static_cast<size_t>(n); metrics_.bytes_sent += static_cast<uint64_t>(n);
            c.last_activity = Clock::now();
        } else if (n < 0 && errno == EINTR) continue;
        else if (n < 0 && (errno == EAGAIN || errno == EWOULDBLOCK)) break;
        else { ++metrics_.write_errors; close(c.id); return; }
    }
    arm(c);
}
void Reactor::begin_shutdown() {
    if (draining_) return;
    draining_ = true; ready_ = false; drain_start_ = Clock::now();
    ::epoll_ctl(epoll_.get(), EPOLL_CTL_DEL, listener_.get(), nullptr); listener_.reset();
    std::vector<uint64_t> idle;
    for (auto& [id, c] : connections_) {
        if (c->state == ConnectionState::reading) idle.push_back(id);
        else c->close_after = true;
    }
    for (auto id : idle) close(id);
}
void Reactor::deadlines() {
    const auto now = Clock::now();
    std::vector<uint64_t> expired;
    for (auto& [id, c] : connections_) {
        auto idle = std::chrono::duration_cast<std::chrono::milliseconds>(now - c->last_activity).count();
        auto phase = std::chrono::duration_cast<std::chrono::milliseconds>(now - c->phase_start).count();
        if (draining_ && now - drain_start_ >= std::chrono::milliseconds(config_.shutdown_timeout_ms)) { expired.push_back(id); continue; }
        if (c->state == ConnectionState::processing && phase >= config_.processing_timeout_ms) {
            ++metrics_.timeouts; respond(*c, HttpResponse::text(504, "processing timeout\n"), true);
        } else if (c->state == ConnectionState::reading && c->message_started &&
                   phase >= (c->parser.reading_body() ? config_.body_timeout_ms : config_.header_timeout_ms)) {
            ++metrics_.timeouts; respond(*c, HttpResponse::text(408, "request timeout\n"), true);
        } else if (idle >= config_.idle_timeout_ms && c->state != ConnectionState::processing) {
            ++metrics_.timeouts; expired.push_back(id);
        }
    }
    for (auto id : expired) close(id);
    metrics_.queue_depth = pool_.depth();
    if (listener_) {
        // Re-add only if descriptor exhaustion temporarily removed the listener.
        epoll_event event{}; event.events = EPOLLIN; event.data.u64 = 1;
        ::epoll_ctl(epoll_.get(), EPOLL_CTL_ADD, listener_.get(), &event);
    }
}
void Reactor::run() {
    epoll_event events[128];
    auto next_tick = Clock::now();
    while (!draining_ || !connections_.empty()) {
        if (stop_.load(std::memory_order_relaxed)) begin_shutdown();
        int n = ::epoll_wait(epoll_.get(), events, 128, 50);
        if (n < 0) { if (errno == EINTR) continue; check(false, "epoll_wait"); }
        for (int i = 0; i < n; ++i) {
            const auto id = events[i].data.u64;
            if (id == 1) { if (!draining_) accept_connections(); continue; }
            if (id == 2) { completions(); continue; }
            if (id == 3) {
                signalfd_siginfo info{};
                while (::read(signal_.get(), &info, sizeof(info)) > 0) {}
                begin_shutdown(); continue;
            }
            auto it = connections_.find(id);
            if (it == connections_.end()) continue;
            auto& c = *it->second;
            if (events[i].events & (EPOLLERR | EPOLLHUP)) { close(id); continue; }
            if (c.state == ConnectionState::reading && events[i].events & (EPOLLIN | EPOLLRDHUP)) read(c);
            else if (c.state == ConnectionState::writing && events[i].events & EPOLLOUT) write(c);
        }
        if (Clock::now() >= next_tick) { deadlines(); next_tick = Clock::now() + std::chrono::milliseconds(50); }
    }
    // C++ cannot forcibly cancel an arbitrary application handler. Built-in compute is bounded.
    pool_.shutdown();
    completions();
}
} // namespace pulsehttp
