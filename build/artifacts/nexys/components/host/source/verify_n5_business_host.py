"""Archive focused SRB5 host checks and decode packets emitted by actual RTL."""
import argparse
import hashlib
import json
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'board/nexys'))
from decode_business_diagnostic import decode


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('uart_evidence', type=Path)
    parser.add_argument('output', type=Path)
    args = parser.parse_args()
    out = args.output.resolve()
    out.relative_to(ROOT / 'build/nexys_bridge/work')
    out.mkdir()
    (out / 'source').mkdir()
    inputs = []
    for name in ('board/nexys/decode_business_diagnostic.py', 'board/nexys/test_business_diagnostic.py',
                 'board/nexys/analyze_business_capture.py', 'board/nexys/test_business_capture.py',
                 'board/nexys/decode_bridge_diagnostic.py', 'board/nexys/decode_frame_diagnostic.py',
                 'build/nexys_bridge/verify_n5_business_host.py'):
        data = (ROOT / name).read_bytes()
        (out / 'source' / Path(name).name).write_bytes(data)
        inputs.append(dict(path=name, sha256=hashlib.sha256(data).hexdigest()))
    with (out / 'tests.txt').open('w', encoding='utf-8') as log:
        result = unittest.TextTestRunner(stream=log, verbosity=2).run(
            unittest.defaultTestLoader.loadTestsFromNames(['test_business_diagnostic', 'test_business_capture']))
    packet_path = args.uart_evidence / 'sr_identity_uart_packets.hex'
    raw = b''.join(bytes.fromhex(line) for line in packet_path.read_text().splitlines())
    decoded = decode(raw)
    transport_ok = not decoded['errors'] and not decoded['skipped_bytes'] and len(decoded['frames']) == 2
    if transport_ok:
        transport_ok = (decoded['frames'][0]['words'] == [int.from_bytes(bytes((j % 256 for j in range(i, i+4))), 'little') for i in range(0, 368, 4)] and
                        decoded['frames'][1]['words'] == [0xffffffff] * 92)
    (out / 'actual_uart.bin').write_bytes(raw)
    status = dict(passed=result.wasSuccessful() and not result.skipped and transport_ok,
                  host_tests=result.testsRun, skipped=len(result.skipped), failures=len(result.failures),
                  errors=len(result.errors), actual_rtl_uart_decoded=transport_ok, inputs=inputs,
                  uart_evidence=str(args.uart_evidence), uart_sha256=hashlib.sha256(raw).hexdigest(),
                  hardware_access=False, n5_passed=False, n6_passed=False)
    (out / 'result.json').write_text(json.dumps(status, indent=2)+'\n', encoding='utf-8')
    print(json.dumps(status))
    if not status['passed']:
        raise SystemExit(1)


if __name__ == '__main__':
    main()
