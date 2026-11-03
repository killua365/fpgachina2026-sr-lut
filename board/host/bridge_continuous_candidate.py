"""Gate the reviewed 26100901 continuous-clock SRAM image; never Flash/release."""
import argparse
import re
import json
from pathlib import Path
import bridge_reacquire_candidate as old

ROOT=old.ROOT
PROFILE=ROOT/'build/manifest/n4_continuous_candidate_26100901.json'
CANDIDATE=ROOT/'build/frozen/nexys_2026-10-11_bridge_continuous_sram_2026_r1'
FILES=old.FILES
TOOLS=('board/host/bridge_continuous_candidate.py','board/host/generate_bridge_continuous_bit.tcl',
       'board/host/bridge_reacquire_candidate.py','board/host/bridge_candidate.py',
       'build/scripts/nexys/archive_audit.py','build/scripts/nexys/frame_implementation.py',
       'build/manifest/n4_continuous_candidate_26100901.json')
sha,read,require,exact,inventory,entry,git=(old.sha,old.read,old.require,old.exact,old.inventory,old.entry,old.git)


def profile():
    p=read(PROFILE)
    require(p['candidate_id']=='26100901','Wrong continuous-clock candidate')
    for key,n in (('source_commit',40),('audit_sha256',64),('dcp_sha256',64)):
        require(re.fullmatch('[0-9a-f]{'+str(n)+'}',p[key]),'Unsealed implementation identity')
    audit=(ROOT/p['audit']).resolve();audit.relative_to(ROOT/'build/frozen')
    return p,audit


def preflight():
    p,audit=profile()
    require(sha(audit/'manifest.json')==p['audit_sha256'],'Changed implementation manifest')
    inventory(audit);r=read(audit/'result.json')
    require(r['source_commit']==p['source_commit'],'Wrong implementation source')
    require(r['offline_tests_passed'] and r['board_implemented'] and not r['hardware_touched'],'Incomplete qualification')
    require(r['critical_cdc_reviewed'] and r['methodology_reviewed'] and r['electrical_pins_reviewed'] and
            r['clock_continuity_reviewed'],'Missing physical/clock review')
    require(read(audit/'build_identity.json')['candidate_id']=='26100901','Wrong build identity')
    require(sha(audit/'route/routed.dcp')==p['dcp_sha256'],'Changed checkpoint')
    timing=old.shared.timing_summary((audit/'route/timing_route.rpt').read_text())
    require(timing==r['timing'] and old.shared.timing_passed(timing) and timing['setup_wns_ns']>=.1,'Timing failed')
    exact(ROOT,r['inputs'])
    for item in r['inputs']:
        require(old.shared.hashlib.sha256(git(['show',p['source_commit']+':'+item['path']])).hexdigest()==item['sha256'],
                'Implementation source not committed')
    for name in ('factory_read_1.bin','factory_read_2.bin'):
        path=old.shared.BACKUP/name
        require(path.stat().st_size==33554432 and sha(path)==old.shared.BACKUP_SHA,'Factory backup changed')
    return dict(passed=True,source_commit=p['source_commit'],dcp_sha256=p['dcp_sha256'],
                user_access='26100901',flash_write_allowed=False,n4_passed=False)


def bit_identity(folder):
    p,audit=profile()
    values=dict(line.split('=',1) for line in (folder/'bitgen_identity.txt').read_text().splitlines() if line)
    require(values==dict(vivado='2026.1',part='xc7a200tsbg484-1',user_access='0x26100901',
        source_dcp=(audit/'route/routed.dcp').relative_to(ROOT).as_posix(),source_sha256=p['dcp_sha256'],
        flash_write_allowed='false'),'Wrong BIT identity')
    log=(folder/'bitgen.log').read_text(errors='replace')
    require('ERROR:' not in log and 'CRITICAL WARNING:' not in log and '# close_design' in log and
        'INFO: [Common 17-206] Exiting Vivado' in log,'Incomplete BIT generation')
    require(old.shared.drc_summary((folder/'bitgen_drc.rpt').read_text())==read(audit/'result.json')['drc'],'BIT DRC changed')
    require((folder/'pattern.bit').stat().st_size>1_000_000,'Incomplete BIT')


def check(folder):
    r=read(folder/'candidate.json')
    require(r['contract']==preflight(),'Candidate contract differs')
    require({i['path'] for i in r['files']}==set(FILES),'Wrong image inventory')
    require({i['path'] for i in r['tools']}==set(TOOLS),'Wrong tool inventory')
    exact(folder,r['files']);exact(ROOT,r['tools']);bit_identity(folder)
    return r['contract']


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('mode',choices=('preflight','seal','check'))
    p.add_argument('folder',type=Path,nargs='?');a=p.parse_args()
    if a.mode=='preflight':r=preflight()
    elif a.mode=='check':r=check(a.folder.resolve())
    else:
        folder=a.folder.resolve();folder.relative_to(ROOT/'build/frozen');contract=preflight();bit_identity(folder)
        with (folder/'candidate.json').open('x') as stream:
            json.dump(dict(contract=contract,files=[entry(folder/n,n) for n in FILES],
                           tools=[entry(ROOT/n,n) for n in TOOLS]),stream,indent=2)
        r=check(folder)
    print(json.dumps(r))
