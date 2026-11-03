"""Verify existing legacy packages, exercise 8083, and restore formal 8080.

No installation, symlink replacement, enablement change or HDMI download occurs.
The prepare command runs on the host; run requires root on the verified spare.
"""

import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import subprocess
import time
import urllib.request
import zipfile


PACKAGES = {
    "formal": ("6DBCF99BB2BA515C8821213C19B8D39E33A3F1A19BCD94B6D28B46F502E07909",
               "/opt/sr-lut/current", "/opt/sr-lut/releases/app_1_9_0_stage39_formal_6DBCF99BB2BA"),
    "candidate": ("3589C1909FD15CBFCB5A9B9F0DEE1FC7C18BD701759967457F3C098B8286865C",
                  "/opt/sr-lut/candidate", "/opt/sr-lut/candidates/app_1_9_0_stage39_candidate_3589C1909FD1"),
}
PORTAL = "sr-lut-portal.service"
CANDIDATE = "sr-lut-candidate.service"


def digest(stream):
    result = hashlib.sha256()
    for chunk in iter(lambda: stream.read(1024 * 1024), b""):
        result.update(chunk)
    return result.hexdigest()


def prepare(formal, candidate, output):
    profile = {"kind": "legacy_m0_service_recovery_profile", "packages": {}}
    for name, path in (("formal", formal), ("candidate", candidate)):
        expected, link, resolved = PACKAGES[name]
        with path.open("rb") as stream:
            if digest(stream) != expected.lower():
                raise ValueError("Unexpected signed package archive: " + name)
        files = []
        with zipfile.ZipFile(path) as archive:
            for entry in archive.infolist():
                if entry.is_dir():
                    continue
                relative = PurePosixPath(entry.filename)
                if relative.is_absolute() or ".." in relative.parts or "\\" in entry.filename:
                    raise ValueError("Invalid archive path")
                # The installer extracts the single archive root into its release directory.
                installed = relative.relative_to("sr_lut_app_1.9.0_board")
                with archive.open(entry) as stream:
                    files.append({"path": installed.as_posix(), "sha256": digest(stream)})
        profile["packages"][name] = {"archive_sha256": expected, "link": link,
                                     "resolved": resolved, "files": files}
    with output.open("x", encoding="utf-8") as stream:
        json.dump(profile, stream, indent=2)
        stream.write("\n")
    print(json.dumps({name: len(item["files"]) for name, item in profile["packages"].items()}))


def systemctl(*args):
    return subprocess.run(["systemctl", *args], stdin=subprocess.DEVNULL,
                          capture_output=True, text=True, timeout=60)


def active(service):
    return systemctl("is-active", service).stdout.strip()


def bootpy_snapshot():
    properties = ('LoadState,Type,RemainAfterExit,ActiveState,SubState,Result,'
                  'ExecMainStatus,ExecMainCode,MainPID,ControlPID,'
                  'ExecMainExitTimestampMonotonic,ControlGroup')
    result = systemctl('show', 'bootpy.service', '--property=' + properties)
    if result.returncode:
        raise RuntimeError('Cannot inspect boot initialization service')
    state = dict(line.split('=', 1) for line in result.stdout.splitlines() if '=' in line)
    group = state.get('ControlGroup', '')
    processes = []
    if group:
        relative = PurePosixPath(group)
        if not relative.is_absolute() or '..' in relative.parts:
            raise RuntimeError('Invalid boot initialization cgroup')
        directory = Path('/sys/fs/cgroup').joinpath(*relative.parts[1:])
        if directory.exists():
            for file in directory.rglob('cgroup.procs'):
                processes.extend(file.read_text().split())
    state['cgroup_processes'] = processes
    return state


def require_bootpy_completed(state=None):
    """An active/exited oneshot is idle only after verified successful completion."""
    state = bootpy_snapshot() if state is None else state
    expected = dict(LoadState='loaded', Type='oneshot', RemainAfterExit='yes',
                    ActiveState='active', SubState='exited', Result='success',
                    ExecMainStatus='0', ExecMainCode='1', MainPID='0', ControlPID='0')
    if (any(state.get(k) != v for k, v in expected.items()) or
            not str(state.get('ExecMainExitTimestampMonotonic', '')).isdigit() or
            int(state['ExecMainExitTimestampMonotonic']) <= 0 or
            state.get('cgroup_processes') != []):
        raise RuntimeError('Boot initialization must be completed once this boot, with no live hardware owner')
    return state


def wait_status(port, candidate):
    deadline = time.monotonic() + 90
    last = None
    while time.monotonic() < deadline:
        try:
            with urllib.request.urlopen("http://127.0.0.1:{}/api/status".format(port), timeout=3) as response:
                status = json.load(response)["status"]
            identity = status["hardware_identity"]
            name = "candidate" if candidate else "formal"
            if (status["candidate"] != candidate or not identity["identity_match"] or
                    not identity["manifest_match"] or
                    identity["release_archive_sha256"].upper() != PACKAGES[name][0]):
                raise ValueError("Unexpected active package/hardware identity")
            return status
        except ValueError:
            raise
        except Exception as exc:
            last = str(exc)
            time.sleep(1)
    raise TimeoutError("Legacy service did not become ready: " + str(last))


def preflight(profile_path, expected_cid):
    device = os.stat("/").st_dev
    root_block = Path("/sys/dev/block/{}:{}".format(os.major(device), os.minor(device))).resolve(strict=True)
    disk = root_block.parent if (root_block / "partition").exists() else root_block
    cid = (disk / "device/cid").read_text().strip()
    if cid != expected_cid or cid != "035344534333324780dba606de014500":
        raise RuntimeError("This recovery test is restricted to the verified spare card")
    if active(PORTAL) != "active" or active(CANDIDATE) not in ("inactive", "failed"):
        raise RuntimeError("Begin with only the formal portal running")
    profile = json.loads(profile_path.read_text())
    if set(profile["packages"]) != set(PACKAGES):
        raise ValueError("Both known legacy packages are required")
    for name, package in profile["packages"].items():
        expected, link, resolved = PACKAGES[name]
        if package["archive_sha256"] != expected or package["link"] != link or package["resolved"] != resolved:
            raise ValueError("Unexpected package profile")
        directory = Path(link).resolve(strict=True)
        if str(directory) != resolved:
            raise ValueError("Legacy release symlink changed")
        for item in package["files"]:
            path = (directory / item["path"]).resolve(strict=True)
            path.relative_to(directory)
            with path.open("rb") as stream:
                if digest(stream) != item["sha256"]:
                    raise ValueError("Installed package file differs: " + str(path))
    return cid, wait_status(8080, False)


def run(profile_path, expected_cid, destination):
    if not hasattr(os, "geteuid") or os.geteuid() != 0:
        raise RuntimeError("Run on the spare card using sudo")
    cid, before = preflight(profile_path, expected_cid)
    destination.mkdir(parents=True, exist_ok=False)
    report = {"kind": "hdmi_m0_legacy_service_rollback", "root_card_cid": cid,
              "boot_id": Path("/proc/sys/kernel/random/boot_id").read_text().strip(),
              "board_clock_trusted": False, "board_utc_untrusted": datetime.now(timezone.utc).isoformat(),
              "errors": [], "candidate_verified": False, "formal_restored": False,
              "passed": False, "hdmi_mode_rollback_verified": None,
              "hdmi_package_rollback_verified": None, "hdmi_acceptance": False}
    with profile_path.open("rb") as stream:
        report["profile_sha256"] = digest(stream)

    def save(name, value):
        (destination / name).write_text(json.dumps(value, indent=2) + "\n")

    def command(*args):
        result = systemctl(*args)
        report.setdefault("commands", []).append({"args": list(args), "returncode": result.returncode,
                                                   "stdout": result.stdout, "stderr": result.stderr})
        if result.returncode:
            raise RuntimeError("systemctl failed: " + " ".join(args))

    save("formal_before.json", before)
    started = time.monotonic()
    try:
        command("stop", PORTAL)
        if active(PORTAL) != "inactive":
            raise RuntimeError("Formal service did not stop")
        command("start", CANDIDATE)
        candidate = wait_status(8083, True)
        save("candidate.json", candidate)
        if active(PORTAL) != "inactive" or active(CANDIDATE) != "active":
            raise RuntimeError("Services are not mutually exclusive")
        report["candidate_verified"] = True
    except (Exception, KeyboardInterrupt) as exc:
        report["errors"].append(type(exc).__name__ + ": " + str(exc))
    finally:
        try:
            command("stop", CANDIDATE)
            command("start", PORTAL)
            restored = wait_status(8080, False)
            save("formal_restored.json", restored)
            if active(CANDIDATE) != "inactive" or active(PORTAL) != "active":
                raise RuntimeError("Formal service recovery is not exclusive")
            report["formal_restored"] = True
        except (Exception, KeyboardInterrupt) as exc:
            report["errors"].append("Restore failed: " + type(exc).__name__ + ": " + str(exc))
        report["elapsed_seconds"] = time.monotonic() - started
        report["passed"] = report["candidate_verified"] and report["formal_restored"] and not report["errors"]
        save("report.json", report)
    print(json.dumps(report, indent=2))
    return 0 if report["passed"] else 1


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    prep = commands.add_parser("prepare")
    for name in ("formal", "candidate", "output"):
        prep.add_argument("--" + name, required=True, type=Path)
    for name in ("check", "run"):
        execute = commands.add_parser(name)
        execute.add_argument("--profile", required=True, type=Path)
        execute.add_argument("--expected-cid", required=True)
        if name == "run":
            execute.add_argument("--destination", required=True, type=Path)
    args = parser.parse_args()
    if args.command == "prepare":
        prepare(args.formal, args.candidate, args.output)
        return 0
    if args.command == "check":
        cid, _ = preflight(args.profile, args.expected_cid)
        print(json.dumps({"preflight_passed": True, "card_cid": cid}))
        return 0
    return run(args.profile, args.expected_cid, args.destination)


if __name__ == "__main__":
    raise SystemExit(main())
