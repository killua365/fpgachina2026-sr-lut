"""Read only complete recent SRB4 records; final audit rechecks the full capture."""
import argparse, json
from pathlib import Path
from decode_bridge_diagnostic import decode, MAGIC, FRAME_BYTES

def inspect(raw):
    first = raw.find(MAGIC)
    if first < 0: return dict(available=False)
    end = first + ((len(raw)-first)//FRAME_BYTES)*FRAME_BYTES
    start = max(first, end-40*FRAME_BYTES)
    parsed = decode(raw[start:end])
    if parsed['errors'] or parsed['skipped_bytes']: raise ValueError('Damaged recent telemetry')
    if not parsed['frames']: return dict(available=False)
    rows = parsed['frames']
    if any(b['sequence'] != (a['sequence']+1)&0xffffffff for a,b in zip(rows, rows[1:])):
        raise ValueError('Recent sequence gap')
    w = rows[-1]['words']
    if (w[0],w[1]) != (0x26100901,0x4e344231): raise ValueError('Wrong live identity')
    return dict(available=True, sequence=rows[-1]['sequence'], error_flags=w[2],
                source_present=bool(w[18]&1), flushing=bool(w[18]&2), loss_events=w[19],
                overflow_events=w[20], accepted=w[21], rejected=w[22], damaged=w[23],
                rx_frames=w[24], rx_good=w[25], rx_bad=w[26],
                geometry=[w[27],w[28],w[29]], epoch=w[12], written=w[4], scanned=w[6],
                link_ready=bool(w[39]&1), root_seconds=(w[30]+(w[31]<<32))/1e8)

if __name__ == '__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('run',type=Path);a=p.parse_args()
    print(json.dumps(inspect((a.run/'capture/capture.bin').read_bytes())))
