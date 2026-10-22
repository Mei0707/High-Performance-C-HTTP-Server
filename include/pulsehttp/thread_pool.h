#pragma once
#include <condition_variable>
#include <deque>
#include <functional>
#include <mutex>
#include <thread>
#include <vector>

namespace pulsehttp {
class ThreadPool {
public:
    using Task = std::function<void()>;
    ThreadPool(size_t threads, size_t capacity);
    ~ThreadPool();
    bool submit(Task task);
    // Drains accepted tasks, rejects new submissions, joins workers. Call from owner only.
    void shutdown();
    size_t depth() const;
private:
    void run();
    size_t capacity_;
    mutable std::mutex mutex_;
    std::condition_variable cv_;
    std::deque<Task> tasks_;
    bool stopping_ = false;
    std::vector<std::thread> workers_;
};
} // namespace pulsehttp
