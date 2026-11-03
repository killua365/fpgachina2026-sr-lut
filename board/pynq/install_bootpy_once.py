"""Install a reversible bootpy oneshot drop-in; never start/restart any service."""
import argparse
import hashlib
import json
import os
from pathlib import Path

from hdmi_m1 import root_card
from legacy_service_rollback import active, wait_status, systemctl, PORTAL, CANDIDATE

CID = '035344534333324780dba606de014500'
PINS = {
    '/lib/systemd/system/bootpy.service': '9b0d7e61c6c0d8950b36cfe75f63dbbccfb24653b027ce1129acd3d9402f587e',
    '/usr/local/bin/bootpy.sh': '5863599da06594345a60c03ee23a3148537ee1cb30203ed29f7c2d5c1580c67c',
    '/boot/boot.py': 'b2653ef809c3271231c78772342c2be728d39686a877d3c6fe8cdabfd4ac1d42',
}
TARGET = Path('/etc/systemd/system/bootpy.service.d/90-sr-lut-once.conf')
CONTENT = b'# SR-LUT: preserve successful boot initialization until the next boot.\n[Service]\nRemainAfterExit=yes\n'


def change_file(target, action):
    """Never overwrite another configuration or follow a symlink."""
    if target.is_symlink() or target.parent.is_symlink():
        raise RuntimeError('Refusing linked bootpy configuration')
    before = target.read_bytes() if target.exists() else None
    if before is not None and before != CONTENT:
        raise RuntimeError('Unowned bootpy drop-in exists')
    if action == 'install' and before is None:
        target.parent.mkdir(parents=True, exist_ok=True)
        with target.open('xb') as stream:
            stream.write(CONTENT)
            stream.flush()
            os.fsync(stream.fileno())
        target.chmod(0o644)
    elif action == 'rollback' and before is not None:
        target.unlink()
    return before


def run(action, output):
    if os.geteuid() != 0 or root_card() != CID:
        raise RuntimeError('Requires root on the verified spare card')
    if output.exists():
        raise FileExistsError('Choose a new result file')
    for name, digest in PINS.items():
        if hashlib.sha256(Path(name).read_bytes()).hexdigest() != digest:
            raise RuntimeError('Boot initialization input changed: '+name)
    if active(PORTAL) != 'active' or active(CANDIDATE) not in ('inactive', 'failed'):
        raise RuntimeError('Begin with the recovered formal service only')
    formal = wait_status(8080, False)
    props = systemctl('show', 'bootpy.service', '--property=Type,ActiveState,SubState,MainPID,ControlPID,DropInPaths')
    if props.returncode:
        raise RuntimeError('Cannot read bootpy state')
    state = dict(line.split('=', 1) for line in props.stdout.splitlines() if '=' in line)
    if (state.get('Type') != 'oneshot' or state.get('MainPID') != '0' or state.get('ControlPID') != '0' or
            (state.get('ActiveState'), state.get('SubState')) not in (('inactive', 'dead'), ('active', 'exited')) or
            state.get('DropInPaths', '') not in ('', str(TARGET))):
        raise RuntimeError('Boot initialization is busy or has unreviewed overrides')
    report = dict(action=action, boot_id=Path('/proc/sys/kernel/random/boot_id').read_text().strip(),
                  target=str(TARGET), state_before=state, formal_before=formal, pins=PINS,
                  passed=False, service_restart_performed=False, reboot_performed=False)
    output.parent.mkdir(parents=True, exist_ok=True)
    # Preserve the original configuration in the receipt before changing anything.
    report['original_content'] = TARGET.read_text() if TARGET.exists() else None
    output.write_text(json.dumps(report, indent=2)+'\n')
    before = change_file(TARGET, action)
    try:
        result = systemctl('daemon-reload')
        if result.returncode:
            raise RuntimeError('daemon-reload failed: '+result.stderr)
        effective = systemctl('show', 'bootpy.service', '--property=RemainAfterExit', '--value')
        wanted = 'yes' if action == 'install' else 'no'
        if effective.returncode or effective.stdout.strip() != wanted:
            raise RuntimeError('Effective RemainAfterExit differs')
        report.update(passed=True, effective_remain_after_exit=wanted,
                      reboot_required=True, old_n5_source_compatible=False if action == 'install' else True)
    except Exception as exc:
        if before is None:
            if TARGET.exists() and TARGET.read_bytes() == CONTENT:
                TARGET.unlink()
        else:
            TARGET.write_bytes(before)
        rollback = systemctl('daemon-reload')
        report.update(error=str(exc), automatic_rollback_returncode=rollback.returncode)
        raise
    finally:
        output.write_text(json.dumps(report, indent=2)+'\n')
        output.chmod(0o644)
    print(json.dumps(report, indent=2))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=('install', 'rollback'))
    parser.add_argument('--output', required=True, type=Path)
    args = parser.parse_args()
    run(args.action, args.output)
