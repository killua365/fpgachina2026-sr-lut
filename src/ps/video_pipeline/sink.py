"""Board-side YUV420P TCP sink."""

from __future__ import annotations

import json
import socket
import time
from typing import Any

from .protocol import EndRecord, FrameHeader, SessionHeader, crc32, frame_digest_chain, send_all


class Yuv420FrameSink:
    def __init__(self, host: str, port: int, session: SessionHeader, *, connect_timeout_seconds: float = 15.0, io_timeout_seconds: float = 30.0, socket_factory: Any = socket.create_connection) -> None:
        if not host or not 1 <= int(port) <= 65535:
            raise ValueError("host and port are invalid")
        self.session = session
        self.socket = socket_factory((host, int(port)), connect_timeout_seconds)
        self.socket.settimeout(io_timeout_seconds)
        self.socket.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        # Leave SO_SNDBUF unlocked so Linux TCP autotuning can grow it up to
        # net.ipv4.tcp_wmem.  PYNQ images commonly cap net.core.wmem_max at
        # 180224 bytes; an explicit multi-megabyte request is then clamped and
        # disables autotuning, which reduces sustained video throughput.
        self.socket_send_buffer_policy = "kernel-autotuned"
        self.socket_send_buffer_bytes = int(
            self.socket.getsockopt(socket.SOL_SOCKET, socket.SO_SNDBUF)
        )
        self.socket_send_api = (
            "sendall" if callable(getattr(self.socket, "sendall", None)) else "send-loop"
        )
        self.socket_send_calls = send_all(self.socket, session.pack())
        self.frame_header_send_calls = 0
        self.payload_send_calls = 0
        self.frame_count = 0
        self.total_payload_bytes = 0
        self.frame_crc_chain = 0
        self.crc_seconds: list[float] = []
        self.hardware_crc_frames = 0
        self.send_seconds: list[float] = []
        self.closed = False

    def send_frame(self, frame_index: int, payload: bytes | memoryview, *, hardware_status: int, hardware_error_code: int, input_word_count: int, output_word_count: int, payload_crc32: int | None = None) -> None:
        if self.closed:
            raise RuntimeError("sink is closed")
        if frame_index != self.frame_count:
            raise ValueError(f"frame order mismatch: {frame_index}/{self.frame_count}")
        view = memoryview(payload).cast("B")
        if len(view) != self.session.bytes_per_frame:
            raise ValueError(f"payload length mismatch: {len(view)}/{self.session.bytes_per_frame}")
        if payload_crc32 is None:
            crc_started = time.perf_counter()
            digest = crc32(view)
            self.crc_seconds.append(time.perf_counter() - crc_started)
        else:
            digest = int(payload_crc32)
            if not 0 <= digest <= 0xFFFFFFFF:
                raise ValueError("payload_crc32 must be an unsigned 32-bit value")
            self.hardware_crc_frames += 1
        header = FrameHeader(frame_index, len(view), digest, hardware_status, hardware_error_code, input_word_count, output_word_count)
        started = time.perf_counter()
        header_calls = send_all(self.socket, header.pack())
        payload_calls = send_all(self.socket, view)
        self.frame_header_send_calls += header_calls
        self.payload_send_calls += payload_calls
        self.socket_send_calls += header_calls + payload_calls
        self.send_seconds.append(time.perf_counter() - started)
        self.frame_count += 1
        self.total_payload_bytes += len(view)
        self.frame_crc_chain = frame_digest_chain(self.frame_crc_chain, digest)

    def finish(self, report: dict[str, Any]) -> None:
        if self.closed:
            return
        payload = json.dumps(report, ensure_ascii=False, allow_nan=False, sort_keys=True).encode("utf-8")
        self.socket_send_calls += send_all(
            self.socket,
            EndRecord(
                self.frame_count,
                self.total_payload_bytes,
                self.frame_crc_chain,
                len(payload),
            ).pack(),
        )
        self.socket_send_calls += send_all(self.socket, payload)
        try:
            self.socket.shutdown(socket.SHUT_WR)
        except OSError:
            pass
        self.close()

    def fail(self) -> None:
        if self.closed:
            return
        self.closed = True
        try:
            self.socket.shutdown(socket.SHUT_RDWR)
        except OSError:
            pass
        self.socket.close()

    def close(self) -> None:
        if not self.closed:
            self.closed = True
            self.socket.close()

    def __enter__(self) -> "Yuv420FrameSink":
        return self

    def __exit__(self, exc_type: Any, exc: Any, traceback: Any) -> None:
        self.close()
