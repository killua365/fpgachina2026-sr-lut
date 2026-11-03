"""Recent SRB5 status for supervision; complete raw data remains for final audit."""
from decode_business_diagnostic import decode, MAGIC, FRAME_BYTES


def initializing(w):
    s = w[40:]
    return not w[2] and (not any(s) or (s[:2] == [0x53524944, 1] and not any(s[2:]) and
           (w[35] != 0x3b or not w[32] or w[33] >= 10_000_000)))


def inspect(raw):
    first = raw.find(MAGIC)
    if first < 0:
        return dict(available=False)
    if len(raw)-first > 42*FRAME_BYTES:
        # Re-anchor a bounded recent window after transport byte loss. The
        # monitor retains the original fault and the complete-session auditor
        # still rejects every damaged byte. This only lets a later clean tail
        # prove that the source has stopped, without a five-minute timeout.
        candidate = raw.find(MAGIC, len(raw)-42*FRAME_BYTES)
        while candidate >= 0 and candidate+FRAME_BYTES <= len(raw):
            anchor = decode(raw[candidate:candidate+FRAME_BYTES])
            if len(anchor['frames']) == 1 and not anchor['errors'] and not anchor['skipped_bytes']:
                first = candidate
                break
            candidate = raw.find(MAGIC, candidate+1)
        else:
            raise ValueError('Damaged recent business telemetry')
        if len(raw)-first < 40*FRAME_BYTES:
            raise ValueError('Insufficient clean recent business telemetry')
    end = first+(len(raw)-first)//FRAME_BYTES*FRAME_BYTES
    parsed = decode(raw[max(first, end-40*FRAME_BYTES):end])
    if parsed['errors'] or parsed['skipped_bytes']:
        raise ValueError('Damaged recent business telemetry')
    rows = parsed['frames']
    if not rows:
        return dict(available=False)
    if any(b['sequence'] != (a['sequence']+1)&0xffffffff for a, b in zip(rows, rows[1:])):
        raise ValueError('Business telemetry sequence gap')
    w = rows[-1]['words']
    if (w[0], w[1]) == (0x26101201, 0x4e344231) and initializing(w) and (w[30]+(w[31]<<32)) < 1_000_000_000:
        return dict(available=False, initializing=True)
    if ((w[0], w[1], w[40]) != (0x26101201, 0x4e344231, 0x53524944) or w[41] not in (1, 9) or
            w[35] != 0x3b or not w[32] or w[33] >= 10_000_000):
        raise ValueError('Wrong candidate/identity observer fault')
    return dict(available=True, sequence=rows[-1]['sequence'], error_flags=w[2],
                source_present=bool(w[18]&1), flushing=bool(w[18]&2), loss_events=w[19],
                overflow_events=w[20], accepted=w[21], rejected=w[22], damaged=w[23],
                rx_frames=w[24], rx_good=w[25], rx_bad=w[26], geometry=w[27:30],
                epoch=w[12], written=w[4], scanned=w[6], link_ready=bool(w[39]&1),
                root_seconds=(w[30]+(w[31]<<32))/1e8, generation=w[53], source_sequence=w[54],
                mode=w[55]&255, scale=w[55]>>8, new_results=w[47], repeat_results=w[48])
