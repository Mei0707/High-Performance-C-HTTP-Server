#include "pulsehttp/config.h"
#include "pulsehttp/router.h"
#include "pulsehttp/socket.h"
#include "pulsehttp/thread_pool.h"
#include <gtest/gtest.h>
#include <atomic>
#include <fcntl.h>
#include <future>
#include <latch>

using namespace pulsehttp;

TEST(ThreadPool, BoundedQueueAndDrainingShutdown) {
    ThreadPool pool(1, 1);
    std::promise<void> release, started;
    auto gate = release.get_future().share();
    std::atomic<int> count = 0;
    ASSERT_TRUE(pool.submit([&] { started.set_value(); gate.wait(); ++count; }));
    started.get_future().wait();
    EXPECT_TRUE(pool.submit([&] { ++count; }));
    EXPECT_FALSE(pool.submit([] {}));
    EXPECT_EQ(pool.depth(), 1);
    release.set_value(); pool.shutdown();
    EXPECT_EQ(count, 2); EXPECT_EQ(pool.depth(), 0);
    EXPECT_FALSE(pool.submit([] {}));
    pool.shutdown();
}
TEST(ThreadPool, ExceptionsDoNotRemoveWorkers) {
    ThreadPool pool(1, 4);
    std::promise<void> finished;
    ASSERT_TRUE(pool.submit([] { throw std::runtime_error("test"); }));
    ASSERT_TRUE(pool.submit([&] { finished.set_value(); }));
    EXPECT_EQ(finished.get_future().wait_for(std::chrono::seconds(2)), std::future_status::ready);
    pool.shutdown();
}
TEST(ThreadPool, ConcurrentProducersAndConsumers) {
    ThreadPool pool(4, 64);
    std::atomic<size_t> accepted{0}, completed{0};
    std::vector<std::thread> producers;
    for (int p = 0; p < 8; ++p) producers.emplace_back([&] {
        for (int i = 0; i < 500; ++i) if (pool.submit([&] { ++completed; })) ++accepted;
    });
    for (auto& p : producers) p.join();
    pool.shutdown(); EXPECT_EQ(accepted.load(), completed.load()); EXPECT_GT(accepted, 0);
}
TEST(Fd, MoveAndCloseExactlyOnce) {
    UniqueFd first(::open("/dev/null", O_RDONLY));
    ASSERT_TRUE(bool(first)); int n = first.get();
    { UniqueFd second(std::move(first)); EXPECT_FALSE(bool(first)); EXPECT_EQ(second.get(), n); }
    EXPECT_EQ(::fcntl(n, F_GETFD), -1); EXPECT_EQ(errno, EBADF);
}
TEST(Config, ValidatesCrossFieldLimits) {
    Config c; EXPECT_NO_THROW(c.validate());
    c.workers = 0; EXPECT_THROW(c.validate(), std::invalid_argument);
    c.workers = 1; c.max_input_bytes = 10; EXPECT_THROW(c.validate(), std::invalid_argument);
}
TEST(Metrics, HistogramBucketsAndCounters) {
    Metrics m; m.observe(.002); m.observe(.2);
    EXPECT_EQ(m.requests, 2); EXPECT_EQ(m.buckets[0], 0); EXPECT_EQ(m.buckets[3], 1); EXPECT_EQ(m.buckets[7], 2);
    EXPECT_NE(m.render().find("http_request_duration_seconds_count 2\n"), std::string::npos);
}
TEST(Router, MethodSemanticsAndImmutableRoutes) {
    Config c; c.document_root = "/tmp";
    Metrics m; std::atomic<bool> ready{true}; Router router(c, m, ready);
    HttpRequest q; q.method = "HEAD"; q.path = "/hello"; q.target = q.path;
    EXPECT_EQ(router.dispatch(q).status, 200);
    q.method = "POST"; auto r = router.dispatch(q); EXPECT_EQ(r.status, 405); EXPECT_FALSE(r.headers.empty());
    q.method = "MYSTERY"; EXPECT_EQ(router.dispatch(q).status, 501);
    router.freeze(); EXPECT_THROW(router.add_route("GET", "/x", [](auto&) { return HttpResponse{}; }), std::logic_error);
    ready = false; q.method = "GET"; q.path = "/ready"; EXPECT_EQ(router.dispatch(q).status, 503);
}
