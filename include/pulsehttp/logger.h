#pragma once
#include "pulsehttp/metrics.h"
#include <condition_variable>
#include <deque>
#include <mutex>
#include <string>
#include <thread>

namespace pulsehttp {
class AsyncLogger {
public:
    AsyncLogger(bool enabled, Metrics& metrics);
    ~AsyncLogger();
    void write(std::string line);
private:
    bool enabled_, stopping_ = false;
    Metrics& metrics_;
    std::mutex mutex_;
    std::condition_variable cv_;
    std::deque<std::string> queue_;
    std::thread thread_;
};
} // namespace pulsehttp
