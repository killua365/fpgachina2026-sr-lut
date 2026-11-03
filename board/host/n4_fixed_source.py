"""Bounded, one-overlay N4 test source using the qualified DMA recovery owner.

No automatic scale reload is permitted in this entry. The older two-scale CLI
remains available for its original diagnostic purpose.
"""
import argparse
import faulthandler
import json
import math
from pathlib import Path
import sys

PROFILE = {
    "kind": "n4_fixed_overlay_v1", "mode": "1920x1080@30",
    "minimum_pattern_seconds_total": 120,
    "maximum_pattern_seconds_total": 1200,
    "default_pattern_seconds_total": 960,
    "patterns": 4, "overlay_loads": 1, "supervisor_allowance_seconds": 240,
}


def validate_fixed_run(manifest, scale, duration):
    if manifest.get("fixed_source_profile") != PROFILE or manifest.get("mode") != PROFILE["mode"]:
        raise ValueError("Package lacks the exact fixed-source contract")
    if type(scale) is not int or scale not in (2, 4):
        raise ValueError("Fixed source scale must be x2 or x4")
    if (isinstance(duration, bool) or not isinstance(duration, (int, float)) or
            not math.isfinite(duration) or not 120 <= duration <= 1200):
        raise ValueError("Fixed source duration must be finite and 120..1200 seconds")
    for name in ("board/host/n4_fixed_source.py", "board/pynq/hdmi_m1.py",
                 "board/pynq/hdmi_m1_supervisor.py"):
        if name not in {entry["path"] for entry in manifest["files"]}:
            raise ValueError("Fixed source code is absent from package identity")
    return {"scale": scale, "pattern_seconds_total": duration,
            "seconds_per_pattern": duration / 4, "overlay_loads": 1,
            "automatic_scale_switch": False, "hold_display": False}


def worker_plan(root, destination, manifest, scale, duration):
    contract = validate_fixed_run(manifest, scale, duration)
    return {
        "command": [sys.executable, "-B", "-X", "faulthandler",
                    str(root / "board/host/n4_fixed_source.py"), "_worker",
                    "--root", str(root), "--destination", str(destination / "worker"),
                    "--scale", str(scale), "--duration", str(duration)],
        "timeout_seconds": duration + PROFILE["supervisor_allowance_seconds"],
        "contract": contract,
    }


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("check", "run", "_worker"))
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[2])
    parser.add_argument("--destination", type=Path)
    parser.add_argument("--scale", type=int, choices=(2, 4), required=True)
    parser.add_argument("--duration", type=float, default=960)
    parser.add_argument("--lock-fd", type=int, help=argparse.SUPPRESS)
    args = parser.parse_args(argv)
    from hdmi_m1 import run, validate_package
    manifest, _ = validate_package(args.root)
    contract = validate_fixed_run(manifest, args.scale, args.duration)
    if args.command == "check":
        print(json.dumps({"package_verified": True, "contract": contract}))
        return 0
    if args.destination is None:
        parser.error("A new result destination is required")
    faulthandler.enable(all_threads=True)
    if args.command == "_worker":
        return run(args.root, args.destination, contract["seconds_per_pattern"],
                   args.lock_fd, scales=(args.scale,))
    from hdmi_m1_supervisor import supervise
    return supervise(args.root, args.destination, contract["seconds_per_pattern"],
                     fixed_scale=args.scale)


if __name__ == "__main__":
    raise SystemExit(main())
