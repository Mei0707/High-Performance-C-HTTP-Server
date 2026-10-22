#include "pulsehttp/thread_pool.h"
#include <stdexcept>

namespace pulsehttp {
ThreadPool::ThreadPool(size_t threads, size_t capacity) : capacity_(capacity) {
    if (!threads || !capacity) throw std::invalid_argument("thread pool requires positive capacity and workers");
    try {
        for (size_t i = 0; i < threads; ++i) workers_.emplace_back([this] { run(); });
    } catch (...) { shutdown(); throw; }
}
ThreadPool::~ThreadPool() { shutdown(); }
bool ThreadPool::submit(Task task) {
    {
        std::lock_guard lock(mutex_);
        if (stopping_ || tasks_.size() >= capacity_) return false;
        tasks_.push_back(std::move(task));
    }
    cv_.notify_one();
    return true;
}
size_t ThreadPool::depth() const { std::lock_guard lock(mutex_); return tasks_.size(); }
void ThreadPool::shutdown() {
    { std::lock_guard lock(mutex_); stopping_ = true; }
    cv_.notify_all();
    for (auto& worker : workers_) if (worker.joinable()) worker.join();
}
void ThreadPool::run() {
    for (;;) {
        Task task;
        {
            std::unique_lock lock(mutex_);
            cv_.wait(lock, [this] { return stopping_ || !tasks_.empty(); });
            if (tasks_.empty()) return;
            task = std::move(tasks_.front()); tasks_.pop_front();
        }
        try { task(); } catch (...) { /* A failed task must not kill a worker. */ }
    }
}
} // namespace pulsehttp
