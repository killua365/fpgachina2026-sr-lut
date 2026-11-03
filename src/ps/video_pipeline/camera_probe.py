"""Measure negotiated USB camera modes without loading the FPGA overlay."""

from __future__ import annotations

import argparse
import json
import time

import cv2


def fourcc_value(name: str) -> int:
    if len(name) != 4:
        raise ValueError("FOURCC must contain four characters")
    return cv2.VideoWriter_fourcc(*name)


def probe(device: str, fourcc: str, width: int, height: int, fps: int, frames: int):
    capture = cv2.VideoCapture(device, cv2.CAP_V4L2)
    if not capture.isOpened():
        raise RuntimeError(f"camera cannot be opened: {device}")
    try:
        capture.set(cv2.CAP_PROP_FOURCC, fourcc_value(fourcc))
        capture.set(cv2.CAP_PROP_FRAME_WIDTH, width)
        capture.set(cv2.CAP_PROP_FRAME_HEIGHT, height)
        capture.set(cv2.CAP_PROP_FPS, fps)
        if hasattr(cv2, "CAP_PROP_BUFFERSIZE"):
            capture.set(cv2.CAP_PROP_BUFFERSIZE, 1)
        negotiated = int(capture.get(cv2.CAP_PROP_FOURCC))
        negotiated_name = "".join(
            chr((negotiated >> (8 * index)) & 0xFF) for index in range(4)
        ).rstrip("\x00")
        started = time.perf_counter()
        successful = 0
        for _ in range(frames):
            ok, frame = capture.read()
            if not ok or frame is None:
                break
            successful += 1
        elapsed = time.perf_counter() - started
        return {
            "requested_fourcc": fourcc,
            "negotiated_fourcc": negotiated_name,
            "negotiated_width": int(capture.get(cv2.CAP_PROP_FRAME_WIDTH)),
            "negotiated_height": int(capture.get(cv2.CAP_PROP_FRAME_HEIGHT)),
            "negotiated_fps": float(capture.get(cv2.CAP_PROP_FPS)),
            "frames": successful,
            "elapsed_seconds": elapsed,
            "measured_fps": successful / elapsed,
        }
    finally:
        capture.release()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--device", default="/dev/video0")
    parser.add_argument("--width", type=int, default=640)
    parser.add_argument("--height", type=int, default=480)
    parser.add_argument("--fps", type=int, default=30)
    parser.add_argument("--frames", type=int, default=120)
    args = parser.parse_args()
    results = []
    for fourcc in ("YUYV", "MJPG"):
        results.append(
            probe(args.device, fourcc, args.width, args.height, args.fps, args.frames)
        )
    print(json.dumps(results, indent=2))


if __name__ == "__main__":
    main()
