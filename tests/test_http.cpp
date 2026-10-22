#include "pulsehttp/http_parser.h"
#include "pulsehttp/http_response.h"
#include <gtest/gtest.h>

using namespace pulsehttp;

TEST(Parser, FragmentedRequestAndBinaryBody) {
    std::string request = "POST /api/echo?q=1 HTTP/1.1\r\nHost: localhost\r\nContent-Length: 4\r\n\r\n";
    request.append("a\0b\n", 4);
    HttpParser parser;
    for (size_t i = 0; i < request.size(); ++i)
        EXPECT_EQ(parser.parse(std::string_view(request).substr(0, i)).state, ParseState::incomplete) << i;
    auto result = parser.parse(request);
    ASSERT_EQ(result.state, ParseState::complete);
    EXPECT_EQ(result.consumed, request.size());
    auto q = parser.take_request();
    EXPECT_EQ(q.method, "POST"); EXPECT_EQ(q.path, "/api/echo");
    EXPECT_EQ(q.body, std::string("a\0b\n", 4)); EXPECT_TRUE(q.keep_alive);
}
TEST(Parser, PipelinedRequestsAndConnectionTokens) {
    std::string one = "GET /hello HTTP/1.1\r\nHost: x\r\n\r\n";
    std::string two = "HEAD /health HTTP/1.1\r\nHost: x\r\nConnection: Keep-Alive, CLOSE\r\n\r\n";
    HttpParser p;
    auto r = p.parse(one + two);
    ASSERT_EQ(r.state, ParseState::complete); EXPECT_EQ(r.consumed, one.size());
    EXPECT_EQ(p.take_request().path, "/hello");
    ASSERT_EQ(p.parse(two).state, ParseState::complete);
    auto q = p.take_request(); EXPECT_FALSE(q.keep_alive); EXPECT_EQ(q.method, "HEAD");
}
struct BadRequest { const char* wire; int status; };
class Framing : public testing::TestWithParam<BadRequest> {};
TEST_P(Framing, RejectsAndRemembersError) {
    const auto [wire, status] = GetParam();
    HttpParser p;
    auto r = p.parse(wire);
    EXPECT_EQ(r.state, ParseState::error) << wire; EXPECT_EQ(r.status, status);
    EXPECT_EQ(p.parse(wire).status, status);
}
INSTANTIATE_TEST_SUITE_P(StrictHTTP, Framing, testing::Values(
    BadRequest{"GET / HTTP/1.1\r\n\r\n", 400},
    BadRequest{"GET / HTTP/1.1\r\nHost: x\r\nHost: x\r\n\r\n", 400},
    BadRequest{"GET / HTTP/1.1\r\nHost:\r\n\r\n", 400},
    BadRequest{"GET / HTTP/1.1\r\nHost: x, y\r\n\r\n", 400},
    BadRequest{"GET / HTTP/1.1\r\nHost: x\r\nContent-Length: 1\r\nContent-Length: 1\r\n\r\na", 400},
    BadRequest{"GET / HTTP/1.1\r\nHost: x\r\nContent-Length: 1, 1\r\n\r\na", 400},
    BadRequest{"GET / HTTP/1.1\r\nHost: x\r\nContent-Length: -1\r\n\r\n", 400},
    BadRequest{"GET / HTTP/1.1\r\nHost: x\r\nContent-Length: +1\r\n\r\n", 400},
    BadRequest{"GET / HTTP/1.1\r\nHost: x\r\nContent-Length: 1x\r\n\r\n", 400},
    BadRequest{"GET / HTTP/1.1\r\nHost: x\r\nContent-Length: 99999999999999999999999999\r\n\r\n", 400},
    BadRequest{"GET / HTTP/1.1\r\nHost: x\r\nContent-Length: 1048577\r\n\r\n", 413},
    BadRequest{"GET / HTTP/1.1\r\nHost: x\r\nTransfer-Encoding: chunked\r\n\r\n", 501},
    BadRequest{"GET / HTTP/1.1\r\nHost: x\r\nTransfer-Encoding: identity\r\nContent-Length: 0\r\n\r\n", 400},
    BadRequest{"GET / HTTP/1.1\r\nHost: x\r\nExpect: 100-continue\r\n\r\n", 417},
    BadRequest{"GET / HTTP/1.1\r\nHost : x\r\n\r\n", 400},
    BadRequest{"GET / HTTP/1.1\r\nHost: x\r\n folded: value\r\n\r\n", 400},
    BadRequest{"GET / HTTP/1.1\r\nHost: x\r\nX: a\nb\r\n\r\n", 400},
    BadRequest{"GET / HTTP/1.0\r\nHost: x\r\n\r\n", 505},
    BadRequest{"GET / HTTP/9.9\r\nHost: x\r\n\r\n", 400},
    BadRequest{"GET  / HTTP/1.1\r\nHost: x\r\n\r\n", 400},
    BadRequest{"GET http://x/ HTTP/1.1\r\nHost: x\r\n\r\n", 400},
    BadRequest{"GET /#fragment HTTP/1.1\r\nHost: x\r\n\r\n", 400}
));
TEST(Parser, HeaderLimitsAndNul) {
    HttpParser p(64, 4);
    EXPECT_EQ(p.parse(std::string(64, 'x')).status, 431);
    HttpParser q;
    std::string wire = "GET / HTTP/1.1\r\nHost: x\r\nX: a";
    wire.push_back('\0'); wire += "b\r\n\r\n";
    EXPECT_EQ(q.parse(wire).status, 400);
    HttpParser r(64, 4);
    EXPECT_EQ(r.parse("POST / HTTP/1.1\r\nHost: x\r\nContent-Length: 5\r\n\r\n").status, 413);
}
TEST(Parser, HeaderLimitDoesNotCountPipelinedBytes) {
    const std::string one = "GET / HTTP/1.1\r\nHost: x\r\n\r\n";
    HttpParser p(one.size(), 1000);
    EXPECT_EQ(p.parse(one + std::string(1000, 'x')).state, ParseState::complete);
}
TEST(Response, HeadAndBinaryLength) {
    auto r = HttpResponse::text(200, std::string("a\0b", 3));
    auto head = r.serialize(true, true);
    EXPECT_NE(head.find("Content-Length: 3\r\n"), std::string::npos);
    EXPECT_NE(head.find("Connection: close\r\n"), std::string::npos);
    EXPECT_TRUE(head.ends_with("\r\n\r\n"));
    EXPECT_EQ(r.serialize(false, false).size(), r.serialize(true, false).size() + 3);
}
TEST(Response, RejectsResponseSplittingAndFramingOverrides) {
    auto r = HttpResponse::text(200, "x");
    r.headers.emplace_back("X-Test", "ok\r\nInjected: value");
    EXPECT_THROW(r.serialize(false, false), std::invalid_argument);
    r.headers = {{"Content-Length", "100"}};
    EXPECT_THROW(r.serialize(false, false), std::invalid_argument);
    r.headers.clear(); r.status = 204;
    EXPECT_TRUE(r.serialize(false, false).ends_with("\r\n\r\n"));
    EXPECT_EQ(r.serialize(false, false).find("Content-Length"), std::string::npos);
}
