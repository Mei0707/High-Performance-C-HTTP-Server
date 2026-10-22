# Proposal review

The proposal defines a useful systems project: a reactor that owns sockets, bounded
workers, explicit framing and reproducible comparisons. It correctly identifies descriptor
reuse, partial I/O, slow clients, request ordering, static-path confinement and overload as
correctness concerns. No throughput target is needed to evaluate these mechanisms.

The source folder was empty apart from the PDF. This implementation uses its technical
requirements as the project specification. Portfolio suggestions and the seven-week
schedule are background, rather than instructions to publish claims or perform external actions.

Decisions needed before implementation:

| Proposal ambiguity | Implemented decision and reason |
|---|---|
| Multiple overload strategies | Immediate 503 + close when worker admission fails; immediate close at connection cap. The reactor never waits for a worker slot. |
| Completion queue capacity | Workers, pending tasks and unconsumed completions share an outstanding-task budget. Otherwise disconnected clients could leave an unbounded second queue. |
| Pipelined concurrency | One handler and response at a time per connection. Pause socket reads until the prior response finishes; process already buffered requests in order. |
| Duplicate lengths | Reject every repeated Content-Length, including identical repetitions, and comma lists. This intentionally conservative subset avoids ambiguity. |
| Unsupported protocol features | HTTP/1.1 origin-form only; reject transfer codings and expectations early. Do not wait for a body that a client expects permission to send. |
| Timeouts | Absolute header/body phases, processing deadline including queue wait, idle/progress deadline while reading/writing; 50 ms sweep resolution. |
| Shutdown guarantees | Stop accepting, mark unready, finish accepted handlers/responses within network drain budget, close others. C++ handlers cannot be forcibly stopped safely. |
| Static file containment | Open each component relative to an open directory descriptor using O_NOFOLLOW; reject all symlinks, traversal, nonregular files and excessive size. |
| Performance claims | Preserve raw samples and build identity. Compare LT/ET, buffered/sendfile, workers, keep-alive and the bounded thread baseline. Separate overload errors from successful throughput. |
| Platform verification | Build and run the Linux epoll backend in Docker on the macOS host; do not infer Linux behavior from an alternate backend. |

Implementation references: [HTTP/1.1 message syntax and framing (RFC 9112)](https://www.rfc-editor.org/rfc/rfc9112.html)
and [Linux epoll semantics](https://man7.org/linux/man-pages/man7/epoll.7.html).

The long-running engineering goals still require judgment: dedicated-client capacity
testing, long soaks, privileged perf collection and extended fuzz campaigns are separate
from a short development validation. The validation record identifies exactly what ran.
