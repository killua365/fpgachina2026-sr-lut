"""Diagnose archived N5 costs and scan rates; never promote interactive runs to N6."""
import argparse
from collections import defaultdict
import hashlib
import json
import math
from pathlib import Path
import statistics

MODE = {1: 'image', 2: 'video', 3: 'camera'}
TIMINGS = ('prepare_seconds', 'dma_sr_seconds', 'unpack_seconds', 'processing_seconds',
           'validation_seconds', 'color_convert_seconds', 'compose_seconds',
           'display_submit_seconds', 'pl_frame_seconds')


def require(condition, message):
    if not condition:
        raise ValueError(message)


def finite(value, name):
    require(type(value) in (int, float) and math.isfinite(value) and value >= 0, 'Invalid '+name)
    return value


def stats(values):
    if not values:
        return None
    values = sorted(values)
    position = (len(values)-1)*0.95
    lo, hi = math.floor(position), math.ceil(position)
    return dict(count=len(values), median=statistics.median(values),
                p95=values[lo]+(values[hi]-values[lo])*(position-lo), maximum=values[-1])


def summarize(samples, frames, events, clock_hz=100_000_000):
    finite(clock_hz, 'Nexys clock')
    require(clock_hz > 0, 'Nexys clock must be positive')
    identities, grouped_frames = {}, defaultdict(list)
    previous, stamp = None, -1
    for frame in frames:
        key = frame['generation'], frame['sequence']
        require(all(type(x) is int and x >= 0 for x in key), 'Invalid source identity')
        require(previous is None or key > previous, 'Source identity replay/regression')
        require(frame['mode'] in MODE and frame['scale'] in (2, 4), 'Wrong source mode/scale')
        when = finite(frame['submitted_monotonic_ns'], 'source time')
        require(when > stamp, 'Source clock regression')
        identities[key] = frame
        grouped_frames[key[0]].append(frame)
        previous, stamp = key, when
    grouped_events, last_tick, previous, number = defaultdict(list), -1, None, 0
    for event in events:
        key = event['generation'], event['source_sequence']
        require(key in identities, 'Displayed event has no same-run source')
        require(previous is None or key > previous, 'Displayed identity replay/regression')
        require(event['event_sequence'] == number+1, 'Displayed journal gap')
        source = identities[key]
        require((event['mode'], event['scale']) == (source['mode'], source['scale']), 'Displayed identity mismatch')
        tick, latency = event['complete_ticks'], event['latency_cycles']
        require(type(tick) is int and type(latency) is int and 0 < latency <= tick and tick > last_tick,
                'Invalid Nexys event clock')
        grouped_events[key[0]].append(event)
        last_tick, previous, number = tick, key, event['event_sequence']

    groups, previous_time = [], -1
    for sample in samples:
        now = finite(sample['monotonic'], 'sample time')
        require(now > previous_time, 'Sample clock regression')
        runtime = sample['runtime']
        require(runtime['mode'] in (*MODE.values(), 'idle') and runtime['scale'] in (2, 4), 'Invalid sample mode')
        key = (runtime['generation'], runtime['mode'], runtime['scale'], runtime['layout'])
        if not groups or groups[-1]['key'] != key:
            groups.append(dict(key=key, samples=[]))
        groups[-1]['samples'].append(sample)
        previous_time = now

    segments, layouts = [], defaultdict(set)
    for group in groups:
        generation, mode, scale, layout = group['key']
        rows = group['samples']
        layouts[generation].add(layout)
        first, last = rows[0], rows[-1]
        elapsed = last['monotonic']-first['monotonic']
        counters = {}
        for field in ('processed_source_frames', 'submitted_new_frames'):
            values = [s['runtime'][field] for s in rows]
            require(all(type(v) is int and v >= 0 for v in values), 'Invalid '+field)
            require(all(b >= a for a, b in zip(values, values[1:])), 'Counter regression: '+field)
            counters[field] = values[-1]-values[0]
        ack = [s['display']['acknowledged_source_pairs'] for s in rows]
        require(all(type(v) is int and v >= 0 for v in ack) and all(b >= a for a, b in zip(ack, ack[1:])),
                'VDMA counter regression')
        timing = {key: [] for key in TIMINGS}
        fresh = 0
        for before, after in zip(rows, rows[1:]):
            rt = after['runtime']
            active = any(rt.get(k, {}).get('state') == 'running' for k in ('video_export', 'video_import'))
            if active or rt['processed_source_frames'] == before['runtime']['processed_source_frames']:
                continue
            fresh += 1
            for key in TIMINGS:
                value = rt.get('last_timings', {}).get(key)
                if value is not None:
                    timing[key].append(finite(value, key))
        segments.append(dict(generation=generation, mode=mode, scale=scale, layout=layout,
            sample_count=len(rows), source_clock_start=first['monotonic'], source_clock_end=last['monotonic'],
            sample_span_seconds=elapsed, processed_delta=counters['processed_source_frames'],
            submitted_delta=counters['submitted_new_frames'], pynq_vdma_ack_delta=ack[-1]-ack[0],
            submitted_fps=counters['submitted_new_frames']/elapsed if elapsed > 0 else None,
            pynq_vdma_ack_fps=(ack[-1]-ack[0])/elapsed if elapsed > 0 else None,
            activity_snapshot_count=fresh, timing_snapshot_seconds={k: stats(v) for k, v in timing.items()},
            active_media_operation_samples=sum(any(s['runtime'].get(k, {}).get('state') == 'running'
                for k in ('video_export', 'video_import')) for s in rows),
            camera_not_streaming_samples=sum(s['runtime'].get('camera', {}).get('state') != 'streaming'
                for s in rows) if mode == 'camera' else None,
            scope='Whole observed segment, including startup, pauses and user work; not a dedicated performance window'))

    generations = []
    for generation, source in grouped_frames.items():
        shown = grouped_events[generation]
        require(len({(f['mode'], f['scale']) for f in source}) == 1, 'Generation mixed mode or scale')
        elapsed = (shown[-1]['complete_ticks']-shown[0]['complete_ticks'])/clock_hz if len(shown) >= 2 else None
        gaps = [(b['complete_ticks']-a['complete_ticks'])/clock_hz for a, b in zip(shown, shown[1:])]
        generations.append(dict(generation=generation, mode=MODE[source[0]['mode']], scale=source[0]['scale'],
            observed_layouts=sorted(layouts[generation]), submitted_identities=len(source),
            complete_new_scans=len(shown), submitted_not_observed_complete=len(source)-len(shown),
            event_span_seconds=elapsed, nexys_event_spacing_fps=(len(shown)-1)/elapsed if elapsed else None,
            complete_scan_gap_seconds=stats(gaps),
            nexys_receive_to_complete_latency_ms=stats([e['latency_cycles']/clock_hz*1000 for e in shown]),
            scope='Nexys clock between first/last new scan; omits boundary idle time and cannot establish N6'))
    return dict(kind='n5_archived_performance_diagnostic', segments=segments, generations=generations,
        timing_accounting=dict(outer=['prepare_seconds', 'dma_sr_seconds', 'unpack_seconds'],
            processing_seconds='Includes the three outer processing phases; do not add it again',
            validation_seconds='Subset of unpack_seconds; fused RGB copy is included',
            color_convert_seconds='Subset of unpack_seconds',
            pl_frame_seconds='Subset of dma_sr_seconds, measured by FPGA; not end-to-end latency',
            compose_seconds='Separate, includes UI and identity overlay',
            display_submit_seconds='Separate, includes buffer flush/wait',
            caveat='Only fresh last-frame snapshots are summarized; not every processed frame was timed in this archive'),
        clock_policy='PYNQ and Nexys timestamps are never subtracted from each other',
        performance_fixture_verified=False, dedicated_300_second_windows=False,
        n6_cases=[dict(mode=mode, scale=scale, layout=layout, status='pending', minimum_seconds=300, minimum_new_fps=20)
            for mode, scale in (('video', 2), ('video', 4), ('camera', 4)) for layout in ('sr', 'split')],
        n6_passed=False, formal_release=False)


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--run', type=Path, required=True)
    p.add_argument('--source', type=Path, required=True)
    p.add_argument('--out', type=Path, required=True)
    a = p.parse_args()
    from analyze_business_session import audit, read
    from reconcile_business_session import inventory, canonical_sha
    from decode_business_diagnostic import UI_HZ
    for path in (a.run, a.source):
        require(a.out.resolve() != path.resolve() and path.resolve() not in a.out.resolve().parents,
                'Output must be outside original evidence')
    require(not a.out.exists(), 'Preserve earlier performance results')
    before = dict(run=inventory(a.run), source=inventory(a.source))
    independent = audit(a.run, a.source)
    require(independent['session_audit_passed'], 'Session audit failed')
    def lines(path):
        return [json.loads(line) for line in path.read_text('utf-8').splitlines() if line]
    result = summarize(lines(a.source/'worker/samples.jsonl'), lines(a.source/'worker/frames.jsonl'),
                       independent['events'], UI_HZ)
    require(before == dict(run=inventory(a.run), source=inventory(a.source)), 'Evidence changed during analysis')
    launch = read(a.run/'source_launch.json')
    result.update(run_id=launch['run_id'], package_manifest_sha256=launch['package_manifest_sha256'],
        raw_sha256=independent['raw_sha256'], input_inventory_sha256=canonical_sha(before),
        session_independently_recomputed=True, inputs=before,
        analyzer_sha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest())
    a.out.mkdir(parents=True)
    (a.out/'result.json').write_text(json.dumps(result, indent=2, allow_nan=False)+'\n', encoding='utf-8')
    print(json.dumps(dict(segments=len(result['segments']), generations=len(result['generations']), n6_passed=False)))


if __name__ == '__main__':
    main()
