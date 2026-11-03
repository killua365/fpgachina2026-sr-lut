"""Reject a generated PYNQ hardware description that differs from the current frozen design."""
import argparse
import hashlib
import json
from pathlib import Path, PurePosixPath
import xml.etree.ElementTree as ET

ROOT = Path(__file__).resolve().parents[1]


def signature(path, *, snapshot=None):
    tree = ET.parse(path)
    modules = {}
    for module in tree.findall('.//MODULE'):
        name = module.get('INSTANCE')
        assert name not in modules, 'Duplicate hardware instance'
        params = {p.get('NAME'): p.get('VALUE') for p in module.findall('./PARAMETERS/PARAMETER')}
        if 'MEM_FILE' in params:
            value = params['MEM_FILE']
            filename = PurePosixPath(value.replace('\\', '/')).name
            if snapshot is not None:
                memory = Path(value).resolve(strict=True)
                memory.relative_to(snapshot.resolve())
                reference = ROOT / 'data/lut/bram_mem' / filename
                assert memory.read_bytes() == reference.read_bytes(), 'LUT contents differ'
            params['MEM_FILE'] = filename
        modules[name] = dict(type=module.get('MODTYPE'), vlnv=module.get('VLNV'),
                             parameters=params,
                             ports={p.get('NAME'): dict(p.attrib) for p in module.findall('./PORTS/PORT')})
    ranges = sorted((dict(p.attrib) for p in tree.findall('.//MEMRANGE')), key=lambda p: json.dumps(p, sort_keys=True))
    return dict(modules=modules, address_ranges=ranges)


def check(snapshot, scale):
    actual = snapshot / ('build/hdmi_x%d_1080p30_work/sr_lut_s40_dyn.gen/sources_1/bd/sr_lut_s40_dyn/hw_handoff/sr_lut_s40_dyn.hwh' % scale)
    frozen = ROOT / ('build/artifacts/pynq_x%d/sr_lut_x%d.hwh' % (scale, scale))
    expected, observed = signature(frozen), signature(actual, snapshot=snapshot)
    differences = []
    for name in sorted(expected['modules'].keys() | observed['modules'].keys()):
        a, b = expected['modules'].get(name), observed['modules'].get(name)
        if a != b:
            differences.append(dict(module=name, expected=a, actual=b))
    if expected['address_ranges'] != observed['address_ranges']:
        differences.append(dict(address_ranges_expected=expected['address_ranges'], address_ranges_actual=observed['address_ranges']))
    return dict(passed=not differences, scale=scale, mode='1920x1080@30',
                actual_hwh_sha256=hashlib.sha256(actual.read_bytes()).hexdigest(),
                frozen_hwh_sha256=hashlib.sha256(frozen.read_bytes()).hexdigest(),
                compared='All modules, parameters, port attributes and memory-map ranges; only relocated LUT pathname normalized, with LUT bytes checked',
                differences=differences, hardware_access=False)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--snapshot', type=Path, required=True)
    parser.add_argument('--scale', type=int, choices=(2, 4), required=True)
    parser.add_argument('--out', type=Path, required=True)
    args = parser.parse_args()
    value = check(args.snapshot, args.scale)
    with args.out.open('x', encoding='utf-8') as stream:
        json.dump(value, stream, indent=2)
        stream.write('\n')
    print(json.dumps({k:v for k,v in value.items() if k != 'differences'}))
    if not value['passed']:
        raise SystemExit('Hardware configuration differs from the frozen 1080p30 design; see ' + str(args.out))


if __name__ == '__main__':
    main()
