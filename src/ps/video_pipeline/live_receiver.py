"""Receive and display a pipelined MB420 live stream."""

from __future__ import annotations

import argparse
from collections import deque
import json
from pathlib import Path
import queue
import socket
import subprocess
import sys
import threading
import time
from typing import Any, BinaryIO, Optional, Sequence

import numpy as np

SCRIPT_DIR = Path(__file__).resolve().parent
PYNQ_DIR = SCRIPT_DIR.parent
if str(PYNQ_DIR) not in sys.path:
    sys.path.insert(0, str(PYNQ_DIR))

from video_pipeline.mb420 import mb420_to_i420
from video_pipeline.protocol import END_MAGIC, END_STRUCT, FRAME_MAGIC, FRAME_STRUCT
from video_pipeline.protocol import PIXEL_FORMAT_MB420, SESSION_STRUCT
from video_pipeline.protocol import EndRecord, FrameHeader, ProtocolError, SessionHeader
from video_pipeline.protocol import crc32, frame_digest_chain, read_exact, read_exact_into


SOCKET_RECEIVE_BUFFER_BYTES = 16 * 1024 * 1024
DEFAULT_RECEIVE_BUFFER_COUNT = 4


class _SampleStats:
    def __init__(self, limit: int = 4096) -> None:
        self.samples: deque[float] = deque(maxlen=limit)
        self.count = 0
        self.total = 0.0
        self.minimum = float("inf")
        self.maximum = 0.0

    def add(self, value: float) -> None:
        value = float(value)
        self.samples.append(value)
        self.count += 1
        self.total += value
        self.minimum = min(self.minimum, value)
        self.maximum = max(self.maximum, value)

    def summary(self) -> dict[str, float | int] | None:
        if not self.count:
            return None
        values = np.asarray(self.samples, dtype=np.float64)
        return {
            "count": self.count,
            "sample_count": int(values.size),
            "minimum": self.minimum,
            "mean": self.total / self.count,
            "p95": float(np.percentile(values, 95)),
            "p99": float(np.percentile(values, 99)),
            "maximum": self.maximum,
        }


def _write_all(stream: BinaryIO, payload: bytes | memoryview) -> None:
    view = memoryview(payload).cast("B")
    while view:
        written = stream.write(view)
        if written is None:
            stream.flush()
            return
        if written <= 0:
            raise BrokenPipeError("ffplay stdin closed")
        view = view[written:]


def ffplay_command(executable: str, session: SessionHeader, window_title: str) -> list[str]:
    return [
        executable, "-hide_banner", "-loglevel", "warning", "-autoexit",
        "-fflags", "nobuffer", "-flags", "low_delay", "-framedrop",
        "-f", "rawvideo", "-pixel_format", "yuv420p",
        "-video_size", f"{session.output_width}x{session.output_height}",
        "-framerate", f"{session.fps_numerator}/{session.fps_denominator}",
        "-window_title", window_title, "-i", "pipe:0",
    ]


def receive_live_session(
    connection: Any,
    *,
    ffplay: str = "ffplay",
    window_title: str = "SR-LUT FPGA Live x4",
    receive_buffer_count: int = DEFAULT_RECEIVE_BUFFER_COUNT,
    socket_timeout_seconds: float = 30.0,
    player_factory: Any = subprocess.Popen,
) -> dict[str, Any]:
    """Receive MB420 frames while a worker reorders and writes prior frames."""

    if isinstance(receive_buffer_count, bool) or not 2 <= int(receive_buffer_count) <= 16:
        raise ValueError("receive_buffer_count must be in 2..16")
    if socket_timeout_seconds <= 0:
        raise ValueError("socket_timeout_seconds must be positive")
    receive_buffer_count = int(receive_buffer_count)

    settimeout = getattr(connection, "settimeout", None)
    if callable(settimeout):
        settimeout(float(socket_timeout_seconds))
    actual_receive_buffer = None
    setsockopt = getattr(connection, "setsockopt", None)
    getsockopt = getattr(connection, "getsockopt", None)
    if callable(setsockopt) and callable(getsockopt):
        try:
            setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, SOCKET_RECEIVE_BUFFER_BYTES)
            actual_receive_buffer = int(getsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF))
        except OSError:
            actual_receive_buffer = None

    session = SessionHeader.unpack(read_exact(connection, SESSION_STRUCT.size))
    if session.pixel_format != PIXEL_FORMAT_MB420:
        raise ProtocolError("live receiver requires MB420 input")
    command = ffplay_command(ffplay, session, window_title)
    player = player_factory(command, stdin=subprocess.PIPE)
    if player.stdin is None:
        raise RuntimeError("ffplay did not provide a stdin pipe")

    frame_buffers = [bytearray(session.bytes_per_frame) for _ in range(receive_buffer_count)]
    output_buffers = [bytearray(session.bytes_per_frame) for _ in range(receive_buffer_count)]
    free_buffers: queue.Queue[int] = queue.Queue(maxsize=receive_buffer_count)
    processor_queue: queue.Queue[Any] = queue.Queue(maxsize=receive_buffer_count)
    for index in range(receive_buffer_count):
        free_buffers.put(index)

    processor_stop = threading.Event()
    processor_errors: list[BaseException] = []
    network_stats = _SampleStats()
    crc_stats = _SampleStats()
    reorder_stats = _SampleStats()
    display_stats = _SampleStats()
    processed = {"count": 0, "bytes": 0, "chain": 0}

    def processor_target() -> None:
        active_index: int | None = None
        try:
            while not processor_stop.is_set():
                try:
                    item = processor_queue.get(timeout=0.1)
                except queue.Empty:
                    continue
                if item is None:
                    return
                active_index, header = item
                payload = memoryview(frame_buffers[active_index])
                started = time.perf_counter()
                actual_crc = crc32(payload)
                crc_stats.add(time.perf_counter() - started)
                if actual_crc != header.payload_crc32:
                    raise ProtocolError(f"frame {header.frame_index} CRC mismatch")
                started = time.perf_counter()
                mb420_to_i420(payload, session.output_width, session.output_height, output_buffers[active_index])
                reorder_stats.add(time.perf_counter() - started)
                started = time.perf_counter()
                _write_all(player.stdin, output_buffers[active_index])
                display_stats.add(time.perf_counter() - started)
                processed["count"] += 1
                processed["bytes"] += len(payload)
                processed["chain"] = frame_digest_chain(processed["chain"], header.payload_crc32)
                free_buffers.put(active_index)
                active_index = None
        except BaseException as exc:
            processor_errors.append(exc)
            processor_stop.set()
        finally:
            if active_index is not None:
                free_buffers.put(active_index)

    def raise_processor_error() -> None:
        if processor_errors:
            raise processor_errors[0]

    def acquire_buffer() -> int:
        while True:
            raise_processor_error()
            try:
                return free_buffers.get(timeout=0.1)
            except queue.Empty:
                continue

    def enqueue(item: Any) -> None:
        while True:
            raise_processor_error()
            try:
                processor_queue.put(item, timeout=0.1)
                return
            except queue.Full:
                continue

    processor = threading.Thread(target=processor_target, name="live-frame-processor", daemon=True)
    processor.start()
    started = time.perf_counter()
    received_count = 0
    received_bytes = 0
    board_report: dict[str, Any] | None = None
    end: EndRecord | None = None
    return_code: int | None = None
    try:
        while True:
            marker = read_exact(connection, 8)
            if marker == END_MAGIC:
                end = EndRecord.unpack(marker + read_exact(connection, END_STRUCT.size - 8))
                report_payload = read_exact(connection, end.report_length)
                board_report = json.loads(report_payload.decode("utf-8"))
                if not isinstance(board_report, dict) or board_report.get("status") != "pass":
                    raise ProtocolError("board report did not pass")
                break
            if marker != FRAME_MAGIC:
                raise ProtocolError(f"unexpected record marker {marker!r}")
            header = FrameHeader.unpack(marker + read_exact(connection, FRAME_STRUCT.size - 8))
            if header.frame_index != received_count:
                raise ProtocolError("live frame index is out of order")
            if header.payload_length != session.bytes_per_frame:
                raise ProtocolError("live frame payload length mismatch")
            if header.hardware_error_code != 0 or not header.hardware_status & (1 << 2):
                raise ProtocolError("board reported an unsuccessful live frame")
            if (header.input_word_count, header.output_word_count) != (
                session.input_width * session.input_height,
                session.hardware_output_words,
            ):
                raise ProtocolError("live frame DMA word counts do not match the session")
            buffer_index = acquire_buffer()
            owned_by_processor = False
            try:
                receive_started = time.perf_counter()
                read_exact_into(connection, frame_buffers[buffer_index])
                network_stats.add(time.perf_counter() - receive_started)
                enqueue((buffer_index, header))
                owned_by_processor = True
                received_count += 1
                received_bytes += header.payload_length
            finally:
                if not owned_by_processor:
                    free_buffers.put(buffer_index)
            if received_count % 30 == 0:
                elapsed = time.perf_counter() - started
                print(f"LIVE_RECEIVER_FRAME frame={received_count} fps={received_count / max(elapsed, 1e-9):.2f}", flush=True)

        enqueue(None)
        processor.join(timeout=max(10.0, socket_timeout_seconds))
        if processor.is_alive():
            raise TimeoutError("live frame processor did not stop")
        raise_processor_error()
        assert end is not None
        if (processed["count"], processed["bytes"]) != (received_count, received_bytes):
            raise ProtocolError("processed live totals do not match received totals")
        if (end.actual_frame_count, end.total_payload_bytes, end.frame_crc_chain) != (
            processed["count"], processed["bytes"], processed["chain"],
        ):
            raise ProtocolError("live end record does not match received frames")
        if session.declared_frame_count >= 0 and session.declared_frame_count != received_count:
            raise ProtocolError("declared live frame count does not match received frames")
    finally:
        processor_stop.set()
        if processor.is_alive():
            try:
                processor_queue.put_nowait(None)
            except queue.Full:
                pass
            processor.join(timeout=5.0)
        try:
            player.stdin.close()
        except OSError:
            pass
        try:
            return_code = player.wait(timeout=10.0)
        except subprocess.TimeoutExpired:
            player.terminate()
            return_code = player.wait(timeout=5.0)
    if return_code != 0:
        raise RuntimeError(f"ffplay failed with exit code {return_code}")

    elapsed = time.perf_counter() - started
    return {
        "schema_version": 2,
        "status": "pass",
        "session": {
            "input_width": session.input_width,
            "input_height": session.input_height,
            "output_width": session.output_width,
            "output_height": session.output_height,
            "fps": session.fps_numerator / session.fps_denominator,
            "pixel_format": session.pixel_format_name,
            "bytes_per_frame": session.bytes_per_frame,
        },
        "received": {"frame_count": received_count, "payload_bytes": received_bytes},
        "pipeline": {
            "receive_buffer_count": receive_buffer_count,
            "reusable_receive_buffers": True,
            "separate_frame_processor": True,
            "socket_receive_buffer_requested_bytes": SOCKET_RECEIVE_BUFFER_BYTES,
            "socket_receive_buffer_bytes": actual_receive_buffer,
        },
        "performance": {
            "elapsed_seconds": elapsed,
            "receive_fps": received_count / max(elapsed, 1e-9),
            "payload_megabytes_per_second": received_bytes / max(elapsed, 1e-9) / 1_000_000,
            "network_receive_seconds": network_stats.summary(),
            "crc32_seconds": crc_stats.summary(),
            "mb420_to_i420_seconds": reorder_stats.summary(),
            "ffplay_write_seconds": display_stats.summary(),
        },
        "ffplay": {"command": command, "return_code": return_code},
        "board_report": board_report,
    }


def serve(
    bind_host: str,
    port: int,
    ffplay: str,
    window_title: str,
    *,
    receive_buffer_count: int = DEFAULT_RECEIVE_BUFFER_COUNT,
    socket_timeout_seconds: float = 30.0,
) -> dict[str, Any]:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
        listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        listener.bind((bind_host, port))
        listener.listen(1)
        print(f"LIVE_RECEIVER_LISTENING {bind_host}:{port}", flush=True)
        connection, address = listener.accept()
        print(f"LIVE_RECEIVER_CONNECTED {address[0]}:{address[1]}", flush=True)
        with connection:
            result = receive_live_session(
                connection,
                ffplay=ffplay,
                window_title=window_title,
                receive_buffer_count=receive_buffer_count,
                socket_timeout_seconds=socket_timeout_seconds,
            )
    print(f"LIVE_RECEIVER_PASS frames={result['received']['frame_count']}", flush=True)
    return result


def main(argv: Optional[Sequence[str]] = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bind-host", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=19037)
    parser.add_argument("--ffplay", default="ffplay")
    parser.add_argument("--window-title", default="SR-LUT FPGA Live x4")
    parser.add_argument("--buffers", type=int, default=DEFAULT_RECEIVE_BUFFER_COUNT)
    parser.add_argument("--timeout", type=float, default=30.0)
    args = parser.parse_args(argv)
    serve(
        args.bind_host,
        args.port,
        args.ffplay,
        args.window_title,
        receive_buffer_count=args.buffers,
        socket_timeout_seconds=args.timeout,
    )


if __name__ == "__main__":
    main()
