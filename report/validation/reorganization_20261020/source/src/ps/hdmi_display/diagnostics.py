"""Collect M1 scanout diagnostics from a caller-authorized display instance.

This module does not download an Overlay or bypass the M0 installation gate.
Monitor photographs and operator observations are separate evidence. A register
test can pass without proving that a display has received correct pixels.
"""

from __future__ import annotations

import hashlib
import json
import math
from pathlib import Path
import threading
import time

from PIL import Image

from .media import sha256_file
from .patterns import PATTERNS, draw_pattern
from .scanout import MODE_720P60


def scan_delta(before, after):
    """The hardware frame counter is a wrapping 32-bit unsigned counter."""
    return (int(after) - int(before)) & 0xFFFFFFFF


def evaluate_interval(before, after, elapsed, *, locked_samples):
    if not math.isfinite(elapsed) or elapsed <= 0:
        raise ValueError("The measurement interval must be finite and positive")
    scans = scan_delta(before["scan_frames"], after["scan_frames"])
    switches = after["dma_frame_switches"] - before["dma_frame_switches"]
    refresh = scans / elapsed
    reasons = []
    if not before["locked"] or not after["locked"] or not locked_samples:
        reasons.append("video_output_lost_lock")
    if before["counters_saturated"] or after["counters_saturated"]:
        reasons.append("error_counters_saturated")
    for field in ("underflow_events", "overflow_events"):
        if after[field] != before[field]:
            reasons.append(field + "_changed")
    if after["dma_status"] & 0x71 or not after["running"]:
        reasons.append("vdma_stopped_or_errored")
    expected_refresh = float(before.get("refresh_hz", 60))
    if abs(refresh - expected_refresh) > max(1.5, expected_refresh * 0.08):
        reasons.append("scan_clock_outside_declared_mode_range")
    if switches <= 0 or switches > scans + 1:
        reasons.append("invalid_frame_store_progress")
    expected_bytes = int(before.get("width", 1280)) * int(before.get("height", 720)) * 4 * 3
    if before["allocated_bytes"] != expected_bytes or after["allocated_bytes"] != expected_bytes:
        reasons.append("unexpected_display_allocation")
    return {
        "elapsed_seconds": elapsed, "scan_frames": scans,
        "measured_scan_hz": refresh, "dma_frame_switches": switches,
        "acknowledged_pattern_fps": switches / elapsed,
        "repeated_scans_estimate": max(0, scans - switches),
        "passed": not reasons, "failure_reasons": reasons,
    }


def run_scanout_test(display, destination, *, hardware_identity,
                     seconds_per_pattern=10.0, pattern_fps=30.0,
                     warmup_seconds=2.0, cancel=None, decorate=None,
                     hold_display=False, on_hold=None,
                     clock=time.monotonic, sleep=time.sleep):
    """Own start/stop and capture bounded diagnostic evidence for the active mode.

    The caller must validate the package, authorize spare-card testing after M0,
    and exclude every other FPGA user before constructing the display. On a
    failed halt, its object must remain alive with its retained CMA buffers.
    The result records failures, including cancellation and stop failures.
    Optional observation runs before stop, retaining the display and its buffers.
    """
    for value in (seconds_per_pattern, pattern_fps, warmup_seconds):
        if not math.isfinite(value) or value <= 0:
            raise ValueError("Diagnostic durations and rate must be finite and positive")
    if seconds_per_pattern < 2 or seconds_per_pattern > 300 or pattern_fps > 60 or warmup_seconds > 30:
        raise ValueError("Pattern intervals must be 2..300 seconds, warmup <=30, rate <=60")
    if hardware_identity.get("scale") not in (2, 4):
        raise ValueError("An HDMI x2 or x4 hardware identity is required")
    for key in ("bit_sha256", "hwh_sha256"):
        value = hardware_identity.get(key, "")
        if not isinstance(value, str) or len(value) != 64 or any(c not in "0123456789abcdefABCDEF" for c in value):
            raise ValueError("BIT and HWH SHA-256 identities are required")
    destination = Path(destination)
    destination.mkdir(parents=True, exist_ok=False)
    cancel = cancel or threading.Event()
    report = {
        "schema_version": 1, "kind": "hdmi_m1_scanout_diagnostic",
        "hardware": dict(hardware_identity), "mode": getattr(getattr(display, "mode", None), "name", MODE_720P60.name),
        "requested_pattern_fps": pattern_fps, "warmup_seconds": warmup_seconds,
        "intervals": [], "images": [], "errors": [],
        "register_checks_passed": False, "display_stopped": False,
        "physical_hdmi_verified": False, "schema3_acceptance": False,
        "sr_processing_fps": None,
        "hold": {"requested": bool(hold_display), "entered": False},
    }
    sequence = 0
    attempted_start = False
    with (destination / "samples.jsonl").open("x", encoding="utf-8") as samples:
        def snapshot(phase):
            state = display.status()
            samples.write(json.dumps({"monotonic": clock(), "phase": phase, **state}) + "\n")
            samples.flush()
            return state

        def produce(name, count, save=False):
            if cancel.is_set():
                raise InterruptedError("Scanout diagnostic cancelled")
            lease = display.acquire()
            try:
                draw_pattern(lease.pixels, name, count, getattr(display, "mode", MODE_720P60))
                if decorate is not None:
                    decorate(lease.pixels, name, count)
                if save:
                    # Hash and copy before submit transfers buffer ownership.
                    rgb = lease.pixels[:, :, :3].copy()
                    path = destination / f"{name}_{count:010d}.png"
                    Image.fromarray(rgb).save(path)
                    report["images"].append({"path": path.name, "sequence": count,
                        "rgb_sha256": hashlib.sha256(rgb.tobytes()).hexdigest(),
                        "png_sha256": sha256_file(path), "pattern": name})
            except BaseException:
                display.discard(lease)
                raise
            display.submit(lease)

        try:
            if cancel.is_set():
                raise InterruptedError("Scanout diagnostic cancelled before start")
            attempted_start = True
            display.start()
            warmup_end = clock() + warmup_seconds
            while clock() < warmup_end:
                produce("motion", sequence)
                sequence += 1
                sleep(min(1 / pattern_fps, max(0, warmup_end - clock())))
            report["after_warmup"] = snapshot("warmup_end")
            for name in PATTERNS:
                # PNG I/O lies outside the timed interval.
                produce(name, sequence, save=True)
                sequence += 1
                started = clock()
                deadline = started + seconds_per_pattern
                before = snapshot(name + "_start")
                locked = bool(before["locked"])
                next_frame = started
                while clock() < deadline:
                    produce(name, sequence)
                    sequence += 1
                    current = snapshot(name)
                    locked = locked and bool(current["locked"])
                    next_frame = max(next_frame + 1 / pattern_fps, clock())
                    sleep(min(max(0, next_frame - clock()),
                              max(0, deadline - clock())))
                after = snapshot(name + "_end")
                report["intervals"].append({"pattern": name, "before": before, "after": after,
                    **evaluate_interval(before, after, clock() - started, locked_samples=locked)})
            report["register_checks_passed"] = all(item["passed"] for item in report["intervals"])
            if hold_display and report["register_checks_passed"] and not cancel.is_set():
                report["hold"].update(entered=True, started_monotonic=clock())
                held = snapshot("hold_start")
                if not held["running"] or not held["locked"]:
                    raise RuntimeError("Cannot observe a stopped or unlocked display")
                if on_hold is not None:
                    on_hold(held)
                last_snapshot = clock()
                while not cancel.is_set():
                    produce("motion", sequence)
                    sequence += 1
                    if clock() - last_snapshot >= 1:
                        current = snapshot("hold")
                        if (not current["locked"] or current["counters_saturated"] or
                                any(current[key] != held[key] for key in
                                    ("underflow_events", "overflow_events"))):
                            raise RuntimeError("Display fault during physical observation")
                        last_snapshot = clock()
                    sleep(1 / pattern_fps)
                report["hold"]["ended_by_operator"] = True
        except InterruptedError as exc:
            if report["hold"]["entered"] and cancel.is_set():
                report["hold"]["ended_by_operator"] = True
            else:
                report["errors"].append(f"{type(exc).__name__}: {exc}")
        except (Exception, KeyboardInterrupt) as exc:
            report["errors"].append(f"{type(exc).__name__}: {exc}")
        finally:
            if attempted_start:
                try:
                    display.stop()
                    report["display_stopped"] = True
                except (Exception, KeyboardInterrupt) as exc:
                    report["errors"].append(f"Stop failed: {type(exc).__name__}: {exc}")
            report["register_checks_passed"] = report["register_checks_passed"] and not report["errors"]
    report["samples_sha256"] = sha256_file(destination / "samples.jsonl")
    (destination / "report.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    return report
