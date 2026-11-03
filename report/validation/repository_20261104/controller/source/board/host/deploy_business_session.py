"""Install a new verified development directory; never starts hardware/services."""
import argparse
import hashlib
import json
from pathlib import Path
import shlex
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT/'board/host'))
from business_session_source import CONTRACT
from business_session_remote import BOARD_PYTHON

EXTRACT = '''from pathlib import Path
import hashlib,json,sys,zipfile
root=Path(sys.argv[1]); archive=root.with_suffix('.zip')
assert hashlib.sha256(archive.read_bytes()).hexdigest()==sys.argv[2]
if root.exists():
    assert root.resolve()==root
else:
    with zipfile.ZipFile(archive) as z:
        names=z.namelist()
        assert len(names)==len(set(names))
        for name in names:
            p=Path(name)
            assert not p.is_absolute() and '..' not in p.parts and ':' not in name and '\\\\' not in name
        root.mkdir()
        z.extractall(root)
assert hashlib.sha256((root/'hdmi_business_package.json').read_bytes()).hexdigest()==sys.argv[3]
print('SOURCE_DIRECTORY_READY_NO_HARDWARE')
'''


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--run', action='store_true')
    a = p.parse_args()
    c = json.loads(CONTRACT.read_text('utf-8'))
    archive = ROOT/c['archive']
    if hashlib.sha256(archive.read_bytes()).hexdigest() != c['package_sha256']:
        raise ValueError('Source ZIP changed')
    if not a.run:
        print('LOCAL_PREFLIGHT_ONLY: no SSH or hardware access')
        return
    options = ['-i', str(Path.home()/'.ssh/sr_lut_pynq_z2_ed25519'), '-o', 'BatchMode=yes',
               '-o', 'StrictHostKeyChecking=yes', '-o', 'ConnectTimeout=8']
    remote = c['remote_package']
    check = subprocess.run(['ssh.exe', *options, 'xilinx@192.168.2.99',
        shlex.join(['test', '-e', remote+'.zip'])], timeout=15)
    if check.returncode == 1:
        subprocess.run(['scp.exe', *options, str(archive), 'xilinx@192.168.2.99:'+remote+'.zip'], check=True, timeout=180)
    elif check.returncode != 0:
        raise RuntimeError('Remote connection failed before upload')
    subprocess.run(['ssh.exe', *options, 'xilinx@192.168.2.99', shlex.join(
        ['python3', '-B', '-', remote, c['package_sha256'], c['package_manifest_sha256']])],
        input=EXTRACT, text=True, check=True, timeout=60)
    subprocess.run(['ssh.exe', *options, 'xilinx@192.168.2.99', shlex.join(
        [BOARD_PYTHON, '-B', remote+'/board/pynq/hdmi_business_app.py', 'check', '--root', remote])], check=True, timeout=90)


if __name__ == '__main__':
    main()
