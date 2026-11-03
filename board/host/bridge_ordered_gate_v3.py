"""Independent tool identity for 26100801 hardware; no hardware access."""
import argparse
import json
from pathlib import Path
import sys
import bridge_reacquire_candidate as old
from bridge_ordered_policy import LIMITS

ROOT=old.ROOT
LEGACY=ROOT/'build/frozen/nexys_2026-10-08_bridge_reacquire_tools_r1'
import bridge_ordered_gate_v2 as previous
import bridge_ordered_gate as original
NEW_INPUTS=(
    'board/host/bridge_ordered_transition.py','board/host/bridge_ordered_gate_v3.py',
    'board/host/supervise_bridge_ordered_v3.py','board/host/run_bridge_ordered_v3.tcl',
    'sim/nexys/qualify_bridge_ordered_v3.py',
    'sim/nexys/test_bridge_ordered_transition.py',
    'sim/nexys/test_bridge_ordered_loader_v3.py',
    'sim/nexys/test_bridge_ordered_supervisor_v3.py',
    'sim/nexys/test_bridge_ordered_gate_v3.py',
    'report/current/n4_ordered_exit_v3.json')
MODULES=original.MODULES+('test_bridge_ordered_json.py','test_bridge_ordered_transition.py',
    'test_bridge_ordered_loader_v3.py','test_bridge_ordered_supervisor_v3.py','test_bridge_ordered_gate_v3.py')
INPUTS=tuple(sorted(set(previous.INPUTS+NEW_INPUTS)))
CONTRACT=dict(tool_version='n4_ordered_exit_v3_26100903',hardware_candidate='26100801',limits=LIMITS,
              hardware_access=False,n4_passed=False,formal_release=False)


def check(package):
    old.check_package(LEGACY)
    m=old.read(package/'manifest.json')
    old.require(m['contract']==CONTRACT,'Wrong ordered tool contract')
    host=(ROOT/m['host']).resolve(); host.relative_to(ROOT/'report/validation')
    old.require(old.sha(host/'manifest.json')==m['host_sha256'],'Changed host qualification')
    old.inventory(host); r=old.read(host/'result.json')
    old.require(r['passed'] is True and r['hardware_access'] is False and r['contract']==CONTRACT,'Host qualification failed')
    old.require(set(r['modules'])==set(MODULES) and all(x==0 for x in r['modules'].values()),'Missing fault module')
    old.require({x['path'] for x in r['inputs']}==set(INPUTS),'Unbound dependencies')
    old.exact(ROOT,r['inputs'])
    for entry in r['inputs']:
        old.require(old.shared.hashlib.sha256(old.git(['show',m['source_commit']+':'+entry['path']])).hexdigest()==entry['sha256'],
                    'Tool input not committed: '+entry['path'])
    old.require(old.git(['show',m['source_commit']+':'+host.relative_to(ROOT).as_posix()+'/manifest.json'])==
                (host/'manifest.json').read_bytes(),'Qualification not committed')
    sys.path.insert(0,str(ROOT/'board/pynq'))
    from hdmi_hardware_validation import check_archive
    from bridge_ordered_remote import ZIP_SHA
    p=check_archive(ROOT/'board/releases/2026-10-08/n4_fixed_source_r1.zip')
    old.require(p['sha256']==ZIP_SHA,'Source ZIP changed')
    return dict(passed=True,**CONTRACT)


def seal(package,host):
    host=host.resolve(); host.relative_to(ROOT/'report/validation')
    package=package.resolve(); package.relative_to(ROOT/'build/frozen'); package.mkdir(parents=True,exist_ok=False)
    m=dict(contract=CONTRACT,host=host.relative_to(ROOT).as_posix(),host_sha256=old.sha(host/'manifest.json'),
           source_commit=old.git(['rev-parse','HEAD']).decode().strip())
    (package/'manifest.json').write_text(json.dumps(m,indent=2)+'\n',encoding='utf-8')
    return check(package)


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__); p.add_argument('mode',choices=('check','seal'))
    p.add_argument('package',type=Path); p.add_argument('--host',type=Path)
    a=p.parse_args(); print(json.dumps(check(a.package) if a.mode=='check' else seal(a.package,a.host)))
