"""Freeze passed N5 offline identity evidence; never authorize a board session."""
import argparse
import hashlib
import json
from pathlib import Path
import re
import shutil
import subprocess

from frame_implementation import drc_summary
from prepare_n5_bit import preflight
from review_n5_route import ROOT, require, sha

WORK = ROOT / 'build/nexys_bridge/work'
ROUTE = WORK / 'n5_board_20261012_r1'
CORE = WORK / 'n5_core_20261012_r2'
INSPECT = WORK / 'n5_inspect_20261012_r1'
BIT = WORK / 'n5_bit_20261012_r1'
REVIEW = ROOT / 'report/current/n5_identity_implementation_review_r2.json'
COMPONENTS = {'identity_pipeline_uart': WORK/'n5_identity_20261012_211846',
              'atomic_telemetry': WORK/'n5_identity_20261012_212714',
              'host': WORK/'n5_business_host_20261012_r4',
              'marker': ROOT/'sim/formal/n5_20261012/marker_receiver'}


def read(path):
    return json.loads(path.read_text('utf-8-sig'))


def copy(source, destination):
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(source, destination)


def config(path):
    return {key: values[0]['value'] for key, values in read(path)['ip_inst']['parameters']['component_parameters'].items()}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('out', type=Path)
    args = parser.parse_args()
    out = args.out.resolve()
    out.relative_to(ROOT/'build/frozen')
    require(not out.exists(), 'Refuse existing evidence')
    contract = preflight(REVIEW)
    review = read(REVIEW)
    sim = CORE/'project/vdmatest.sim/sim_1/behav/xsim'
    logs = [CORE.with_name(CORE.name+'_vivado.log')] + [sim/name for name in (
        'compile.log', 'xvlog.log', 'xvhdl.log', 'elaborate.log', 'simulate.log')]
    for path in logs:
        content = path.read_text(errors='replace')
        require(not re.search(r'\b(?:error|fatal)\s*:|Simulator command interrupted|engine not responding', content, re.I), 'Failed simulation stage: '+str(path))
    content = logs[0].read_text(errors='replace')
    require('N5_IDENTITY_SIMULATION_RETURNED_CHECK_LOG' in content and 'INFO: [Common 17-206] Exiting Vivado' in content,
            'Full core simulation has not finished')
    require('PASS: sr_identity_core actual VDMA' in content, 'Missing full-core PASS')
    match = re.search(r'BUSINESS new=\s*(\d+) repeat=\s*(\d+) scans=\s*(\d+)', content)
    require(match and int(match[1]) >= 2 and int(match[2]) >= 1 and int(match[3]) >= 5 and
            int(match[1])+int(match[2]) == int(match[3]), 'Missing distinct-result versus raster qualification')
    for folder in COMPONENTS.values():
        result = read(folder/'result.json')
        require(result['passed'], 'Unpassed component: '+str(folder))
        for item in result.get('inputs', result.get('sources', [])):
            # Record the historical runner too, but only functional source and
            # testbench changes invalidate an existing component simulation.
            if item['path'].startswith('build/'):
                continue
            require(sha(ROOT/item['path']) == item['sha256'], 'Stale component source: '+item['path'])
    require(read(COMPONENTS['host']/'result.json')['host_tests'] == 17, 'Incomplete host suite')
    inputs = {item['path']: item for item in review['inputs']}
    for path in (CORE/'source').iterdir():
        if path.name == 'assertions_only.tcl':
            require(path.read_text().strip() == 'run all', 'Unexpected simulator commands')
            continue
        matches = [ROOT/relative/path.name for relative in ('src/rtl/nexys_hdmi', 'src/rtl/nexys_framebuffer', 'sim/nexys_bridge', 'build/nexys_bridge')]
        matches = [p for p in matches if p.is_file()]
        require(len(matches) == 1 and matches[0].read_bytes() == path.read_bytes(), 'Stale/ambiguous full core source: '+path.name)
        name = matches[0].relative_to(ROOT).as_posix()
        inputs[name] = dict(path=name, sha256=sha(path))
    for folder in COMPONENTS.values():
        for item in read(folder/'result.json').get('inputs', read(folder/'result.json').get('sources', [])):
            if not item['path'].startswith('build/'):
                inputs[item['path']] = item
    source = subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=ROOT, text=True).strip()
    for item in inputs.values():
        committed = subprocess.check_output(['git', 'show', source+':'+item['path']], cwd=ROOT)
        require(hashlib.sha256(committed).hexdigest() == item['sha256'], 'Uncommitted qualified bytes: '+item['path'])
    vdma = ROUTE/'project/n5_bridge.srcs/sources_1/ip/n3_vdma/n3_vdma.xci'
    vdma_sim = CORE/'project/vdmatest.srcs/sources_1/ip/n3_vdma/n3_vdma.xci'
    mig = ROUTE/'project/n5_bridge.srcs/sources_1/ip/n3_mig/n3_mig.xci'
    accepted = ROOT/'build/frozen/nexys_2026-10-05_n3_frame_board_r1/dependencies'
    require(config(vdma) == config(vdma_sim) == config(accepted/'n3_vdma.xci'), 'VDMA configuration changed')
    mig_config, prior_mig = config(mig), config(accepted/'n3_mig.xci')
    mig_path, prior_mig_path = mig_config.pop('XML_INPUT_FILE'), prior_mig.pop('XML_INPUT_FILE')
    require(mig_config == prior_mig, 'MIG configuration changed')
    mig_xml = (mig.parent/mig_path).resolve()
    require(mig_xml.read_bytes() == (accepted/'mig.prj').read_bytes() ==
            (ROOT/'src/rtl/nexys_framebuffer/mig_128_shared_clock.prj').read_bytes(), 'MIG electrical configuration changed')
    bit_log = BIT.with_suffix('.log').read_text(errors='replace')
    require('N5_OFFLINE_BIT_GENERATED_NO_HARDWARE' in bit_log and 'INFO: [Common 17-206] Exiting Vivado' in bit_log and
            not re.search(r'ERROR:|CRITICAL WARNING:', bit_log), 'Incomplete BIT generation')
    identity = dict(line.split('=', 1) for line in (BIT/'bitgen_identity.txt').read_text().splitlines() if line)
    require(identity == dict(vivado='2026.1', part='xc7a200tsbg484-1', user_access='0x26101201',
        source_dcp=(ROUTE/'routed.dcp').as_posix(), source_sha256=contract['dcp_sha256'],
        review_sha256=contract['review_sha256'], flash_write_allowed='false', hardware_loading_allowed='false'), 'BIT identity differs')
    require(drc_summary((BIT/'bitgen_drc.rpt').read_text()) == review['drc'], 'BIT DRC differs')
    require((BIT/'pattern.bit').stat().st_size > 1_000_000, 'Incomplete BIT')
    out.mkdir()
    for path in ROUTE.iterdir():
        if path.is_file() and (path.suffix in ('.rpt', '.txt') or path.name in ('routed.dcp', 'build_identity.json')):
            copy(path, out/'route'/path.name)
    shutil.copytree(ROUTE/'source', out/'route/source')
    copy(ROUTE.with_name(ROUTE.name+'_vivado.log'), out/'route/vivado.log')
    shutil.copytree(CORE/'source', out/'core/source')
    for path in logs:
        copy(path, out/'core/logs'/path.name)
    shutil.copytree(INSPECT, out/'inspection')
    copy(INSPECT.with_suffix('.log'), out/'inspection/vivado.log')
    for name, folder in COMPONENTS.items():
        for path in folder.rglob('*'):
            if path.is_file() and (path.parent == folder or 'source' in path.relative_to(folder).parts) and path.suffix in ('.json', '.txt', '.log', '.sv', '.py', '.hex', '.bin'):
                copy(path, out/'components'/name/path.relative_to(folder))
    for path in BIT.iterdir():
        if path.is_file():
            copy(path, out/'bit'/path.name)
    copy(BIT.with_suffix('.log'), out/'bit/bitgen.log')
    for name, path in (('n3_vdma.xci', vdma), ('n3_mig.xci', mig), ('mig.prj', mig_xml)):
        copy(path, out/'dependencies'/name)
    copy(REVIEW, out/'implementation_review.json')
    copy(Path(__file__), out/'freeze_n5_identity.py')
    # Keep the cancelled long-waveform run separate from successful evidence.
    aborted = WORK/'n5_core_20261012_r1'
    copy(aborted.with_name(aborted.name+'_vivado.log'), out/'history/core_r1_cancelled/vivado.log')
    for name in ('tb_sr_identity_core.sv', 'simulate_n5_core.tcl'):
        copy(aborted/'source'/name, out/'history/core_r1_cancelled'/name)
    result = dict(candidate_id='26101201', source_commit=source,
        physical_review_source_commit=review['source_commit'], offline_tests_passed=True,
        complete_vdma_identity_simulation=True,
        simulated_new_results=int(match[1]), simulated_repeat_results=int(match[2]), simulated_scans=int(match[3]),
        component_benches=6, host_tests=17, actual_rtl_uart_decoded=True,
        board_implemented=True, bitstream_generated=True, bit_sha256=sha(BIT/'pattern.bit'),
        dcp_sha256=contract['dcp_sha256'], timing=review['timing'], drc=review['drc'],
        mig_reference_path_relocated=(mig_path != prior_mig_path), mig_project_bytes_unchanged=True,
        critical_cdc_reviewed=True, methodology_reviewed=True, electrical_pins_reviewed=True,
        inputs=sorted(inputs.values(), key=lambda item:item['path']),
        hardware_touched=False, hardware_loading_eligible=False, n5_passed=False, n6_passed=False, formal_release=False,
        remaining=['Bounded N5 business session loader and ordered cleanup integration',
                   'On-board raw SR golden comparisons and image/video/camera/controls acceptance'],
        limitations=['Digital full-core test uses actual VDMA and behavioral DDR; it is not monitor pixel capture.',
                     'Latency covers selected HDMI acceptance to complete DP scan in one Nexys clock domain.',
                     'r1 full-core simulation was cancelled before completion; only r2 grants core qualification.',
                     'Display-end DP reconnection tests remain excluded by the user.'])
    (out/'result.json').write_text(json.dumps(result, indent=2)+'\n', encoding='utf-8')
    files = [dict(path=p.relative_to(out).as_posix(), size=p.stat().st_size, sha256=sha(p)) for p in sorted(out.rglob('*')) if p.is_file()]
    (out/'manifest.json').write_text(json.dumps(dict(files=files), indent=2)+'\n', encoding='utf-8')
    print(json.dumps(dict(archive=str(out), files=len(files), manifest_sha256=sha(out/'manifest.json'), bit_sha256=result['bit_sha256'])))


if __name__ == '__main__':
    main()
