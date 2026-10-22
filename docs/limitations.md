# Limitations

This is a systems portfolio server with an explicit HTTP subset, rather than a general
HTTP framework. TLS, HTTP/2/3, chunked request bodies, upgrades, WebSockets, proxying,
conditional/range requests and compression are outside the implemented scope. IPv4 literal
binding only. See protocol.md for exact parsing and status rules.

One reactor and one handler per connection simplify ownership and ordering. Deadline scans
are linear in active connections; parser header scans retain position but there is no custom
allocator or ring buffer. At configured maxima, input and output alone can approach
`max_connections * (max_input_bytes + max_output_bytes)`, plus active task/response copies,
kernel buffers, stacks and overhead. Defaults are caps, not a promise to fit every machine.

Buffered files are read on workers and subject to output limits. Linux sendfile runs on the
reactor and may stall on storage page faults; a nonblocking socket does not make disk I/O
asynchronous. Files must remain stable during transfer. Truncation closes a partial response;
growth never sends beyond the advertised size. No file cache, disk I/O pool or rate limiter
is implemented. No symlinks are served, including otherwise safe internal symlinks.

Time sweeps have roughly 50 ms resolution plus reactor scheduling delay. Processing timeouts
and socket drain deadlines cannot cancel arbitrary user code; handlers must cooperate by
finishing promptly. Logging is bounded and may drop entries; a blocked output sink can delay
final logger destruction. There is no per-request distributed tracing or status-label breakdown.

FD exhaustion temporarily suspends listener interest and retries on a later sweep. Admission
over the connection cap closes without an HTTP response. There is no reserve-FD accept
strategy or adaptive overload tuning. Allocation failure is not a recoverable operational
mode; choose limits appropriate to available RAM and descriptors.

Short fuzz/stress/sanitizer runs are evidence for tested paths, not proof of memory safety.
Hosted CI results and artifacts are linked from the follow-up validation report.
Local Docker measurements share a load generator and virtualized host; they cannot establish
production throughput, maximum sustainable connection count, or optimal worker sizing.
The follow-up tests isolate client/server CPU sets in separate containers on one VM;
their raw timeout counters and local clock discontinuities remain explicit qualifications.
