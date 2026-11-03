"""Freeze a completed development build without claiming HDMI acceptance."""

import argparse
import hashlib
import json
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
import xml.etree.ElementTree as ET
import zipfile

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "board/pynq"))
from hdmi_hardware_validation import (parse_routed_timing, require_clean_timing,
                                      validate_clock_contract)


def digest(path):
    result = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(4 * 1024 * 1024), b""):
            result.update(chunk)
    return result.hexdigest()


def freeze(root, scale, destination, source_commit):
    scope = ["build/scripts/pynq", "src/rtl", "src/third_party/digilent_video", "data/lut"]
    revision = subprocess.check_output(["git", "rev-parse", f"{source_commit}^{{commit}}"],
                                       cwd=root, text=True).strip()
    subprocess.run(["git", "diff", "--quiet", revision, "--", *scope], cwd=root, check=True)
    # 1080p60 builds are isolated from the legacy 720p work directories.  Keep
    # the legacy fallback for older development snapshots, but never silently
    # select a stale artifact when a 1080p60 build is present.
    isolated_export = root / f"build/hdmi_x{scale}_1080p60_work/export"
    legacy_export = root / f"build/hdmi_x{scale}_work/export"
    export = isolated_export if isolated_export.is_dir() else legacy_export
    isolated_reports = root / f"board/local/hdmi_x{scale}_1080p60"
    legacy_reports = root / f"board/local/hdmi_x{scale}"
    reports = isolated_reports if isolated_reports.is_dir() else legacy_reports
    artifacts = [export / f"sr_lut_x{scale}.{ext}" for ext in ("bit", "hwh", "xsa")]
    hwh = ET.parse(artifacts[1]).getroot()
    modules = {module.get("INSTANCE"): module for module in hwh.findall(".//MODULE")}

    def parameter(module, name):
        return modules[module].find(f"./PARAMETERS/PARAMETER[@NAME='{name}']").get("VALUE")

    if hwh.get("VIVADOVERSION") != "2026.1" or int(parameter("sr_lut_scale_dynamic_0", "SCALE")) != scale:
        raise ValueError("HWH tool version or scale mismatch")
    # 1080p60 requires the doubled CEA pixel and TMDS clocks.  A stale HWH
    # generated from a 1080p30/720p project can otherwise pair with a newer
    # BIT and silently produce a 30 Hz scanout on the board.
    if abs(float(parameter("hdmi_clock", "C_CLKOUT0_ACTUAL_FREQ")) - 148.5) > 0.01 or \
       abs(float(parameter("hdmi_clock", "C_CLKOUT1_ACTUAL_FREQ")) - 742.5) > 0.01:
        raise ValueError("HDMI clock HWH is not 148.5/742.5 MHz for 1080p60")
    reset_ports = {p.get("NAME"): p.get("SIGNAME") for p in modules["hdmi_reset"].findall("./PORTS/PORT")}
    if int(parameter("hdmi_reset", "C_AUX_RESET_HIGH")) != 0 or reset_ports.get("aux_reset_in") != "hdmi_one_dout":
        raise ValueError("HDMI auxiliary reset is not deasserted; refusing to freeze")
    for key, value in {"C_INCLUDE_MM2S": 1, "C_INCLUDE_S2MM": 0, "C_NUM_FSTORES": 3,
                       "C_M_AXIS_MM2S_TDATA_WIDTH": 32}.items():
        if int(parameter("hdmi_vdma", key)) != value:
            raise ValueError(f"Unexpected VDMA configuration: {key}")
    memory = {node.get("INSTANCE"): int(node.get("BASEVALUE"), 0) for node in hwh.findall(".//MEMRANGE")}
    for name, address in {"hdmi_vdma": 0x43010000, "hdmi_vtc": 0x43C10000, "hdmi_status": 0x41250000}.items():
        if memory.get(name) != address:
            raise ValueError(f"HDMI address contract mismatch: {name}")
    with zipfile.ZipFile(artifacts[2]) as archive:
        if archive.testzip() is not None:
            raise ValueError("XSA archive CRC check failed")
        bits = [name for name in archive.namelist() if name.endswith(".bit")]
        if len(bits) != 1 or hashlib.sha256(archive.read(bits[0])).hexdigest() != digest(artifacts[0]):
            raise ValueError("XSA bitstream differs from the standalone BIT")
    timing_text = (reports / f"stage_40m_scale{scale}_system_route_timing.rpt").read_text()
    timing = parse_routed_timing(timing_text)
    require_clean_timing(timing)
    clocks = validate_clock_contract(artifacts[1], timing_text, "1920x1080@60")
    report_files = sorted(reports.glob("*.rpt"))
    required = {f"hdmi_x{scale}_{name}.rpt" for name in ("cdc", "clock_interaction", "bus_skew")}
    if not required.issubset({path.name for path in report_files}):
        raise ValueError("CDC, clock-interaction and bus-skew reports are required")
    cdc_text = (reports / f"hdmi_x{scale}_cdc.rpt").read_text()
    cdc = [{"id": name, "severity": severity, "count": int(count)} for name, severity, count in
           re.findall(r"^(CDC-\d+)\s+(Info|Warning|Critical)\s+(\d+)\s", cdc_text, re.M)]
    destination = destination.resolve()
    if destination.exists() or not destination.parent.is_dir():
        raise ValueError("Choose a new frozen directory under an existing directory")
    temp = Path(tempfile.mkdtemp(prefix=".hdmi_freeze_", dir=destination.parent))
    try:
        files = []
        for source in artifacts + report_files:
            target = temp / source.name
            before = digest(source)
            shutil.copyfile(source, target)
            if digest(target) != before or digest(source) != before:
                raise RuntimeError("Source changed or frozen copy hash mismatch")
            files.append({"path": source.name, "size_bytes": target.stat().st_size, "sha256": before})
        report = {"schema_version": 1, "kind": "hdmi_development_hardware", "scale": scale,
                  "vivado_version": "2026.1", "hardware_source_commit": revision,
                  "source_scope": scope, "timing": timing, "cdc_summary": cdc,
                  "drc_cdc_clock_review_required": True, "xsa_bit_matches": True,
                  "clock_contract": clocks,
                  "physical_hdmi_verified": False, "release_schema3_verified": False,
                  "deployment_allowed": False, "files": files}
        (temp / "artifact_manifest.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
        temp.rename(destination)
        return report
    finally:
        if temp.exists():
            shutil.rmtree(temp)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--scale", type=int, choices=(2, 4), required=True)
    parser.add_argument("--destination", type=Path, required=True)
    parser.add_argument("--source-commit", required=True)
    options = parser.parse_args()
    print(json.dumps(freeze(Path(__file__).resolve().parents[3], options.scale,
                            options.destination, options.source_commit), indent=2))
