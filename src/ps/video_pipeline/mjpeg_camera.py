"""Capture a USB camera and send Stage 39 hardware MJPEG to a laptop."""

from __future__ import annotations

import argparse
from fractions import Fraction
import json
import socket
import sys
import time
from pathlib import Path
from typing import Any, Sequence

SCRIPT_DIR = Path(__file__).resolve().parent
PYNQ_DIR = SCRIPT_DIR.parent
if str(PYNQ_DIR) not in sys.path:
    sys.path.insert(0, str(PYNQ_DIR))

from sr_lut_x4_driver import load_overlay
from video_pipeline.live_pipeline import LatestFrameCapture, _configure_capture
from video_pipeline.live_pipeline import prepare_frame
from video_pipeline.mjpeg_protocol import MjpegEndRecord, MjpegFrameHeader
from video_pipeline.mjpeg_protocol import MjpegSessionHeader, crc32
from video_pipeline.mjpeg_protocol import frame_digest_chain, send_all
from video_pipeline.mjpeg_session import MjpegFrameSession


def stream_mjpeg_camera(
    device: str,
    receiver_host: str,
    receiver_port: int,
    *,
    camera_width: int = 640,
    camera_height: int = 360,
    camera_fps: int = 30,
    camera_fourcc: str = "YUYV",
    duration_seconds: float = 0.0,
    frame_limit: int = 0,
    timeout_seconds: float = 10.0,
    buffer_count: int = 2,
    cv2_module: Any = None,
    overlay_loader: Any = load_overlay,
    session_factory: Any = MjpegFrameSession,
) -> dict[str, Any]:
    if cv2_module is None:
        import cv2 as cv2_module
    if duration_seconds < 0 or frame_limit < 0:
        raise ValueError("duration and frame limit must be non-negative")
    capture = cv2_module.VideoCapture(device, cv2_module.CAP_V4L2)
    if not capture.isOpened():
        raise RuntimeError(f"could not open camera {device}")
    camera = _configure_capture(
        capture,
        cv2_module,
        camera_width,
        camera_height,
        camera_fps,
        camera_fourcc,
    )
    latest = LatestFrameCapture(
        capture,
        lambda frame: prepare_frame(frame, 480, 270, cv2_module),
    )
    fps = Fraction(str(camera.get("negotiated_fps") or camera_fps)).limit_denominator(1001)
    declared_count = frame_limit if frame_limit else -1
    header = MjpegSessionHeader(480, 270, 1920, 1080, fps.numerator, fps.denominator, declared_count)
    overlay = overlay_loader()
    started = time.perf_counter()
    last_sequence = -1
    frame_count = 0
    total_bytes = 0
    digest_chain = 0
    frame_seconds: list[float] = []
    latest.start()
    try:
        with socket.create_connection(
            (receiver_host, int(receiver_port)), timeout=timeout_seconds
        ) as connection, session_factory(
            overlay,
            timeout_seconds=timeout_seconds,
            buffer_count=buffer_count,
        ) as session:
            connection.settimeout(timeout_seconds)
            send_all(connection, header.pack())
            while True:
                if frame_limit and frame_count >= frame_limit:
                    break
                if duration_seconds and time.perf_counter() - started >= duration_seconds:
                    break
                captured = latest.get_after(last_sequence, timeout_seconds)
                last_sequence = captured.source_sequence
                result = session.run_bgr(
                    captured.image,
                    buffer_index=frame_count % buffer_count,
                )
                frame_crc = crc32(result.jpeg)
                frame_header = MjpegFrameHeader(
                    frame_index=frame_count,
                    timestamp_ns=int(captured.captured_at * 1_000_000_000),
                    jpeg_length=result.jpeg_length,
                    jpeg_crc32=frame_crc,
                    sr_status=result.sr_status,
                    mjpeg_status=result.mjpeg_status,
                    sr_output_words=result.sr_output_word_count,
                )
                send_all(connection, frame_header.pack())
                send_all(connection, result.jpeg)
                frame_count += 1
                total_bytes += result.jpeg_length
                digest_chain = frame_digest_chain(digest_chain, frame_crc)
                frame_seconds.append(result.total_seconds)
                if frame_count % 30 == 0:
                    elapsed = time.perf_counter() - started
                    print(
                        f"STAGE38_MJPEG_FRAME frame={frame_count} "
                        f"fps={frame_count / max(elapsed, 1e-9):.2f}",
                        flush=True,
                    )
            elapsed = time.perf_counter() - started
            report = {
                "status": "pass",
                "pipeline": "stage38-mjpeg-hardware",
                "frames": frame_count,
                "jpeg_bytes": total_bytes,
                "elapsed_seconds": elapsed,
                "end_to_end_fps": frame_count / max(elapsed, 1e-9),
                "mean_fpga_frame_seconds": (
                    sum(frame_seconds) / len(frame_seconds) if frame_seconds else None
                ),
                "camera": camera,
                "capture": latest.report(),
            }
            report_payload = json.dumps(
                report, sort_keys=True, separators=(",", ":")
            ).encode("utf-8")
            send_all(
                connection,
                MjpegEndRecord(
                    frame_count,
                    total_bytes,
                    digest_chain,
                    len(report_payload),
                ).pack(),
            )
            send_all(connection, report_payload)
            return report
    finally:
        latest.stop()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--device", default="/dev/video0")
    parser.add_argument("--receiver-host", required=True)
    parser.add_argument("--receiver-port", type=int, default=5038)
    parser.add_argument("--camera-width", type=int, default=640)
    parser.add_argument("--camera-height", type=int, default=360)
    parser.add_argument("--camera-fps", type=int, default=30)
    parser.add_argument("--camera-fourcc", default="YUYV")
    parser.add_argument("--duration", type=float, default=0.0)
    parser.add_argument("--frames", type=int, default=0)
    parser.add_argument("--timeout", type=float, default=10.0)
    parser.add_argument("--buffers", type=int, default=2)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    report = stream_mjpeg_camera(
        args.device,
        args.receiver_host,
        args.receiver_port,
        camera_width=args.camera_width,
        camera_height=args.camera_height,
        camera_fps=args.camera_fps,
        camera_fourcc=args.camera_fourcc,
        duration_seconds=args.duration,
        frame_limit=args.frames,
        timeout_seconds=args.timeout,
        buffer_count=args.buffers,
    )
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
