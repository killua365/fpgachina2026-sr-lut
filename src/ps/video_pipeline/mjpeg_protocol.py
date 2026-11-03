"""Framed Stage 39 MJPEG transport for a laptop display client."""

from __future__ import annotations

from dataclasses import dataclass
import struct
from typing import BinaryIO
import zlib


SESSION_MAGIC = b"SL38MJPG"
FRAME_MAGIC = b"SL38JPEG"
END_MAGIC = b"SL38DONE"
PROTOCOL_VERSION = 1
MAX_JPEG_BYTES = 4 * 1024 * 1024
MAX_REPORT_BYTES = 1024 * 1024

SESSION_STRUCT = struct.Struct("!8sHHIIIIIIq")
FRAME_STRUCT = struct.Struct("!8sQQIIIII")
END_STRUCT = struct.Struct("!8sQQII")


class MjpegProtocolError(ValueError):
    """Raised when a Stage 39 MJPEG stream violates its wire contract."""


def read_exact(reader: BinaryIO | object, size: int) -> bytes:
    if size < 0:
        raise ValueError("size must be non-negative")
    chunks: list[bytes] = []
    remaining = size
    while remaining:
        chunk = (
            reader.recv(remaining)
            if hasattr(reader, "recv")
            else reader.read(remaining)
        )
        if not chunk:
            raise EOFError(f"unexpected EOF after {size - remaining}/{size} bytes")
        chunks.append(bytes(chunk))
        remaining -= len(chunk)
    return b"".join(chunks)


def read_exact_into(
    reader: BinaryIO | object, destination: bytearray | memoryview
) -> memoryview:
    view = memoryview(destination).cast("B")
    offset = 0
    while offset < len(view):
        target = view[offset:]
        if hasattr(reader, "recv_into"):
            received = int(reader.recv_into(target))
        else:
            chunk = (
                reader.recv(len(target))
                if hasattr(reader, "recv")
                else reader.read(len(target))
            )
            received = len(chunk) if chunk else 0
            if received:
                target[:received] = chunk
        if not received:
            raise EOFError(f"unexpected EOF after {offset}/{len(view)} bytes")
        offset += received
    return view


def send_all(writer: object, payload: bytes | memoryview) -> None:
    view = memoryview(payload).cast("B")
    sendall = getattr(writer, "sendall", None)
    if callable(sendall):
        sendall(view)
        return
    while view:
        sent = int(writer.send(view))
        if sent <= 0:
            raise ConnectionError("socket closed while sending")
        view = view[sent:]


def crc32(payload: bytes | memoryview) -> int:
    return zlib.crc32(payload) & 0xFFFFFFFF


def frame_digest_chain(previous: int, frame_crc: int) -> int:
    return zlib.crc32(struct.pack("!I", frame_crc), previous) & 0xFFFFFFFF


@dataclass(frozen=True)
class MjpegSessionHeader:
    input_width: int
    input_height: int
    output_width: int
    output_height: int
    fps_numerator: int
    fps_denominator: int
    declared_frame_count: int = -1

    def pack(self) -> bytes:
        if (self.input_width, self.input_height) != (480, 270):
            raise MjpegProtocolError("Stage 39 input must be 480x270")
        if (self.output_width, self.output_height) != (1920, 1080):
            raise MjpegProtocolError("Stage 39 output must be 1920x1080")
        if self.fps_numerator <= 0 or self.fps_denominator <= 0:
            raise MjpegProtocolError("FPS fraction must be positive")
        if self.declared_frame_count < -1:
            raise MjpegProtocolError("declared frame count must be -1 or non-negative")
        return SESSION_STRUCT.pack(
            SESSION_MAGIC,
            PROTOCOL_VERSION,
            SESSION_STRUCT.size,
            self.input_width,
            self.input_height,
            self.output_width,
            self.output_height,
            self.fps_numerator,
            self.fps_denominator,
            self.declared_frame_count,
        )

    @classmethod
    def unpack(cls, payload: bytes) -> "MjpegSessionHeader":
        if len(payload) != SESSION_STRUCT.size:
            raise MjpegProtocolError("invalid MJPEG session header size")
        values = SESSION_STRUCT.unpack(payload)
        if values[:3] != (SESSION_MAGIC, PROTOCOL_VERSION, SESSION_STRUCT.size):
            raise MjpegProtocolError("invalid MJPEG session identity")
        result = cls(*values[3:])
        result.pack()
        return result


@dataclass(frozen=True)
class MjpegFrameHeader:
    frame_index: int
    timestamp_ns: int
    jpeg_length: int
    jpeg_crc32: int
    sr_status: int
    mjpeg_status: int
    sr_output_words: int

    def pack(self) -> bytes:
        if self.frame_index < 0 or self.timestamp_ns < 0:
            raise MjpegProtocolError("invalid MJPEG frame index or timestamp")
        if not 4 <= self.jpeg_length <= MAX_JPEG_BYTES:
            raise MjpegProtocolError("invalid JPEG payload length")
        if self.mjpeg_status & 0x7:
            raise MjpegProtocolError(
                f"FPGA MJPEG status is nonzero: 0x{self.mjpeg_status:08x}"
            )
        return FRAME_STRUCT.pack(
            FRAME_MAGIC,
            self.frame_index,
            self.timestamp_ns,
            self.jpeg_length,
            self.jpeg_crc32,
            self.sr_status,
            self.mjpeg_status,
            self.sr_output_words,
        )

    @classmethod
    def unpack(cls, payload: bytes) -> "MjpegFrameHeader":
        if len(payload) != FRAME_STRUCT.size:
            raise MjpegProtocolError("invalid MJPEG frame header size")
        values = FRAME_STRUCT.unpack(payload)
        if values[0] != FRAME_MAGIC:
            raise MjpegProtocolError("invalid MJPEG frame magic")
        result = cls(*values[1:])
        result.pack()
        return result


@dataclass(frozen=True)
class MjpegEndRecord:
    actual_frame_count: int
    total_jpeg_bytes: int
    frame_crc_chain: int
    report_length: int

    def pack(self) -> bytes:
        if min(self.actual_frame_count, self.total_jpeg_bytes, self.report_length) < 0:
            raise MjpegProtocolError("invalid MJPEG end record")
        if self.report_length > MAX_REPORT_BYTES:
            raise MjpegProtocolError("MJPEG end report is too large")
        return END_STRUCT.pack(
            END_MAGIC,
            self.actual_frame_count,
            self.total_jpeg_bytes,
            self.frame_crc_chain,
            self.report_length,
        )

    @classmethod
    def unpack(cls, payload: bytes) -> "MjpegEndRecord":
        if len(payload) != END_STRUCT.size:
            raise MjpegProtocolError("invalid MJPEG end record size")
        values = END_STRUCT.unpack(payload)
        if values[0] != END_MAGIC:
            raise MjpegProtocolError("invalid MJPEG end magic")
        result = cls(*values[1:])
        result.pack()
        return result


__all__ = [
    "END_MAGIC",
    "END_STRUCT",
    "FRAME_MAGIC",
    "FRAME_STRUCT",
    "MAX_JPEG_BYTES",
    "MjpegEndRecord",
    "MjpegFrameHeader",
    "MjpegProtocolError",
    "MjpegSessionHeader",
    "SESSION_STRUCT",
    "crc32",
    "frame_digest_chain",
    "read_exact",
    "read_exact_into",
    "send_all",
]
