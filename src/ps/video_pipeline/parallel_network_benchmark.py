"""Measure aggregate TCP throughput across multiple independent streams."""

from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path
import sys
import time

SCRIPT_DIR = Path(__file__).resolve().parent
PYNQ_DIR = SCRIPT_DIR.parent
if str(PYNQ_DIR) not in sys.path:
    sys.path.insert(0, str(PYNQ_DIR))

from video_pipeline.network_benchmark import receive_once, send_once


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--listen", action="store_true")
    mode.add_argument("--send", metavar="HOST")
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=19100)
    parser.add_argument("--streams", type=int, default=4)
    parser.add_argument("--bytes-per-stream", type=int, default=64 * 1024 * 1024)
    args = parser.parse_args()
    if not 1 <= args.streams <= 8 or args.bytes_per_stream <= 0:
        raise ValueError("streams must be in 1..8 and byte count must be positive")

    started = time.perf_counter()
    with ThreadPoolExecutor(max_workers=args.streams) as executor:
        futures = []
        for index in range(args.streams):
            port = args.port + index
            if args.listen:
                futures.append(
                    executor.submit(receive_once, args.host, port, args.bytes_per_stream)
                )
            else:
                futures.append(
                    executor.submit(send_once, args.send, port, args.bytes_per_stream)
                )
        streams = [future.result() for future in futures]
    elapsed = time.perf_counter() - started
    total = args.streams * args.bytes_per_stream
    print(
        json.dumps(
            {
                "mode": "receive" if args.listen else "send",
                "stream_count": args.streams,
                "total_bytes": total,
                "wall_seconds": elapsed,
                "aggregate_megabytes_per_second": total / elapsed / 1_000_000,
                "aggregate_megabits_per_second": total * 8 / elapsed / 1_000_000,
                "streams": streams,
            },
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    main()
