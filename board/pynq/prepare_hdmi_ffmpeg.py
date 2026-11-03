"""Fetch verified Ubuntu packages and extract a private board FFmpeg runtime.

No apt installation, maintainer script, ldconfig, base Python or library change.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import urllib.request


def digest(path):
    result = hashlib.sha256()
    with path.open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            result.update(block)
    return result.hexdigest()


def fetch(plan, destination):
    destination.mkdir(parents=True, exist_ok=True)
    for item in plan['packages']:
        path = destination / Path(item['filename']).name
        if path.exists() and digest(path) == item['sha256']:
            continue
        uri = item['uri'].replace('http://ports.ubuntu.com/', 'https://ports.ubuntu.com/', 1)
        if not uri.startswith('https://ports.ubuntu.com/ubuntu-ports/pool/'):
            raise ValueError('Unexpected package origin')
        part = path.with_suffix('.part')
        print('Fetching ' + item['name'], flush=True)
        for attempt in range(3):
            offset = part.stat().st_size if part.exists() else 0
            request = urllib.request.Request(uri, headers={'Range': 'bytes={}-'.format(offset)} if offset else {})
            try:
                with urllib.request.urlopen(request, timeout=30) as response:
                    resume = response.status == 206 and offset > 0
                    if resume and not response.headers.get('Content-Range', '').startswith('bytes {}-'.format(offset)):
                        raise ValueError('Unexpected resumed byte range')
                    with part.open('ab' if resume else 'wb') as stream:
                        shutil.copyfileobj(response, stream)
                break
            except (OSError, urllib.error.URLError):
                if attempt == 2:
                    raise
                print('Retrying ' + item['name'], flush=True)
        if part.stat().st_size != item['size'] or digest(part) != item['sha256']:
            raise ValueError('Package digest mismatch: ' + item['name'])
        part.replace(path)
    (destination / 'packages.json').write_text(json.dumps(plan, indent=2) + '\n')
    return {'packages_verified': len(plan['packages']), 'destination': str(destination)}


def install(bundle, destination):
    if Path('/sys/block/mmcblk0/device/cid').read_text().strip() != '035344534333324780dba606de014500':
        raise RuntimeError('Only the verified spare card is supported')
    destination = destination.resolve()
    destination.relative_to(Path('/home/xilinx/sr_lut_hdmi_envs'))
    if destination.exists():
        raise FileExistsError('Choose a new isolated environment; never modify one in use')
    plan = json.loads((bundle / 'packages.json').read_text())
    for item in plan['packages']:
        path = bundle / Path(item['filename']).name
        if digest(path) != item['sha256']:
            raise ValueError('Package digest mismatch: ' + item['name'])
    temporary = Path(tempfile.mkdtemp(prefix='.ffmpeg_', dir=destination.parent))
    try:
        for item in plan['packages']:
            subprocess.run(['dpkg-deb', '-x', str(bundle / Path(item['filename']).name), str(temporary)],
                           check=True, timeout=60)
        # Every package's Debian copyright/license file remains under usr/share/doc.
        binary = temporary / 'bin'
        binary.mkdir()
        for name in ('ffmpeg', 'ffprobe'):
            script = binary / name
            script.write_text('#!/bin/sh\nset -eu\n'
                'ff_root=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)\n'
                'export LD_LIBRARY_PATH="$ff_root/usr/lib/arm-linux-gnueabihf${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}"\n'
                'exec "$ff_root/usr/bin/' + name + '" "$@"\n')
            script.chmod(0o755)
        versions = {}
        for name in ('ffmpeg', 'ffprobe'):
            result = subprocess.run([str(binary / name), '-version'], check=True, capture_output=True,
                                    text=True, timeout=30)
            versions[name] = result.stdout
        report = {'kind': 'isolated_hdmi_ffmpeg', 'packages': plan['packages'], 'versions': versions,
                  'base_system_modified': False, 'maintainer_scripts_executed': False,
                  'formal_release': False}
        (temporary / 'installation.json').write_text(json.dumps(report, indent=2) + '\n')
        temporary.rename(destination)
        return {'runtime': str(destination), 'versions': {k: v.splitlines()[0] for k, v in versions.items()}}
    finally:
        # Only the unique temporary directory created above is eligible for cleanup.
        if temporary.exists():
            shutil.rmtree(temporary)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command', choices=('fetch', 'install'))
    parser.add_argument('--plan', type=Path, default=Path(__file__).with_name('hdmi_ffmpeg_packages.json'))
    parser.add_argument('--bundle', type=Path, required=True)
    parser.add_argument('--destination', type=Path)
    args = parser.parse_args()
    if args.command == 'fetch':
        result = fetch(json.loads(args.plan.read_text()), args.bundle)
    else:
        if args.destination is None:
            parser.error('install requires --destination')
        result = install(args.bundle.resolve(), args.destination)
    print(json.dumps(result, indent=2))
