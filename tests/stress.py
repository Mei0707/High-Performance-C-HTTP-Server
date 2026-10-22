#!/usr/bin/env python3
"""Concurrent persistent echo sessions, repeated churn, and descriptor recovery."""
import argparse
import asyncio
import json
from pathlib import Path
import signal
import time


async def main(args):
    process = await asyncio.create_subprocess_exec(
        args.server, "--port", "0", "--queue-capacity", str(args.connections * 2),
        "--max-connections", str(args.connections * 2), "--edge-triggered",
        stdout=asyncio.subprocess.PIPE)
    try:
        startup = await asyncio.wait_for(process.stdout.readline(), 20)
        port = json.loads(startup)["port"]
        before_fds = len(list(Path(f"/proc/{process.pid}/fd").iterdir()))
        start = time.monotonic()

        async def session(round_number, client):
            reader, writer = await asyncio.wait_for(asyncio.open_connection("127.0.0.1", port), 10)
            try:
                for request in range(args.requests):
                    body = (f"{round_number}:{client}:{request}:".encode() + bytes(range(256))) * 4
                    wire = (f"POST /api/echo HTTP/1.1\r\nHost: x\r\nContent-Length: {len(body)}\r\n\r\n").encode() + body
                    writer.write(wire)
                    await writer.drain()
                    headers = await asyncio.wait_for(reader.readuntil(b"\r\n\r\n"), 10)
                    if not headers.startswith(b"HTTP/1.1 200 "):
                        raise AssertionError(headers)
                    if b"Content-Length: " + str(len(body)).encode() + b"\r\n" not in headers:
                        raise AssertionError("incorrect response length")
                    echoed = await asyncio.wait_for(reader.readexactly(len(body)), 10)
                    if echoed != body:
                        raise AssertionError("cross-connection or corrupted response")
            finally:
                writer.close()
                await writer.wait_closed()

        fd_samples = []
        for round_number in range(args.rounds):
            await asyncio.gather(*(session(round_number, c) for c in range(args.connections)))
            await asyncio.sleep(.2)
            fds = len(list(Path(f"/proc/{process.pid}/fd").iterdir()))
            fd_samples.append(fds)
            if fds > before_fds + 2:
                raise AssertionError(f"descriptors did not recover: {before_fds} -> {fds}")
        report = {"connections_per_round": args.connections, "rounds": args.rounds,
                  "requests_per_connection": args.requests,
                  "verified_responses": args.connections * args.rounds * args.requests,
                  "elapsed_seconds": time.monotonic() - start,
                  "baseline_fds": before_fds, "fds_after_rounds": fd_samples}
        print(json.dumps(report, indent=2))
        if args.output:
            Path(args.output).parent.mkdir(parents=True, exist_ok=True)
            Path(args.output).write_text(json.dumps(report, indent=2) + "\n")
    finally:
        if process.returncode is None:
            process.send_signal(signal.SIGTERM)
        try:
            code = await asyncio.wait_for(process.wait(), 15)
        except asyncio.TimeoutError:
            process.kill()
            await process.wait()
            raise RuntimeError("shutdown timed out")
        if code:
            raise RuntimeError(f"server exit {code}")


if __name__ == "__main__":
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--server", default="build/pulsehttp_server")
    p.add_argument("--connections", type=int, default=1000)
    p.add_argument("--rounds", type=int, default=3)
    p.add_argument("--requests", type=int, default=10)
    p.add_argument("--output")
    args = p.parse_args()
    if min(args.connections, args.rounds, args.requests) < 1:
        p.error("counts must be positive")
    asyncio.run(main(args))
