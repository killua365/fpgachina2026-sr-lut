"""Separate 26100901 package; retain the immutable v3 dependency qualification."""
import argparse
import json
from pathlib import Path
import bridge_ordered_gate_v3 as previous
import bridge_continuous_candidate as candidate

old=candidate
ROOT=old.ROOT
LEGACY=ROOT/'build/frozen/nexys_2026-10-09_bridge_ordered_tools_v3_r1'
NEW_INPUTS=tuple('board/host/'+n for n in (
    'bridge_continuous_candidate.py','bridge_continuous_gate.py','bridge_continuous_live.py',
    'supervise_bridge_continuous.py','run_bridge_continuous.tcl','generate_bridge_continuous_bit.tcl',
    'analyze_bridge_continuous.py','analyze_bridge_continuous_ordered.py'))+tuple('sim/nexys/'+n for n in (
    'qualify_bridge_continuous.py','test_bridge_continuous_loader.py','test_bridge_continuous_supervisor.py',
    'test_bridge_continuous_identity.py','test_bridge_continuous_analysis.py',
    'test_bridge_continuous_ordered_analysis.py'))
INPUTS=tuple(sorted(set(previous.INPUTS+candidate.TOOLS+NEW_INPUTS)))
MODULES=previous.MODULES+('test_bridge_continuous_loader.py','test_bridge_continuous_supervisor.py',
                          'test_bridge_continuous_identity.py','test_bridge_continuous_analysis.py',
                          'test_bridge_continuous_ordered_analysis.py')
CONTRACT=dict(tool_version='n4_continuous_exit_v1_26101101',hardware_candidate='26100901',
              limits=previous.LIMITS,hardware_access=False,n4_passed=False,formal_release=False)


def check(package,candidate_folder=None):
    if candidate_folder is not None:
        old.require(Path(candidate_folder).resolve()==candidate.CANDIDATE.resolve(),'Unbound candidate directory')
    previous.check(LEGACY)
    candidate.check(candidate.CANDIDATE)
    m=old.read(package/'manifest.json')
    old.require(m['contract']==CONTRACT,'Wrong continuous-clock tool contract')
    old.require(m['candidate_sha256']==old.sha(candidate.CANDIDATE/'candidate.json'),'Changed candidate')
    host=(ROOT/m['host']).resolve();host.relative_to(ROOT/'report/validation')
    old.require(old.sha(host/'manifest.json')==m['host_sha256'],'Changed qualification manifest')
    old.inventory(host);r=old.read(host/'result.json')
    old.require(r['passed'] is True and r['hardware_access'] is False and r['contract']==CONTRACT,'Qualification failed')
    old.require(set(r['modules'])==set(MODULES) and all(x==0 for x in r['modules'].values()),'Missing test modules')
    old.require({x['path'] for x in r['inputs']}==set(INPUTS),'Unbound dependencies')
    old.exact(ROOT,r['inputs'])
    for item in r['inputs']:
        old.require(candidate.old.shared.hashlib.sha256(old.git(['show',m['source_commit']+':'+item['path']])).hexdigest()==item['sha256'],
                    'Tool source not committed')
    old.require(old.git(['show',m['source_commit']+':'+host.relative_to(ROOT).as_posix()+'/manifest.json'])==
                (host/'manifest.json').read_bytes(),'Qualification not committed')
    return dict(passed=True,**CONTRACT)


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('mode',choices=('check','seal'))
    p.add_argument('package',type=Path);p.add_argument('--host',type=Path)
    p.add_argument('--candidate',type=Path);a=p.parse_args()
    if a.mode=='seal':
        host=a.host.resolve();host.relative_to(ROOT/'report/validation')
        out=a.package.resolve();out.relative_to(ROOT/'build/frozen');out.mkdir(parents=True,exist_ok=False)
        (out/'manifest.json').write_text(json.dumps(dict(contract=CONTRACT,source_commit=old.git(['rev-parse','HEAD']).decode().strip(),
            candidate_sha256=old.sha(candidate.CANDIDATE/'candidate.json'),host=host.relative_to(ROOT).as_posix(),
            host_sha256=old.sha(host/'manifest.json')),indent=2)+'\n',encoding='utf-8')
    print(json.dumps(check(a.package,a.candidate)))
