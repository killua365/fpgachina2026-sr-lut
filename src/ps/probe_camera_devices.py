"""Probe V4L2 camera nodes without loading an FPGA Overlay."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import time
from typing import Any, Callable


def _fourcc_name(value: float) -> str:
    code = int(value)
    return "".join(chr((code >> (8 * index)) & 0xFF) for index in range(4))


def probe_device(
    device: str,
    *,
    width: int = 640,
    height: int = 360,
    fps: int = 30,
    fourcc: str = "YUYV",
    cv2_module: Any = None,
    capture_factory: Callable[..., Any] | None = None,
) -> dict[str, Any]:
    if min(int(width), int(height), int(fps)) < 1:
        raise ValueError("width, height and fps must be positive")
    if len(fourcc) != 4 or not fourcc.isascii():
        raise ValueError("FOURCC must contain four ASCII characters")
    if cv2_module is None:
        try:
            import cv2 as cv2_module
        except ImportError as exc:
            return {
                "device": device,
                "ok": False,
                "error": f"OpenCV unavailable: {exc}",
            }
    if capture_factory is None:
        capture_factory = cv2_module.VideoCapture
    capture = capture_factory(device, cv2_module.CAP_V4L2)
    started = time.perf_counter()
    try:
        if not capture.isOpened():
            return {"device": device, "ok": False, "error": "open failed"}
        capture.set(
            cv2_module.CAP_PROP_FOURCC,
            int.from_bytes(fourcc.encode("ascii"), "little"),
        )
        capture.set(cv2_module.CAP_PROP_FRAME_WIDTH, int(width))
        capture.set(cv2_module.CAP_PROP_FRAME_HEIGHT, int(height))
        capture.set(cv2_module.CAP_PROP_FPS, int(fps))
        ok, frame = capture.read()
        negotiated_fourcc = capture.get(cv2_module.CAP_PROP_FOURCC)
        return {
            "device": device,
            "ok": bool(ok and frame is not None),
            "read_seconds": time.perf_counter() - started,
            "frame_shape": None if frame is None else list(frame.shape),
            "requested": {
                "width": int(width),
                "height": int(height),
                "fps": int(fps),
                "fourcc": fourcc,
            },
            "negotiated": {
                "width": int(capture.get(cv2_module.CAP_PROP_FRAME_WIDTH)),
                "height": int(capture.get(cv2_module.CAP_PROP_FRAME_HEIGHT)),
                "fps": float(capture.get(cv2_module.CAP_PROP_FPS)),
                "fourcc": _fourcc_name(negotiated_fourcc),
            },
            "error": None if ok and frame is not None else "first frame unavailable",
        }
    except BaseException as exc:
        return {
            "device": device,
            "ok": False,
            "error": f"{type(exc).__name__}: {exc}",
        }
    finally:
        capture.release()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("devices", nargs="*", default=["/dev/video0", "/dev/video1"])
    parser.add_argument("--width", type=int, default=640)
    parser.add_argument("--height", type=int, default=360)
    parser.add_argument("--fps", type=int, default=30)
    parser.add_argument("--fourcc", default="YUYV")
    parser.add_argument("--output", type=Path)
    return parser


def main() -> int:
    args = build_parser().parse_args()
    report = {
        "schema_version": 1,
        "probe": "v4l2-camera-nodes",
        "results": [
            probe_device(
                device,
                width=args.width,
                height=args.height,
                fps=args.fps,
                fourcc=args.fourcc,
            )
            for device in args.devices
        ],
    }
    rendered = json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True)
    print(rendered)
    if args.output is not None:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered + "\n", encoding="utf-8")
    return 0 if any(item["ok"] for item in report["results"]) else 1


if __name__ == "__main__":
    raise SystemExit(main())
