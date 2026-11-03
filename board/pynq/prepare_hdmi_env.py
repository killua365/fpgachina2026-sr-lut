"""Create a spare-only HDMI venv, sharing immutable baseline packages read-only."""

import argparse
import hashlib
from pathlib import Path
import subprocess
import sys


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--destination", type=Path, required=True)
    parser.add_argument("--evdev-source", type=Path, required=True)
    parser.add_argument("--build-wheels", type=Path, required=True)
    args = parser.parse_args()
    if Path("/sys/block/mmcblk0/device/cid").read_text().strip() != "035344534333324780dba606de014500":
        raise RuntimeError("Environment creation is restricted to the verified spare")
    expected = "5d3278892ce1f92a74d6bf888cc8525d9f68af85dbe336c95d1c87fb8f423069"
    if hashlib.sha256(args.evdev_source.read_bytes()).hexdigest() != expected:
        raise ValueError("Unexpected evdev 1.9.2 source archive")
    destination = args.destination.resolve()
    destination.relative_to(Path("/home/xilinx/sr_lut_hdmi_envs"))
    if destination.exists():
        raise FileExistsError("Refusing to overwrite an existing HDMI environment")
    subprocess.run([sys.executable, "-m", "venv", str(destination)], check=True)
    site = destination / "lib/python3.10/site-packages"
    interpreter = str(destination / "bin/python3")
    subprocess.run([interpreter, "-m", "pip", "install", "--no-index", "--no-deps",
                    "--find-links", str(args.build_wheels),
                    "setuptools==75.8.0", "wheel==0.45.1", "packaging==24.2"], check=True)
    subprocess.run([interpreter, "-m", "pip", "install", "--no-index", "--no-build-isolation",
                    "--no-deps", str(args.evdev_source)], check=True)
    # Build in isolation first, then expose the immutable baseline runtime.
    # Old packaging/setuptools entry points must not enter the build process.
    baseline_paths = [value for value in sys.path if value.endswith(("site-packages", "dist-packages"))]
    (site / "pynq_baseline.pth").write_text("\n".join(baseline_paths) + "\n")
    subprocess.run([interpreter, "-B", "-c",
                    "import evdev, pynq, numpy, cv2, PIL; from importlib.metadata import version; "
                    "assert version('evdev') == '1.9.2'; print('HDMI dependency imports/version PASS')"], check=True)


if __name__ == "__main__":
    main()
