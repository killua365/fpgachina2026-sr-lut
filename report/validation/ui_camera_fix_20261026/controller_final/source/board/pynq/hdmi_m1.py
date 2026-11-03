"""Supervised spare-card HDMI diagnostic; never enables HDMI at boot."""

import argparse
import faulthandler
import hashlib
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import threading
import time


def sha256(path):
    result = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            result.update(block)
    return result.hexdigest()


def load_json(path):
    return json.loads(path.read_text(encoding="utf-8-sig"))


def validate_package(root, filename="hdmi_m1_package.json", kind="hdmi_m1_development"):
    manifest = load_json(root / filename)
    if manifest["kind"] != kind or set(manifest["overlays"]) != {"2", "4"}:
        raise ValueError("An isolated two-overlay HDMI diagnostic package is required")
    for item in manifest["files"]:
        path = (root / item["path"]).resolve(strict=True)
        path.relative_to(root.resolve())
        if sha256(path).lower() != item["sha256"].lower():
            raise ValueError("Package hash mismatch: " + item["path"])
    baseline = load_json(root / "board/recovery/hdmi_rollback_baseline.json")
    if not baseline.get("m0_rollback_prerequisites_verified") or not baseline.get("hdmi_development_board_test_allowed"):
        raise ValueError("M0 has not opened supervised spare-card testing")
    for key, field, result_path in (
            ("spare_card_legacy_smoke", "record", ("legacy_smoke_passed",)),
            ("physical_card_rollback", "round_trip_record", ("physical_card_rollback_verified",)),
            ("legacy_service_rollback", "record", ("report", "passed"))):
        item = baseline[key]
        record = root / item[field]
        if item["passed"] is not True or sha256(record).lower() != item["sha256"].lower():
            raise ValueError("M0 evidence hash/check failed: " + key)
        value = load_json(record)
        for component in result_path:
            value = value[component]
        if value is not True:
            raise ValueError("M0 underlying evidence did not pass: " + key)
    for scale, overlay in manifest["overlays"].items():
        for kind in ("bit", "hwh"):
            if sha256(root / overlay[kind]).lower() != overlay[kind + "_sha256"].lower():
                raise ValueError("HDMI hardware hash mismatch")
        if manifest.get("mode", "").startswith("1920x1080"):
            from hdmi_hardware_validation import validate_artifact
            checked = validate_artifact((root / overlay["bit"]).parent, int(scale), manifest["mode"])
            if manifest.get("timing", {}).get(scale) != checked["timing"]:
                raise ValueError("Package timing differs from routed evidence")
    return manifest, baseline


def root_card():
    device = os.stat("/").st_dev
    block = Path("/sys/dev/block/{}:{}".format(os.major(device), os.minor(device))).resolve(strict=True)
    disk = block.parent if (block / "partition").is_file() else block
    return (disk / "device/cid").read_text().strip()


def run(root, destination, seconds, lock_fd, hold_display=False, *, scales=(2, 4)):
    manifest, baseline = validate_package(root)
    if scales != (2, 4):
        from n4_fixed_source import validate_fixed_run
        if len(scales) != 1 or hold_display:
            raise ValueError("Fixed source requires exactly one bounded overlay")
        validate_fixed_run(manifest, scales[0], seconds * 4)
    if os.geteuid() != 0:
        raise RuntimeError("Root is required for FPGA, DMA and mouse access")
    cid = root_card()
    if cid != baseline["spare_card_readback"]["card_cid"] or cid == baseline["physical_card_rollback"]["original_card_cid"]:
        raise RuntimeError("Refusing to load HDMI on an unverified or original card")
    # PYNQ registers its embedded device driver only when XRT is sourced.
    os.environ["XILINX_XRT"] = "/usr"
    from pynq import Device
    if not Device.devices:
        raise RuntimeError("No PYNQ device detected; legacy service remains running")
    import fcntl

    if lock_fd is None or os.fstat(lock_fd).st_ino != Path("/run/lock/sr-lut-hdmi.lock").stat().st_ino:
        raise RuntimeError("A live supervisor lock is required")
    fcntl.flock(lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    from legacy_service_rollback import active, wait_status, PORTAL, CANDIDATE

    if active(PORTAL) != "active" or active(CANDIDATE) not in ("inactive", "failed"):
        raise RuntimeError("Begin with the formal portal active and candidate stopped")
    before = wait_status(8080, False)
    destination.mkdir(parents=True, exist_ok=False)
    cancelled = threading.Event()
    for signum in (signal.SIGINT, signal.SIGTERM, signal.SIGHUP):
        signal.signal(signum, lambda *_: cancelled.set())
    report = {"kind": "hdmi_m1_supervised_run", "package_sha256": sha256(root / "hdmi_m1_package.json"),
              "source_commit": manifest["source_commit"], "card_cid": cid,
              "boot_id": Path("/proc/sys/kernel/random/boot_id").read_text().strip(),
              "started_monotonic": time.monotonic(), "board_clock_trusted": False,
              "xilinx_xrt": os.environ["XILINX_XRT"],
              "phase": "preflight", "overlays": {}, "errors": [], "legacy_restored": False,
              "requested_scales": list(scales), "seconds_per_pattern": seconds,
              "physical_hdmi_verified": False, "formal_release": False}

    def save(name, value):
        (destination / name).write_text(json.dumps(value, indent=2, default=str) + "\n")

    def checkpoint(phase):
        report["phase"] = phase
        save("status.json", report)
        print(phase, flush=True)

    def control(*args):
        result = subprocess.run(["systemctl", *args], capture_output=True, text=True, timeout=120)
        if result.returncode:
            raise RuntimeError("systemctl " + " ".join(args) + ": " + result.stderr)

    save("legacy_before.json", before)
    checkpoint("preflight_passed")
    display = mouse = overlay = None
    try:
        control("stop", PORTAL, CANDIDATE)
        for service in (PORTAL, CANDIDATE, "bootpy.service", "sr-lut-hdmi.service"):
            if active(service) not in ("inactive", "failed", "unknown"):
                raise RuntimeError("Another hardware owner is active: " + service)
        sys.path.insert(0, str(root / "src/ps"))
        import cv2
        from pynq import Overlay, Clocks
        from hdmi_display.diagnostics import run_scanout_test
        from hdmi_display.mouse import Mouse
        from hdmi_display.scanout import Scanout, MODE_720P60, MODE_1080P30, MODE_1080P60

        mouse = Mouse()
        checkpoint("legacy_stopped")
        for scale in scales:
            if cancelled.is_set():
                raise InterruptedError("Cancelled before Overlay load")
            identity = manifest["overlays"][str(scale)]
            checkpoint("loading_hdmi_x{}".format(scale))
            overlay = Overlay(str(root / identity["bit"]))
            save("hdmi_x{}_ip.json".format(scale), overlay.ip_dict)
            report["fclk0_mhz"] = Clocks.fclk0_mhz
            display_mode = {MODE_720P60.name: MODE_720P60,
                            MODE_1080P30.name: MODE_1080P30,
                            MODE_1080P60.name: MODE_1080P60}.get(
                                manifest.get("mode"), MODE_720P60)
            display = Scanout.from_overlay(overlay, mode=display_mode, trace=lambda event: print(
                json.dumps({"monotonic": time.monotonic(), "operation": event}), flush=True))
            save("hdmi_x{}_before.json".format(scale), display.status())

            def decorate(pixels, pattern, sequence):
                state = mouse.poll()
                cv2.putText(pixels, "HDMI x{}  mouse {}  clicks {}".format(
                    scale, "connected" if state["connected"] else "missing", state["clicks"]),
                    (40, 690), cv2.FONT_HERSHEY_SIMPLEX, 0.7, (255, 255, 255, 0), 1, cv2.LINE_AA)
                point = (state["x"], state["y"])
                cv2.circle(pixels, point, 10, (0, 0, 0, 0), 4)
                cv2.circle(pixels, point, 9, (255, 230, 40, 0), 2)

            checkpoint("testing_hdmi_x{}".format(scale))
            result = run_scanout_test(display, destination / "hdmi_x{}".format(scale),
                hardware_identity={"scale": scale, "bit_sha256": identity["bit_sha256"],
                                   "hwh_sha256": identity["hwh_sha256"]},
                seconds_per_pattern=seconds, decorate=decorate, cancel=cancelled,
                hold_display=hold_display and scale == 4,
                on_hold=lambda state: checkpoint("display_hold_active"))
            report["overlays"][str(scale)] = result
            report["mouse"] = mouse.status()
            checkpoint("hdmi_x{}_finished".format(scale))
            if not result["register_checks_passed"]:
                raise RuntimeError("HDMI x{} register test failed; inspect raw report".format(scale))
            display = overlay = None
    except (Exception, KeyboardInterrupt) as exc:
        report["errors"].append(type(exc).__name__ + ": " + str(exc))
    finally:
        if mouse is not None:
            report["mouse"] = mouse.status()
            mouse.close()
        # If halt fails, retain the process and buffers until the DMA is stopped.
        # Exiting here would release CMA while hardware may still scan it.
        while display is not None and display.buffers:
            try:
                display.stop()
            except Exception as exc:
                report["retained_dma_error"] = str(exc)
                checkpoint("dma_halt_failed_buffers_retained")
                time.sleep(2)
        checkpoint("restoring_legacy")
        try:
            control("start", PORTAL)
            save("legacy_restored.json", wait_status(8080, False))
            report["legacy_restored"] = active(PORTAL) == "active" and active(CANDIDATE) == "inactive"
        except Exception as exc:
            report["errors"].append("Legacy restore failed: " + str(exc))
        report["elapsed_seconds"] = time.monotonic() - report["started_monotonic"]
        report["register_tests_passed"] = (set(report["overlays"]) == {str(s) for s in scales} and
            all(item["register_checks_passed"] for item in report["overlays"].values()) and not report["errors"])
        checkpoint("finished")
        save("report.json", report)
    return 0 if report["register_tests_passed"] and report["legacy_restored"] else 1


def main():
    faulthandler.enable(all_threads=True)
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("check", "run", "_worker"))
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[2])
    parser.add_argument("--destination", type=Path)
    parser.add_argument("--seconds-per-pattern", type=float, default=15)
    parser.add_argument("--lock-fd", type=int, help=argparse.SUPPRESS)
    parser.add_argument("--hold-display", action="store_true",
                        help="keep x4 patterns and mouse active until interrupted")
    args = parser.parse_args()
    if args.command == "check":
        manifest, _ = validate_package(args.root)
        print(json.dumps({"package_verified": True, "files": len(manifest["files"])}))
        return 0
    if args.destination is None or not 2 <= args.seconds_per_pattern <= 60:
        parser.error("run needs a new destination and 2..60 seconds per pattern")
    if args.command == "_worker":
        return run(args.root, args.destination, args.seconds_per_pattern, args.lock_fd, args.hold_display)
    from hdmi_m1_supervisor import supervise
    return supervise(args.root, args.destination, args.seconds_per_pattern, args.hold_display)


if __name__ == "__main__":
    raise SystemExit(main())
