"""Stream a USB UVC camera through the pipelined x4 MB420 path."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
from typing import Optional, Sequence

SCRIPT_DIR = Path(__file__).resolve().parent
PYNQ_DIR = SCRIPT_DIR.parent
if str(PYNQ_DIR) not in sys.path:
    sys.path.insert(0, str(PYNQ_DIR))

from video_pipeline.live_pipeline import DEFAULT_BUFFER_COUNT, DEFAULT_CAMERA_FOURCC
from video_pipeline.live_pipeline import DEFAULT_CAMERA_SIZE, DEFAULT_INPUT_SIZE, stream_camera


def _parse_size(value: str) -> tuple[int, int]:
    try:
        width, height = (int(part) for part in value.lower().split("x", 1))
    except (TypeError, ValueError) as exc:
        raise argparse.ArgumentTypeError("size must look like 480x270") from exc
    if width < 32 or width > 480 or height < 32 or height > 320:
        raise argparse.ArgumentTypeError("size must be within 32..480 x 32..320")
    if width % 2 or height % 2:
        raise argparse.ArgumentTypeError("size must be even for YUV420")
    return width, height

def main(argv: Optional[Sequence[str]] = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--device", default="/dev/video0")
    parser.add_argument("--receiver-host", required=True)
    parser.add_argument("--receiver-port", type=int, default=19037)
    parser.add_argument("--output-size", type=_parse_size, default=DEFAULT_INPUT_SIZE)
    parser.add_argument("--camera-width", type=int, default=DEFAULT_CAMERA_SIZE[0])
    parser.add_argument("--camera-height", type=int, default=DEFAULT_CAMERA_SIZE[1])
    parser.add_argument("--camera-fps", type=int, default=30)
    parser.add_argument("--camera-fourcc", choices=("YUYV", "MJPG"), default=DEFAULT_CAMERA_FOURCC)
    parser.add_argument("--duration", type=float, default=0.0, help="seconds; 0 means until Ctrl-C")
    parser.add_argument("--frames", type=int, default=0, help="frame limit; 0 means unlimited")
    parser.add_argument("--buffer-count", type=int, choices=range(1, 9), default=DEFAULT_BUFFER_COUNT)
    parser.add_argument("--timeout", type=float, default=10.0)
    args = parser.parse_args(argv)
    report = stream_camera(
        args.device,
        args.receiver_host,
        args.receiver_port,
        input_size=args.output_size,
        camera_size=(args.camera_width, args.camera_height),
        camera_fps=args.camera_fps,
        camera_fourcc=args.camera_fourcc,
        duration_seconds=args.duration,
        frame_limit=args.frames,
        buffer_count=args.buffer_count,
        timeout_seconds=args.timeout,
    )
    print("LIVE_CAMERA_PASS", json.dumps(report, ensure_ascii=False), flush=True)


if __name__ == "__main__":
    main()
