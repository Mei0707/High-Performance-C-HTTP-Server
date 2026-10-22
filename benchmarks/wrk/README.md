Run from a dedicated client machine and preserve both stdout and environment metadata:

```sh
wrk -t4 -c128 -d10s --latency http://SERVER:8080/hello   # warm-up
wrk -t4 -c128 -d30s --latency http://SERVER:8080/hello > hello-1.txt
wrk -t4 -c128 -d30s --latency -s benchmarks/wrk/echo.lua http://SERVER:8080/api/echo > echo-1.txt
```

Repeat at least three times for each configuration. Capture errors, `pidstat -t -r -u -w -p PID 1`,
`/proc/PID/fd`, `/metrics`, compiler flags and host metadata. wrk's printed distribution
does not provide every desired percentile; retain the tool's actual output without inventing p95.
