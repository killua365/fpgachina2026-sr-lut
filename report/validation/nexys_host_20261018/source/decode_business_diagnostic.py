"""SRB5 complete-scan identity journal. Telemetry alone cannot accept N5/N6."""
import argparse
import binascii
import json
import math
import struct
from pathlib import Path

from decode_bridge_diagnostic import assess as assess_bridge

MAGIC = b'SRB5'
PAYLOAD_BYTES, HEADER_BYTES, FRAME_BYTES = 368, 11, 381
UI_HZ = 100_000_000
FIELDS = ('valid_inputs', 'control_inputs', 'marker_errors', 'written_results',
          'result_scans', 'new_results', 'repeat_results', 'identity_errors',
          'unmarked_scans', 'incomplete_scans', 'control_scans')
COUNTERS = {i + 2: name for i, name in enumerate(FIELDS)}
COUNTERS.update({18: 'sequence_skips', 19: 'discarded_result_buffers'})


def u64(words, index):
    return words[index] | words[index + 1] << 32


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
        if frame[4:7] != bytes((1, 112, 1)):
            errors.append(dict(offset=index, error='version_or_length'))
            pos = index + 4
            continue
        if binascii.crc_hqx(frame[:-2], 0xffff) != int.from_bytes(frame[-2:], 'little'):
            errors.append(dict(offset=index, error='crc'))
            pos = index + 4
            continue
        frames.append(dict(offset=index, sequence=int.from_bytes(frame[7:11], 'little'),
                           words=list(struct.unpack('<92I', frame[11:-2]))))
        pos = index + FRAME_BYTES
    return dict(frames=frames, errors=errors, skipped_bytes=skipped)


def journal(words):
    result = {}
    for slot in range(4):
        v = words[20 + slot * 8:28 + slot * 8]
        if v[0]:
            result[slot] = dict(event_sequence=v[0], generation=v[1], source_sequence=v[2],
                                mode=v[3] & 255, scale=v[3] >> 8,
                                latency_cycles=u64(v, 4), complete_ticks=u64(v, 6))
        elif any(v):
            raise ValueError('nonempty_unused_journal_slot')
    return result


def assess_identities(parsed, mode, scale, generation, minimum_new_fps=None):
    """Independent recomputation; never infer SR rate from raster/VDMA counters."""
    if mode not in (1, 2, 3) or scale not in (2, 4) or not 0 <= generation <= 0xffffffff:
        raise ValueError('Explicit valid mode, scale and generation are required')
    if minimum_new_fps is not None and not 0 < minimum_new_fps <= 30:
        raise ValueError('Minimum new-result rate must be in (0,30]')
    reasons, events, prior = [], [], None
    increments = dict.fromkeys(COUNTERS.values(), 0)
    if parsed['errors'] or parsed['skipped_bytes']:
        reasons.append('business_transport_corrupt_or_unframed')
    if len(parsed['frames']) < 3:
        reasons.append('insufficient_business_records')
    previous_event, independent_skips = None, 0
    for row in parsed['frames']:
        if len(row['words']) != 92:
            raise ValueError('SRB5 requires exactly 92 words')
        w, ticks = row['words'][40:], u64(row['words'], 16)
        if w[0] != 0x53524944 or w[1] != 9:
            reasons.append('identity_schema_disabled_saturated_or_faulted')
        if (w[13], w[15]) != (generation, scale << 8 | mode):
            reasons.append('unexpected_business_identity')
        if w[7] + w[8] + w[9] != w[6] or w[7] > w[5] or w[5] > w[2]:
            reasons.append('business_accounting_inconsistent')
        if w[6] + w[10] + w[12] > row['words'][6]:
            reasons.append('business_exceeds_complete_scans')
        try:
            slots = journal(w)
        except ValueError as exc:
            reasons.append(str(exc))
            slots = {}
        expected = set(range(max(1, w[7] - 3), w[7] + 1))
        if {e['event_sequence'] for e in slots.values()} != expected:
            reasons.append('journal_retention_invalid')
        for slot, event in slots.items():
            if slot != (event['event_sequence'] - 1) % 4:
                reasons.append('journal_slot_invalid')
            if event['mode'] not in (1, 2, 3) or event['scale'] not in (2, 4):
                reasons.append('journal_identity_format_invalid')
            if not 0 < event['latency_cycles'] <= event['complete_ticks'] <= ticks:
                reasons.append('journal_time_invalid')
        latest = slots.get((w[7] - 1) % 4) if w[7] else None
        if latest and (latest['generation'], latest['source_sequence'], latest['latency_cycles']) != (w[13], w[14], w[16]):
            reasons.append('last_identity_journal_disagreement')
        if w[17] < w[16]:
            reasons.append('max_latency_inconsistent')
        if prior is not None:
            p, pt, prow, oldslots = prior
            dt = ticks - pt
            if dt <= 0:
                reasons.append('business_time_not_increasing')
            if row['sequence'] != (prow['sequence'] + 1) & 0xffffffff:
                reasons.append('business_telemetry_gap')
            for index, name in COUNTERS.items():
                delta = w[index] - p[index]
                # Source sequence gaps are not HDMI rates; all counters saturate,
                # so regression is always invalid, including a 32-bit wrap.
                if delta < 0 or (index != 18 and delta > max(dt, 0) * 100 // UI_HZ + 3):
                    reasons.append('business_counter_regression_or_rate_' + name)
                increments[name] += delta
            if w[17] < p[17]:
                reasons.append('max_latency_regression')
            count = w[7] - p[7]
            if not 0 <= count <= 4:
                reasons.append('display_event_journal_gap')
            else:
                for number in range(p[7] + 1, w[7] + 1):
                    event = slots.get((number - 1) % 4)
                    if not event or event['event_sequence'] != number:
                        reasons.append('display_event_missing')
                        continue
                    if (event['generation'], event['mode'], event['scale']) != (generation, mode, scale):
                        reasons.append('journal_business_identity_changed')
                    if not pt < event['complete_ticks'] <= ticks:
                        reasons.append('event_outside_snapshot_interval')
                    # Anchor the first interval to the prior snapshot's latest
                    # result too; otherwise a replay at the boundary is missed.
                    anchor = previous_event or oldslots.get((p[7] - 1) % 4)
                    if anchor and (event['source_sequence'] <= anchor['source_sequence'] or
                                   event['complete_ticks'] <= anchor['complete_ticks']):
                        reasons.append('display_identity_replay_or_time_regression')
                    elif anchor:
                        independent_skips += event['source_sequence'] - anchor['source_sequence'] - 1
                    previous_event = event
                    events.append(event)
                for slot, event in oldslots.items():
                    if event['event_sequence'] in expected and slots.get(slot) != event:
                        reasons.append('retained_event_mutated')
        prior = w, ticks, row, slots
    rows = parsed['frames']
    seconds = (u64(rows[-1]['words'], 16) - u64(rows[0]['words'], 16)) / UI_HZ if len(rows) >= 2 else 0
    if len(events) != increments['new_results']:
        reasons.append('independent_new_count_mismatch')
    if independent_skips != increments['sequence_skips']:
        reasons.append('independent_sequence_gap_mismatch')
    for name in ('marker_errors', 'identity_errors', 'unmarked_scans', 'incomplete_scans', 'control_scans', 'control_inputs'):
        if increments[name]:
            reasons.append('stable_business_' + name)
    fps = len(events) / seconds if seconds > 0 else 0
    if minimum_new_fps is not None and fps < minimum_new_fps:
        reasons.append('new_display_rate_below_requested_target')
    latencies = sorted(e['latency_cycles'] / (UI_HZ / 1000) for e in events)
    def percentile(q):
        return latencies[max(0, math.ceil(q * len(latencies)) - 1)] if latencies else None
    return dict(identity_telemetry_passed=not reasons, reasons=sorted(set(reasons)),
                ui_seconds=seconds, increments=increments, new_display_fps=fps,
                independently_recovered_events=len(events), events=events,
                latency_ms=dict(p50=percentile(.50), p95=percentile(.95), p99=percentile(.99),
                                maximum=max(latencies, default=None)),
                latency_definition='Selected HDMI frame acceptance to complete DP scan, same Nexys UI clock',
                pynq_capture_to_display_latency_measured=False,
                n5_passed=False, n6_passed=False, formal_release=False)


def assess(parsed, mode, scale, generation, candidate=0x26101201, minimum_seconds=1.0, minimum_new_fps=None):
    identity = assess_identities(parsed, mode, scale, generation, minimum_new_fps)
    prefix = dict(parsed, frames=[dict(r, words=r['words'][:40]) for r in parsed['frames']])
    bridge = assess_bridge(prefix, candidate, minimum_seconds)
    reasons = sorted(set(identity['reasons'] + bridge['reasons']))
    return dict(identity=identity, bridge=bridge, telemetry_passed=not reasons, reasons=reasons,
                n5_passed=False, n6_passed=False, formal_release=False)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('raw', type=Path)
    parser.add_argument('--out', required=True, type=Path)
    parser.add_argument('--mode', type=int, choices=(1, 2, 3), required=True)
    parser.add_argument('--scale', type=int, choices=(2, 4), required=True)
    parser.add_argument('--generation', type=int, required=True)
    parser.add_argument('--minimum-seconds', type=float, default=1.0)
    parser.add_argument('--minimum-new-fps', type=float)
    args = parser.parse_args()
    parsed = decode(args.raw.read_bytes())
    result = assess(parsed, args.mode, args.scale, args.generation,
                    minimum_seconds=args.minimum_seconds, minimum_new_fps=args.minimum_new_fps)
    with args.out.open('x', encoding='utf-8') as stream:
        json.dump(dict(summary=result, decoded=parsed), stream, indent=2)
    print(json.dumps({key: value for key, value in result.items() if key not in ('identity', 'bridge')}))
