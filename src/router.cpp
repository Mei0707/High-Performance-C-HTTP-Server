#include "pulsehttp/router.h"
#include "pulsehttp/http_parser.h"
#include <charconv>
#include <fcntl.h>
#include <stdexcept>
#include <sys/stat.h>

namespace pulsehttp {
Router::Router(const Config& config, Metrics& metrics, const std::atomic<bool>& ready)
    : config_(config), root_(::open(config.document_root.c_str(), O_RDONLY | O_DIRECTORY | O_CLOEXEC)) {
    if (!root_) throw std::runtime_error("cannot open document root: " + config.document_root);
    add_route("GET", "/health", [](const auto&) {
        auto r = HttpResponse::text(200, "{\"status\":\"ok\"}\n"); r.content_type = "application/json"; return r;
    });
    add_route("GET", "/ready", [&ready](const auto&) {
        const bool available = ready.load();
        return HttpResponse::text(available ? 200 : 503, available ? "ready\n" : "draining\n");
    });
    add_route("GET", "/hello", [](const auto&) { return HttpResponse::text(200, "Hello from PulseHTTP!\n"); });
    add_route("POST", "/api/echo", [](const auto& q) {
        auto r = HttpResponse::text(200, q.body); r.content_type = "application/octet-stream"; return r;
    });
    add_route("GET", "/metrics", [&metrics](const auto&) {
        auto r = HttpResponse::text(200, metrics.render()); r.content_type = "text/plain; version=0.0.4"; return r;
    });
    add_route("GET", "/api/compute", [](const HttpRequest& q) {
        uint64_t iterations = 100000;
        auto at = q.target.find('?');
        if (at != std::string::npos) {
            std::string_view args(q.target.data() + at + 1, q.target.size() - at - 1);
            if (!args.starts_with("iterations=")) return HttpResponse::text(400, "use ?iterations=1..20000000\n");
            args.remove_prefix(11);
            auto [ptr, ec] = std::from_chars(args.data(), args.data() + args.size(), iterations);
            if (ec != std::errc{} || ptr != args.data() + args.size() || iterations == 0 || iterations > 20000000)
                return HttpResponse::text(400, "iterations out of range\n");
        }
        uint64_t x = 0x123456789abcdefULL;
        for (uint64_t i = 0; i < iterations; ++i) { x ^= x << 13; x ^= x >> 7; x ^= x << 17; }
        return HttpResponse::text(200, std::to_string(x) + "\n");
    });
}
void Router::add_route(std::string method, std::string path, Handler handler) {
    if (frozen_) throw std::logic_error("routes are immutable after startup");
    if (!http_token(method) || path.empty() || path.front() != '/' || !handler)
        throw std::invalid_argument("invalid route");
    routes_.insert_or_assign({std::move(method), std::move(path)}, std::move(handler));
}
HttpResponse Router::dispatch(const HttpRequest& q) const {
    if (q.method != "GET" && q.method != "HEAD" && q.method != "POST" && q.method != "PUT" &&
        q.method != "DELETE" && q.method != "OPTIONS" && q.method != "PATCH" && q.method != "TRACE" && q.method != "CONNECT")
        return HttpResponse::text(501, "unknown method\n");
    const auto method = q.method == "HEAD" ? "GET" : q.method;
    auto it = routes_.find({method, q.path});
    if (it != routes_.end()) return it->second(q);
    if (q.path.starts_with("/static/")) {
        if (method == "GET") return static_file(q);
        auto r = HttpResponse::text(405, "method not allowed\n"); r.headers.emplace_back("Allow", "GET, HEAD"); return r;
    }
    std::string allow;
    for (const auto& [key, handler] : routes_) {
        (void)handler;
        if (key.second != q.path) continue;
        if (!allow.empty()) allow += ", ";
        allow += key.first == "GET" ? "GET, HEAD" : key.first;
    }
    if (!allow.empty()) {
        auto r = HttpResponse::text(405, "method not allowed\n"); r.headers.emplace_back("Allow", allow); return r;
    }
    return HttpResponse::text(404, "not found\n");
}
static int hex(char c) {
    if (c >= '0' && c <= '9') return c - '0';
    if (c >= 'a' && c <= 'f') return c - 'a' + 10;
    if (c >= 'A' && c <= 'F') return c - 'A' + 10;
    return -1;
}
HttpResponse Router::static_file(const HttpRequest& q) const {
    std::string path;
    auto encoded = std::string_view(q.path).substr(8);
    for (size_t i = 0; i < encoded.size(); ++i) {
        unsigned char c = static_cast<unsigned char>(encoded[i]);
        if (c == '%') {
            if (i + 2 >= encoded.size() || hex(encoded[i+1]) < 0 || hex(encoded[i+2]) < 0)
                return HttpResponse::text(400, "invalid path escape\n");
            c = static_cast<unsigned char>(hex(encoded[i+1]) * 16 + hex(encoded[i+2])); i += 2;
        }
        if (c < 32 || c == 127 || c == '\\') return HttpResponse::text(403, "forbidden path\n");
        path += static_cast<char>(c);
    }
    UniqueFd dir(::fcntl(root_.get(), F_DUPFD_CLOEXEC, 0));
    if (!dir) return HttpResponse::text(500, "file open failed\n");
    std::string_view remaining(path);
    for (;;) {
        auto slash = remaining.find('/');
        std::string part(remaining.substr(0, slash));
        if (part.empty() || part == "." || part == "..") return HttpResponse::text(403, "forbidden path\n");
        bool last = slash == std::string_view::npos;
        UniqueFd file(::openat(dir.get(), part.c_str(), O_RDONLY | O_CLOEXEC | O_NOFOLLOW | O_NONBLOCK | (last ? 0 : O_DIRECTORY)));
        if (!file) return HttpResponse::text(404, "file not found\n");
        if (!last) { dir = std::move(file); remaining.remove_prefix(slash + 1); continue; }
        struct stat st{};
        if (::fstat(file.get(), &st) != 0 || !S_ISREG(st.st_mode)) return HttpResponse::text(403, "not a regular file\n");
        if (st.st_size < 0 || static_cast<uint64_t>(st.st_size) > config_.max_file_bytes)
            return HttpResponse::text(413, "file too large\n");
        HttpResponse r;
        auto dot = part.rfind('.');
        auto ext = dot == std::string::npos ? "" : ascii_lower(std::string_view(part).substr(dot));
        if (ext == ".html") r.content_type = "text/html; charset=utf-8";
        else if (ext == ".css") r.content_type = "text/css";
        else if (ext == ".js") r.content_type = "text/javascript";
        else if (ext == ".json") r.content_type = "application/json";
        else if (ext == ".txt") r.content_type = "text/plain; charset=utf-8";
        else if (ext == ".svg") r.content_type = "image/svg+xml";
        else if (ext == ".png") r.content_type = "image/png";
        else if (ext == ".jpg" || ext == ".jpeg") r.content_type = "image/jpeg";
        else r.content_type = "application/octet-stream";
        r.headers.emplace_back("X-Content-Type-Options", "nosniff");
        if (config_.sendfile) r.file = std::make_shared<FileBody>(std::move(file), static_cast<size_t>(st.st_size));
        else {
            if (static_cast<uint64_t>(st.st_size) + 512 > config_.max_output_bytes)
                return HttpResponse::text(413, "buffered file exceeds output limit; use sendfile\n");
            r.body.resize(static_cast<size_t>(st.st_size));
            size_t offset = 0;
            while (offset < r.body.size()) {
                auto n = ::read(file.get(), r.body.data() + offset, r.body.size() - offset);
                if (n < 0 && errno == EINTR) continue;
                if (n <= 0) return HttpResponse::text(500, "file changed during read\n");
                offset += static_cast<size_t>(n);
            }
        }
        return r;
    }
}
std::string Router::route_label(std::string_view path) {
    if (path.starts_with("/static/")) return "/static/*";
    for (auto p : {"/health", "/ready", "/hello", "/api/echo", "/api/compute", "/metrics"})
        if (path == p) return p;
    return "unmatched";
}
} // namespace pulsehttp
