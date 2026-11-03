"""Recompute the r5 measurements from their verified independent evidence capsule."""
import argparse
import json
from pathlib import Path
import subprocess
import sys
import tempfile
from archive_store import ROOT, extract_checked


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", required=True, type=Path)
    args = parser.parse_args()
    archive = ROOT / "report/performance/camera_r5_evidence.zip"
    meta = json.loads(archive.with_suffix(".json").read_text("utf-8"))
    with tempfile.TemporaryDirectory(prefix="sr_lut_r5_evidence_") as folder:
        capsule = extract_checked(archive, folder, meta["sha256"], meta["files"])
        subprocess.run([sys.executable, "-X", "utf8", "-E", "-B",
                        str(capsule / "tools/recompute_camera.py"), "--out", str(args.out.resolve())],
                       cwd=capsule, check=True)


if __name__ == "__main__":
    main()
