"""Resolve the content-addressed offline runtimes initialized by python.cmd."""
import json
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def runtime_python(mode="host"):
    if mode not in ("host", "reference"):
        raise ValueError("Unknown runtime: " + mode)
    entry = json.loads((ROOT / "runtime/environments.json").read_text("utf-8"))["environments"][mode]
    cache = Path(os.environ.get("SR_LUT_CACHE_DIR") or
                 Path(os.environ["LOCALAPPDATA"]) / "sr_lut_fpga/cache")
    return cache / (mode + "-" + entry["sha256"][:16]) / entry["executable"]
