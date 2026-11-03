"""Archive offline evidence; a successful tool exit is not a hardware pass."""
import argparse
import hashlib
import json
from pathlib import Path
import re
import shutil
import zipfile

ROOT = Path(__file__).resolve().parents[3]
FIELDS = ("setup_wns_ns", "setup_tns_ns", "setup_failing_endpoints", "setup_total_endpoints",
          "hold_whs_ns", "hold_ths_ns", "hold_failing_endpoints", "hold_total_endpoints",
          "pulse_wpws_ns", "pulse_tpws_ns", "pulse_failing_endpoints", "pulse_total_endpoints")


def sha256(path):
    value = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for data in iter(lambda: stream.read(1024 * 1024), b""):
            value.update(data)
    return value.hexdigest()


def timing_summary(text):
    sections = text.split("| Design Timing Summary")
    if len(sections) != 2:
        raise ValueError("Missing or ambiguous Design Timing Summary")
    table = sections[1].split("| Clock Summary")[0]
    candidates = []
    for line in table.splitlines():
        tokens = line.split()
        if len(tokens) == 12 and all(re.fullmatch(r"-?\d+(?:\.\d+)?", item) for item in tokens):
            candidates.append(tokens)
    if len(candidates) != 1:
        raise ValueError("Expected exactly one complete design timing row")
    values = dict(zip(FIELDS, map(float, candidates[0])))
    for key in FIELDS:
        if key.endswith("endpoints"):
            if values[key] != int(values[key]) or values[key] < 0:
                raise ValueError("Invalid endpoint count")
            values[key] = int(values[key])
    return values


def timing_passed(values):
    return (all(values[key] >= 0 for key in ("setup_wns_ns", "hold_whs_ns", "pulse_wpws_ns"))
            and all(values[key] == 0 for key in ("setup_tns_ns", "hold_ths_ns", "pulse_tpws_ns",
                "setup_failing_endpoints", "hold_failing_endpoints", "pulse_failing_endpoints"))
            and all(values[key] > 0 for key in ("setup_total_endpoints", "hold_total_endpoints", "pulse_total_endpoints")))


def drc_summary(text):
    totals = re.findall(r"Checks found:\s*(\d+)", text)
    if len(totals) != 1:
        raise ValueError("Missing or ambiguous DRC total")
    rules = {}
    counts = {"Error": 0, "Critical Warning": 0, "Warning": 0, "Info": 0}
    for line in text.splitlines():
        fields = [field.strip() for field in line.split("|")]
        if len(fields) == 6 and re.fullmatch(r"[A-Z][A-Z0-9]+-\d+", fields[1]):
            rule, severity, count = fields[1], fields[2], int(fields[4])
            if rule in rules or severity not in counts or count <= 0:
                raise ValueError("Invalid DRC summary row")
            rules[rule] = {"severity": severity, "count": count}
            counts[severity] += count
    if sum(counts.values()) != int(totals[0]):
        raise ValueError("DRC rows do not match reported total")
    return {"checks": int(totals[0]), "by_severity": counts, "rules": rules}


def utilization_summary(text):
    labels = {"lut": "Slice LUTs", "ff": "Register as Flip Flop", "ramb36": "RAMB36/FIFO*",
              "ramb18": "RAMB18", "dsp": "DSPs", "gtp_channels": "GTPE2_CHANNEL"}
    result = {}
    for key, label in labels.items():
        values = re.findall(r"^\|\s*" + re.escape(label) + r"\s*\|\s*(\d+)\s*\|", text, re.M)
        if not values or len(set(values)) != 1:
            raise ValueError(f"Missing or conflicting resource row: {label}")
        result[key] = int(values[0])
    return result


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("run", type=Path)
    parser.add_argument("destination", type=Path)
    parser.add_argument("--log", type=Path, required=True)
    parser.add_argument("--variant", choices=("upstream", "cdc_review", "bridge_control", "dp1080"), required=True)
    parser.add_argument("--source-snapshot", type=Path)
    args = parser.parse_args()
    args.run = args.run.resolve()
    args.destination = args.destination.resolve()
    args.destination.relative_to(ROOT / "build/frozen")
    if args.destination.exists():
        raise ValueError("Archive exists; never overwrite evidence")
    timing = timing_summary((args.run / "timing_route.rpt").read_text())
    drc = drc_summary((args.run / "drc_route.rpt").read_text())
    utilization = utilization_summary((args.run / "utilization_route.rpt").read_text())
    source_root = ROOT
    snapshot_record = None
    if args.source_snapshot:
        source_root = args.source_snapshot.resolve()
        manifest = source_root.parent / "source_manifest.json"
        snapshot_record = json.loads(manifest.read_text("utf-8-sig"))
        if snapshot_record["variant"] != args.variant:
            raise ValueError("Source variant does not match requested audit")
        expected_paths = set()
        for entry in snapshot_record["files"]:
            path = (source_root / entry["path"]).resolve()
            path.relative_to(source_root)
            if entry["path"] in expected_paths:
                raise ValueError("Duplicate snapshot source")
            expected_paths.add(entry["path"])
            if path.stat().st_size != entry["size_bytes"] or sha256(path) != entry["sha256"].lower():
                raise ValueError(f"Source snapshot changed: {entry['path']}")
        actual_paths = {p.relative_to(source_root).as_posix() for p in source_root.rglob("*") if p.is_file()}
        if actual_paths != expected_paths:
            raise ValueError("Unlisted snapshot source")
    inputs = [args.run / name for name in ("audit_identity.txt", "timing_route.rpt", "drc_route.rpt",
              "cdc_route.rpt", "methodology_route.rpt", "clock_interaction.rpt", "clocks_route.rpt",
              "exceptions_route.rpt", "utilization_route.rpt")] + [args.log]
    if args.variant in ("bridge_control", "dp1080"):
        inputs += [source_root / "src/rtl/nexys_bridge" / name for name in (
            "hotplug_decode.vhd", "aux_channel.vhd", "channel_managemnt.vhd", "gtx_tx_reset_controller.vhd")]
        # New split AUX pads are optional only for historical snapshots which
        # predate them. Archive exclusively from this run's actual source root.
        for name in ("aux_io.vhd", "aux_interface.vhd", "top_level.vhd", "edid_decode.vhd", "dp_register_decode.vhd", "dp_aux_messages.vhd", "transceiver.vhd", "link_signal_mgmt.vhd", "gtp_tx_drive.vhd", "gtp_ref_monitor.vhd", "dp_1080p60_source.vhd"):
            path = source_root / "src/rtl/nexys_bridge" / name
            if path.is_file():
                inputs.append(path)
        inputs += [source_root / "build/scripts/nexys/audit_reference.tcl", source_root / "build/scripts/nexys/reference_cdc.tcl"]
        for name in ("reference_clocks.tcl", "reference_pins.xdc"):
            clock_script = source_root / "build/scripts/nexys" / name
            if clock_script.is_file():
                inputs.append(clock_script)
    if snapshot_record:
        inputs.append(source_root.parent / "source_manifest.json")
    for path in inputs:
        if not path.is_file():
            raise ValueError(f"Incomplete report set: {path}")
    args.destination.mkdir(parents=True)
    records = []
    for path in inputs:
        dest = args.destination / path.name
        shutil.copyfile(path, dest)
        expected = sha256(path)
        if sha256(dest) != expected:
            raise ValueError("Evidence copy mismatch")
        records.append({"path": dest.name, "sha256": expected, "size_bytes": dest.stat().st_size})
    if snapshot_record:
        archive_path = args.destination / "source_snapshot.zip"
        with zipfile.ZipFile(archive_path, "x", compression=zipfile.ZIP_DEFLATED) as archive:
            for name in sorted(expected_paths):
                archive.write(source_root / name, name)
        records.append({"path": archive_path.name, "sha256": sha256(archive_path),
                        "size_bytes": archive_path.stat().st_size})
    scope = "1920x1080_60_rgb888_2lane_offline_audit" if args.variant == "dp1080" else "800x600_reference_offline_audit"
    if snapshot_record and snapshot_record.get("ser_mode"):
        scope = "hbr_2lane_enhanced_ser_diagnostic_only"
    record = {"schema_version": 1, "scope": scope,
              "variant": args.variant, "vivado": "2026.1", "part": "xc7a200tsbg484-1",
              "upstream_commit": "8b9cba275af11566e219f393b6493753ecec87ae",
              "dependency_lock_sha256": sha256(ROOT / "src/third_party/fpga_displayport_origin.json"),
              "timing": timing, "numeric_timing_passed": timing_passed(timing),
              "drc": drc, "resources": utilization,
              "complete_hardware_gate_passed": False, "deployable": False,
              "reason": "Offline implementation only; numeric timing/DRC/CDC need separate review; no physical display acceptance",
              "bitstream_generated": False, "board_programmed": False, "files": records}
    record["exact_build_snapshot_verified"] = snapshot_record is not None
    with (args.destination / "audit.json").open("x", encoding="utf-8", newline="\n") as stream:
        json.dump(record, stream, indent=2)
        stream.write("\n")
    print(json.dumps({"archive": str(args.destination), "numeric_timing_passed": timing_passed(timing),
                      "deployable": False}))


if __name__ == "__main__":
    main()
