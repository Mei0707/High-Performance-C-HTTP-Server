-- Fixed-cardinality counters; callback validation is enabled identically in every run.
local threads = {}
function setup(thread)
  table.insert(threads, thread)
end
function init(args)
  ok = 0
  other = 0
  corrupt = 0
  validate_hello = args[1] == "hello"
end
function response(status, headers, body)
  if status == 200 then
    ok = ok + 1
    if validate_hello and body ~= "Hello from PulseHTTP!\n" then corrupt = corrupt + 1 end
  else
    other = other + 1
  end
end
function done(summary, latency, requests)
  local success, errors, invalid = 0, 0, 0
  for _, thread in ipairs(threads) do
    success = success + thread:get("ok")
    errors = errors + thread:get("other")
    invalid = invalid + thread:get("corrupt")
  end
  io.write(string.format('PULSE_RESULT {"http_200":%d,"http_other":%d,"invalid_bodies":%d,"duration_us":%d,"successful_rps":%.6f,"p50_ms":%.6f,"p95_ms":%.6f,"p99_ms":%.6f,"connect_errors":%d,"read_errors":%d,"write_errors":%d,"timeouts":%d}\n',
    success, errors, invalid, summary.duration, (success-invalid)*1000000/summary.duration,
    latency:percentile(50)/1000, latency:percentile(95)/1000, latency:percentile(99)/1000,
    summary.errors.connect, summary.errors.read, summary.errors.write, summary.errors.timeout))
end
