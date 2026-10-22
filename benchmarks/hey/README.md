```sh
hey -z 10s -c 128 http://SERVER:8080/hello  # warm-up
hey -z 30s -c 128 http://SERVER:8080/hello > keep-alive-1.txt
hey -z 30s -c 128 -disable-keepalive http://SERVER:8080/hello > new-connection-1.txt
```

Use the same load-generator version and server limits, repeat each run at least three times,
and report successful throughput separately from non-200 responses and transport errors.
