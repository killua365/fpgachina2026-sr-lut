"""SRF3 N3 telemetry. Physical observations and recovery are separate evidence."""
import argparse,binascii,json,struct
from pathlib import Path
MAGIC=b'SRF3'
FRAME_BYTES=124
UI_HZ=100_000_000
COUNTERS={3:'source',4:'written',5:'read',6:'scanned',7:'new',8:'repeated',9:'dropped',13:'write_beats',14:'read_beats'}


def decode(raw):
    frames=[];errors=[];pos=0;skipped=0
    while pos<len(raw):
        index=raw.find(MAGIC,pos)
        if index<0:skipped+=len(raw)-pos;break
        skipped+=index-pos
        if len(raw)-index<FRAME_BYTES:errors.append(dict(offset=index,error='truncated'));break
        frame=raw[index:index+FRAME_BYTES]
        if frame[4:6]!=bytes((1,112)):
            errors.append(dict(offset=index,error='version_or_length'));pos=index+4;continue
        if binascii.crc_hqx(frame[:122],0xffff)!=int.from_bytes(frame[122:124],'little'):
            errors.append(dict(offset=index,error='crc'));pos=index+4;continue
        frames.append(dict(offset=index,sequence=int.from_bytes(frame[6:10],'little'),
                           words=list(struct.unpack('<28I',frame[10:122]))))
        pos=index+FRAME_BYTES
    return dict(frames=frames,errors=errors,skipped_bytes=skipped)


def u64(words,index):return words[index]|(words[index+1]<<32)


def assess(parsed,candidate=0x26100502,minimum_seconds=119.0):
    reasons=[];rows=parsed['frames'];prior=None;increments={name:0 for name in COUNTERS.values()}
    if parsed['errors']:reasons.append('corrupt_or_truncated_frames')
    if parsed['skipped_bytes']:reasons.append('unframed_bytes')
    if len(rows)<3:reasons.append('insufficient_records')
    for row in rows:
        w=row['words']
        if w[0]!=candidate or w[1]!=0x4e334631:reasons.append('identity_or_schema_mismatch')
        if w[2]!=0:reasons.append('frame_pipeline_error')
        if w[23]&0xff!=0x3b:reasons.append('clock_reset_or_calibration_invalid')
        if w[21]>=10_000_000 or not w[20]:reasons.append('stale_ui_snapshot')
        if not w[27]&1:reasons.append('physical_link_not_ready')
        if (w[7]+w[8])&0xffffffff!=w[6]:reasons.append('scan_accounting_inconsistent')
        if prior is not None:
            p=prior['words']
            if row['sequence']!=(prior['sequence']+1)&0xffffffff:reasons.append('sequence_discontinuity')
            dt=u64(w,16)-u64(p,16)
            if dt<=0 or u64(w,18)<=u64(p,18):reasons.append('time_not_increasing')
            if not 0<((w[20]-p[20])&0xffffffff)<100:reasons.append('snapshot_sequence_invalid')
            if w[12]!=p[12]:reasons.append('generation_changed')
            if w[22]!=p[22]:reasons.append('reset_event_changed')
            for index,name in COUNTERS.items():
                delta=(w[index]-p[index])&0xffffffff
                # Counter wrap is allowed only when bounded elapsed time proves
                # that at most one wrap could occur. Implausible regressions fail.
                limit=(max(dt,0)+1) if index in (13,14) else (max(dt,0)*100//UI_HZ+2)
                if delta>limit:reasons.append('counter_rate_or_regression_'+name)
                increments[name]+=delta
        prior=row
    duration=(u64(rows[-1]['words'],16)-u64(rows[0]['words'],16))/UI_HZ if len(rows)>=2 else 0
    rates={key:value/duration for key,value in increments.items()} if duration>0 else {}
    if duration<minimum_seconds:reasons.append('insufficient_valid_duration')
    if duration>0:
        for name,low,high in [('source',29.5,30.5),('written',29.5,30.5),('read',59,61),('scanned',59,61),('new',29.5,30.5)]:
            if not low<=rates[name]<=high:reasons.append('rate_outside_n3_contract_'+name)
        if increments['dropped']:reasons.append('stable_input_frame_drops')
        # Count every accepted memory data beat, including overlap, independently
        # of the complete-frame counters. This is effective payload throughput.
        throughput=16*(rates['write_beats']+rates['read_beats'])
        if not 0.98*746496000<=throughput<=1.02*746496000:reasons.append('payload_throughput_outside_contract')
        root_duration=(u64(rows[-1]['words'],18)-u64(rows[0]['words'],18))/UI_HZ
        if abs(root_duration-duration)>max(.02,.001*duration):reasons.append('clock_timebase_disagreement')
    else:throughput=0
    max_latency=max((r['words'][10] for r in rows),default=0)/100000
    return dict(telemetry_passed=not reasons,n3_passed=False,board_recovery_verified=False,
        reasons=sorted(set(reasons)),records=len(rows),ui_seconds=duration,increments=increments,rates=rates,
        payload_bytes_per_second=throughput,max_new_frame_latency_ms=max_latency,
        latency_p95_upper_bound_ms=max_latency if max_latency>0 else None,
        latency_100ms_target=0<max_latency<=100,
        error_counter_ber_valid=False)


def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('raw',type=Path)
    p.add_argument('--out',type=Path,required=True);p.add_argument('--candidate',type=lambda x:int(x,16),default=0x26100502)
    a=p.parse_args();parsed=decode(a.raw.read_bytes());result=assess(parsed,a.candidate)
    with a.out.open('x',encoding='utf-8') as f:json.dump(dict(summary=result,decoded=parsed),f,indent=2)
    print(json.dumps(result))
if __name__=='__main__':main()
