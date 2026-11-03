"""Seal/check only the independent VGA experiment, never the normal demo gate."""
import argparse
import hashlib
import json
from pathlib import Path
import shutil
import sys

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
WORK = ROOT/'build/reproduction_work/nexys_vga_26102101'
sys.path.insert(0,str(ROOT/'board/pynq'))
from hdmi_hardware_validation import parse_routed_timing, require_clean_timing


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def seal():
    source=json.loads((HERE/'source.json').read_text())
    incremental=(WORK/'route_incremental/candidate_bit_created.txt').is_file()
    route=WORK/('route_incremental' if incremental else 'route')
    build_log=WORK/('incremental.log' if incremental else 'vivado.log')
    assert (route/'candidate_bit_created.txt').is_file(), 'BIT build did not finish'
    expected_marker='VGA_INCREMENTAL_CANDIDATE_BIT_CREATED_NO_HARDWARE' if incremental else 'VGA_CANDIDATE_BIT_CREATED_NO_HARDWARE'
    assert expected_marker in build_log.read_text(errors='replace')
    timing=parse_routed_timing((route/'timing_route.rpt').read_text())
    require_clean_timing(timing)
    changed=[]
    for original in (ROOT/'src/rtl').rglob('*'):
        if original.is_file():
            rel=original.relative_to(ROOT)
            assert (WORK/'source'/rel).is_file()
            if sha(original)!=sha(WORK/'source'/rel): changed.append(rel.as_posix())
    assert changed==['src/rtl/nexys_bridge/aux_channel.vhd'], changed
    assert sha(WORK/'source/src/rtl/nexys_bridge/aux_channel.vhd')==sha(HERE/'aux_channel.vhd')
    assert sha(HERE/'aux_channel.vhd')==source['source_patch']['candidate_sha256']
    simulation=WORK/'power_sim_r3'
    for i in range(9):
        log=(simulation/f'case_{i}.log').read_text(errors='replace')
        assert f'VGA_POWER_CASE_PASS {i}' in log and 'Failure:' not in log
    artifacts=HERE/'artifacts'
    artifacts.mkdir(exist_ok=True)
    for name in ('pattern.bit','timing_route.rpt','drc_route.rpt','utilization_route.rpt'):
        shutil.copy2(route/name,artifacts/name)
    shutil.copy2(WORK/'route/build_identity.json',artifacts/'build_identity.json')
    if incremental:shutil.copy2(route/'incremental_reuse.rpt',artifacts/'incremental_reuse.rpt')
    evidence=HERE/'simulation'
    evidence.mkdir(exist_ok=True)
    for name in ('compile.log',*[f'case_{i}.log' for i in range(9)]):
        shutil.copy2(simulation/name,evidence/name)
    source['route_script_sha256']=sha(WORK/'nexys_route.tcl')
    if incremental:
        source['incremental_script_sha256']=sha(HERE/'build_incremental.tcl')
        source['physical_reference_dcp_sha256']=sha(ROOT/'build/artifacts/nexys/route/routed.dcp')
    (HERE/'source.json').write_text(json.dumps(source,indent=2)+'\n',encoding='utf-8')
    result=dict(candidate_id='26102101',part='xc7a200tsbg484-1',
        implementation_revision=2 if incremental else 1,
        source_patch=source['source_patch'],timing=timing,
        simulations=9,simulations_passed=True,bitstream_generated=True,
        hardware_tested=False,normal_demo_eligible=False,default_files_replaced=False,
        flash_write_allowed=False,route_script_sha256=sha(WORK/'nexys_route.tcl'),
        policy=source['policy'],base_bit_sha256=source['base_bit_sha256'],
        physical_reference_dcp_sha256=source.get('physical_reference_dcp_sha256'))
    (HERE/'result.json').write_text(json.dumps(result,indent=2)+'\n',encoding='utf-8')
    entries=[dict(path=p.relative_to(HERE).as_posix(),bytes=p.stat().st_size,sha256=sha(p))
             for p in sorted(HERE.rglob('*')) if p.is_file() and p.name!='manifest.json'
             and '__pycache__' not in p.parts]
    (HERE/'manifest.json').write_text(json.dumps(dict(schema='nexys_vga_candidate_v1',files=entries),indent=2)+'\n',encoding='utf-8')


def check():
    manifest=json.loads((HERE/'manifest.json').read_text())
    assert manifest['schema']=='nexys_vga_candidate_v1'
    for item in manifest['files']:
        path=(HERE/item['path']).resolve()
        path.relative_to(HERE)
        assert path.is_file() and path.stat().st_size==item['bytes'] and sha(path)==item['sha256'],item['path']
    result=json.loads((HERE/'result.json').read_text())
    assert result['candidate_id']=='26102101' and result['simulations_passed'] and result['bitstream_generated']
    assert result['normal_demo_eligible'] is False and result['flash_write_allowed'] is False
    require_clean_timing(result['timing'])
    assert sha(ROOT/'build/artifacts/nexys/bit/pattern.bit')==result['base_bit_sha256']
    source=result['source_patch']
    assert sha(ROOT/source['path'])==source['base_sha256'], 'Default RTL changed'
    assert sha(HERE/'aux_channel.vhd')==source['candidate_sha256']
    assert (HERE/'artifacts/pattern.bit').stat().st_size>1_000_000
    return dict(passed=True,candidate_id='26102101',default_images_unchanged=True,
                candidate_bit_sha256=sha(HERE/'artifacts/pattern.bit'),timing=result['timing'])


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('mode',choices=('seal','check'))
    args=p.parse_args()
    if args.mode=='seal':seal()
    print(json.dumps(check()))
