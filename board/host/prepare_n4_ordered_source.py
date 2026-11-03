"""Prepare a unique local launch record; this command never contacts a board."""
import argparse
from datetime import datetime, timezone, timedelta
import hashlib
import json
from pathlib import Path
import sys
import uuid

ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT/'board/host'))
from bridge_ordered_remote import ZIP_SHA,MANIFEST_SHA,SOURCE,PACKAGE,validate_launch


def prepare(scale,duration):
    now=datetime.now(timezone(timedelta(hours=8)))
    uid=uuid.uuid4().hex; suffix=now.strftime('%Y%m%d_%H%M%S')+'_'+uid[:8]
    value=dict(schema='n4_ordered_source_launch_v1',run_id=uid,host_utc=now.astimezone(timezone.utc).isoformat(),
               package_sha256=ZIP_SHA,package_manifest_sha256=MANIFEST_SHA,source_commit=SOURCE,
               remote_package=PACKAGE,remote_results='/home/xilinx/n4_fixed_results_'+suffix,
               scale=scale,pattern_seconds_total=duration,supervisor_deadline_seconds=duration+240,
               automatic_scale_switch=False,hold_display=False,physical_hdmi_verified=False)
    validate_launch(value)
    folder=ROOT/'board/acceptance'/now.strftime('%Y-%m-%d')/'n4_ordered_source_launches'
    folder.mkdir(parents=True,exist_ok=True)
    path=folder/('launch_'+suffix+'.json')
    with path.open('x',encoding='utf-8',newline='\n') as stream: json.dump(value,stream,indent=2)
    return path


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--scale',type=int,choices=(2,4),required=True)
    p.add_argument('--duration',type=int,default=1200)
    a=p.parse_args()
    archive=ROOT/'board/releases/2026-10-08/n4_fixed_source_r1.zip'
    if hashlib.sha256(archive.read_bytes()).hexdigest()!=ZIP_SHA: raise ValueError('Source ZIP changed')
    print(prepare(a.scale,a.duration))


if __name__=='__main__': main()
