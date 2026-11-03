"""Gate the independently identified 26100801 SRAM experiment, never Flash or N4 release."""
import argparse
import json
import re
from pathlib import Path
import bridge_candidate as shared

ROOT = shared.ROOT
PROFILE = ROOT / 'build/manifest/n4_reacquire_candidate_26100801.json'
HOST = ROOT / 'report/validation/nexys_2026-10-08/bridge_reacquire_host_r1'
CANDIDATE = ROOT / 'build/frozen/nexys_2026-10-08_bridge_reacquire_sram_r1'
FILES = shared.FILES
MODULES = {'test_bridge_reacquire_loader.py', 'test_bridge_reacquire_supervisor.py',
           'test_bridge_reacquire_analysis.py', 'test_bridge_reacquire_candidate.py',
           'test_bridge_matrix.py', 'test_longrun_process.py', 'test_longrun_lease.py',
           'test_longrun_heartbeat.py', 'test_bridge_diagnostic.py'}
TOOLS = tuple('board/host/' + n for n in (
    'bridge_reacquire_candidate.py', 'generate_bridge_reacquire_bit.tcl',
    'supervise_bridge_reacquire.py', 'run_bridge_reacquire.tcl',
    'analyze_bridge_reacquire.py', 'bridge_reacquire_live.py',
    'bridge_candidate.py', 'bridge_matrix_policy.py', 'capture_bridge_matrix_uart.ps1',
    'record_bridge_matrix_event.py', 'longrun_hw.tcl', 'recover_hd1080_long.tcl',
    'longrun_worker.ps1', 'longrun_process.py', 'longrun_inventory.ps1',
    'longrun_lease.py', 'longrun_heartbeat.py', 'longrun_heartbeat.ps1',
    'decode_bridge_diagnostic.py', 'decode_frame_diagnostic.py', 'ddr_device_status.py')) + (
    'build/scripts/nexys/archive_audit.py', 'build/scripts/nexys/frame_implementation.py',
    'build/manifest/n4_reacquire_candidate_26100801.json')
sha, read, require, exact, inventory, entry, git = (
    shared.sha, shared.read, shared.require, shared.exact, shared.inventory, shared.entry, shared.git)


def profile():
    p = read(PROFILE)
    require(p['candidate_id'] == '26100801', 'Wrong reacquisition candidate')
    for key, length in [('source_commit', 40), ('audit_sha256', 64), ('dcp_sha256', 64)]:
        require(re.fullmatch('[0-9a-f]{' + str(length) + '}', p[key]), 'Implementation review is not sealed')
    audit = (ROOT / p['audit']).resolve(); audit.relative_to(ROOT / 'build/frozen')
    return p, audit


def preflight():
    p, audit = profile()
    require(sha(audit / 'manifest.json') == p['audit_sha256'], 'Changed implementation manifest')
    inventory(audit); r = read(audit / 'result.json')
    require(r['source_commit'] == p['source_commit'], 'Wrong implementation source')
    require(r['offline_tests_passed'] and r['board_implemented'] and not r['hardware_touched'], 'Incomplete qualification')
    require(r['critical_cdc_reviewed'] and r['methodology_reviewed'] and r['electrical_pins_reviewed'], 'Missing implementation review')
    require(read(audit / 'build_identity.json')['candidate_id'] == p['candidate_id'], 'Wrong synthesized identity')
    require(sha(audit / 'route/routed.dcp') == p['dcp_sha256'], 'Changed checkpoint')
    timing = shared.timing_summary((audit / 'route/timing_route.rpt').read_text())
    require(timing == r['timing'] and shared.timing_passed(timing) and timing['setup_wns_ns'] >= .1, 'Timing failed')
    exact(ROOT, r['inputs'])
    for i in r['inputs']:
        require(shared.hashlib.sha256(git(['show', p['source_commit'] + ':' + i['path']])).hexdigest() == i['sha256'],
                'Implementation source differs from commit')
    for name in ('factory_read_1.bin', 'factory_read_2.bin'):
        path = shared.BACKUP / name
        require(path.stat().st_size == 33554432 and sha(path) == shared.BACKUP_SHA, 'Factory backup changed')
    return dict(passed=True, source_commit=p['source_commit'], dcp_sha256=p['dcp_sha256'],
                user_access=p['candidate_id'], flash_write_allowed=False, n4_passed=False)


def bit_identity(folder):
    p, audit = profile()
    values = dict(line.split('=', 1) for line in (folder / 'bitgen_identity.txt').read_text().splitlines() if line)
    expected = dict(vivado='2026.1', part='xc7a200tsbg484-1', user_access='0x26100801',
        source_dcp=(audit / 'route/routed.dcp').relative_to(ROOT).as_posix(),
        source_sha256=p['dcp_sha256'], flash_write_allowed='false')
    require(values == expected, 'BIT source/USR_ACCESS identity differs')
    log = (folder / 'bitgen.log').read_text(errors='replace')
    require('ERROR:' not in log and 'CRITICAL WARNING:' not in log and '# close_design' in log
            and 'INFO: [Common 17-206] Exiting Vivado' in log, 'BIT generation incomplete')
    require(shared.drc_summary((folder / 'bitgen_drc.rpt').read_text()) == read(audit / 'result.json')['drc'], 'BIT DRC changed')
    require((folder / 'pattern.bit').stat().st_size > 1_000_000, 'Incomplete BIT')


def check(folder):
    r = read(folder / 'candidate.json')
    require(r['contract'] == preflight(), 'Candidate contract differs')
    require({i['path'] for i in r['files']} == set(FILES), 'Wrong candidate files')
    require({i['path'] for i in r['tools']} == set(TOOLS), 'Wrong candidate tools')
    exact(folder, r['files']); exact(ROOT, r['tools']); bit_identity(folder)
    return r['contract']


def check_package(package):
    m = read(package / 'manifest.json'); check(CANDIDATE)
    require(m['candidate_sha256'] == sha(CANDIDATE / 'candidate.json'), 'Changed candidate')
    require(m['host_sha256'] == sha(HOST / 'manifest.json'), 'Changed host evidence')
    inventory(HOST); r = read(HOST / 'result.json')
    require(r['passed'] is True and r['hardware_access'] is False, 'Host qualification failed')
    require(set(r['modules']) == MODULES and all(code == 0 for code in r['modules'].values()), 'Missing fault tests')
    require(set(TOOLS) <= {i['path'] for i in r['inputs']}, 'Untested tool')
    exact(ROOT, r['inputs'])
    for i in r['inputs']:
        require(shared.hashlib.sha256(git(['show', m['source_commit'] + ':' + i['path']])).hexdigest() == i['sha256'], 'Tool not committed')
    require(git(['show', m['source_commit'] + ':' + HOST.relative_to(ROOT).as_posix() + '/manifest.json']) ==
            (HOST / 'manifest.json').read_bytes(), 'Host evidence not committed')
    require(m['contract'] == dict(candidate='26100801', warmup_seconds=30, window_limit_seconds=480,
                                hold_limit_seconds=600, n4_passed=False), 'Unqualified experiment limits')
    return dict(passed=True, **m['contract'])


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('mode', choices=('preflight', 'seal', 'check', 'seal-package', 'check-package'))
    parser.add_argument('folder', type=Path, nargs='?'); a = parser.parse_args()
    folder = a.folder.resolve() if a.folder else None
    if a.mode == 'preflight': result = preflight()
    elif a.mode == 'check': result = check(folder)
    elif a.mode == 'check-package': result = check_package(folder)
    elif a.mode == 'seal':
        folder.relative_to(ROOT / 'build/frozen'); contract = preflight(); bit_identity(folder)
        with (folder / 'candidate.json').open('x') as f:
            json.dump(dict(contract=contract, files=[entry(folder / n, n) for n in FILES],
                           tools=[entry(ROOT / n, n) for n in TOOLS]), f, indent=2)
        result = check(folder)
    else:
        folder.relative_to(ROOT / 'build/frozen'); folder.mkdir(parents=True, exist_ok=False)
        (folder / 'manifest.json').write_text(json.dumps(dict(source_commit=git(['rev-parse', 'HEAD']).decode().strip(),
            candidate_sha256=sha(CANDIDATE / 'candidate.json'), host_sha256=sha(HOST / 'manifest.json'),
            contract=dict(candidate='26100801', warmup_seconds=30, window_limit_seconds=480,
                          hold_limit_seconds=600, n4_passed=False)), indent=2) + '\n')
        result = check_package(folder)
    print(json.dumps(result))


if __name__ == '__main__': main()
