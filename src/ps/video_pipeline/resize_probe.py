"""Benchmark camera frame resize choices on the board CPU."""

from __future__ import annotations

import argparse
import json
import time

import cv2


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--device", default="/dev/video0")
    parser.add_argument("--camera-width", type=int, default=640)
    parser.add_argument("--camera-height", type=int, default=360)
    parser.add_argument("--target-width", type=int, default=480)
    parser.add_argument("--target-height", type=int, default=270)
    parser.add_argument("--iterations", type=int, default=300)
    args = parser.parse_args()

    capture = cv2.VideoCapture(args.device, cv2.CAP_V4L2)
    capture.set(cv2.CAP_PROP_FRAME_WIDTH, args.camera_width)
    capture.set(cv2.CAP_PROP_FRAME_HEIGHT, args.camera_height)
    ok, frame = capture.read()
    capture.release()
    if not ok or frame is None:
        raise RuntimeError("camera did not return a frame")

    results = []
    for name, interpolation in (
        ("nearest", cv2.INTER_NEAREST),
        ("linear", cv2.INTER_LINEAR),
        ("area", cv2.INTER_AREA),
    ):
        started = time.perf_counter()
        for _ in range(args.iterations):
            cv2.resize(
                frame,
                (args.target_width, args.target_height),
                interpolation=interpolation,
            )
        elapsed = time.perf_counter() - started
        results.append(
            {
                "interpolation": name,
                "iterations": args.iterations,
                "mean_seconds": elapsed / args.iterations,
                "frames_per_second": args.iterations / elapsed,
            }
        )
    print(json.dumps({"source_shape": frame.shape, "results": results}, indent=2))


if __name__ == "__main__":
    main()
