"""Prepare a unique N5 launch record locally; does not contact either board."""
import argparse
from datetime import datetime, timezone, timedelta
import json
import os
from pathlib import Path
import sys
import time
import uuid

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT/'board/host'))
from business_session_source import CONTRACT
from business_session_remote import validate_launch


def source_start_check(folder, launch, now=None):
    """Require this live Nexys session before presenting a source sudo prompt."""
    plan = json.loads((folder/'plan.json').read_text('utf-8'))
    result = json.loads((folder/'result.json').read_text('utf-8'))
    path = folder/'live_status.json'
    # The controller atomically replaces this file. Read the bytes and mtime
    # from the same open file, then sample the clock after the read.
    with path.open(encoding='utf-8') as stream:
        live = json.load(stream)
        live_mtime = os.fstat(stream.fileno()).st_mtime
    now = time.time() if now is None else now
    video = live.get('input') or {}
    expected = 'candidate=26101201\nrun_id='+launch['run_id']+'\n'
    if (plan.get('schema') != 'n5_business_session_v1' or plan.get('run_id') != launch['run_id'] or
            result.get('state') != 'running' or result.get('run_id') != launch['run_id'] or
            not video.get('available') or not video.get('link_ready') or video.get('error_flags') != 0 or
            not 0 <= now-live_mtime <= 10 or live['monitor']['failures'] or
            live['monitor']['state'] not in ('warming', 'starting', 'sampling') or
            (folder/'board/active.txt').read_text('ascii') != expected or
            (folder/'board/recovery_started_qpc.txt').exists() or (folder/'control/close.request').exists()):
        raise ValueError('This source requires its active, healthy Nexys-first session')
    return True


def prepare(seconds=None, end_mode=None):
    # Explicit --seconds retains the old timed automation entry point.
    end_mode = end_mode or ('demo' if seconds is None else 'timed')
    seconds = None if end_mode == 'demo' else ((1800 if end_mode == 'operator' else 180) if seconds is None else seconds)
    contract = json.loads(CONTRACT.read_text('utf-8'))
    now, run = datetime.now(timezone(timedelta(hours=8))), uuid.uuid4().hex
    value = dict(schema='n5_business_launch_v1', run_id=run, host_utc=now.astimezone(timezone.utc).isoformat(),
        **{key: contract[key] for key in ('package_sha256', 'package_manifest_sha256', 'source_commit', 'remote_package')},
        remote_results='/home/xilinx/n5_business_results_'+run, seconds=seconds, end_mode=end_mode, profile_seconds=0,
        camera_device='/dev/video0', supervisor_deadline_seconds=None if seconds is None else seconds+240,
        physical_hdmi_verified=False, n5_passed=False)
    validate_launch(value, contract)
    folder = ROOT/'board/acceptance'/now.strftime('%Y-%m-%d')/'n5_source_launches'
    folder.mkdir(parents=True, exist_ok=True)
    path = folder/('launch_'+run+'.json')
    with path.open('x', encoding='utf-8', newline='\n') as stream:
        json.dump(value, stream, indent=2)
    return path


if __name__ == '__main__':
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--seconds', type=int, help='Timed automation duration; omit for operator-ended interaction')
    p.add_argument('--end-mode', choices=('timed', 'operator', 'demo'))
    a = p.parse_args()
    print(prepare(a.seconds, a.end_mode))
