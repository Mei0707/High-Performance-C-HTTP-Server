#include "pulsehttp/http_parser.h"
#include <cstddef>
#include <cstdint>
#include <cstdlib>

extern "C" int LLVMFuzzerTestOneInput(const uint8_t* data, size_t size) {
    if (size > 131072) return 0;
    const std::string_view wire(reinterpret_cast<const char*>(data), size);
    pulsehttp::HttpParser whole(4096, 65536), fragmented(4096, 65536);
    const auto expected = whole.parse(wire);
    pulsehttp::ParseResult actual;
    size_t n = 0;
    do {
        n = std::min(size, n + 1 + (size ? data[n % size] : 0));
        actual = fragmented.parse(wire.substr(0, n));
        if (actual.state != pulsehttp::ParseState::incomplete) break;
    } while (n < size);
    if (actual.state != expected.state || actual.status != expected.status || actual.consumed != expected.consumed)
        std::abort();
    if (actual.state == pulsehttp::ParseState::complete) {
        auto a = whole.take_request(), b = fragmented.take_request();
        if (a.method != b.method || a.path != b.path || a.body != b.body || a.keep_alive != b.keep_alive)
            std::abort();
        // Continue through any pipeline using the consumed byte count.
        size_t offset = actual.consumed;
        while (offset < size) {
            auto result = whole.parse(wire.substr(offset));
            if (result.state != pulsehttp::ParseState::complete) break;
            offset += result.consumed;
            (void)whole.take_request();
        }
    }
    return 0;
}
