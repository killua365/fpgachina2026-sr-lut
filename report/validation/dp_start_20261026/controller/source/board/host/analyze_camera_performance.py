"""Count distinct complete Nexys camera scans over fixed hardware-clock windows."""
import argparse
import hashlib
import json
from pathlib import Path
from analyze_business_performance import require, stats
from decode_business_diagnostic import UI_HZ


def assess(campaign, events, clock_hz=UI_HZ):
    require(campaign.get('capture_complete') is True, 'Camera campaign incomplete')
    require(campaign.get('input_size') == [480,270] and campaign.get('sr_size') == [1920,1080],
            'Camera resolution changed')
    golden=campaign.get('golden',{})
    require(golden.get('passed') is True and golden.get('full_x_byte_validation') is True and
            len(golden.get('checks',[])) >= 30 and all(x['output_rgb_sha256'] == x['expected_rgb_sha256']
                for x in golden['checks']), 'Borrowed camera results lack exact golden proof')
    if campaign.get('direct_display'):
        direct=campaign.get('direct_golden',{})
        checks=direct.get('checks',[])
        require(direct.get('passed') is True and direct.get('full_x_byte_validation') is True
            and len(checks)>=30 and {x['slot'] for x in checks}=={0,1,2}
            and all(x['output_rgb_sha256']==x['expected_rgb_sha256'] for x in checks),
            'Direct camera scanout allocations lack exact golden proof')
    if campaign.get('fused_copy_display'):
        copied=campaign.get('copy_golden',{})
        checks=copied.get('checks',[])
        require(copied.get('passed') is True and copied.get('full_x_byte_validation') is True
            and len(checks)>=30 and {x['slot'] for x in checks}=={0,1,2}
            and {x['layout'] for x in checks}=={'sr','split'}
            and all(x['output_rgb_sha256']==x['expected_rgb_sha256']
                    and x.get('exact_display_rgbx_match') is True for x in checks),
            'Checked copy lacks exact full/half-size scanout proof')
    cases=[]
    for case in campaign['cases']:
        generation=case['generation']
        shown=[e for e in events if e['generation']==generation]
        require(all((e['mode'],e['scale'])==(3,4) for e in shown),'Camera event mode/scale changed')
        require(case['input_size']==[480,270] and case['sr_size']==[1920,1080], 'Case geometry changed')
        require(all(b['complete_ticks']>a['complete_ticks'] and b['source_sequence']>a['source_sequence']
                    for a,b in zip(shown,shown[1:])), 'Camera event replay or time regression')
        record=dict(stage=case['stage'],layout=case['layout'],generation=generation,
            source_submitted_fps=case['submitted_pair_fps'],complete_new_scans=len(shown),
            timings=case['timings'],passed=False)
        if len(shown)>1:
            record.update(diagnostic_event_spacing_fps=(len(shown)-1)*clock_hz/
                (shown[-1]['complete_ticks']-shown[0]['complete_ticks']),
                receive_to_complete_ms=stats([e['latency_cycles']*1000/clock_hz for e in shown]))
        if case['stage']=='acceptance_capture':
            usable=[e for e in shown if case['first_sequence']+2<=e['source_sequence']<=case['last_sequence']]
            require(usable and case['requested_seconds']>=310,'Missing window guard frames')
            start=usable[0]['complete_ticks']
            end=start+300*clock_hz
            require(usable[-1]['complete_ticks']>end,'Fewer than 300 seconds of complete camera scans')
            window=[e for e in usable if start<e['complete_ticks']<=end]
            fps=len(window)/300
            record.update(window_start_ticks=start,window_end_ticks=end,window_seconds=300,
                window_new_scans=len(window),nexys_complete_new_fps=fps,passed=fps>=20)
        cases.append(record)
    formal=[c for c in cases if c['stage']=='acceptance_capture']
    passed=(len(formal)==2 and {c['layout'] for c in formal}=={'sr','split'} and all(c['passed'] for c in formal))
    return dict(kind='camera20_complete_scan_acceptance',camera_20fps_passed=passed,cases=cases,
        input_size=[480,270],sr_size=[1920,1080],minimum_fps=20,window_seconds=300,
        clock_policy='Only Nexys hardware ticks define acceptance windows; no cross-board timestamp subtraction',
        n6_all_six_cases_passed=False,formal_release=False)


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--run',type=Path,required=True)
    parser.add_argument('--source',type=Path,required=True)
    parser.add_argument('--out',type=Path,required=True)
    args=parser.parse_args()
    from analyze_business_session import audit, read
    from reconcile_business_session import inventory,canonical_sha
    require(not args.out.exists(),'Preserve previous results')
    for original in (args.run,args.source):
        require(original.resolve()!=args.out.resolve() and original.resolve() not in args.out.resolve().parents,
                'Analysis must not modify original evidence')
    before=dict(run=inventory(args.run),source=inventory(args.source))
    session=audit(args.run,args.source)
    require(session['session_audit_passed'],'Independent session audit failed')
    report=read(args.source/'worker/status.json')
    result=assess(report['camera_performance'],session['events'])
    require(before==dict(run=inventory(args.run),source=inventory(args.source)),'Evidence changed during analysis')
    result.update(run_id=report['run_id'],package_manifest_sha256=report['manifest_sha256'],
        session_independently_recomputed=True,input_inventory_sha256=canonical_sha(before),
        analyzer_sha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest())
    args.out.mkdir(parents=True)
    (args.out/'result.json').write_text(json.dumps(result,indent=2,allow_nan=False)+'\n',encoding='utf-8')
    print(json.dumps(result))


if __name__=='__main__': main()
