"""Independent complete-session audit. Business observation and N6 remain separate."""
import argparse
import bisect
import csv
import hashlib
import json
from pathlib import Path
from business_session_gate import resolve_payload
import sys
from business_session_policy import limits_for
from business_session_remote import validate_launch, verify_final, require
from business_session_source import CONTRACT, ROOT
from decode_business_diagnostic import MAGIC, FRAME_BYTES, decode, journal, u64
from ddr_device_status import properties
from bridge_ordered_protocol import receive
from supervise_bridge_reacquire import operation
from business_session_live import initializing
from ftdi_status import check_status

sys.path.insert(0, str(ROOT/'board/pynq'))
from business_evidence_files import verify_copy, validate_inventory
from audit_hdmi_business_pixels import audit as pixels


def read(p):
    return json.loads(p.read_text('utf-8-sig'))


def timeline(raw, chunks, frequency, until):
    require(frequency > 0, 'Invalid capture clock')
    ends, times, total, prior = [], [], 0, -1
    for row in chunks:
        count, end, stamp = int(row['count']), int(row['total_bytes']), int(row['qpc_ticks'])
        require(count > 0 and end == total+count and stamp >= prior, 'Invalid capture chronology')
        total, prior = end, stamp
        ends.append(end)
        times.append(stamp*1_000_000_000//frequency)
    require(total == len(raw), 'Capture size mismatch')
    n = bisect.bisect_right(times, until)
    require(n > 0, 'No candidate capture')
    limit = ends[n-1]
    first = raw.find(MAGIC, 0, limit)
    require(first >= 0, 'No SRB5 startup')
    end = first+(limit-first)//FRAME_BYTES*FRAME_BYTES
    parsed = decode(raw[first:end])
    require(not parsed['errors'] and not parsed['skipped_bytes'] and parsed['frames'], 'Damaged complete candidate stream')
    rows = parsed['frames']
    stamps = [times[bisect.bisect_right(ends, first+row['offset']+FRAME_BYTES-1)] for row in rows]
    require(rows[0]['sequence'] == 0 and u64(rows[0]['words'], 30) <= 100_000_000, 'Startup missing')
    require(all(b['sequence'] == (a['sequence']+1)&0xffffffff for a, b in zip(rows, rows[1:])), 'Candidate sequence gap')
    require(all(0 <= b-a <= 300_000_000 for a, b in zip(stamps, stamps[1:])), 'Capture delivery stall')
    require(until-stamps[-1] <= 300_000_000, 'Missing final candidate tail')
    return rows, stamps, dict(prefix_bytes=first, candidate_end=end, suffix_bytes=len(raw)-end)


def bind_events(rows, source, *, stamps=None, ready_after=None):
    identities, previous, seen, events = {}, None, {}, []
    for frame in source:
        pair = frame['generation'], frame['sequence']
        require(all(type(x) is int and 0 <= x <= 0xffffffff for x in pair), 'Invalid source identity')
        require(previous is None or pair > previous, 'Source trace replay/regression')
        require(frame['mode'] in (1, 2, 3) and frame['scale'] in (2, 4), 'Wrong source mode/scale')
        identities[pair], previous = frame, pair
    last_count, last_ticks = 0, -1
    observer_seen = False
    for index, row in enumerate(rows):
        w, s = row['words'], row['words'][40:]
        if not observer_seen and initializing(w) and stamps is not None and ready_after is not None and stamps[index] < ready_after:
            require(w[0:2] == [0x26101201, 0x4e344231], 'Wrong startup candidate')
            continue
        observer_seen = True
        require((w[0], w[1], s[0]) == (0x26101201, 0x4e344231, 0x53524944), 'Wrong candidate')
        require(not w[2] and s[1] in (1, 9) and w[35] == 0x3b and w[33] < 10_000_000 and w[32],
                'Hardware/observer/snapshot fault')
        require(s[7]+s[8]+s[9] == s[6] and not s[9] and u64(w, 16) > last_ticks,
                'Identity accounting or clock regression')
        slots = journal(s)
        expected = set(range(max(1, s[7]-3), s[7]+1))
        require({e['event_sequence'] for e in slots.values()} == expected, 'Missing retained identity events')
        require(0 <= s[7]-last_count <= 4, 'New-result journal overflow/regression')
        for slot, event in slots.items():
            number = event['event_sequence']
            require(slot == (number-1)%4, 'Wrong journal slot')
            require(0 < event['latency_cycles'] <= event['complete_ticks'] <= u64(w, 16), 'Wrong event time')
            if number in seen:
                require(seen[number] == event, 'Retained event changed')
        for number in range(last_count+1, s[7]+1):
            event = slots[(number-1)%4]
            key = event['generation'], event['source_sequence']
            require(key in identities and (identities[key]['mode'], identities[key]['scale']) ==
                    (event['mode'], event['scale']), 'Displayed event absent from matching source trace')
            if events:
                require(key > (events[-1]['generation'], events[-1]['source_sequence']) and
                        event['complete_ticks'] > events[-1]['complete_ticks'], 'Displayed identity replay')
            seen[number] = event
            events.append(event)
        last_count, last_ticks = s[7], u64(w, 16)
    require(events, 'No SR result reached a complete scan')
    return events


def audit(folder, source):
    c = read(CONTRACT)
    launch, plan, result = read(folder/'source_launch.json'), read(folder/'plan.json'), read(folder/'result.json')
    validate_launch(launch, c)
    limits = limits_for(launch.get('end_mode', 'timed'))
    require(plan.get('end_mode', 'timed') == launch.get('end_mode', 'timed'), 'Different session end mode')
    require(plan['schema'] == 'n5_business_session_v1' and plan['run_id'] == launch['run_id'] and plan['limits'] == limits,
            'Wrong session plan')
    require(result['execution_complete'] and not result['monitor']['failures'], 'Controller did not complete')
    require(result['run_id'] == launch['run_id'] and plan['case'] == 'business', 'Different controller session')
    m = result['monitor']
    start, close, restore = (round(m[key]*1e9) for key in ('sample_start', 'sample_close', 'restore_request'))
    transition = int((folder/'board/recovery_started_qpc.txt').read_text())
    require(start < close <= restore <= transition <= restore+120_000_000_000, 'Invalid close/restore chronology')
    require(40e9 <= close-start <= (limits['window']+limits['acknowledgement'])*1e9, 'Invalid measurement duration')
    op = operation(folder/'board')
    require(all(op.get(k) == v for k, v in dict(run_id=launch['run_id'], flash_write_called='false',
            loaded='1', sample_started='1', sample_closed='1', restore_requested='1', test_exit='0', recovery_exit='0').items()),
            'Loader did not complete the ordered protocol')
    require(receive(folder/'board/start.ack', launch['run_id'], 'start') and
            receive(folder/'board/close.ack', launch['run_id'], 'close'), 'Missing loader acknowledgements')
    stop = read(folder/'source_before_restore.json')
    require(stop['verified'] and close <= stop['snapshot_qpc_ns'] <= restore and restore-stop['snapshot_qpc_ns'] <= 8e9,
            'Stale source-stop proof')
    verify_final(stop['snapshot'], launch, result['source_binding'])
    capture = folder/'capture'
    identity, closed = read(capture/'identity.json'), read(capture/'exit.json')
    raw = (capture/'capture.bin').read_bytes()
    require(identity['baud'] == 115200 and identity['format'] == '8N1' and identity['writes_performed'] is False and
            identity['pnp_id'].upper().startswith('FTDIBUS\\VID_0403+PID_6001+A904CVLW'), 'Wrong UART')
    require(closed['success'] and closed['bytes'] == len(raw), 'Capture not complete')
    with (capture/'chunks.csv').open(encoding='utf-8-sig', newline='') as stream:
        chunks = list(csv.DictReader(stream))
    rows, stamps, meta = timeline(raw, chunks, identity['qpc_frequency'], transition)
    check_status(identity, closed, stamps[0], transition)
    require(stamps[0] <= start and all(r['words'][39]&1 for r, t in zip(rows, stamps) if t >= start),
            'Incomplete measurement or DP link loss')
    stable = [row for row, stamp in zip(rows, stamps) if restore-2e9 <= stamp <= restore]
    require(len(stable) >= 30 and all(not r['words'][18]&1 and r['words'][39]&1 for r in stable), 'Missing stable no-input tail')
    index = read(source.with_name(source.name+'_download_index.json'))
    require(index['run_id'] == launch['run_id'] and index['all_copy_hashes_matched'], 'Wrong source download')
    verify_copy(source, validate_inventory(index))
    worker, parent = read(source/'worker/report.json'), read(source/'report.json')
    require(worker == stop['snapshot']['worker'] and parent == stop['snapshot']['supervisor'], 'Copied reports differ from stop proof')
    frame_path = source/'worker/frames.jsonl'
    require(hashlib.sha256(frame_path.read_bytes()).hexdigest() == worker['source_trace_sha256'], 'Changed source trace')
    frames = [json.loads(line) for line in frame_path.read_text().splitlines() if line.strip()]
    require(len(frames) == worker['source_trace_records'], 'Wrong trace count')
    events = bind_events(rows, frames, stamps=stamps, ready_after=start)
    for tag, expected in (('before', '00000000'), ('loaded', '26101201'), ('end', '26101201'), ('restored', '00000000')):
        properties(folder/'board'/('device_'+tag+'.rpt'), expected)
    pixel = pixels(source/'worker', resolve_payload(c, ROOT), c['package_manifest_sha256'],
                   read(source/'worker/pixels.json'), read(source/'worker/video/pixels.json'))
    source_ok = parent['worker_exit_code'] == 0 and not parent['errors'] and not worker['errors']
    return dict(session_audit_passed=bool(pixel['passed'] and source_ok), source_recovery_verified=True,
        configuration_recovery_verified=True, raw_sha256=hashlib.sha256(raw).hexdigest(), capture=meta,
        complete_candidate_records=len(rows), source_frames=len(frames), displayed_new_results=len(events),
        events=events, pixel_audit=pixel, physical_recovery_verified=False,
        interaction_acceptance_pending=True, n5_passed=False, n6_passed=False, formal_release=False)


if __name__ == '__main__':
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('run', type=Path)
    p.add_argument('source', type=Path)
    p.add_argument('--out', type=Path, required=True)
    a = p.parse_args()
    value = audit(a.run, a.source)
    with a.out.open('x', encoding='utf-8') as stream:
        json.dump(value, stream, indent=2)
    print(json.dumps({k:v for k, v in value.items() if k not in ('events', 'pixel_audit')}))
