"""Keep recovery outside the process that owns native PYNQ/MMIO objects."""

import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import threading
import time


def publish_status(path, report):
    """Readers must see the complete previous or next status, never a truncate."""
    temporary = path.with_suffix('.tmp')
    temporary.write_text(json.dumps(report, indent=2) + '\n', encoding='utf-8')
    temporary.replace(path)


def watch_worker(command, destination, *, pass_fds=(), timeout, cancel=None, cancel_path=None, bounded_output=False):
    cancel = cancel or threading.Event()
    started = time.monotonic()
    report = {"worker_exit_code": None, "termination_requested": False,
              "worker_reaped": False, "errors": []}
    with (destination / "worker_stdout.txt").open("x") as output, \
            (destination / "worker_stderr.txt").open("x") as errors:
        process = subprocess.Popen(command, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE if bounded_output else output,
                                   stderr=subprocess.PIPE if bounded_output else errors, pass_fds=pass_fds, start_new_session=True)
        drains=[]
        if bounded_output:
            from bounded_logs import BoundedLog
            def drain(pipe,path):
                with BoundedLog(path,binary=True) as log:
                    for data in iter(lambda:pipe.read(4096),b''):
                        log.write(data);log.flush()
                pipe.close()
            for pipe,path in ((process.stdout,destination/'worker_stdout.txt'),(process.stderr,destination/'worker_stderr.txt')):
                task=threading.Thread(target=drain,args=(pipe,path),daemon=True)
                task.start();drains.append(task)
        report["worker_pid"] = process.pid
        try:
            while process.poll() is None:
                file_cancel = cancel_path is not None and cancel_path.is_file()
                if not report["termination_requested"] and (cancel.is_set() or file_cancel or timeout is not None and time.monotonic() - started > timeout):
                    report["termination_requested"] = True
                    report["termination_reason"] = ("operator_interrupt" if cancel.is_set() else
                                                     "operator_cancel_file" if file_cancel else "deadline")
                    if not (cancel.is_set() or file_cancel):
                        report["errors"].append("Worker deadline exceeded")
                    process.send_signal(signal.SIGTERM)
                report["elapsed_seconds"] = time.monotonic() - started
                publish_status(destination / "supervisor_status.json", report)
                time.sleep(0.25)
        finally:
            # Even an evidence-write failure cannot authorize overlapping owners.
            # Never SIGKILL a worker retaining live DMA buffers after a failed halt.
            if process.poll() is None:
                process.send_signal(signal.SIGTERM)
            report["worker_exit_code"] = process.wait()
            for task in drains: task.join()
            report["worker_reaped"] = True
            report["elapsed_seconds"] = time.monotonic() - started
            publish_status(destination / "supervisor_status.json", report)
    return report


def supervise(root, destination, seconds, hold_display=False, *, fixed_scale=None):
    import fcntl
    from hdmi_m1 import root_card, sha256, validate_package
    from legacy_service_rollback import active, wait_status, PORTAL, CANDIDATE

    manifest, baseline = validate_package(root)
    fixed_plan = None
    if fixed_scale is not None:
        from n4_fixed_source import worker_plan
        if hold_display:
            raise ValueError("Fixed source cannot hold the display indefinitely")
        fixed_plan = worker_plan(root, destination, manifest, fixed_scale, seconds * 4)
    if os.geteuid() != 0 or root_card() != baseline["spare_card_readback"]["card_cid"]:
        raise RuntimeError("Supervisor requires root on the verified spare")
    lock = Path("/run/lock/sr-lut-hdmi.lock").open("a")
    fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    if active(PORTAL) != "active" or active(CANDIDATE) not in ("inactive", "failed"):
        raise RuntimeError("Begin with only the formal service running")
    wait_status(8080, False)
    destination.mkdir(parents=True, exist_ok=False)
    cancelled = threading.Event()
    previous = {s: signal.signal(s, lambda *_: cancelled.set())
                for s in (signal.SIGINT, signal.SIGTERM, signal.SIGHUP)}
    report = {"kind": "hdmi_m1_independent_supervisor", "source_commit": manifest["source_commit"],
              "package_sha256": sha256(root / "hdmi_m1_package.json"),
              "started_monotonic": time.monotonic(), "errors": [],
              "legacy_restored": False, "physical_hdmi_verified": False,
              "register_tests_passed": False, "formal_release": False}
    try:
        command = [sys.executable, "-B", "-X", "faulthandler",
            str(root / "board/pynq/hdmi_m1.py"), "_worker", "--root", str(root),
            "--destination", str(destination / "worker"), "--seconds-per-pattern", str(seconds),
            "--lock-fd", str(lock.fileno())] + (["--hold-display"] if hold_display else [])
        timeout = 8 * seconds + 240 if not hold_display else 86400
        if fixed_plan is not None:
            command = fixed_plan["command"] + ["--lock-fd", str(lock.fileno())]
            timeout = fixed_plan["timeout_seconds"]
            report["fixed_source"] = fixed_plan["contract"]
        result = watch_worker(command, destination, pass_fds=(lock.fileno(),),
            timeout=timeout, cancel=cancelled,
            cancel_path=destination / "cancel.request" if fixed_plan is not None else None)
        report.update(result)
        if result["worker_exit_code"] != 0:
            report["errors"].append("HDMI worker exited with code " + str(result["worker_exit_code"]))
        child_report = destination / "worker/report.json"
        if child_report.exists():
            child = json.loads(child_report.read_text())
            report["register_tests_passed"] = result["worker_exit_code"] == 0 and child["register_tests_passed"]
        else:
            report["errors"].append("Worker final report missing; incomplete run")
    finally:
        # This process has never imported PYNQ or mapped the failed peripheral.
        try:
            kernel = subprocess.run(["dmesg", "-T"], capture_output=True, text=True, timeout=15)
            (destination / "kernel.txt").write_text(kernel.stdout + kernel.stderr)
        except Exception as exc:
            report["errors"].append("Kernel capture failed: " + str(exc))
        try:
            if active(CANDIDATE) not in ("inactive", "failed"):
                subprocess.run(["systemctl", "stop", CANDIDATE], check=True, timeout=60)
            # The worker normally restores the formal portal itself. Avoid a
            # second start transaction, which can block while the FPGA owner
            # is still releasing resources.
            if active(PORTAL) != "active":
                subprocess.run(["systemctl", "start", PORTAL], check=True, timeout=120)
            restored = wait_status(8080, False)
            (destination / "legacy_restored.json").write_text(json.dumps(restored, indent=2) + "\n")
            report["legacy_restored"] = active(PORTAL) == "active"
        except Exception as exc:
            report["errors"].append("Independent recovery failed: " + str(exc))
        report["elapsed_seconds"] = time.monotonic() - report["started_monotonic"]
        (destination / "report.json").write_text(json.dumps(report, indent=2) + "\n")
        for signum, handler in previous.items():
            signal.signal(signum, handler)
        lock.close()
    print(json.dumps(report, indent=2), flush=True)
    return 0 if report["register_tests_passed"] and report["legacy_restored"] and not report["errors"] else 1
