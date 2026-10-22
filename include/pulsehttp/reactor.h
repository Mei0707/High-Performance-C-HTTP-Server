#pragma once
#include "pulsehttp/connection.h"
#include "pulsehttp/logger.h"
#include "pulsehttp/router.h"
#include "pulsehttp/thread_pool.h"
#include <deque>
#include <unordered_map>

namespace pulsehttp {
class Reactor {
public:
    Reactor(const Config& config, const Router& router, Metrics& metrics,
            std::atomic<bool>& ready, const std::atomic<bool>& stop);
    ~Reactor();
    void run();
private:
    struct Completion { uint64_t id; HttpResponse response; };
    void watch(int operation, int fd, uint64_t token, uint32_t events);
    void arm(Connection& c);
    void accept_connections();
    void read(Connection& c);
    void parse(Connection& c);
    void write(Connection& c);
    void respond(Connection& c, HttpResponse response, bool close = false);
    void completions();
    void close(uint64_t id);
    void deadlines();
    void begin_shutdown();
    Config config_;
    const Router& router_;
    Metrics& metrics_;
    std::atomic<bool>& ready_;
    const std::atomic<bool>& stop_;
    AsyncLogger logger_;
    UniqueFd epoll_, wake_, signal_, listener_;
    std::unordered_map<uint64_t, std::unique_ptr<Connection>> connections_;
    uint64_t next_id_ = 10;
    size_t outstanding_ = 0;
    bool draining_ = false;
    Clock::time_point drain_start_;
    std::mutex completion_mutex_;
    std::deque<Completion> completed_;
    // Last member: joins before completion queues, descriptors, and logger are destroyed.
    ThreadPool pool_;
};
} // namespace pulsehttp
