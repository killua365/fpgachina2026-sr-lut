"""Measure sustained TCP payload throughput between a PYNQ board and PC."""

from __future__ import annotations

import argparse
import json
import socket
import time
from typing import Optional, Sequence


DEFAULT_BYTES = 256 * 1024 * 1024
BUFFER_BYTES = 1024 * 1024


def summary(mode: str, byte_count: int, elapsed: float) -> dict[str, float | int | str]:
    if byte_count <= 0 or elapsed <= 0:
        raise ValueError("benchmark requires positive bytes and elapsed time")
    return {
        "mode": mode,
        "bytes": int(byte_count),
        "seconds": float(elapsed),
        "megabytes_per_second": byte_count / elapsed / 1_000_000.0,
        "megabits_per_second": byte_count * 8.0 / elapsed / 1_000_000.0,
    }


def receive_once(host: str, port: int, expected_bytes: int) -> dict[str, float | int | str]:
    buffer = bytearray(BUFFER_BYTES)
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
        listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        listener.bind((host, int(port)))
        listener.listen(1)
        connection, _ = listener.accept()
        with connection:
            received = 0
            started = time.perf_counter()
            while received < expected_bytes:
                count = connection.recv_into(
                    buffer,
                    min(len(buffer), expected_bytes - received),
                )
                if not count:
                    break
                received += count
            elapsed = time.perf_counter() - started
    if received != expected_bytes:
        raise RuntimeError(f"received {received}/{expected_bytes} bytes")
    return summary("receive", received, elapsed)


def send_once(host: str, port: int, byte_count: int) -> dict[str, float | int | str]:
    payload = bytes(BUFFER_BYTES)
    with socket.create_connection((host, int(port)), timeout=15) as connection:
        sent = 0
        started = time.perf_counter()
        while sent < byte_count:
            view = memoryview(payload)[: min(len(payload), byte_count - sent)]
            connection.sendall(view)
            sent += len(view)
        connection.shutdown(socket.SHUT_WR)
        elapsed = time.perf_counter() - started
    return summary("send", sent, elapsed)


def main(argv: Optional[Sequence[str]] = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--listen", action="store_true")
    mode.add_argument("--send", metavar="HOST")
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=19037)
    parser.add_argument("--bytes", type=int, default=DEFAULT_BYTES)
    args = parser.parse_args(argv)
    if args.bytes <= 0:
        raise ValueError("--bytes must be positive")
    result = (
        receive_once(args.host, args.port, args.bytes)
        if args.listen
        else send_once(args.send, args.port, args.bytes)
    )
    print(json.dumps(result, sort_keys=True))


if __name__ == "__main__":
    main()
