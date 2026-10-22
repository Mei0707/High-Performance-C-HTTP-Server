# Supported protocol contract

PulseHTTP implements a documented HTTP/1.1 subset. Request targets must be origin-form
(`/path?query`), with exactly two separating spaces in the request line. Methods and field
names use HTTP token syntax. Header names are case insensitive, values reject controls
except HTAB, and obsolete folding is rejected. A single nonempty Host is required. Header
NULs are rejected; request bodies are binary safe.

Content-Length is an unsigned decimal number with no signs, internal whitespace or lists.
Every duplicate is rejected, even if identical. Transfer-Encoding plus Content-Length is
400. Any transfer coding without a length is 501, including chunked. Expect is 417; no
interim 100 response is emitted. Errors close the connection, leaving no ambiguous bytes
to interpret as another request. Requests without framing have zero-length bodies. Bytes
following a complete request are the next pipelined request, rather than unframed body data.

HTTP/1.1 persists unless a Connection token requests close, the request cap is reached, a
limit/error forces close, or shutdown begins. One response finishes before the next handler
starts. Client write-half-close still permits a valid request's response. An incomplete
message at EOF is rejected. 1.0 and 2.0 request-line versions receive 505; malformed versions
receive 400.

GET and POST handlers are built in. HEAD uses the GET handler and preserves representation
length while omitting its body. Known methods on a route that does not support them receive
405 with Allow. Unknown methods receive 501. Missing routes receive 404. Response headers
use CRLF and a computed byte length; application headers cannot override framing or contain
CR/LF. 204/304 omit content and framing. No upgrades, ranges, chunking, compression,
trailers, absolute-form proxy targets, multipart helpers or TLS are implemented.

Status policy: oversized headers 431; bodies, input limits and file size 413; unavailable
worker/output capacity 503; malformed syntax/framing 400; header/body deadlines 408;
processing deadline 504. Idle/writing deadlines close the socket. A partial output failure
closes immediately rather than appending a second HTTP response.

Static paths are percent-decoded once. Empty, dot, parent and backslash components are
rejected. Every component is opened relative to the previous directory descriptor with
O_NOFOLLOW; intermediates require O_DIRECTORY and the final descriptor must be a regular
file. This rejects symlink escapes and special files without string-prefix containment
checks. A configured root is trusted; content served from it must be trusted too.

Protocol reference: [RFC 9112](https://www.rfc-editor.org/rfc/rfc9112.html).
