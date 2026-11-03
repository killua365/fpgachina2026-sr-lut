"""Audit an explicitly bounded SRB5 window using the existing raw UART capture."""
import argparse
import bisect
import csv
import hashlib
import json
import math
from pathlib import Path

from decode_business_diagnostic import MAGIC, FRAME_BYTES, decode, assess


def window(raw, chunks, frequency, start_ns, finish_ns, *, mode, scale, generation,
           minimum_seconds=1.0, minimum_new_fps=None, source_frames=None):
    if frequency <= 0 or not math.isfinite(minimum_seconds) or minimum_seconds <= 0 or finish_ns-start_ns < minimum_seconds*1e9:
        raise ValueError('Short or invalid requested window')
    ends, stamps, total, previous = [], [], 0, -1
    for row in chunks:
        count, end, tick = int(row['count']), int(row['total_bytes']), int(row['qpc_ticks'])
        if count <= 0 or end != total+count or tick < previous:
            raise ValueError('Invalid raw capture chunk chronology')
        total, previous = end, tick
        ends.append(end)
        stamps.append(tick*1_000_000_000//frequency)
    if total != len(raw):
        raise ValueError('Capture length mismatch')
    included = bisect.bisect_right(stamps, finish_ns)
    if not included:
        raise ValueError('No captured data before window end')
    limit = ends[included-1]
    first = raw.find(MAGIC, 0, limit)
    if first < 0:
        raise ValueError('No SRB5 startup')
    end = first + (limit-first)//FRAME_BYTES*FRAME_BYTES
    parsed = decode(raw[first:end])
    if parsed['errors'] or parsed['skipped_bytes'] or not parsed['frames']:
        raise ValueError('Damaged candidate transport before or during the window')
    rows = parsed['frames']
    if rows[0]['sequence'] != 0 or rows[0]['words'][30]+(rows[0]['words'][31]<<32) > 100_000_000:
        raise ValueError('Candidate startup was not captured')
    if any(b['sequence'] != (a['sequence']+1) & 0xffffffff for a, b in zip(rows, rows[1:])):
        raise ValueError('Candidate telemetry sequence gap')
    received = [stamps[bisect.bisect_right(ends, first+r['offset']+FRAME_BYTES-1)] for r in rows]
    if received[0] > start_ns or finish_ns-received[-1] > 250_000_000:
        raise ValueError('Incomplete requested window coverage')
    if any(b-a > 300_000_000 for a, b in zip(received, received[1:])):
        raise ValueError('Transport delivery stalled')
    baseline = bisect.bisect_right(received, start_ns)-1
    if start_ns-received[baseline] > 250_000_000:
        raise ValueError('Missing start boundary')
    formal = dict(frames=rows[baseline:], errors=[], skipped_bytes=0)
    result = assess(formal, mode, scale, generation,
                    minimum_seconds=minimum_seconds, minimum_new_fps=minimum_new_fps)
    duration = (received[-1]-received[baseline])/1e9
    if duration <= 0 or abs(duration-result['identity']['ui_seconds']) > max(.03, .0015*duration):
        result['reasons'].append('host_hardware_timebase_disagreement')
    events = result['identity']['events']
    source_bound = False
    if source_frames is not None:
        # Join by the actual wire identity. The two boards' timestamps are
        # never subtracted: their clocks have not been synchronized.
        identities, last_by_generation = set(), {}
        for frame in source_frames:
            pair = frame['generation'], frame['sequence']
            if any(type(v) is not int or not 0 <= v <= 0xffffffff for v in pair):
                raise ValueError('Invalid source identity')
            if pair in identities or pair[1] <= last_by_generation.get(pair[0], -1):
                raise ValueError('Duplicate or regressing source trace')
            identities.add(pair)
            last_by_generation[pair[0]] = pair[1]
        missing = [(e['generation'], e['source_sequence']) for e in events
                   if (e['generation'], e['source_sequence']) not in identities]
        if missing:
            result['reasons'].append('displayed_identity_absent_from_source_trace')
        source_bound = bool(events) and not missing
    result['reasons'] = sorted(set(result['reasons']))
    result.update(telemetry_passed=not result['reasons'], source_identities_bound=source_bound,
                  pynq_capture_to_display_latency_measured=False,
                  raw_sha256=hashlib.sha256(raw).hexdigest(), raw_bytes=len(raw),
                  preserved_prefix_bytes=first, analyzed_end_offset=end,
                  preserved_suffix_bytes=len(raw)-end, formal_first_record=baseline,
                  requested_host_seconds=(finish_ns-start_ns)/1e9,
                  observed_host_seconds=duration,
                  host_new_display_fps=len(events)/duration if duration > 0 else None,
                  n5_passed=False, n6_passed=False, formal_release=False)
    return dict(summary=result, decoded=parsed)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('capture', type=Path)
    parser.add_argument('--start-qpc-ns', type=int, required=True)
    parser.add_argument('--finish-qpc-ns', type=int, required=True)
    parser.add_argument('--mode', type=int, choices=(1, 2, 3), required=True)
    parser.add_argument('--scale', type=int, choices=(2, 4), required=True)
    parser.add_argument('--generation', type=int, required=True)
    parser.add_argument('--minimum-seconds', type=float, default=1.0)
    parser.add_argument('--minimum-new-fps', type=float)
    parser.add_argument('--source-frames', type=Path)
    parser.add_argument('--out', type=Path, required=True)
    args = parser.parse_args()
    identity = json.loads((args.capture/'identity.json').read_text('utf-8-sig'))
    closed = json.loads((args.capture/'exit.json').read_text('utf-8-sig'))
    raw = (args.capture/'capture.bin').read_bytes()
    if (identity['baud'], identity['format'], identity['writes_performed']) != (115200, '8N1', False):
        raise ValueError('Capture UART configuration mismatch')
    if not identity['pnp_id'].upper().startswith('FTDIBUS\\VID_0403+PID_6001+A904CVLW'):
        raise ValueError('Capture UART identity mismatch')
    if not closed['success'] or closed['bytes'] != len(raw):
        raise ValueError('Capture did not close successfully')
    with (args.capture/'chunks.csv').open(encoding='utf-8-sig', newline='') as stream:
        chunks = list(csv.DictReader(stream))
    frames = None
    if args.source_frames:
        frames = [json.loads(line) for line in args.source_frames.read_text().splitlines() if line.strip()]
    result = window(raw, chunks, identity['qpc_frequency'], args.start_qpc_ns, args.finish_qpc_ns,
                    mode=args.mode, scale=args.scale, generation=args.generation,
                    minimum_seconds=args.minimum_seconds, minimum_new_fps=args.minimum_new_fps,
                    source_frames=frames)
    if args.source_frames:
        result['summary']['source_trace_sha256'] = hashlib.sha256(args.source_frames.read_bytes()).hexdigest()
    with args.out.open('x', encoding='utf-8') as stream:
        json.dump(result, stream, indent=2)
    print(json.dumps({k:v for k,v in result['summary'].items() if k not in ('identity', 'bridge')}))
