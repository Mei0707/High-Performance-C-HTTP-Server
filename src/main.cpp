#include "pulsehttp/server.h"
#include <csignal>
#include <iostream>
#include <string_view>

int main(int argc, char** argv) {
    if (argc == 2 && std::string_view(argv[1]) == "--help") {
        std::cout << pulsehttp::Config::usage(); return 0;
    }
    ::signal(SIGPIPE, SIG_IGN);
    try {
        pulsehttp::Server server(pulsehttp::Config::from_args(argc, argv));
        server.run();
    } catch (const std::exception& e) { std::cerr << "PulseHTTP: " << e.what() << '\n'; return 1; }
}
