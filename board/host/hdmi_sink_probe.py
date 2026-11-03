"""Short 720p / 1080p30 / 720p sink test with separate hardware processes.

Only the verified spare is permitted. No service is enabled and no legacy
package is changed. A failed DMA halt triggers recovery instead of freeing CMA.
"""
import argparse
import faulthandler
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import threading
import time

from hdmi_m1 import root_card, sha256, validate_package
from hdmi_hardware_validation import validate_artifact
from legacy_service_rollback import active, wait_status, PORTAL, CANDIDATE

STAGES = (("720_before", "1280x720@60"), ("1080_trial", "1920x1080@30"),
          ("720_after", "1280x720@60"))
MONITORS = ("DELL P2414H", "LEN T2324C")


def save(path, value):
    temporary = path.with_suffix(path.suffix + ".new")
    temporary.write_text(json.dumps(value, indent=2, default=str) + "\n", encoding="utf-8")
    temporary.replace(path)


def check(root):
    manifest, baseline = validate_package(root)
    if manifest.get("sink_probe") != {"modes": ["1280x720@60", "1920x1080@30"], "stage_seconds": 15}:
        raise ValueError("An explicit bounded sink-probe package is required")
    for scale, identity in manifest["overlays"].items():
        if (root / identity["bit"]).with_suffix(".xsa").exists():
            raise ValueError("PYNQ BIT loader must not have an adjacent same-name XSA; store XSA under evidence")
        for mode in manifest["sink_probe"]["modes"]:
            validate_artifact((root / identity["bit"]).parent, int(scale), mode)
    return manifest, baseline


def worker(args):
    manifest, baseline = check(args.root)
    if os.geteuid() != 0 or root_card() != baseline["spare_card_readback"]["card_cid"]:
        raise RuntimeError("Probe requires root on the verified spare card")
    import fcntl
    if args.lock_fd is None or os.fstat(args.lock_fd).st_ino != Path("/run/lock/sr-lut-hdmi.lock").stat().st_ino:
        raise RuntimeError("Missing supervisor lock")
    fcntl.flock(args.lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    for service in (PORTAL, CANDIDATE, "sr-lut-hdmi.service", "bootpy.service"):
        if active(service) not in ("inactive", "failed", "unknown"):
            raise RuntimeError("Another FPGA owner is active: " + service)
    args.destination.mkdir(parents=True, exist_ok=False)
    report = {"stage": args.stage, "scale": args.scale, "mode": dict(STAGES)[args.stage],
              "monitor": args.monitor,
              "package_manifest_sha256": sha256(args.root / "hdmi_m1_package.json"),
              "physical_hdmi_verified": False, "register_checks_passed": False,
              "dma_stopped": False, "errors": [], "samples": [], "images": []}
    cancelled = threading.Event()
    for sig in (signal.SIGINT, signal.SIGTERM, signal.SIGHUP):
        signal.signal(sig, lambda *_: cancelled.set())
    display = mouse = None
    saved_images = []
    try:
        os.environ["XILINX_XRT"] = "/usr"
        sys.path.insert(0, str(args.root / "src/ps"))
        from pynq import Overlay
        import cv2
        from PIL import Image
        from hdmi_display.scanout import Scanout, MODE_720P60, MODE_1080P30
        from hdmi_display.patterns import draw_pattern, PATTERNS
        from hdmi_display.mouse import Mouse
        from hdmi_display.diagnostics import evaluate_interval
        identity = manifest["overlays"][str(args.scale)]
        report["hardware"] = identity
        overlay = Overlay(str(args.root / identity["bit"]))
        mode = MODE_1080P30 if args.stage == "1080_trial" else MODE_720P60
        display = Scanout.from_overlay(overlay, mode=mode)
        mouse = Mouse(width=mode.width, height=mode.height)
        display.start()
        started = time.monotonic()
        report["display_started_monotonic"] = started
        save(args.destination / "status.json", report)
        print("DISPLAY_ACTIVE " + report["stage"] + " " + mode.name, flush=True)
        sequence, before, last_pattern = 0, None, None
        locked = True
        while not cancelled.is_set() and time.monotonic() - started < 15:
            elapsed = time.monotonic() - started
            name = PATTERNS[min(3, int(elapsed / 3.75))]
            lease = display.acquire()
            try:
                draw_pattern(lease.pixels, name, sequence, mode)
                state = mouse.poll()
                cv2.putText(lease.pixels, "{} x{} {}".format(args.monitor, args.scale, args.stage),
                            (40, mode.height - 35), cv2.FONT_HERSHEY_SIMPLEX, 0.8, (255, 255, 255, 0), 2)
                cv2.circle(lease.pixels, (state["x"], state["y"]), 10, (255, 230, 40, 0), 2)
                if name != last_pattern:
                    saved_images.append((name, sequence, lease.pixels[:, :, :3].copy()))
                    last_pattern = name
                display.submit(lease)
            except BaseException:
                if display.writing is lease:
                    display.discard(lease)
                raise
            now = time.monotonic()
            state = display.status()
            report["samples"].append({"monotonic": now, "sequence": sequence, **state})
            if now - started >= 2:
                if before is None:
                    before, interval_start = state, now
                locked = locked and state["locked"]
            sequence += 1
            time.sleep(min(0.04, max(0, 15 - (time.monotonic() - started))))
        if before is not None:
            report["interval"] = evaluate_interval(before, display.status(), time.monotonic() - interval_start,
                                                    locked_samples=locked)
            report["register_checks_passed"] = report["interval"]["passed"] and not cancelled.is_set()
        report["cancelled"] = cancelled.is_set()
    except BaseException as exc:
        report["errors"].append(type(exc).__name__ + ": " + str(exc))
    finally:
        if mouse is not None:
            report["mouse"] = mouse.status()
            mouse.close()
        try:
            if display is not None:
                display.stop()
            report["dma_stopped"] = True
        except BaseException as exc:
            report["errors"].append("DMA halt failed: " + str(exc))
            report["recovery_required"] = "retain CMA; supervisor requests controlled spare-card reboot"
        report["display_stopped_monotonic"] = time.monotonic()
        save(args.destination / "report.json", report)
        if not report["dma_stopped"]:
            # Recovery state: do not exit and release buffers still read by PL.
            # Parent will request a reboot. If that fails this process retains
            # the lock and allocations; manual power recovery is then required.
            while True:
                time.sleep(1)
        for name, sequence, rgb in saved_images:
            path = args.destination / (name + ".png")
            Image.fromarray(rgb).save(path)
            report["images"].append({"path": path.name, "sequence": sequence, "sha256": sha256(path)})
        save(args.destination / "report.json", report)
    return 0 if report["register_checks_passed"] and not report["errors"] else 1


def run_child(command, folder, lock_fd, cancel, timeout=65, grace=10):
    """A stopped-worker receipt is required before another FPGA owner starts."""
    with (folder.parent / (folder.name + "_stdout.txt")).open("x") as out, \
            (folder.parent / (folder.name + "_stderr.txt")).open("x") as err:
        process = subprocess.Popen(command, stdin=subprocess.DEVNULL, stdout=out, stderr=err,
                                   pass_fds=(lock_fd,), start_new_session=True)
        start, termination = time.monotonic(), None
        while process.poll() is None:
            now = time.monotonic()
            if termination is None and (cancel.is_set() or now - start > timeout):
                termination = now
                process.send_signal(signal.SIGTERM)
            receipt = folder / "report.json"
            if receipt.exists():
                value = json.loads(receipt.read_text())
                if value.get("recovery_required"):
                    return {"safe_to_restore": False, "pid": process.pid, "reason": value["recovery_required"]}
            if termination is not None and now - termination > grace:
                return {"safe_to_restore": False, "pid": process.pid, "reason": "worker failed to stop by deadline"}
            time.sleep(0.1)
        receipt = folder / "report.json"
        value = json.loads(receipt.read_text()) if receipt.exists() else {}
        return {"safe_to_restore": process.returncode in (0, 1) and value.get("dma_stopped") is True,
                "exit_code": process.returncode, "worker": value,
                "reason": "worker receipt and exit status checked"}


def supervise(args):
    import fcntl
    manifest, baseline = check(args.root)
    if os.geteuid() != 0 or root_card() != baseline["spare_card_readback"]["card_cid"]:
        raise RuntimeError("Only root on the verified spare may run the probe")
    args.destination.mkdir(parents=True, exist_ok=False)
    lock = Path("/run/lock/sr-lut-hdmi.lock").open("a")
    fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    if active(PORTAL) != "active" or active(CANDIDATE) not in ("inactive", "failed"):
        raise RuntimeError("Begin with only the formal 8080 service active")
    report = {"kind": "bounded_hdmi_sink_probe", "monitor": args.monitor, "scale": args.scale,
              "connection": "PYNQ-Z2 HDMI OUT to monitor DVI via a single HDMI-to-DVI cable",
              "source_commit": manifest["source_commit"], "stages": {}, "errors": [],
              "package_manifest_sha256": sha256(args.root / "hdmi_m1_package.json"),
              "physical_hdmi_verified": False, "formal_release": False, "legacy_restored": False,
              "boot_id": Path("/proc/sys/kernel/random/boot_id").read_text().strip(),
              "board_clock_trusted": False}
    save(args.destination / "legacy_before.json", wait_status(8080, False))
    cancel = threading.Event()
    for sig in (signal.SIGINT, signal.SIGTERM, signal.SIGHUP):
        signal.signal(sig, lambda *_: cancel.set())
    safe = True
    try:
        subprocess.run(["systemctl", "stop", PORTAL, CANDIDATE], check=True, timeout=90)
        for stage, mode in STAGES:
            if cancel.is_set():
                break
            print("START {} {} x{} (15 seconds; watch the monitor)".format(stage, mode, args.scale), flush=True)
            folder = args.destination / stage
            command = [sys.executable, "-B", "-X", "faulthandler", str(Path(__file__).resolve()), "_worker",
                       "--root", str(args.root), "--destination", str(folder), "--scale", str(args.scale),
                       "--stage", stage, "--lock-fd", str(lock.fileno()), "--monitor", args.monitor]
            # From this point a native worker may own PL/CMA. Exceptions must
            # never assume that hardware ownership has already been released.
            safe = False
            result = run_child(command, folder, lock.fileno(), cancel)
            safe = result["safe_to_restore"]
            report["stages"][stage] = result
            save(args.destination / "status.json", report)
            print("END {}: registers={} safely_stopped={}".format(stage,
                  result.get("worker", {}).get("register_checks_passed"), safe), flush=True)
            if not safe:
                break
            if stage == "720_before" and not result["worker"].get("register_checks_passed"):
                report["errors"].append("720p baseline failed; 1080p trial skipped")
                break
    except BaseException as exc:
        report["errors"].append(type(exc).__name__ + ": " + str(exc))
    finally:
        try:
            result = subprocess.run(["dmesg", "-T"], capture_output=True, text=True, timeout=10)
            (args.destination / "kernel.txt").write_text(result.stdout + result.stderr)
        except Exception as exc:
            report["errors"].append("Kernel capture: " + str(exc))
        if safe:
            try:
                subprocess.run(["systemctl", "start", PORTAL], check=True, timeout=90)
                save(args.destination / "legacy_restored.json", wait_status(8080, False))
                report["legacy_restored"] = active(PORTAL) == "active"
            except Exception as exc:
                report["errors"].append("Recovery: " + str(exc))
        else:
            report["recovery_required"] = "controlled spare-card reboot; do not start another FPGA owner"
            save(args.destination / "report.json", report)
            print("DMA safety recovery: requesting spare-card reboot", flush=True)
            try:
                subprocess.run(["sync"], timeout=10)
                subprocess.run(["systemctl", "reboot", "--no-block"], check=True, timeout=10)
                report["reboot_requested"] = True
            except Exception as exc:
                report["errors"].append("Reboot request failed; manual cold restart required: " + str(exc))
        save(args.destination / "report.json", report)
        lock.close()
    report["register_tests_passed"] = (set(report["stages"]) == set(dict(STAGES)) and
        all(s.get("worker", {}).get("register_checks_passed") for s in report["stages"].values()))
    save(args.destination / "report.json", report)
    print(json.dumps({"results": str(args.destination), "legacy_restored": report["legacy_restored"],
                      "physical_hdmi_verified": False, "errors": report["errors"]}), flush=True)
    return 0 if safe and report["legacy_restored"] and report["register_tests_passed"] and not report["errors"] else 1


def main():
    faulthandler.enable(all_threads=True)
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("check", "run", "_worker"))
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[2])
    parser.add_argument("--destination", type=Path)
    parser.add_argument("--scale", type=int, choices=(2, 4), default=2)
    parser.add_argument("--monitor", choices=MONITORS, help="actual attached monitor; required for run and worker")
    parser.add_argument("--stage", choices=tuple(dict(STAGES)))
    parser.add_argument("--lock-fd", type=int)
    parser.add_argument("--board-parser", action="store_true", help="read-only installed PYNQ metadata parse; no FPGA load")
    args = parser.parse_args()
    if args.command == "check":
        manifest, _ = check(args.root)
        if args.board_parser:
            os.environ["XILINX_XRT"] = "/usr"
            from pynq.pl_server.embedded_device import _get_bitstream_handler
            for scale, identity in manifest["overlays"].items():
                parsed = _get_bitstream_handler(str(args.root / identity["bit"])).get_parser()
                if parsed is None or "hdmi_vdma" not in parsed.ip_dict:
                    raise RuntimeError("Installed PYNQ failed metadata preflight")
                print(json.dumps({"scale": scale, "installed_pynq_parser_passed": True, "fpga_loaded": False}))
        print(json.dumps({"package_verified": True, "files": len(manifest["files"])}))
        return 0
    if args.destination is None:
        parser.error("A unique destination is required")
    if args.monitor is None:
        parser.error("An explicit --monitor is required; never infer the physical display from the package name")
    if args.command == "_worker":
        return worker(args)
    return supervise(args)


if __name__ == "__main__":
    raise SystemExit(main())
