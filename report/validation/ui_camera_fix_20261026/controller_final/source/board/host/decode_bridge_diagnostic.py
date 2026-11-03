"""SRB4 transport and stable-window checks; never grants N4 acceptance alone."""
import argparse
import binascii
import json
import struct
from pathlib import Path

from decode_frame_diagnostic import assess as assess_frame

MAGIC = b'SRB4'
FRAME_BYTES = 172


def decode(raw):
    frames, errors, pos, skipped = [], [], 0, 0
    while pos < len(raw):
        index = raw.find(MAGIC, pos)
        if index < 0:
            skipped += len(raw) - pos
            break
        skipped += index - pos
        if len(raw) - index < FRAME_BYTES:
            errors.append(dict(offset=index, error='truncated'))
            break
        frame = raw[index:index + FRAME_BYTES]
        if frame[4:6] != bytes((1, 160)):
            errors.append(dict(offset=index, error='version_or_length'))
            pos = index + 4
            continue
        if binascii.crc_hqx(frame[:170], 0xffff) != int.from_bytes(frame[170:], 'little'):
            errors.append(dict(offset=index, error='crc'))
            pos = index + 4
            continue
        frames.append(dict(offset=index, sequence=int.from_bytes(frame[6:10], 'little'),
                           words=list(struct.unpack('<40I', frame[10:170]))))
        pos = index + FRAME_BYTES
    return dict(frames=frames, errors=errors, skipped_bytes=skipped)


def assess(parsed, candidate=0x26100601, minimum_seconds=119.0):
    # Shared DDR/DP fields retain the N3 semantics. Explicitly adapt layout;
    # preserve every original record and require the real N4 identity first.
    rows = parsed['frames']
    mapped, reasons, prior = [], [], None
    totals = dict(accepted=0, rejected=0, damaged=0, rx_frames=0, rx_good=0, rx_bad=0)
    fields = {21:'accepted', 22:'rejected', 23:'damaged', 24:'rx_frames', 25:'rx_good', 26:'rx_bad'}
    for row in rows:
        w = row['words']
        if len(w) != 40:
            raise ValueError('SRB4 requires exactly 40 words')
        if w[1] != 0x4e344231:
            reasons.append('bridge_schema_mismatch')
        converted = w[:18] + w[30:40]
        converted[1] = 0x4e334631
        mapped.append(dict(row, words=converted))
        if w[18] != 1:
            reasons.append('input_absent_or_flushing')
        if (w[27], w[28], w[29]) != (1920*1080, 2200, 2200*1125):
            reasons.append('input_geometry_invalid')
        if (w[25] + w[26]) & 0xffffffff != w[24]:
            reasons.append('input_accounting_inconsistent')
        if prior is not None:
            dt = (w[16] | w[17] << 32) - (prior[16] | prior[17] << 32)
            for index in (19, 20):
                if w[index] != prior[index]:
                    reasons.append('input_loss_or_overflow')
            for index, name in fields.items():
                delta = (w[index] - prior[index]) & 0xffffffff
                if delta > max(0, dt) * 100 // 100_000_000 + 2:
                    reasons.append('input_counter_regression_' + name)
                totals[name] += delta
        prior = w
    result = assess_frame(dict(parsed, frames=mapped), candidate, minimum_seconds)
    reasons.extend(result['reasons'])
    seconds = result['ui_seconds']
    rates = {k:v / seconds for k,v in totals.items()} if seconds > 0 else {}
    if seconds > 0:
        for name in ('accepted', 'rx_frames', 'rx_good'):
            if not 29.5 <= rates[name] <= 30.5:
                reasons.append('input_rate_outside_n4_contract_' + name)
        for name in ('rejected', 'damaged', 'rx_bad'):
            if totals[name]:
                reasons.append('stable_input_' + name)
    result.update(telemetry_passed=not reasons, n4_passed=False, formal_release=False,
                  reasons=sorted(set(reasons)), input_increments=totals, input_rates=rates,
                  source_frame_identity_verified=False, physical_observation_verified=False)
    return result


if __name__ == '__main__':
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('raw', type=Path)
    p.add_argument('--out', required=True, type=Path)
    p.add_argument('--candidate', type=lambda v:int(v, 16), default=0x26100601)
    a = p.parse_args()
    parsed = decode(a.raw.read_bytes())
    result = assess(parsed, a.candidate)
    with a.out.open('x', encoding='utf-8') as f:
        json.dump(dict(summary=result, decoded=parsed), f, indent=2)
    print(json.dumps(result))
