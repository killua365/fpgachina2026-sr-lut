"""Export the verified delivery and portable Git history to a new submission ZIP."""
import argparse
import hashlib
import json
from pathlib import Path
import subprocess
import tempfile
import zipfile

from archive_store import ROOT, sha
from verify_delivery import files


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--out', type=Path, required=True)
    args = parser.parse_args()
    out = args.out.resolve()
    if out.exists() or out.with_suffix('.zip.sha256').exists() or out.with_suffix('.json').exists():
        raise FileExistsError('Choose a new submission archive path')
    if out.is_relative_to(ROOT):
        raise ValueError('Write the submission archive outside the project')
    if subprocess.check_output(['git', 'status', '--porcelain'], cwd=ROOT).strip():
        raise ValueError('Commit the reviewed project before export')
    manifest_path = ROOT/'board/release/file_manifest.json'
    if sha(manifest_path) != manifest_path.with_suffix('.sha256').read_text().split()[0]:
        raise ValueError('Delivery manifest digest differs')
    manifest = json.loads(manifest_path.read_text('utf-8'))
    records = {x['path']: x for x in manifest['files']}
    if set(records) != {name for _, name in files()}:
        raise ValueError('Delivery files differ from manifest')
    for name, record in records.items():
        path = ROOT/name
        if path.stat().st_size != record['bytes'] or sha(path) != record['sha256']:
            raise ValueError('Changed delivery file: '+name)
    qualification = json.loads((ROOT/'report/validation/verify_result.json').read_text('utf-8'))
    if qualification.get('passed') is not True or qualification.get('manifest_sha256') != sha(manifest_path):
        raise ValueError('Run tools/verify_delivery.py before export')
    out.parent.mkdir(parents=True, exist_ok=True)
    prefix = 'sr_lut_fpga/'
    head = subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=ROOT, text=True).strip()
    with tempfile.TemporaryDirectory(prefix='sr_lut_export_', dir=out.parent) as temporary:
        gitdir = Path(temporary)/'repository'
        subprocess.run(['git', 'clone', '--bare', '--no-hardlinks', '--single-branch', '--no-tags',
                        str(ROOT), str(gitdir)], check=True)
        # Repack only the export repository. The working repository is unchanged.
        subprocess.run(['git', '--git-dir', str(gitdir), 'repack', '-a', '-d'], check=True)
        subprocess.run(['git', '--git-dir', str(gitdir), 'fsck', '--full', '--no-reflogs'], check=True)
        (gitdir/'config').write_text('[core]\n\trepositoryformatversion = 0\n\tfilemode = false\n\tbare = false\n\tlogallrefupdates = false\n', encoding='ascii')
        subprocess.run(['git', '--git-dir', str(gitdir), '--work-tree', str(ROOT), 'read-tree', 'HEAD'], check=True)
        git_files = [p for p in gitdir.rglob('*') if p.is_file() and
                     (p.relative_to(gitdir).parts[0] in ('objects', 'refs') or
                      p.relative_to(gitdir).as_posix() in ('HEAD', 'config', 'packed-refs', 'index'))]
        if (gitdir/'objects/info/alternates').exists():
            raise ValueError('Export Git history must be independent')
        extra = ['board/release/file_manifest.json', 'board/release/file_manifest.sha256',
                 'report/validation/verify_result.json']
        expected = {}
        with zipfile.ZipFile(out, 'x', zipfile.ZIP_DEFLATED, compresslevel=6, allowZip64=True) as bundle:
            for name in sorted([*records, *extra]):
                path = ROOT/name
                expected[prefix+name] = sha(path)
                bundle.write(path, prefix+name)
            for path in sorted(git_files):
                name = prefix+'.git/'+path.relative_to(gitdir).as_posix()
                expected[name] = sha(path)
                bundle.write(path, name)
        with zipfile.ZipFile(out) as bundle:
            if set(bundle.namelist()) != set(expected) or len(bundle.namelist()) != len(expected):
                raise ValueError('Submission archive inventory differs')
            for name, digest in expected.items():
                value = hashlib.sha256()
                with bundle.open(name) as stream:
                    for block in iter(lambda: stream.read(4*1024*1024), b''):
                        value.update(block)
                if value.hexdigest() != digest:
                    raise ValueError('Submission archive content differs: '+name)
        result = dict(schema='sr_lut_submission_archive_v1', passed=True,
                      archive=str(out), sha256=sha(out), bytes=out.stat().st_size,
                      delivery_files=len(records)+len(extra), git_files=len(git_files),
                      source_commit=head, git_history_verified=True,
                      archive_members_verified=len(expected), hardware_access=False)
    out.with_suffix('.zip.sha256').write_text(result['sha256']+'  '+out.name+'\n', encoding='ascii')
    out.with_suffix('.json').write_text(json.dumps(result, indent=2)+'\n', encoding='utf-8')
    print(json.dumps(result))


if __name__ == '__main__':
    main()
