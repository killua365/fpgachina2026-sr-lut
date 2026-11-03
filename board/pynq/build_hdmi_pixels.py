"""Build the isolated ARM HDMI copy library; does not access FPGA or root files."""
import hashlib
import json
from pathlib import Path
import subprocess
import sys


def main():
    source = Path(sys.argv[1]).resolve(strict=True)
    folder = source.parent
    target = folder / 'libhdmi_pixels.so'
    record = folder / 'build.json'
    if target.exists() or record.exists():
        raise FileExistsError('Choose a fresh native build directory')
    machine = subprocess.check_output(['gcc', '-dumpmachine'], text=True).strip()
    if machine != 'arm-linux-gnueabihf':
        raise RuntimeError('Build this library on the PYNQ ARM hard-float toolchain')
    flags = ['-shared', '-O3', '-fPIC', '-mfpu=neon', '-mfloat-abi=hard', '-march=armv7-a',
             '-Wall', '-Wextra', '-Werror', '-fno-strict-aliasing']
    subprocess.run(['gcc', *flags, str(source), '-o', str(target)], check=True)
    report = {'machine': machine, 'compiler': subprocess.check_output(['gcc', '--version'], text=True),
              'flags': flags, 'source_sha256': hashlib.sha256(source.read_bytes()).hexdigest(),
              'library_sha256': hashlib.sha256(target.read_bytes()).hexdigest(), 'hardware_access': False}
    record.write_text(json.dumps(report, indent=2) + '\n')
    print(json.dumps(report, indent=2))


if __name__ == '__main__':
    main()
