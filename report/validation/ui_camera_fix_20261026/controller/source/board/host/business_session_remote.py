"""Run-bound, read-only PYNQ probe; cancellation writes only its owned request."""
import base64
import hashlib
import json
import math
import os
from pathlib import Path
import re
import subprocess
import sys
import time
import urllib.request

CID = '035344534333324780dba606de014500'
BOARD_PYTHON = '/home/xilinx/sr_lut_hdmi_envs/hdmi_m1_20260909_r11/bin/python3'
FORMAL_SHA = '6dbcf99bb2ba515c8821213c19b8d39e33a3f1a19bcd94b6d28b46f502e07909'


def stage(name):
    print('SRLUT_PROBE '+json.dumps(dict(stage=name, monotonic=time.monotonic())),
          file=sys.stderr, flush=True)


def require(ok, message):
    if not ok:
        raise ValueError(message)


def validate_launch(a, contract):
    require(a.get('schema') == 'n5_business_launch_v1', 'Wrong launch schema')
    require(re.fullmatch('[0-9a-f]{32}', a.get('run_id', '')), 'Invalid run ID')
    require(a.get('remote_results') == '/home/xilinx/n5_business_results_' + a['run_id'], 'Foreign result path')
    for key in ('package_sha256', 'package_manifest_sha256', 'source_commit', 'remote_package'):
        require(a.get(key) == contract[key], 'Wrong source contract: ' + key)
    require(re.fullmatch(r'/home/xilinx/(?:n5_business_\d{8}_r\d+|sr_lut_demo_r\d+_repack)', a['remote_package']), 'Invalid package path')
    mode = a.get('end_mode', 'timed')
    require(mode in ('timed', 'operator', 'demo'), 'Wrong business end mode')
    require((a.get('seconds') is None if mode == 'demo' else type(a.get('seconds')) is int and
            (60 <= a['seconds'] <= 300 if mode == 'timed' else a['seconds'] == 1800)), 'Wrong business duration')
    require(a.get('profile_seconds') == 0 and a.get('camera_device') == '/dev/video0', 'Wrong source mode')
    require(a.get('supervisor_deadline_seconds') == (None if mode == 'demo' else a['seconds']+240), 'Wrong source deadline')
    return a


def verify_worker(worker, launch, boot):
    require((worker.get('kind'), worker.get('manifest_sha256'), worker.get('source_commit')) ==
            ('hdmi_business_worker', launch['package_manifest_sha256'], launch['source_commit']), 'Wrong worker')
    require(worker.get('run_id') == launch['run_id'] and worker.get('boot_id') == boot and
            worker.get('card_cid') == CID, 'Worker session/card mismatch')
    require(worker.get('requested_seconds') == launch['seconds'] and worker.get('profile_seconds') == 0 and
            worker.get('camera_device') == launch['camera_device'], 'Worker command differs')
    require(worker.get('end_mode', 'timed') == launch.get('end_mode', 'timed'), 'Worker end mode differs')
    require(type(worker.get('started_monotonic')) in (int, float) and
            math.isfinite(worker['started_monotonic']) and worker['started_monotonic'] > 0, 'Invalid worker start')
    require(worker.get('business_profile', {}).get('name') == 'nexys_dp_bridge_v1', 'Wrong business profile')


def verify_final(value, launch, binding):
    worker, report = value['worker'], value['supervisor']
    verify_worker(worker, launch, value['boot_id'])
    require(value['binding'] == binding, 'Different worker instance')
    require((report.get('kind'), report.get('manifest_sha256'), report.get('source_commit'), report.get('run_id')) ==
            ('hdmi_business_supervisor', launch['package_manifest_sha256'], launch['source_commit'], launch['run_id']),
            'Wrong supervisor identity')
    require(report.get('requested_seconds') == launch['seconds'] and report.get('profile_seconds') == 0,
            'Wrong final source plan')
    require(report.get('end_mode', 'timed') == launch.get('end_mode', 'timed'), 'Supervisor end mode differs')
    require(report.get('worker_reaped') is True and type(report.get('worker_exit_code')) is int and
            report.get('worker_pid') == binding['worker_pid'] and report.get('legacy_restored') is True and
            report.get('hardware_closed') is True, 'Worker/recovery incomplete')
    require(worker.get('closed') is True and worker.get('phase') == 'finished' and
            worker.get('display_stopped') is True and worker.get('sr_dma_stopped') is True and
            not worker.get('retained_dma_error'), 'DMA/display closure unverified')
    identity = value['legacy_status'].get('hardware_identity', {})
    require(value['portal'] == 'active' and value['candidate_service'] in ('inactive', 'failed') and
            value['legacy_status'].get('candidate') is False and identity.get('identity_match') is True and
            identity.get('manifest_match') is True and identity.get('release_archive_sha256', '').lower() == FORMAL_SHA,
            'Original service live identity not restored')
    return True


def verify_finalizing(value, launch, binding):
    """Worker has closed hardware; parent recovery/reaping is still unproved."""
    require(binding is not None and value.get('binding') == binding, 'Unbound finalizing worker')
    worker = value['worker']
    verify_worker(worker, launch, value['boot_id'])
    require(worker.get('phase') == 'finished' and worker.get('closed') is True and
            worker.get('display_stopped') is True and worker.get('sr_dma_stopped') is True and
            not worker.get('retained_dma_error'), 'Finalizing worker did not close hardware')
    require(value['supervisor'].get('worker_pid') == binding['worker_pid'], 'Finalizing PID changed')
    return True


def read(path):
    require(not path.is_symlink(), 'Evidence symlink rejected')
    for attempt in range(5):
        try:
            return json.loads(path.read_text('utf-8'))
        except json.JSONDecodeError:
            if attempt == 4:
                raise
            time.sleep(.05)


def worker_command(launch, lock_fd):
    root = launch['remote_package']
    return [BOARD_PYTHON, '-B', '-X', 'faulthandler', root+'/board/pynq/hdmi_business_app.py', '_worker',
            '--root', root, '--destination', launch['remote_results']+'/worker',
            *(['--seconds',str(launch['seconds'])] if launch['seconds'] is not None else []),
            '--camera-device', launch['camera_device'], '--lock-fd', str(lock_fd), '--profile-seconds', '0',
            '--run-id', launch['run_id']] + (['--end-mode', launch['end_mode']] if launch.get('end_mode') in ('operator','demo') else [])


def probe(request):
    stage('python_started')
    launch = validate_launch(request['launch'], request['contract'])
    action = request['action']
    require(action in ('inspect', 'stop'), 'Invalid action')
    require(re.fullmatch('[0-9a-f]{32}', request['challenge']), 'Invalid challenge')
    package, root = Path(launch['remote_package']), Path(launch['remote_results'])
    require(package.resolve() == package and not root.is_symlink(), 'Redirected package or results')
    require(hashlib.sha256((package/'hdmi_business_package.json').read_bytes()).hexdigest() ==
            launch['package_manifest_sha256'], 'Remote package changed')
    stage('package_verified')
    boot = Path('/proc/sys/kernel/random/boot_id').read_text().strip()
    result = dict(challenge=request['challenge'], run_id=launch['run_id'], remote_results=str(root),
                  boot_id=boot, action=action, remote_monotonic=time.monotonic(), state='absent')
    if not root.exists():
        require(action == 'inspect' and request.get('binding') is None, 'Bound source disappeared')
        return result
    require(root.resolve() == root and (root/'worker').resolve() == root/'worker', 'Redirected worker')
    final = (root/'report.json').exists()
    if not final and (not (root/'worker/status.json').exists() or not (root/'supervisor_status.json').exists()):
        require(action == 'inspect' and request.get('binding') is None, 'Bound worker evidence disappeared')
        result['state'] = 'starting'
        return result
    worker = read(root/'worker'/('report.json' if final else 'status.json'))
    supervisor = read(root/('report.json' if final else 'supervisor_status.json'))
    stage('reports_read')
    verify_worker(worker, launch, boot)
    pid = supervisor.get('worker_pid')
    require(type(pid) is int and pid > 1, 'Missing worker PID')
    binding = dict(worker_pid=pid, boot_id=boot, started_monotonic=worker['started_monotonic'])
    require(request.get('binding') in (None, binding), 'Worker instance changed')
    result.update(binding=binding, worker=worker, supervisor=supervisor)
    if final:
        def service(name):
            return subprocess.run(['systemctl', 'is-active', name], capture_output=True, text=True, timeout=3).stdout.strip()
        result.update(state='finished', portal=service('sr-lut-portal.service'), candidate_service=service('sr-lut-candidate.service'))
        stage('services_checked')
        with urllib.request.urlopen('http://127.0.0.1:8080/api/status', timeout=3) as response:
            result['legacy_status'] = json.load(response)['status']
        verify_final(result, launch, binding)
        return result
    # The worker writes its report before exiting. Restoring the old service
    # can take much longer, so its PID can legitimately disappear before the
    # parent report exists. This state closes sampling but cannot authorize
    # QSPI restore; only the fully verified parent report can do that.
    if (root/'worker/report.json').exists():
        result.update(state='finalizing', worker=read(root/'worker/report.json'))
        verify_finalizing(result, launch, request.get('binding'))
        return result
    args = Path('/proc/{}/cmdline'.format(pid)).read_bytes().rstrip(b'\0').decode().split('\0')
    try:
        lock = args[args.index('--lock-fd')+1]
    except (ValueError, IndexError):
        raise ValueError('Worker lock missing')
    require(lock.isdigit() and args == worker_command(launch, lock), 'Live PID is not this session worker')
    require(supervisor.get('worker_reaped') is False, 'Inconsistent live worker')
    stage('live_pid_verified')
    deadline=launch['supervisor_deadline_seconds']
    result.update(state='running', remaining_seconds=None if deadline is None else deadline-(time.monotonic()-worker['started_monotonic']))
    if action == 'stop':
        require(request.get('binding') == binding and os.geteuid() == 0, 'Cancellation needs owned worker and sudo context')
        cancel = root/'cancel.request'
        content = 'ordered_exit_v1 '+launch['run_id']+'\n'
        if cancel.exists():
            require(not cancel.is_symlink() and cancel.read_text('ascii') == content, 'Foreign cancellation file')
        else:
            fd = os.open(str(cancel), os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW, 0o644)
            with os.fdopen(fd, 'w') as stream:
                stream.write(content)
        result['cancellation_requested'] = True
    return result


if __name__ == '__main__':
    response = probe(json.loads(base64.b64decode(sys.argv[1]).decode('utf-8')))
    stage('response_ready')
    print(json.dumps(response))
