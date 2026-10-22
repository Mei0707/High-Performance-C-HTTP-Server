# Concurrency and shutdown

The task queue contains at most `queue_capacity` waiting tasks, with at most `workers`
executing handlers. In addition, the reactor caps submitted-but-unconsumed tasks at their
sum. Only the reactor changes that counter. Workers push completions under a short mutex
and notify an eventfd. The reactor swaps the queue into a local batch before processing it.
This bounds completion memory even when clients disconnect before handlers return.

One connection owns at most one submitted request. Reads pause while processing/writing.
This avoids response reordering and applies TCP backpressure. Already read pipeline bytes
remain bounded by the input limit. Queue saturation yields 503 and close, including for an
already framed body. Other requests remain isolated. Timeouts and disconnected clients
do not free their worker admission slot until the completion arrives.

ThreadPool shutdown rejects new tasks, drains accepted tasks, and joins every worker.
Task exceptions are caught; server handler exceptions become 500 responses. Member order
ensures the pool joins before completion queues and notification descriptors are destroyed.

SIGINT/SIGTERM stop admission and set readiness false. Idle and incomplete connections close
immediately; processing and writing connections drain and then close. The shutdown deadline
limits network draining. Workers still join before destruction. A processing timeout returns
504 and discards any later completion, but does not cancel application code.

Custom handlers must terminate in bounded time and synchronize their own mutable captures.
There is no safe C++ facility for killing an arbitrary running thread. A stuck custom handler
can prevent process exit beyond the drain timeout. Likewise a blocked logging sink can delay
the logger's final join, although it does not stall the reactor during operation.

`Server` is single-use. Register routes before calling `run`; concurrent registration is
unsupported. `request_stop` is safe from another thread and is observed within an epoll tick.
For process signals, call `run` on the main thread before creating unrelated threads, or block
SIGINT/SIGTERM in every application thread yourself. The server restores its caller's mask on
return. Executables ignore SIGPIPE (required for the sendfile path); embedded users must also
ignore SIGPIPE. Multiple process signal-owning server instances are not supported.

The thread-per-connection baseline shares the parser/router/serializer and has bounded
admission and per-thread socket deadlines. It intentionally has no worker queue or separate
processing-timeout response. Idle waiting, absolute header/body phases and write-progress
deadlines mirror the reactor, but each connection polls its socket independently. Normal
completed HTTP responses use the same application behavior and framing as the reactor.
