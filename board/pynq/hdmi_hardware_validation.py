"""Read-only preflight for HDMI hardware; raw timing violations cannot be waived.

No PYNQ imports or hardware access. Used before packaging, upload and FPGA load.
Frozen manifests alone are insufficient: reports and HWH must agree with them.
"""

import hashlib
import json
import math
from pathlib import Path, PurePosixPath
import re
import xml.etree.ElementTree as ET
import tempfile
import zipfile


TIMING_KEYS = (
    "setup_wns_ns", "setup_tns_ns", "setup_failing_endpoints",
    "hold_wns_ns", "hold_tns_ns", "hold_failing_endpoints",
    "pulse_wns_ns", "pulse_tns_ns", "pulse_failing_endpoints",
)
MODES = {
    "1280x720@60": (1280, 720, 60, 74.25, 371.25),
    "1920x1080@30": (1920, 1080, 30, 74.25, 371.25),
    "1920x1080@60": (1920, 1080, 60, 148.5, 742.5),
}


def digest(path):
    h = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for block in iter(lambda: stream.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def parse_routed_timing(text):
    try:
        summary = text.split("Design Timing Summary", 1)[1].split("Clock Summary", 1)[0]
    except IndexError as exc:
        raise ValueError("Missing Design Timing Summary") from exc
    row = re.search(
        r"^\s*(-?\d+\.\d+)\s+(-?\d+\.\d+)\s+(\d+)\s+\d+\s+"
        r"(-?\d+\.\d+)\s+(-?\d+\.\d+)\s+(\d+)\s+\d+\s+"
        r"(-?\d+\.\d+)\s+(-?\d+\.\d+)\s+(\d+)\s+\d+\s*$", summary, re.M)
    if row is None:
        raise ValueError("Cannot identify the full routed timing summary")
    return dict(zip(TIMING_KEYS, map(float, row.groups())))


def require_clean_timing(timing):
    # Keep the values unchanged, including when rejecting a candidate.
    for key in TIMING_KEYS:
        value = timing.get(key)
        if type(value) not in (int, float) or not math.isfinite(value):
            raise ValueError("Missing or invalid timing value: " + key)
        if value < 0 or (("tns" in key or "failing" in key) and value != 0):
            raise ValueError(f"Routed timing violation: {key}={value}; no waiver allowed")


def validate_clock_contract(hwh_path, timing_text, mode):
    if mode not in MODES:
        raise ValueError("Unknown HDMI mode: " + str(mode))
    width, height, hz, pixel, serial = MODES[mode]
    modules = {m.get("INSTANCE"): m for m in ET.parse(hwh_path).findall(".//MODULE")}
    params = {p.get("NAME"): p.get("VALUE") for p in modules["hdmi_clock"].findall("./PARAMETERS/PARAMETER")}
    clock_table = timing_text.split("Clock Summary", 1)[1].split("Intra Clock Table", 1)[0]
    for index, expected in ((1, pixel), (2, serial)):
        hwh_mhz = float(params[f"C_CLKOUT{index - 1}_ACTUAL_FREQ"])
        row = re.search(rf"^\s*clk_out{index}_\S+\s+\{{[^}}]+\}}\s+(\d+\.\d+)\s+(\d+\.\d+)\s*$", clock_table, re.M)
        if row is None:
            raise ValueError(f"Missing routed clock clk_out{index}")
        routed_mhz = float(row.group(2))
        if abs(hwh_mhz - expected) > 0.02 or abs(routed_mhz - expected) > 0.02:
            raise ValueError(f"HDMI clock mismatch: clk_out{index}, HWH={hwh_mhz}, route={routed_mhz}, expected={expected}")
    # RGBX -> RGB adapter transfers one pixel per accepted AXI stream beat.
    port = modules["hdmi_vdma"].find("./PORTS/PORT[@NAME='m_axis_mm2s_aclk']")
    stream_hz = int(port.get("CLKFREQUENCY"))
    required = width * height * hz
    if stream_hz <= required:
        raise ValueError(f"Insufficient pixel stream capacity: {stream_hz} pixels/s <= {required} required")
    return {"pixel_mhz": pixel, "serial_mhz": serial, "stream_hz": stream_hz,
            "minimum_active_pixels_per_second": required}


def validate_artifact(directory, scale, mode):
    directory = Path(directory).resolve(strict=True)
    manifest = json.loads((directory / "artifact_manifest.json").read_text(encoding="utf-8-sig"))
    if manifest.get("scale") != scale:
        raise ValueError("Hardware scale mismatch")
    if manifest.get("pulse_width_waiver"):
        raise ValueError("Rejected hardware: TMDS pulse-width waiver is invalid")
    names = [f"sr_lut_x{scale}.bit", f"sr_lut_x{scale}.hwh",
             f"stage_40m_scale{scale}_system_route_timing.rpt"]
    files = {f["path"]: f for f in manifest["files"]}
    for name in names:
        if name not in files or digest(directory / name) != files[name]["sha256"].lower():
            raise ValueError("Missing or mismatched hardware evidence: " + name)
    text = (directory / names[-1]).read_text(encoding="utf-8-sig")
    timing = parse_routed_timing(text)
    require_clean_timing(timing)
    if any(manifest.get("timing", {}).get(k) != v for k, v in timing.items()):
        raise ValueError("Manifest timing differs from the original routed report")
    clocks = validate_clock_contract(directory / names[1], text, mode)
    return {"timing": timing, "clocks": clocks}


def require_deployable_archive(path):
    """Current host policy supersedes validators bundled in historical ZIPs."""
    registry = Path(__file__).resolve().parents[2] / 'build/manifest/hdmi_blocked_packages.json'
    policy = json.loads(registry.read_text(encoding='utf-8-sig'))
    actual = digest(path)
    for item in policy['packages']:
        if actual == item['sha256'].lower():
            raise ValueError('Historical HDMI package blocked: ' + item['reason'])


def check_archive(path):
    require_deployable_archive(path)
    from hdmi_m1 import validate_package
    with tempfile.TemporaryDirectory(prefix="hdmi_preflight_") as folder:
        root = Path(folder)
        with zipfile.ZipFile(path) as archive:
            for entry in archive.infolist():
                relative = PurePosixPath(entry.filename)
                if relative.is_absolute() or ".." in relative.parts or "\\" in entry.filename or ":" in entry.filename:
                    raise ValueError("Invalid archive path")
            archive.extractall(root)
        manifest, _ = validate_package(root)
        # A mislabeled/missing mode must not avoid the 1080p-only check inside
        # older diagnostic validators. All newly deployed profiles need evidence.
        if manifest.get('mode') not in MODES:
            raise ValueError('Explicit supported display mode is required')
        for scale, overlay in manifest['overlays'].items():
            validate_artifact((root / overlay['bit']).parent, int(scale), manifest['mode'])
        if manifest.get('sink_probe'):
            from hdmi_sink_probe import check
            check(root)
        return {"package_verified": True, "mode": manifest.get("mode"),
                "sha256": digest(path), "physical_hdmi_verified": False}


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--package", type=Path, required=True)
    args = parser.parse_args()
    try:
        print(json.dumps(check_archive(args.package)))
    except (ValueError, KeyError, OSError) as exc:
        print(json.dumps({"package_verified": False, "error": str(exc)}))
        raise SystemExit(1)
