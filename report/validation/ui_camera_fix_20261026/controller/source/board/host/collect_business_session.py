"""Copy completed N5 evidence and verify the same remote inventory before/after."""
import argparse
import json
from pathlib import Path
import subprocess
import sys
from business_evidence_files import INVENTORY, validate_inventory, verify_copy, transfer_timeout

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT/'board/host'))
from business_session_source import CONTRACT
from business_session_remote import validate_launch


def collect(launch, destination):
    c = json.loads(CONTRACT.read_text('utf-8'))
    validate_launch(launch, c)
    remote = launch['remote_results']
    index = destination.with_name(destination.name+'_download_index.json')
    if destination.exists() or index.exists():
        raise FileExistsError('Preserve previous source evidence')
    options = ['-i', str(Path.home()/'.ssh/sr_lut_pynq_z2_ed25519'), '-o', 'BatchMode=yes',
               '-o', 'StrictHostKeyChecking=yes', '-o', 'ConnectTimeout=8']
    def inventory():
        r = subprocess.run(['ssh.exe', *options, 'xilinx@192.168.2.99', 'python3', '-B', '-', remote],
            input=INVENTORY, capture_output=True, text=True, encoding='utf-8', timeout=3600, check=True)
        return validate_inventory(json.loads(r.stdout))
    files = inventory()
    destination.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(['scp.exe', *options, '-r', 'xilinx@192.168.2.99:'+remote, str(destination)],
                   check=True, timeout=transfer_timeout(files))
    verify_copy(destination, files)
    if files != inventory():
        raise ValueError('Source evidence changed during collection')
    report = json.loads((destination/'report.json').read_text())
    if (report.get('run_id'), report.get('manifest_sha256')) != (launch['run_id'], c['package_manifest_sha256']):
        raise ValueError('Copied a different source session')
    index.write_bytes((json.dumps(dict(run_id=launch['run_id'], files=files, all_copy_hashes_matched=True,
        n5_passed=False), indent=2)+'\n').encode())
    return dict(files=len(files), all_copy_hashes_matched=True, n5_passed=False)


if __name__ == '__main__':
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('launch', type=Path)
    p.add_argument('destination', type=Path)
    a = p.parse_args()
    print(json.dumps(collect(json.loads(a.launch.read_text('utf-8-sig')), a.destination)))
