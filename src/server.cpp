#include "pulsehttp/server.h"
#include "pulsehttp/reactor.h"
#include <csignal>
#include <pthread.h>
#include <stdexcept>

namespace pulsehttp {
Server::Server(Config c) : config_(std::move(c)), router_(config_, metrics_, ready_) { config_.validate(); }
void Server::add_route(std::string method, std::string path, Handler handler) {
    if (started_) throw std::logic_error("register routes before run()");
    router_.add_route(std::move(method), std::move(path), std::move(handler));
}
void Server::run() {
    if (started_.exchange(true)) throw std::logic_error("server is single-use");
    router_.freeze();
    // Block before creating any server-owned threads; signalfd consumes process signals.
    sigset_t mask, old;
    sigemptyset(&mask); sigaddset(&mask, SIGINT); sigaddset(&mask, SIGTERM);
    if (pthread_sigmask(SIG_BLOCK, &mask, &old) != 0) throw std::runtime_error("pthread_sigmask");
    struct Restore { sigset_t old; ~Restore() { pthread_sigmask(SIG_SETMASK, &old, nullptr); } } restore{old};
    Reactor reactor(config_, router_, metrics_, ready_, stop_);
    reactor.run();
}
} // namespace pulsehttp
