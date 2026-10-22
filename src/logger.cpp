#include "pulsehttp/logger.h"
#include <iostream>

namespace pulsehttp {
AsyncLogger::AsyncLogger(bool enabled, Metrics& metrics) : enabled_(enabled), metrics_(metrics) {
    if (!enabled_) return;
    thread_ = std::thread([this] {
        for (;;) {
            std::string line;
            {
                std::unique_lock lock(mutex_);
                cv_.wait(lock, [this] { return stopping_ || !queue_.empty(); });
                if (queue_.empty()) return;
                line = std::move(queue_.front()); queue_.pop_front();
            }
            std::cerr << line << '\n';
        }
    });
}
AsyncLogger::~AsyncLogger() {
    { std::lock_guard lock(mutex_); stopping_ = true; }
    cv_.notify_one();
    if (thread_.joinable()) thread_.join();
}
void AsyncLogger::write(std::string line) {
    if (!enabled_) return;
    {
        std::unique_lock lock(mutex_, std::try_to_lock);
        if (!lock || queue_.size() >= 1024) { ++metrics_.log_drops; return; }
        queue_.push_back(std::move(line));
    }
    cv_.notify_one();
}
} // namespace pulsehttp
