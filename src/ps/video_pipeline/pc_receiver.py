"""PC receiver and FFmpeg sink for FPGA-produced YUV420P frames."""

from __future__ import annotations

import argparse
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

from video_pipeline.protocol import END_MAGIC, END_STRUCT, FRAME_MAGIC, FRAME_STRUCT, SESSION_STRUCT
from video_pipeline.protocol import EndRecord, FrameHeader, ProtocolError, SessionHeader, crc32, frame_digest_chain, read_exact, read_exact_into
from video_pipeline.protocol import PIXEL_FORMAT_MB420
from video_pipeline.mb420 import mb420_to_i420
from video_pipeline.identity import APPLICATION_VERSION, HARDWARE_STAGE, VIDEO_PIPELINE


SOCKET_RECEIVE_BUFFER_BYTES = 16 * 1024 * 1024


def _write_all(stream: BinaryIO, payload: bytes | memoryview) -> None:
    view = memoryview(payload).cast("B")
    while view:
        written = stream.write(view)
        if written is None:
            stream.flush()
            return
        if written <= 0:
            raise BrokenPipeError("FFmpeg stdin closed")
        view = view[written:]


def _finite_summary(values: list[float]) -> dict[str, float]:
    if not values:
        raise ValueError("summary requires finite samples")
    samples = np.asarray(values, dtype=np.float64)
    if not np.all(np.isfinite(samples)):
        raise ValueError("summary requires finite samples")
    return {
        "minimum": float(np.min(samples)),
        "mean": float(np.mean(samples)),
        "p95": float(np.percentile(samples, 95)),
        "p99": float(np.percentile(samples, 99)),
        "maximum": float(np.max(samples)),
    }


def ffmpeg_command(executable: Path, output: Path, session: SessionHeader, encoder: str = "libx264") -> list[str]:
    if encoder not in ("libx264", "libx265", "mjpeg"):
        raise ValueError("encoder must be libx264, libx265, or mjpeg")
    command = [str(executable), "-hide_banner", "-loglevel", "warning", "-y", "-f", "rawvideo", "-pix_fmt", "yuv420p", "-video_size", f"{session.output_width}x{session.output_height}", "-framerate", f"{session.fps_numerator}/{session.fps_denominator}", "-i", "pipe:0", "-an"]
    if encoder == "mjpeg":
        command += ["-c:v", "mjpeg", "-q:v", "3"]
    else:
        command += ["-c:v", encoder, "-preset", "veryfast", "-crf", "18", "-pix_fmt", "yuv420p"]
    command += ["-movflags", "+faststart", str(output)]
    return command


def receive_session(
    connection: Any,
    *,
    ffmpeg_path: Optional[Path] = None,
    output_video: Optional[Path] = None,
    output_raw: Optional[Path] = None,
    encoder: str = "libx264",
    report_path: Optional[Path] = None,
    socket_timeout_seconds: float = 60.0,
    receive_buffer_count: int = 4,
) -> dict[str, Any]:
    if isinstance(receive_buffer_count, bool) or not 2 <= int(receive_buffer_count) <= 16:
        raise ValueError("receive_buffer_count must be in 2..16")
    receive_buffer_count = int(receive_buffer_count)
    settimeout = getattr(connection, "settimeout", None)
    if callable(settimeout):
        settimeout(socket_timeout_seconds)
    socket_receive_buffer_bytes = None
    setsockopt = getattr(connection, "setsockopt", None)
    getsockopt = getattr(connection, "getsockopt", None)
    if callable(setsockopt) and callable(getsockopt):
        try:
            setsockopt(
                socket.SOL_SOCKET,
                socket.SO_RCVBUF,
                SOCKET_RECEIVE_BUFFER_BYTES,
            )
            socket_receive_buffer_bytes = int(
                getsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF)
            )
        except OSError:
            socket_receive_buffer_bytes = None
    session = SessionHeader.unpack(read_exact(connection, SESSION_STRUCT.size))
    if bool(ffmpeg_path) != bool(output_video):
        raise ValueError("ffmpeg_path and output_video must be provided together")
    command = ffmpeg_command(ffmpeg_path, output_video, session, encoder) if ffmpeg_path and output_video else None
    if output_video:
        output_video.parent.mkdir(parents=True, exist_ok=True)
    if output_raw:
        output_raw.parent.mkdir(parents=True, exist_ok=True)
    encoder_process = subprocess.Popen(command, stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE) if command else None
    stderr_chunks: list[bytes] = []
    stderr_thread: threading.Thread | None = None
    if encoder_process and encoder_process.stderr:
        def drain_stderr() -> None:
            for chunk in iter(lambda: encoder_process.stderr.read(4096), b""):
                stderr_chunks.append(chunk)
        stderr_thread = threading.Thread(target=drain_stderr, daemon=True)
        stderr_thread.start()
    raw_handle = output_raw.open("wb") if output_raw else None
    count = 0; total = 0; chain = 0; board_report = None; stderr = b""
    frame_buffers = [bytearray(session.bytes_per_frame) for _ in range(receive_buffer_count)]
    reorder_mb420 = session.pixel_format == PIXEL_FORMAT_MB420
    output_buffers = (
        [bytearray(session.bytes_per_frame) for _ in range(receive_buffer_count)]
        if reorder_mb420
        else frame_buffers
    )
    free_buffers: queue.Queue[int] = queue.Queue(maxsize=receive_buffer_count)
    processor_queue: queue.Queue[Any] = queue.Queue(maxsize=receive_buffer_count)
    for buffer_index in range(receive_buffer_count):
        free_buffers.put(buffer_index)
    processor_errors: list[BaseException] = []
    network_receive_seconds: list[float] = []
    crc_seconds: list[float] = []
    encoder_write_seconds: list[float] = []
    mb420_reorder_seconds: list[float] = []
    processor_stop = threading.Event()
    processor_thread: threading.Thread | None = None
    processed = {"count": 0, "total": 0, "chain": 0}

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
                crc_started = time.perf_counter()
                actual_crc = crc32(payload)
                crc_seconds.append(time.perf_counter() - crc_started)
                if actual_crc != header.payload_crc32:
                    raise ProtocolError(
                        f"frame {header.frame_index} CRC mismatch"
                    )
                if reorder_mb420:
                    reorder_started = time.perf_counter()
                    mb420_to_i420(
                        payload,
                        session.output_width,
                        session.output_height,
                        output_buffers[active_index],
                    )
                    mb420_reorder_seconds.append(
                        time.perf_counter() - reorder_started
                    )
                if raw_handle:
                    _write_all(raw_handle, output_buffers[active_index])
                if encoder_process is not None and encoder_process.stdin is not None:
                    write_started = time.perf_counter()
                    _write_all(
                        encoder_process.stdin,
                        output_buffers[active_index],
                    )
                    encoder_write_seconds.append(
                        time.perf_counter() - write_started
                    )
                processed["count"] += 1
                processed["total"] += len(payload)
                processed["chain"] = frame_digest_chain(
                    processed["chain"],
                    header.payload_crc32,
                )
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

    def enqueue_for_processor(item: Any) -> None:
        while True:
            raise_processor_error()
            try:
                processor_queue.put(item, timeout=0.1)
                return
            except queue.Full:
                continue

    processor_thread = threading.Thread(
        target=processor_target,
        name="video-frame-processor",
        daemon=True,
    )
    processor_thread.start()

    receive_started = time.perf_counter()
    received_count = 0
    received_total = 0
    try:
        while True:
            marker = read_exact(connection, 8)
            if marker == END_MAGIC:
                end = EndRecord.unpack(marker + read_exact(connection, END_STRUCT.size - 8))
                board_report = json.loads(read_exact(connection, end.report_length).decode("utf-8"))
                if not isinstance(board_report, dict) or board_report.get("status") != "pass":
                    raise ProtocolError("board report did not pass")
                identities = {
                    "hardware_stage": HARDWARE_STAGE,
                    "application_version": APPLICATION_VERSION,
                    "video_pipeline": VIDEO_PIPELINE,
                }
                for key, expected in identities.items():
                    actual = board_report.get(key)
                    if actual is not None and actual != expected:
                        raise ProtocolError(
                            f"board report {key} mismatch: {actual!r}/{expected!r}"
                        )
                break
            if marker != FRAME_MAGIC:
                raise ProtocolError(f"unexpected record marker {marker!r}")
            header = FrameHeader.unpack(marker + read_exact(connection, FRAME_STRUCT.size - 8))
            if header.frame_index != received_count or header.payload_length != session.bytes_per_frame:
                raise ProtocolError("frame index or payload length mismatch")
            if header.hardware_error_code != 0 or not header.hardware_status & (1 << 2):
                raise ProtocolError("board reported unsuccessful frame")
            if (header.input_word_count, header.output_word_count) != (
                session.input_width * session.input_height,
                session.hardware_output_words,
            ):
                raise ProtocolError("DMA word counts do not match session")
            buffer_index = acquire_buffer()
            owned_by_processor = False
            try:
                payload = memoryview(frame_buffers[buffer_index])
                read_started = time.perf_counter()
                read_exact_into(connection, payload)
                network_receive_seconds.append(time.perf_counter() - read_started)
                enqueue_for_processor((buffer_index, header))
                owned_by_processor = True
                received_count += 1
                received_total += len(payload)
            finally:
                if not owned_by_processor:
                    free_buffers.put(buffer_index)
        enqueue_for_processor(None)
        processor_thread.join(timeout=120)
        if processor_thread.is_alive():
            raise RuntimeError("frame processor did not stop within 120 seconds")
        raise_processor_error()
        count = int(processed["count"])
        total = int(processed["total"])
        chain = int(processed["chain"])
        if (received_count, received_total) != (count, total):
            raise ProtocolError("processed frame totals do not match network input")
        if (end.actual_frame_count, end.total_payload_bytes, end.frame_crc_chain) != (count, total, chain):
            raise ProtocolError("end totals do not match received frames")
    finally:
        processor_stop.set()
        if processor_thread is not None and processor_thread.is_alive():
            try:
                processor_queue.put_nowait(None)
            except queue.Full:
                pass
            processor_thread.join(timeout=5)
        if raw_handle:
            raw_handle.close()
        if encoder_process and encoder_process.stdin:
            encoder_process.stdin.close()
        if encoder_process:
            try:
                return_code = encoder_process.wait(timeout=120)
            except subprocess.TimeoutExpired as error:
                encoder_process.kill()
                encoder_process.wait(timeout=10)
                raise RuntimeError("FFmpeg did not exit within 120 seconds") from error
            if stderr_thread: stderr_thread.join(timeout=5)
            stderr = b"".join(stderr_chunks)
            if encoder_process.stderr: encoder_process.stderr.close()
        else:
            return_code = None
    receive_elapsed = time.perf_counter() - receive_started
    result = {
        "schema_version": 2,
        "status": "pass",
        "session": {
            "input_width": session.input_width,
            "input_height": session.input_height,
            "output_width": session.output_width,
            "output_height": session.output_height,
            "fps": session.fps_numerator / session.fps_denominator,
            "declared_frame_count": session.declared_frame_count,
            "pixel_format": session.pixel_format_name,
            "bytes_per_frame": session.bytes_per_frame,
        },
        "received": {"frame_count": count, "payload_bytes": total},
        "pipeline": {
            "receive_buffer_count": receive_buffer_count,
            "reusable_receive_buffers": True,
            "separate_frame_processor": True,
            "separate_ffmpeg_writer": encoder_process is not None,
            "mb420_to_i420_reorder": reorder_mb420,
            "socket_receive_buffer_requested_bytes": SOCKET_RECEIVE_BUFFER_BYTES,
            "socket_receive_buffer_bytes": socket_receive_buffer_bytes,
        },
        "performance": {
            "receive_wall_seconds": receive_elapsed,
            "receive_fps": count / receive_elapsed,
            "network_receive_seconds": _finite_summary(network_receive_seconds),
            "crc32_seconds": _finite_summary(crc_seconds),
            "mb420_to_i420_seconds": (
                _finite_summary(mb420_reorder_seconds)
                if mb420_reorder_seconds
                else None
            ),
            "ffmpeg_write_seconds": (
                _finite_summary(encoder_write_seconds)
                if encoder_write_seconds
                else None
            ),
            "payload_megabytes_per_second": total / receive_elapsed / 1_000_000.0,
        },
        "ffmpeg": {
            "command": command,
            "return_code": return_code,
            "stderr": stderr.decode("utf-8", errors="replace"),
        },
        "board_report": board_report,
    }
    if session.declared_frame_count >= 0 and session.declared_frame_count != count:
        raise ProtocolError(f"frame count mismatch: {count}/{session.declared_frame_count}")
    if return_code not in (None, 0):
        raise RuntimeError(f"FFmpeg failed with exit code {return_code}")
    if report_path:
        report_path.parent.mkdir(parents=True, exist_ok=True)
        report_path.write_text(json.dumps(result, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return result


def serve_once(bind_host: str, port: int, **kwargs: Any) -> dict[str, Any]:
    import socket
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
        listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1); listener.bind((bind_host, int(port))); listener.listen(1)
        print(f"VIDEO_RECEIVER_LISTENING {bind_host}:{port}", flush=True)
        connection, address = listener.accept(); print(f"VIDEO_RECEIVER_CONNECTED {address[0]}:{address[1]}", flush=True)
        with connection: return receive_session(connection, **kwargs)


def numbered_output_path(path: Path | None, index: int, total: int) -> Path | None:
    if path is None or total == 1:
        return path
    if not 0 <= index < total:
        raise ValueError("session index is outside the requested session count")
    return path.with_name(f"{path.stem}_run{index + 1}{path.suffix}")


def main(argv: Optional[Sequence[str]] = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bind-host", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=19037)
    parser.add_argument("--report", type=Path, required=True)
    parser.add_argument("--ffmpeg-path", type=Path)
    parser.add_argument("--output-video", type=Path)
    parser.add_argument("--raw-output", type=Path)
    parser.add_argument(
        "--encoder",
        choices=("libx264", "libx265", "mjpeg"),
        default="libx264",
    )
    parser.add_argument("--sessions", type=int, default=1)
    args = parser.parse_args(argv)
    if not 1 <= args.sessions <= 16:
        raise ValueError("sessions must be in 1..16")
    results = []
    for index in range(args.sessions):
        report_path = numbered_output_path(args.report, index, args.sessions)
        try:
            result = serve_once(
                args.bind_host,
                args.port,
                ffmpeg_path=args.ffmpeg_path,
                output_video=numbered_output_path(
                    args.output_video,
                    index,
                    args.sessions,
                ),
                output_raw=numbered_output_path(
                    args.raw_output,
                    index,
                    args.sessions,
                ),
                encoder=args.encoder,
                report_path=report_path,
            )
        except BaseException as exc:
            assert report_path is not None
            report_path.parent.mkdir(parents=True, exist_ok=True)
            report_path.write_text(
                json.dumps(
                    {
                        "schema_version": 1,
                        "status": "fail",
                        "session_index": index + 1,
                        "error": f"{type(exc).__name__}: {exc}",
                    },
                    indent=2,
                )
                + "\n",
                encoding="utf-8",
            )
            raise
        results.append(result)
        print(
            f"VIDEO_RECEIVER_SESSION_PASS session={index + 1}/{args.sessions}",
            flush=True,
        )
    if args.sessions == 1:
        print("VIDEO_RECEIVER_PASS", json.dumps(results[0], ensure_ascii=False))
    else:
        print(f"VIDEO_RECEIVER_MATRIX_PASS sessions={args.sessions}")


if __name__ == "__main__":
    main()
