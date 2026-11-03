"""Build the r15 repack directly from declared current inputs, without earlier releases."""
import argparse
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import zipfile
from archive_store import sha

ROOT = Path(__file__).resolve().parents[1]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, default=ROOT / "board/release/sr_lut_demo_r15_repack.zip")
    parser.add_argument("--revision", type=int, default=15)
    args = parser.parse_args()
    if not 15 <= args.revision <= 999:
        parser.error("revision must be between 15 and 999")
    spec = json.loads((ROOT / "board/release/inputs.json").read_text("utf-8"))
    manifest = json.loads((ROOT / "board/release/package_template.json").read_text("utf-8"))
    commit = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip()
    records = []
    args.out.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(args.out, "x", zipfile.ZIP_DEFLATED, compresslevel=6) as output:
        for entry in spec["files"]:
            name = entry["path"]
            if "component" in entry:
                folder = ROOT / "src/third_party"
                meta = json.loads((folder / (entry["component"] + ".json")).read_text("utf-8"))
                source = folder / (entry["component"] + ".zip")
                if sha(source) != meta["sha256"]:
                    raise ValueError("Third-party archive changed")
                with zipfile.ZipFile(source) as archive:
                    data = archive.read(entry["member"])
            else:
                data = (ROOT / entry["source"]).read_bytes()
                if name.endswith(".py") or name.startswith("src/ps/hdmi_display/fonts/"):
                    committed = subprocess.check_output(["git", "show", commit + ":" + entry["source"]], cwd=ROOT)
                    if data != committed:
                        raise ValueError("Commit current source bytes before packaging: " + entry["source"])
            records.append(dict(path=name, size=len(data), sha256=hashlib.sha256(data).hexdigest()))
            item = zipfile.ZipInfo(name, date_time=(2026, 10, 20, 0, 0, 0))
            item.compress_type = zipfile.ZIP_DEFLATED
            output.writestr(item, data)
        manifest.update(source_commit=commit, files=records,
                        packaging_revision="r15_repack_20261020" if args.revision == 15 else f"r{args.revision}_repack")
        manifest_data = (json.dumps(manifest, indent=2) + "\n").encode()
        item = zipfile.ZipInfo("hdmi_business_package.json", date_time=(2026, 10, 20, 0, 0, 0))
        item.compress_type = zipfile.ZIP_DEFLATED
        output.writestr(item, manifest_data)
    contract = dict(package_sha256=sha(args.out),
                    package_manifest_sha256=hashlib.sha256(manifest_data).hexdigest(),
                    source_commit=commit, remote_package=f"/home/xilinx/sr_lut_demo_r{args.revision}_repack",
                    archive=args.out.resolve().relative_to(ROOT).as_posix(),
                    directory="@cache/current_payload",
                    changed_sources=sorted(row["path"] for row in records if row["path"].endswith(".py")),
                    resource_files=sorted(row["path"] for row in records if "/fonts/" in row["path"]),
                    files=len(records), deployed=False, n5_passed=False, n6_passed=False, formal_release=False,
                    interactive_demo=True, demo_duration_seconds=None, normal_demo_startup_checks="skipped",
                    operator_interaction_cap_seconds=1800, timed_interaction_seconds=[60, 300],
                    hardware_lut_native_and_smoke_goldens_unchanged=True)
    (ROOT / "board/release/source.json").write_text(json.dumps(contract, indent=2) + "\n", "utf-8")
    print(json.dumps(dict(files=len(records), source_commit=commit, archive=contract["archive"])))


if __name__ == "__main__":
    main()
