"""Versioned, checksummed YUV420P stream protocol."""

from __future__ import annotations

from dataclasses import dataclass
import struct
from typing import BinaryIO
import zlib

from video_pipeline.identity import MAX_FRAME_HEIGHT
from video_pipeline.identity import MAX_FRAME_WIDTH
from video_pipeline.identity import MIN_FRAME_HEIGHT
from video_pipeline.identity import MIN_FRAME_WIDTH


# These markers are retained for wire compatibility with the validated prototype.
# Product identity is carried in the board report, not inferred from wire magic.
SESSION_MAGIC = b"SL37SESS"
FRAME_MAGIC = b"SL37FRAM"
END_MAGIC = b"SL37DONE"
PROTOCOL_VERSION = 1
PIXEL_FORMAT_YUV420P = 2
PIXEL_FORMAT_MB420 = 3
PIXEL_FORMAT_NAMES = {
    PIXEL_FORMAT_YUV420P: "yuv420p",
    PIXEL_FORMAT_MB420: "mb420",
}
MAX_REPORT_BYTES = 16 * 1024 * 1024
SUPPORTED_SCALES = (2, 4)
SESSION_STRUCT = struct.Struct("!8sHHIIIIIIqHHI")
FRAME_STRUCT = struct.Struct("!8sQIIIIII")
END_STRUCT = struct.Struct("!8sQQLI")


class ProtocolError(ValueError):
    """Raised when a video stream violates its wire contract."""


def read_exact(reader: BinaryIO | object, size: int) -> bytes:
    if size < 0:
        raise ValueError("size must be non-negative")
    chunks: list[bytes] = []
    remaining = size
    while remaining:
        chunk = reader.recv(remaining) if hasattr(reader, "recv") else reader.read(remaining)
        if not chunk:
            raise EOFError(f"unexpected EOF after {size - remaining}/{size} bytes")
        chunks.append(bytes(chunk))
        remaining -= len(chunk)
    return b"".join(chunks)


def read_exact_into(reader: BinaryIO | object, destination: bytearray | memoryview) -> memoryview:
    """Fill a reusable byte buffer from a socket or file-like reader."""

    view = memoryview(destination).cast("B")
    total = len(view)
    offset = 0
    while offset < total:
        target = view[offset:]
        if hasattr(reader, "recv_into"):
            received = reader.recv_into(target)
        else:
            chunk = reader.recv(len(target)) if hasattr(reader, "recv") else reader.read(len(target))
            if not chunk:
                received = 0
            else:
                received = len(chunk)
                target[:received] = chunk
        if not received:
            raise EOFError(f"unexpected EOF after {offset}/{total} bytes")
        offset += int(received)
    return view


def send_all(writer: object, payload: bytes | memoryview) -> int:
    view = memoryview(payload).cast("B")
    sendall = getattr(writer, "sendall", None)
    if callable(sendall):
        sendall(view)
        return 1
    calls = 0
    while view:
        sent = writer.send(view)
        calls += 1
        if not sent:
            raise ConnectionError("socket closed while sending")
        view = view[sent:]
    return calls


def crc32(payload: bytes | memoryview) -> int:
    return zlib.crc32(payload) & 0xFFFFFFFF


def frame_digest_chain(previous: int, frame_crc: int) -> int:
    """Chain frame CRC values without scanning the full pixel payload twice."""

    return zlib.crc32(struct.pack("!I", frame_crc), previous) & 0xFFFFFFFF


@dataclass(frozen=True)
class SessionHeader:
    input_width: int
    input_height: int
    output_width: int
    output_height: int
    fps_numerator: int
    fps_denominator: int
    declared_frame_count: int
    pixel_format: int
    bytes_per_frame: int

    @property
    def pixel_format_name(self) -> str:
        try:
            return PIXEL_FORMAT_NAMES[self.pixel_format]
        except KeyError as exc:
            raise ProtocolError(f"unsupported pixel format id {self.pixel_format}") from exc

    @property
    def hardware_output_words(self) -> int:
        if self.pixel_format == PIXEL_FORMAT_MB420:
            return self.bytes_per_frame // 4
        return self.output_width * self.output_height

    def pack(self) -> bytes:
        if not MIN_FRAME_WIDTH <= self.input_width <= MAX_FRAME_WIDTH:
            raise ProtocolError(
                f"input width must be in {MIN_FRAME_WIDTH}..{MAX_FRAME_WIDTH}"
            )
        if not MIN_FRAME_HEIGHT <= self.input_height <= MAX_FRAME_HEIGHT:
            raise ProtocolError(
                f"input height must be in {MIN_FRAME_HEIGHT}..{MAX_FRAME_HEIGHT}"
            )
        valid_geometry = any(
            (self.output_width, self.output_height)
            == (self.input_width * scale, self.input_height * scale)
            for scale in SUPPORTED_SCALES
        )
        if not valid_geometry:
            raise ProtocolError(
                "output dimensions must be exactly 2x or 4x the input"
            )
        if self.fps_numerator <= 0 or self.fps_denominator <= 0:
            raise ProtocolError("FPS fraction must be positive")
        if self.declared_frame_count < -1:
            raise ProtocolError("declared frame count must be -1 or non-negative")
        if self.pixel_format not in PIXEL_FORMAT_NAMES:
            raise ProtocolError(f"unsupported pixel format id {self.pixel_format}")
        if self.output_width % 2 or self.output_height % 2:
            raise ProtocolError("YUV420P output dimensions must be even")
        expected = self.output_width * self.output_height * 3 // 2
        if self.bytes_per_frame != expected:
            raise ProtocolError(f"YUV420P bytes_per_frame mismatch: {self.bytes_per_frame}/{expected}")
        return SESSION_STRUCT.pack(
            SESSION_MAGIC, PROTOCOL_VERSION, SESSION_STRUCT.size,
            self.input_width, self.input_height, self.output_width, self.output_height,
            self.fps_numerator, self.fps_denominator, self.declared_frame_count,
            self.pixel_format, 0, self.bytes_per_frame,
        )

    @classmethod
    def unpack(cls, payload: bytes) -> "SessionHeader":
        if len(payload) != SESSION_STRUCT.size:
            raise ProtocolError("invalid session header size")
        values = SESSION_STRUCT.unpack(payload)
        magic, version, header_length, _, _, _, _, _, _, _, _, reserved, _ = values
        if magic != SESSION_MAGIC or version != PROTOCOL_VERSION or header_length != SESSION_STRUCT.size or reserved:
            raise ProtocolError("invalid session header identity")
        result = cls(values[3], values[4], values[5], values[6], values[7], values[8], values[9], values[10], values[12])
        result.pack()
        return result


@dataclass(frozen=True)
class FrameHeader:
    frame_index: int
    payload_length: int
    payload_crc32: int
    hardware_status: int
    hardware_error_code: int
    input_word_count: int
    output_word_count: int

    def pack(self) -> bytes:
        if self.frame_index < 0 or self.payload_length <= 0:
            raise ProtocolError("invalid frame index or payload length")
        return FRAME_STRUCT.pack(FRAME_MAGIC, self.frame_index, self.payload_length, self.payload_crc32, self.hardware_status, self.hardware_error_code, self.input_word_count, self.output_word_count)

    @classmethod
    def unpack(cls, payload: bytes) -> "FrameHeader":
        if len(payload) != FRAME_STRUCT.size:
            raise ProtocolError("invalid frame header size")
        values = FRAME_STRUCT.unpack(payload)
        if values[0] != FRAME_MAGIC:
            raise ProtocolError("invalid frame header magic")
        return cls(*values[1:])


@dataclass(frozen=True)
class EndRecord:
    actual_frame_count: int
    total_payload_bytes: int
    frame_crc_chain: int
    report_length: int

    def pack(self) -> bytes:
        if min(self.actual_frame_count, self.total_payload_bytes, self.report_length) < 0:
            raise ProtocolError("invalid end record values")
        if self.report_length > MAX_REPORT_BYTES:
            raise ProtocolError("end report is too large")
        return END_STRUCT.pack(END_MAGIC, self.actual_frame_count, self.total_payload_bytes, self.frame_crc_chain, self.report_length)

    @classmethod
    def unpack(cls, payload: bytes) -> "EndRecord":
        if len(payload) != END_STRUCT.size:
            raise ProtocolError("invalid end record size")
        values = END_STRUCT.unpack(payload)
        if values[0] != END_MAGIC:
            raise ProtocolError("invalid end record magic")
        result = cls(*values[1:])
        result.pack()
        return result
