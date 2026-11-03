"""2026.1 identity accounting and UART regressions; no hardware access."""
import binascii
import datetime
import hashlib
import json
from pathlib import Path
import subprocess

ROOT = Path(__file__).resolve().parents[2]
BIN = Path('D:/vivado2026.1/2026.1/Vivado/bin')
out = ROOT / 'build/nexys_bridge/work' / ('n5_identity_' + datetime.datetime.now().strftime('%Y%m%d_%H%M%S'))
(out / 'source').mkdir(parents=True)
names = ['src/rtl/nexys_hdmi/' + name + '.sv' for name in (
    'sr_identity_tracker', 'bridge_uart', 'bridge_pipeline_control', 'bridge_frame_owner')]
names += ['src/rtl/nexys_framebuffer/' + name + '.sv' for name in (
    'video_pair_fifo', 'video_pattern_axis', 'video_pattern_pixel')]
tests = ['sr_identity_tracker', 'bridge_uart', 'sr_identity_uart', 'bridge_pipeline', 'sr_identity_pipeline']
names += ['sim/nexys_bridge/tb_' + name + '.sv' for name in tests]
names += ['build/nexys_bridge/simulate_n5_identity.py']
inputs = []
for name in names:
    data = (ROOT / name).read_bytes()
    (out / 'source' / Path(name).name).write_bytes(data)
    inputs.append({'path': name, 'sha256': hashlib.sha256(data).hexdigest()})
result = {'passed': False, 'hardware_access': False, 'n5_passed': False, 'simulator': '2026.1', 'inputs': inputs, 'tests': []}
print(out, flush=True)
def run(exe, args, log):
    with (out / log).open('wb') as stream:
        subprocess.run([str(BIN / exe), *args], cwd=out, stdout=stream, stderr=subprocess.STDOUT, check=True, timeout=240)
    value = (out / log).read_text(errors='replace')
    if 'error:' in value.lower() or 'fatal:' in value.lower():
        raise RuntimeError('Simulation error in ' + log)
    return value
try:
    run('xvlog.bat', ['--sv', *('source/' + Path(n).name for n in names if n.endswith('.sv')),
                      str(BIN.parent / 'data/verilog/src/glbl.v')], 'compile.txt')
    for name in tests:
        print('Running ' + name, flush=True)
        run('xelab.bat', ['tb_' + name, 'glbl', '-L', 'xpm', '-s', name], name + '_elaborate.txt')
        log = run('xsim.bat', [name, '-runall'], name + '_simulation.txt')
        if 'PASS' not in log:
            raise RuntimeError('No PASS marker for ' + name)
        if name in ('bridge_uart', 'sr_identity_uart'):
            business = name == 'sr_identity_uart'
            size, header = (368, 11) if business else (160, 10)
            packets = [bytes.fromhex(line) for line in (out / 'uart.hex').read_text().splitlines()]
            assert len(packets) == 2
            for sequence, packet in enumerate(packets):
                assert len(packet) == size + header + 2
                assert packet[:4] == (b'SRB5' if business else b'SRB4')
                assert packet[4] == 1 and int.from_bytes(packet[5:header-4], 'little') == size
                assert int.from_bytes(packet[header-4:header], 'little') == sequence
                assert packet[header:-2] == (bytes(i % 256 for i in range(size)) if sequence == 0 else b'\xff' * size)
                assert int.from_bytes(packet[-2:], 'little') == binascii.crc_hqx(packet[:-2], 0xffff)
            (out / (name + '_packets.hex')).write_text((out / 'uart.hex').read_text())
        result['tests'].append(name)
    result['passed'] = True
finally:
    (out / 'result.json').write_text(json.dumps(result, indent=2) + '\n')
