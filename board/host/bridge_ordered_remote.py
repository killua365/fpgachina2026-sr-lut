"""Host-supplied, ephemeral source probe. No PYNQ import, signals or service changes.

Only the stop action writes: it creates the exact bound worker's existing
cancel.request. Executed through sudo -n for stop; no password is collected.
The signed source supervisor remains the sole owner of DMA shutdown.
"""
import base64
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import time
import urllib.request

ZIP_SHA='0884704ed0e851c4eea3d0d65578c73835ab2e2de3937f7c978be4dbc8c03ffb'
MANIFEST_SHA='2942ccd63904911642b5b15490f85d5339e1a725c4a6a587a8348266ef59e8d9'
SOURCE='2a0e215131167ef4efed709c967fdc5725a0cd8b'
CID='035344534333324780dba606de014500'
PACKAGE='/home/xilinx/n4_fixed_source_20261008_r1'
BOARD_PYTHON='/home/xilinx/sr_lut_hdmi_envs/hdmi_m1_20260909_r11/bin/python3'
FORMAL_SHA='6dbcf99bb2ba515c8821213c19b8d39e33a3f1a19bcd94b6d28b46f502e07909'


def require(ok, message):
    if not ok: raise ValueError(message)


def validate_launch(a):
    require(a.get('schema')=='n4_ordered_source_launch_v1', 'Wrong source launch schema')
    require(re.fullmatch('[0-9a-f]{32}', a.get('run_id','')), 'Invalid source run ID')
    require(re.fullmatch(r'/home/xilinx/n4_fixed_results_\d{8}_\d{6}_[0-9a-f]{8}', a.get('remote_results','')), 'Invalid result path')
    require((a.get('package_sha256'),a.get('package_manifest_sha256'),a.get('source_commit'),a.get('remote_package')) ==
            (ZIP_SHA,MANIFEST_SHA,SOURCE,PACKAGE), 'Source package identity mismatch')
    require(type(a.get('scale')) is int and a['scale'] in (2,4), 'Wrong scale')
    require(type(a.get('pattern_seconds_total')) is int and 120<=a['pattern_seconds_total']<=1200, 'Wrong duration')
    require(a.get('automatic_scale_switch') is False and a.get('hold_display') is False, 'Unsafe source lifetime')
    return a


def verify_worker(worker, launch, boot_id):
    require((worker.get('kind'),worker.get('package_sha256'),worker.get('source_commit'),worker.get('card_cid')) ==
            ('hdmi_m1_supervised_run',MANIFEST_SHA,SOURCE,CID), 'Wrong worker identity')
    require(worker.get('boot_id')==boot_id and worker.get('requested_scales')==[launch['scale']] and
            worker.get('seconds_per_pattern')==launch['pattern_seconds_total']/4, 'Worker launch/boot mismatch')
    require(type(worker.get('started_monotonic')) in (int,float), 'Missing worker start')


def verify_final(value, launch, binding):
    worker=value['worker']; report=value['supervisor']
    verify_worker(worker,launch,value['boot_id'])
    require(value['binding']==binding, 'Different worker instance')
    require((report.get('kind'),report.get('source_commit'),report.get('package_sha256')) ==
            ('hdmi_m1_independent_supervisor',SOURCE,MANIFEST_SHA), 'Wrong supervisor identity')
    contract=report.get('fixed_source',{})
    require(contract.get('scale')==launch['scale'] and contract.get('pattern_seconds_total')==launch['pattern_seconds_total'] and
            contract.get('overlay_loads')==1 and contract.get('automatic_scale_switch') is False and
            contract.get('hold_display') is False, 'Wrong final source contract')
    require(report.get('worker_reaped') is True and type(report.get('worker_exit_code')) is int and
            report.get('legacy_restored') is True and worker.get('legacy_restored') is True and
            worker.get('phase')=='finished', 'Source worker/recovery incomplete')
    require(not worker.get('retained_dma_error'), 'DMA halt failure retained')
    require(worker.get('overlays',{}).get(str(launch['scale']),{}).get('display_stopped') is True,
            'No explicit display/DMA stop evidence')
    status=value['legacy_status']; identity=status.get('hardware_identity',{})
    require(value['portal']=='active' and value['candidate_service'] in ('inactive','failed') and
            status.get('candidate') is False and identity.get('identity_match') is True and
            identity.get('manifest_match') is True and identity.get('release_archive_sha256','').lower()==FORMAL_SHA,
            'Original service live identity not restored')
    return True


def read(path):
    require(not path.is_symlink(), 'Evidence symlink rejected')
    for attempt in range(5):
        try: return json.loads(path.read_text('utf-8'))
        except json.JSONDecodeError:
            if attempt==4: raise
            time.sleep(.05)


def probe(request):
    launch=validate_launch(request['launch']); action=request['action']
    require(action in ('inspect','stop'), 'Invalid action')
    require(re.fullmatch('[0-9a-f]{32}', request['challenge']), 'Invalid challenge')
    root=Path(launch['remote_results'])
    require(not root.is_symlink(), 'Result symlink rejected')
    package=Path(PACKAGE)
    require(package.resolve()==package and hashlib.sha256((package/'hdmi_m1_package.json').read_bytes()).hexdigest()==MANIFEST_SHA,
            'Remote package changed')
    boot=Path('/proc/sys/kernel/random/boot_id').read_text().strip()
    result=dict(challenge=request['challenge'],run_id=launch['run_id'],remote_results=str(root),
                boot_id=boot,action=action,remote_monotonic=time.monotonic(),state='absent')
    if not root.exists():
        require(action=='inspect', 'Cannot cancel an absent worker')
        return result
    require(root.resolve()==root and (root/'worker').resolve()==root/'worker', 'Result path redirected')
    final=(root/'report.json').exists()
    if not final and (not (root/'worker/status.json').exists() or not (root/'supervisor_status.json').exists()):
        require(action=='inspect' and request.get('binding') is None,'Bound worker evidence disappeared')
        result['state']='starting'; return result
    worker=read(root/'worker'/('report.json' if final else 'status.json'))
    supervisor=read(root/('report.json' if final else 'supervisor_status.json'))
    verify_worker(worker,launch,boot)
    pid=supervisor.get('worker_pid')
    require(type(pid) is int and pid>1, 'Missing worker PID')
    binding=dict(worker_pid=pid,boot_id=boot,started_monotonic=worker['started_monotonic'])
    require(request.get('binding') in (None,binding), 'Worker instance changed')
    result.update(binding=binding,worker=worker,supervisor=supervisor)
    if final:
        def service(name):
            return subprocess.run(['systemctl','is-active',name],capture_output=True,text=True,timeout=3).stdout.strip()
        result.update(state='finished',portal=service('sr-lut-portal.service'),candidate_service=service('sr-lut-candidate.service'))
        with urllib.request.urlopen('http://127.0.0.1:8080/api/status',timeout=3) as response:
            result['legacy_status']=json.load(response)['status']
        verify_final(result,launch,binding)
        return result
    args=Path(f'/proc/{pid}/cmdline').read_bytes().rstrip(b'\0').decode().split('\0')
    expected=[BOARD_PYTHON,'-B','-X','faulthandler',PACKAGE+'/board/host/n4_fixed_source.py','_worker',
              '--root',PACKAGE,'--destination',str(root/'worker'),'--scale',str(launch['scale']),
              '--duration',str(float(launch['pattern_seconds_total']))]
    # The signed worker plan serializes its duration as a float.
    require(args[:len(expected)]==expected and len(args)==len(expected)+2 and args[-2]=='--lock-fd' and args[-1].isdigit(),
            'Live PID is not the launched source worker')
    require(supervisor.get('worker_reaped') is False, 'Inconsistent live worker')
    result.update(state='running',remaining_seconds=launch['pattern_seconds_total']-(time.monotonic()-worker['started_monotonic']))
    if action=='stop':
        require(request.get('binding')==binding, 'Cancellation requires prior live binding')
        require(os.geteuid()==0, 'Cancellation needs the visible launcher sudo context or user Ctrl+C')
        cancel=root/'cancel.request'; content='ordered_exit_v1 '+launch['run_id']+'\n'
        if cancel.exists():
            require(not cancel.is_symlink() and cancel.read_text('ascii')==content, 'Foreign cancellation file')
        else:
            fd=os.open(str(cancel),os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o644)
            with os.fdopen(fd,'w') as stream: stream.write(content)
        result['cancellation_requested']=True
    return result


if __name__=='__main__':
    request=json.loads(base64.b64decode(sys.argv[1]).decode('utf-8'))
    print(json.dumps(probe(request)))
