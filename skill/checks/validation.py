"""Portable validation primitives extracted from project validation practices."""
from __future__ import annotations

import hashlib
import math
from pathlib import Path
import re
import xml.etree.ElementTree as ET

import numpy as np


def sha256(path):
    result = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            result.update(block)
    return result.hexdigest()


def local_path(root, name):
    path = (Path(root) / name).resolve()
    path.relative_to(Path(root).resolve())
    return path


def verify_files(root, entries):
    if not entries:
        raise ValueError("File manifest is empty")
    seen = set()
    for item in entries:
        name = item["path"]
        if name in seen:
            raise ValueError("Duplicate manifest entry: " + name)
        seen.add(name)
        path = local_path(root, name)
        if not path.is_file() or sha256(path) != item["sha256"].lower():
            raise ValueError("Missing file or SHA-256 mismatch: " + name)
    return {"files_verified": len(seen), "hardware_function_verified": False}


def validate_hwh(path, required_ips, dma_ip=None):
    root = ET.parse(path).getroot()
    modules = {}
    for node in root.findall(".//MODULE"):
        full = (node.get("FULLNAME") or node.get("INSTANCE") or "").lstrip("/")
        if full:
            modules[full] = node
    if not required_ips or any(name not in modules for name in required_ips):
        raise ValueError("HWH required IP missing (use full hierarchy names)")
    if dma_ip:
        if dma_ip not in modules:
            raise ValueError("HWH DMA missing")
        module = modules[dma_ip]
        if ":axi_dma:" not in (module.get("VLNV") or ""):
            raise ValueError("Selected IP is not AXI DMA")
        params = {n.get("NAME").upper(): n.get("VALUE")
                  for n in module.findall("./PARAMETERS/PARAMETER")}
        for key, expected in (("C_INCLUDE_SG", 0), ("C_INCLUDE_MM2S", 1),
                              ("C_INCLUDE_S2MM", 1)):
            if key not in params or int(params[key]) != expected:
                raise ValueError("Unsupported DMA parameter: " + key)
    info = root.find("SYSTEMINFO")
    return {"required_ips": list(required_ips),
            "device": None if info is None else dict(info.attrib),
            "hardware_function_verified": False}


def compare_arrays(actual, expected, atol=0.0, rtol=0.0):
    actual, expected = np.asarray(actual), np.asarray(expected)
    if actual.shape != expected.shape or actual.dtype != expected.dtype:
        raise ValueError("Shape or dtype differs from golden reference")
    if actual.size == 0 or actual.dtype.kind not in "uif":
        raise ValueError("Only nonempty real numeric arrays are supported")
    if not all(math.isfinite(v) and v >= 0 for v in (atol, rtol)):
        raise ValueError("Tolerances must be finite and nonnegative")
    if not np.isfinite(actual).all() or not np.isfinite(expected).all():
        raise ValueError("Nonfinite values cannot silently pass")
    # Python integer subtraction avoids unsigned wrap and int64 overflow.
    if actual.dtype.kind in "ui":
        if atol or rtol:
            raise ValueError("Integer contracts require exact comparison")
        delta = np.abs(actual.astype(object) - expected.astype(object))
        bad = actual != expected
    else:
        delta = np.abs(actual.astype(np.float64) - expected.astype(np.float64))
        bad = delta > atol + rtol * np.abs(expected.astype(np.float64))
    return {"passed": not bool(np.any(bad)), "elements": int(actual.size),
            "mismatches": int(np.count_nonzero(bad)),
            "maximum_absolute_error": float(delta.max()),
            "atol": atol, "rtol": rtol}


TIMING_KEYS = (
    "setup_wns_ns", "setup_tns_ns", "setup_failing_endpoints",
    "hold_wns_ns", "hold_tns_ns", "hold_failing_endpoints",
    "pulse_wns_ns", "pulse_tns_ns", "pulse_failing_endpoints",
)


def timing_summary(text):
    if "Clock Summary" not in text:
        raise ValueError("Missing Clock Summary after timing results")
    try:
        summary = text.split("Design Timing Summary", 1)[1].split("Clock Summary", 1)[0]
    except IndexError as exc:
        raise ValueError("Missing full Design Timing Summary") from exc
    number = r"(-?\d+(?:\.\d+)?)"
    group = rf"{number}\s+{number}\s+(\d+)\s+\d+"
    rows = re.findall(rf"^\s*{group}\s+{group}\s+{group}\s*$", summary, re.M)
    if len(rows) != 1:
        raise ValueError("Unsupported or ambiguous timing report")
    values = dict(zip(TIMING_KEYS, map(float, rows[0])))
    passed = all(math.isfinite(v) and v >= 0 and
                 (v == 0 if "tns" in k or "failing" in k else True)
                 for k, v in values.items())
    return {"passed": passed, **values,
            "scope": "reported_constrained_paths_only"}


def utilization_summary(text, instance):
    """Vivado report_utilization -hierarchical table, explicit total row."""
    header = None
    aliases = {"lut": "Total LUTs", "ff": "FFs", "ramb36": "RAMB36",
               "ramb18": "RAMB18", "dsp": "DSP Blocks"}
    matches = []
    for line in text.splitlines():
        if not line.startswith("|"):
            continue
        cells = [s.strip() for s in line.strip("|").split("|")]
        if "Instance" in cells and "Total LUTs" in cells:
            header = cells
        elif header and cells and cells[0] == instance:
            row = dict(zip(header, cells))
            try:
                matches.append({k: int(row[v].replace(",", "")) for k, v in aliases.items()})
            except (KeyError, ValueError) as exc:
                raise ValueError("Unsupported utilization table") from exc
    if len(matches) != 1:
        raise ValueError("Missing or ambiguous utilization instance")
    values = matches[0]
    if any(v < 0 for v in values.values()):
        raise ValueError("Invalid resource count")
    values["bram_36k_equivalent"] = values["ramb36"] + values["ramb18"] / 2
    return {"instance": instance, **values, "scope": "reported_usage_not_optimization_score"}


def completion_rate(events, window_start, window_end, stage, epoch):
    """Count distinct successful completions in one explicit [start,end) window."""
    if not all(math.isfinite(t) for t in (window_start, window_end)):
        raise ValueError("Invalid measurement window")
    if window_end <= window_start:
        raise ValueError("Measurement window must be positive")
    seen, completed, duplicates = set(), 0, 0
    previous = -math.inf
    for event in events:
        timestamp = float(event["time_s"])
        if not math.isfinite(timestamp) or timestamp < previous:
            raise ValueError("Events must use one monotonic clock in time order")
        previous = timestamp
        if event["stage"] != stage or event["epoch"] != epoch:
            continue
        if type(event["complete"]) is not bool:
            raise ValueError("complete must be a JSON boolean")
        if not event["complete"]:
            continue
        identity = event["id"]
        if type(identity) not in (str, int):
            raise ValueError("Completion id must be a string or integer")
        if identity in seen:
            if window_start <= timestamp < window_end:
                duplicates += 1
        else:
            seen.add(identity)
            if window_start <= timestamp < window_end:
                completed += 1
    duration = window_end - window_start
    return {"completed_unique": completed, "duplicates_excluded": duplicates,
            "window_seconds": duration, "units_per_second": completed / duration,
            "stage": stage, "epoch": epoch}
