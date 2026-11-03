"""Receive Stage 39 JPEG frames and display them on the laptop with ffplay."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import socket
import subprocess
import sys
import time
from typing import Any, Sequence

SCRIPT_DIR = Path(__file__).resolve().parent
PYNQ_DIR = SCRIPT_DIR.parent
if str(PYNQ_DIR) not in sys.path:
    sys.path.insert(0, str(PYNQ_DIR))

from video_pipeline.mjpeg_protocol import END_MAGIC, END_STRUCT, FRAME_MAGIC
from video_pipeline.mjpeg_protocol import FRAME_STRUCT, SESSION_STRUCT
from video_pipeline.mjpeg_protocol import MjpegEndRecord, MjpegFrameHeader
from video_pipeline.mjpeg_protocol import MjpegProtocolError, MjpegSessionHeader
from video_pipeline.mjpeg_protocol import crc32, frame_digest_chain, read_exact
from video_pipeline.mjpeg_protocol import read_exact_into


def ffplay_command(executable: str, title: str) -> list[str]:
    return [
        executable,
        "-hide_banner",
        "-loglevel",
        "warning",
        "-fflags",
        "nobuffer",
        "-flags",
        "low_delay",
        "-framedrop",
        "-f",
        "mjpeg",
        "-window_title",
        title,
        "-i",
        "pipe:0",
    ]


def receive_mjpeg_session(
    connection: Any,
    *,
    ffplay: str = "ffplay",
    window_title: str = "SR-LUT FPGA 1080P MJPEG",
    timeout_seconds: float = 30.0,
    player_factory: Any = subprocess.Popen,
) -> dict[str, Any]:
    settimeout = getattr(connection, "settimeout", None)
    if callable(settimeout):
        settimeout(timeout_seconds)
    session = MjpegSessionHeader.unpack(read_exact(connection, SESSION_STRUCT.size))
    player = player_factory(
        ffplay_command(ffplay, window_title), stdin=subprocess.PIPE
    )
    if player.stdin is None:
        raise RuntimeError("ffplay did not provide an input pipe")
    frame_count = 0
    total_bytes = 0
    digest_chain = 0
    started = time.perf_counter()
    report = None
    end = None
    buffer = bytearray()
    try:
        while True:
            marker = read_exact(connection, 8)
            if marker == END_MAGIC:
                end = MjpegEndRecord.unpack(
                    marker + read_exact(connection, END_STRUCT.size - 8)
                )
                report = json.loads(
                    read_exact(connection, end.report_length).decode("utf-8")
                )
                break
            if marker != FRAME_MAGIC:
                raise MjpegProtocolError(f"unexpected MJPEG marker {marker!r}")
            header = MjpegFrameHeader.unpack(
                marker + read_exact(connection, FRAME_STRUCT.size - 8)
            )
            if header.frame_index != frame_count:
                raise MjpegProtocolError("MJPEG frame index is out of order")
            if len(buffer) != header.jpeg_length:
                buffer = bytearray(header.jpeg_length)
            payload = read_exact_into(connection, buffer)
            if bytes(payload[:2]) != b"\xff\xd8" or bytes(payload[-2:]) != b"\xff\xd9":
                raise MjpegProtocolError("received payload is not a complete JPEG")
            actual_crc = crc32(payload)
            if actual_crc != header.jpeg_crc32:
                raise MjpegProtocolError(f"frame {frame_count} CRC mismatch")
            player.stdin.write(payload)
            player.stdin.flush()
            frame_count += 1
            total_bytes += len(payload)
            digest_chain = frame_digest_chain(digest_chain, actual_crc)
            if frame_count % 30 == 0:
                elapsed = time.perf_counter() - started
                print(
                    f"STAGE38_RECEIVER_FRAME frame={frame_count} "
                    f"fps={frame_count / max(elapsed, 1e-9):.2f}",
                    flush=True,
                )
        assert end is not None
        if (end.actual_frame_count, end.total_jpeg_bytes, end.frame_crc_chain) != (
            frame_count,
            total_bytes,
            digest_chain,
        ):
            raise MjpegProtocolError("MJPEG end totals do not match received data")
        if session.declared_frame_count >= 0 and session.declared_frame_count != frame_count:
            raise MjpegProtocolError("declared MJPEG frame count does not match")
        if not isinstance(report, dict) or report.get("status") != "pass":
            raise MjpegProtocolError("board MJPEG report did not pass")
        return {
            "status": "pass",
            "frames": frame_count,
            "jpeg_bytes": total_bytes,
            "elapsed_seconds": time.perf_counter() - started,
            "board_report": report,
        }
    finally:
        try:
            player.stdin.close()
        except (AttributeError, BrokenPipeError, OSError):
            pass
        try:
            player.wait(timeout=5.0)
        except (subprocess.TimeoutExpired, AttributeError):
            player.terminate()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=5038)
    parser.add_argument("--ffplay", default="ffplay")
    parser.add_argument("--timeout", type=float, default=30.0)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as server:
        server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        server.bind((args.host, args.port))
        server.listen(1)
        print(f"STAGE38_RECEIVER_LISTEN {args.host}:{args.port}", flush=True)
        connection, address = server.accept()
        with connection:
            print(f"STAGE38_RECEIVER_CONNECTED {address[0]}:{address[1]}", flush=True)
            report = receive_mjpeg_session(
                connection,
                ffplay=args.ffplay,
                timeout_seconds=args.timeout,
            )
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
