"""N5 streaming evidence hashes, including multi-GB retained RGB exports."""
from collect_n4_source import validate_inventory
from audit_hdmi_business_pixels import digest

INVENTORY = '''from pathlib import Path
import hashlib,json,sys
r=Path(sys.argv[1])
if not json.loads((r/'report.json').read_text()).get('worker_reaped'):
    raise RuntimeError('Source run has not completed')
files=[]
for p in sorted(r.rglob('*')):
    if p.is_symlink(): raise RuntimeError('Unexpected evidence symlink')
    if p.is_file():
        p.resolve().relative_to(r.resolve())
        before=p.stat()
        h=hashlib.sha256()
        with p.open('rb') as f:
            for block in iter(lambda:f.read(1024*1024), b''): h.update(block)
        after=p.stat()
        if (before.st_size,before.st_mtime_ns)!=(after.st_size,after.st_mtime_ns):
            raise RuntimeError('Evidence changed while hashing')
        files.append(dict(path=p.relative_to(r).as_posix(),size=after.st_size,sha256=h.hexdigest()))
print(json.dumps(dict(files=files)))
'''


def verify_copy(folder, files):
    actual = {p.relative_to(folder).as_posix() for p in folder.rglob('*') if p.is_file()}
    if actual != {f['path'] for f in files}: raise ValueError('Evidence file list changed during copy')
    for item in files:
        p=folder/item['path']
        if p.is_symlink() or p.stat().st_size != item['size'] or digest(p) != item['sha256']:
            raise ValueError('Evidence bytes changed: '+item['path'])


def transfer_timeout(files):
    # Existing short-run minimum; allow retained RGB at 2 MiB/s plus a minute.
    # One hour remains a hard cap. Timeout preserves the incomplete destination.
    return min(3600, max(180, 60+sum(f['size'] for f in files)//(2*1024*1024)))
