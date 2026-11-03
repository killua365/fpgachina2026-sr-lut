"""Verify the complete portable delivery, qualified demo, and offline component archives."""
import argparse
import json
import os
from pathlib import Path
import re
import subprocess
import sys
from archive_store import ROOT, sha
from runtime_paths import runtime_python

MANIFEST = ROOT/'board/release/file_manifest.json'
RESULT = ROOT/'report/validation/verify_result.json'
EXCLUDE = {'.git','__pycache__','.Xil'}
VOLATILE = ('build/n5_session_tmp/','build/locks/','build/delivery_tmp/',
            'build/reproduction_work/','board/acceptance/','board/preparation/','board/analysis/')
SELF = {'board/release/file_manifest.json','board/release/file_manifest.sha256',
        'report/validation/verify_result.json'}

def original_snapshot_names():
    """Preserve original names within the immutable Nexys source evidence."""
    frozen = ROOT/'build/artifacts/nexys'
    return {'build/artifacts/nexys/'+item['path']
            for item in json.loads((frozen/'manifest.json').read_text('utf-8'))['files']
            if item['path'].startswith('route/source/')}


def exact_case(path):
    current = ROOT
    for part in Path(path).parts:
        assert part in {p.name for p in current.iterdir()}, 'Path case differs: '+path
        current /= part


def files():
    for p in sorted(ROOT.rglob('*')):
        rel=p.relative_to(ROOT)
        if EXCLUDE.intersection(rel.parts) or not p.is_file(): continue
        name=rel.as_posix()
        if name in SELF or name.startswith(VOLATILE) or p.suffix in ('.pyc','.jou'): continue
        yield p,name


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--write-manifest',action='store_true')
    args=parser.parse_args()
    if args.write_manifest:
        source=json.loads((ROOT/'board/release/source.json').read_text('utf-8'))
        version=re.search(r'_r(\d+)_repack',source['remote_package'])
        if version is None:
            raise ValueError('Source contract has no package revision')
        entries=[dict(path=name,bytes=p.stat().st_size,sha256=sha(p)) for p,name in files()]
        MANIFEST.write_text(json.dumps(dict(schema='sr_lut_delivery_files_v2',
            functional_version='r'+version.group(1),organization_revision='submission_20261023',
            excluded=list(VOLATILE)+sorted(EXCLUDE),files=entries),indent=2)+'\n','utf-8')
        MANIFEST.with_suffix('.sha256').write_text(sha(MANIFEST)+'  file_manifest.json\n','ascii')
    assert sha(MANIFEST)==MANIFEST.with_suffix('.sha256').read_text().split()[0], 'Manifest digest differs'
    manifest=json.loads(MANIFEST.read_text('utf-8'))
    observed={name for _,name in files()}
    assert observed=={item['path'] for item in manifest['files']}, 'Unexpected or missing delivery files'
    original_names=original_snapshot_names()
    for item in manifest['files']:
        p=(ROOT/item['path']).resolve(); p.relative_to(ROOT)
        assert p.is_file() and p.stat().st_size==item['bytes'] and sha(p)==item['sha256'],item['path']
        assert item['path'] in original_names or all(
            part=='README.md' or re.fullmatch('[a-z0-9_.-]+',part)
            for part in Path(item['path']).parts),item['path']
    for path in original_names:
        exact_case(path)
    for item in json.loads((ROOT/'board/release/inputs.json').read_text('utf-8'))['files']:
        if 'source' in item:
            exact_case(item['source'])
    assert (ROOT/'.git').is_dir(), 'Standalone Git required for qualified source validation'
    assert not (ROOT/'.git/commondir').exists() and not (ROOT/'.git/objects/info/alternates').exists()
    subprocess.run(['git','fsck','--no-reflogs'],cwd=ROOT,check=True,stdout=subprocess.DEVNULL,stderr=subprocess.PIPE)
    sys.path[:0]=[str(ROOT/'board/host'),str(ROOT/'board/pynq')]
    from demo_session import local_check
    check=local_check()
    sys.path.insert(0,str(ROOT/'build/candidates/nexys_vga_26102101'))
    from qualify_candidate import check as check_vga
    vga=check_vga()
    import numpy,cv2,PIL
    environment=runtime_python('host').parent
    for module in (numpy,cv2,PIL):
        # MSIX can expose the same cache file through logical and redirected
        # paths. Compare Windows file identities rather than path spelling.
        expected=environment/'Lib/site-packages'/module.__name__/'__init__.py'
        assert os.path.samefile(module.__file__,expected), 'External module: '+module.__name__
    source=json.loads((ROOT/'board/release/source.json').read_text('utf-8'))
    result=dict(passed=True,files=len(manifest['files']),bytes=sum(x['bytes'] for x in manifest['files']),
                manifest_sha256=sha(MANIFEST),vga_configuration=vga,
                standalone_git=True,offline_python=True,current_payload_files=source['files'],
                session_loading_eligible=check['session_loading_eligible'],hardware_access=False,
                scope='current_delivery_integrity_and_qualified_software; historical_performance_is_independent')
    RESULT.write_text(json.dumps(result,indent=2)+'\n','utf-8')
    print(json.dumps(result))


if __name__=='__main__':main()
