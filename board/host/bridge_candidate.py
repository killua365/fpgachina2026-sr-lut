"""Gate only the 26100601 SRAM HDMI-to-DDR-to-DP diagnostic; never authorize Flash or N4 release."""
import argparse,hashlib,json,subprocess,sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT/'build/scripts/nexys'))
from archive_audit import timing_summary,timing_passed
from frame_implementation import drc_summary
SOURCE='d93504237b645d75aee4c555fc7e523ff600a2ce'
AUDIT=ROOT/'build/frozen/nexys_2026-10-06_n4_bridge_board_r1'
AUDIT_SHA='545676eaa21a52b66b30dfd1a14bce03a5202eb70060d46f257beabf71ad636c'
DCP_SHA='940ccc1d5073160dd8ac66f2b058340f98f79511c62f4952d53cecbda74aa316'
HOST=ROOT/'report/validation/nexys_2026-10-06/bridge_host_r1'
CANDIDATE=ROOT/'build/frozen/nexys_2026-10-06_bridge_sram_r1'
BACKUP=Path('D:/FPGA/_backups/nexys_factory_2026-09-22_1859_r6')
BACKUP_SHA='84395e23201b09ce375ba7af2dfbd4c54931de5f77ae03cb6a2ae1c7b32433eb'
TOOLS=tuple('board/host/'+n for n in ('bridge_candidate.py','bridge_run_policy.py','ddr_device_status.py',
 'generate_bridge_bit.tcl','run_bridge_diagnostic.tcl','supervise_bridge_diagnostic.py',
 'longrun_hw.tcl','recover_hd1080_long.tcl','longrun_worker.ps1','longrun_process.py',
 'capture_longrun_uart_r3.ps1','longrun_inventory.ps1','longrun_lease.py',
 'longrun_heartbeat.py','longrun_heartbeat.ps1','decode_bridge_diagnostic.py','decode_frame_diagnostic.py','analyze_bridge_run.py'))+('build/scripts/nexys/archive_audit.py','build/scripts/nexys/frame_implementation.py')
FILES=('pattern.bit','bitgen.log','bitgen_drc.rpt','bitgen_identity.txt')
sha=lambda p:hashlib.sha256(p.read_bytes()).hexdigest()
read=lambda p:json.loads(p.read_text('utf-8-sig'))
def entry(p,n):return dict(path=n,size=p.stat().st_size,sha256=sha(p))
def require(value,message):
    if not value:raise ValueError(message)
def exact(base,items):
    names=[x['path'] for x in items];require(len(set(names))==len(names),'Duplicate manifest entry')
    for item in items:
        p=(base/item['path']).resolve();p.relative_to(base.resolve())
        require(p.stat().st_size==item['size'] and sha(p)==item['sha256'],'Changed file: '+item['path'])
def inventory(base):
    m=read(base/'manifest.json');exact(base,m['files'])
    require({p.relative_to(base).as_posix() for p in base.rglob('*') if p.is_file()}==
            {x['path'] for x in m['files']}|{'manifest.json'},'Changed inventory')
    return m
def preflight():
    require(all(len(s)==n for s,n in ((SOURCE,40),(AUDIT_SHA,64),(DCP_SHA,64))), 'Implementation has not been reviewed')
    require(sha(AUDIT/'manifest.json')==AUDIT_SHA,'Unreviewed implementation manifest')
    inventory(AUDIT);r=read(AUDIT/'result.json')
    require(r['offline_tests_passed'] and r['board_implemented'] and not r['hardware_touched'],'Incomplete implementation')
    require(sha(AUDIT/'route/routed.dcp')==DCP_SHA,'Changed DCP')
    timing=timing_summary((AUDIT/'route/timing_route.rpt').read_text())
    require(timing==r['timing'] and timing_passed(timing) and timing['setup_wns_ns']>=.1,'Timing failed')
    require(r['source_commit']==SOURCE,'Implementation source identity differs')
    require(r['critical_cdc_reviewed'] and r['methodology_reviewed'] and r['electrical_pins_reviewed'],
            'Implementation review incomplete')
    exact(ROOT,r['inputs'])
    require(all(hashlib.sha256(git(['show',SOURCE+':'+i['path']])).hexdigest()==i['sha256']
                for i in r['inputs']), 'Implementation differs from committed source')
    for n in ('factory_read_1.bin','factory_read_2.bin'):
        require((BACKUP/n).stat().st_size==33554432 and sha(BACKUP/n)==BACKUP_SHA,'Recovery backup changed')
    return dict(passed=True,source_commit=SOURCE,dcp_sha256=DCP_SHA,user_access='26100601',
                flash_write_allowed=False,n4_passed=False)
def bit_identity(folder):
    values=dict(line.split('=',1) for line in (folder/'bitgen_identity.txt').read_text().splitlines() if line)
    require(values==dict(vivado='2026.1',part='xc7a200tsbg484-1',user_access='0x26100601',
            source_dcp=(AUDIT/'route/routed.dcp').relative_to(ROOT).as_posix(),source_sha256=DCP_SHA,
            flash_write_allowed='false'),'BIT identity differs')
    log=(folder/'bitgen.log').read_text(errors='replace')
    require('ERROR:' not in log and 'CRITICAL WARNING:' not in log and '# close_design' in log
            and 'INFO: [Common 17-206] Exiting Vivado' in log,'BIT generation incomplete')
    require(drc_summary((folder/'bitgen_drc.rpt').read_text())==read(AUDIT/'result.json')['drc'],'BIT DRC differs from reviewed route')
    require((folder/'pattern.bit').stat().st_size>1_000_000,'Incomplete BIT')
def check(folder):
    r=read(folder/'candidate.json')
    require(r['contract']==preflight(),'Candidate contract differs')
    require({x['path'] for x in r['files']}==set(FILES),'Candidate file set differs')
    require({x['path'] for x in r['tools']}==set(TOOLS),'Candidate tool set differs')
    exact(folder,r['files']);exact(ROOT,r['tools']);bit_identity(folder)
    return r['contract']
def git(args):
    return subprocess.check_output(['git','-c','safe.directory='+ROOT.as_posix(),*args],cwd=ROOT)
def check_package(package):
    m=read(package/'manifest.json');check(CANDIDATE)
    require(m['candidate_sha256']==sha(CANDIDATE/'candidate.json'),'Changed candidate')
    require(m['host_sha256']==sha(HOST/'manifest.json'),'Changed host qualification')
    inventory(HOST);r=read(HOST/'result.json');require(r['passed'] is True,'Host tests failed')
    exact(ROOT,r['inputs'])
    require(set(TOOLS)<={x['path'] for x in r['inputs']},'Untested tool')
    require(set(r['modules'])=={'test_bridge_loader.py','test_bridge_supervisor.py','test_bridge_run_analysis.py',
        'test_bridge_candidate.py','test_bridge_diagnostic.py','test_longrun_process.py','test_longrun_lease.py','test_longrun_heartbeat.py'},'Missing fault tests')
    for item in r['inputs']:
        require(hashlib.sha256(git(['show',m['source_commit']+':'+item['path']])).hexdigest()==item['sha256'],'Tool differs from committed qualification')
    require(git(['show',m['source_commit']+':'+HOST.relative_to(ROOT).as_posix()+'/manifest.json'])==(HOST/'manifest.json').read_bytes(),'Host evidence not committed')
    return dict(passed=True,candidate='26100601',sample_seconds=120,configuration_hold_limit_seconds=240,n4_passed=False)
def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('mode',choices=('preflight','seal','check','seal-package','check-package'))
    p.add_argument('folder',type=Path,nargs='?');a=p.parse_args();folder=a.folder.resolve() if a.folder else None
    if a.mode=='preflight':r=preflight()
    elif a.mode=='check':r=check(folder)
    elif a.mode=='check-package':r=check_package(folder)
    elif a.mode=='seal':
        folder.relative_to(ROOT/'build/frozen');r=preflight();bit_identity(folder)
        with (folder/'candidate.json').open('x') as f:json.dump(dict(contract=r,files=[entry(folder/n,n) for n in FILES],tools=[entry(ROOT/n,n) for n in TOOLS]),f,indent=2)
        r=check(folder)
    else:
        folder.relative_to(ROOT/'build/frozen');folder.mkdir(parents=True,exist_ok=False)
        (folder/'manifest.json').write_text(json.dumps(dict(source_commit=git(['rev-parse','HEAD']).decode().strip(),candidate_sha256=sha(CANDIDATE/'candidate.json'),host_sha256=sha(HOST/'manifest.json')),indent=2)+'\n')
        r=check_package(folder)
    print(json.dumps(r))
if __name__=='__main__':main()
