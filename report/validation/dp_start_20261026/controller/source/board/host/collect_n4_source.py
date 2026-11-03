"""Copy a completed N4 source run read-only; verify identities and every byte."""
import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path, PurePosixPath
import re
import subprocess

ZIP_SHA = '1e6a22db129f2abdac4ae7ae2372767cf831ce6ae3ba5151e8ddb5bb18e17dcb'
MANIFEST_SHA = '1bed6283dfe452e1a7fb44327c5b27285eadf07b3e77fdeaff3ce903a6d99721'
SOURCE = 'f4009730a1cab4871ce09d1d2ce850145018dcfb'
CID = '035344534333324780dba606de014500'
REMOTE_RE = r'/home/xilinx/n4_pynq_results_\d{8}_\d{6}_[0-9a-f]{8}'
INVENTORY = '''from pathlib import Path
import hashlib,json,sys
r=Path(sys.argv[1])
report=json.loads((r/'report.json').read_text())
if not report.get('worker_reaped'): raise RuntimeError('Source run has not completed')
files=[]
for p in sorted(r.rglob('*')):
    if p.is_symlink(): raise RuntimeError('Unexpected evidence symlink')
    if p.is_file():
        p.resolve().relative_to(r.resolve())
        files.append(dict(path=p.relative_to(r).as_posix(),size=p.stat().st_size,sha256=hashlib.sha256(p.read_bytes()).hexdigest()))
print(json.dumps(dict(files=files)))
'''


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def validate_inventory(value):
    files = value['files']
    seen = set()
    for f in files:
        name = f['path']
        p = PurePosixPath(name)
        if (not name or p.is_absolute() or '..' in p.parts or ':' in name or '\\' in name
                or str(p) != name or name.casefold() in seen):
            raise ValueError('Unsafe or duplicated evidence filename')
        seen.add(name.casefold())
        if type(f['size']) is not int or f['size'] < 0 or not re.fullmatch('[0-9a-f]{64}', f['sha256']):
            raise ValueError('Invalid evidence size/hash')
    if not {'report.json', 'worker/report.json', 'legacy_restored.json'} <= seen:
        raise ValueError('Missing final report or recovery evidence')
    return files


def verify_copy(folder, files):
    actual = {p.relative_to(folder).as_posix() for p in folder.rglob('*') if p.is_file()}
    if actual != {f['path'] for f in files}:
        raise ValueError('Evidence file list changed during copy')
    for f in files:
        p = folder / f['path']
        if p.is_symlink() or p.stat().st_size != f['size'] or digest(p) != f['sha256']:
            raise ValueError('Evidence bytes changed: ' + f['path'])


def validate_reports(folder):
    supervisor = json.loads((folder / 'report.json').read_text())
    worker = json.loads((folder / 'worker/report.json').read_text())
    for report, kind in ((supervisor, 'hdmi_m1_independent_supervisor'), (worker, 'hdmi_m1_supervised_run')):
        if (report.get('kind'), report.get('source_commit'), report.get('package_sha256')) != (kind, SOURCE, MANIFEST_SHA):
            raise ValueError('Source report identity mismatch')
    if not supervisor.get('worker_reaped') or worker.get('card_cid') != CID:
        raise ValueError('Worker still active or wrong system card')
    # Failed, completed experiments are valid evidence; they are never promoted.
    success = (supervisor.get('worker_exit_code') == 0 and not supervisor.get('termination_requested')
               and all(r.get('register_tests_passed') and r.get('legacy_restored') and not r.get('errors')
                       for r in (supervisor, worker)))
    return supervisor, worker, bool(success)


def collect(launch_path, destination):
    launch = json.loads(launch_path.read_text(encoding='utf-8-sig'))
    remote = launch['remote_results']
    if not re.fullmatch(REMOTE_RE, remote) or launch.get('package_sha256') != ZIP_SHA:
        raise ValueError('Unexpected source run or package identity')
    index = destination.with_name(destination.name + '_download_index.json')
    if destination.exists() or index.exists():
        raise FileExistsError('Keep original evidence; use a new destination')
    options = ['-i', str(Path.home() / '.ssh/sr_lut_pynq_z2_ed25519'), '-o', 'BatchMode=yes',
               '-o', 'StrictHostKeyChecking=yes', '-o', 'ConnectTimeout=8']

    def inventory():
        r = subprocess.run(['ssh.exe', *options, 'xilinx@192.168.2.99', 'python3', '-B', '-', remote],
                           input=INVENTORY, capture_output=True, text=True, encoding='utf-8', timeout=30, check=True)
        return validate_inventory(json.loads(r.stdout))

    before = inventory()
    destination.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(['scp.exe', *options, '-r', 'xilinx@192.168.2.99:' + remote, str(destination)],
                   check=True, timeout=120)
    verify_copy(destination, before)
    if inventory() != before:
        raise ValueError('Remote evidence changed during collection')
    supervisor, worker, passed = validate_reports(destination)
    result = dict(kind='n4_source_verified_download', host_utc=datetime.now(timezone.utc).isoformat(),
                  launch=str(launch_path.resolve()), launch_sha256=digest(launch_path),
                  collector_sha256=digest(Path(__file__)), remote_results=remote,
                  package_zip_sha256=ZIP_SHA, package_manifest_sha256=MANIFEST_SHA, source_commit=SOURCE,
                  files=before, all_hashes_match=True, remote_stable_during_copy=True,
                  source_register_tests_passed=passed, supervisor=supervisor,
                  worker_summary={k: worker.get(k) for k in ('phase', 'errors', 'legacy_restored', 'card_cid')},
                  physical_observation_verified=False, n4_passed=False, formal_release=False)
    with index.open('x', encoding='utf-8', newline='\n') as f:
        json.dump(result, f, indent=2); f.write('\n')
    return dict(files=len(before), all_hashes_match=True, source_register_tests_passed=passed, index=str(index))


if __name__ == '__main__':
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--launch', type=Path, required=True)
    p.add_argument('--destination', type=Path, required=True)
    a = p.parse_args()
    print(json.dumps(collect(a.launch, a.destination)))
