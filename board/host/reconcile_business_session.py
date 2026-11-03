"""Recompute one N5 session and its PNG exports; keep unobserved features pending.

This is a local evidence reader. It never deploys, opens a device, or accepts N5
as a whole. Observation templates deliberately contain no affirmative answers.
"""
import argparse
from datetime import datetime
import hashlib
import json
from pathlib import Path
from business_session_gate import resolve_payload
from business_session_gate import resolve_payload, PurePosixPath

import numpy as np

from analyze_business_session import audit, read
from business_session_gate import check as check_tools
from business_session_source import CONTRACT, ROOT
from business_session_remote import require
from audit_hdmi_business_pixels import digest, rgb, safe_path
from audit_business_video import audit_video


VISUAL = ('business_picture_correct', 'business_osd_1920x1080_60',
          'no_unexpected_blackout_or_freeze', 'factory_pattern_restored',
          'factory_osd_1280x1024_60')
INTERACTIONS = ('roi_create_move_resize_reset', 'compare_split_wipe_local',
                'video_pause_seek_step_loop_speed_pan', 'mouse_and_physical_controls',
                'idle_keeps_display', 'camera_x4_and_recovery')
PENDING = {
    'video_import_formats': 'Board-side H.264 8-bit 4:2:0 and MJPEG import evidence is still required.',
    'video_export': 'Pre-encode RGB, encoded quality, progress, cancel and write-failure evidence is still required.',
    'camera_recovery': 'A timed same-run trace must establish recovery within 10 seconds and retry cancellation.',
    'lifecycle': 'Overlay load counts and DMA ownership during each source/scale transition are still required.',
    'onsite_media': 'N4 photo waiver does not establish a waiver or media evidence for N5.',
}


def canonical_sha(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':'),
                                     allow_nan=False).encode()).hexdigest()


def inventory(folder):
    root = folder.resolve(strict=True)
    files = []
    for path in sorted(folder.rglob('*')):
        require(not path.is_symlink(), 'Evidence symlink: '+str(path))
        path.resolve(strict=True).relative_to(root)
        if path.is_file():
            files.append(dict(path=path.relative_to(folder).as_posix(), size=path.stat().st_size,
                              sha256=digest(path)))
    return files


def observation_template(binding):
    return dict(schema='n5_business_observations_v1', binding=binding,
                checks={key: dict(status='pending', authority=None, recorded_at=None, statement='')
                        for key in VISUAL+INTERACTIONS})


def observations(value, binding):
    if value is None:
        return dict(provided=False, visual_complete=False, failed=[],
                    pending=list(VISUAL+INTERACTIONS), observed_passes=[])
    require(value.get('schema') == 'n5_business_observations_v1' and value.get('binding') == binding,
            'Observation belongs to a different session or changed evidence')
    checks = value['checks']
    require(set(checks) == set(VISUAL+INTERACTIONS), 'Unknown or missing observation field')
    passed, failed, pending = [], [], []
    for key, item in checks.items():
        status = item.get('status')
        require(status in ('pass', 'fail', 'pending'), 'Invalid observation status: '+key)
        if status == 'pending':
            pending.append(key)
            continue
        require(item.get('authority') == 'user' and isinstance(item.get('statement'), str)
                and item['statement'].strip(), 'Missing original user observation: '+key)
        stamp = datetime.fromisoformat(item['recorded_at'])
        require(stamp.tzinfo is not None, 'Observation needs an explicit timezone')
        (passed if status == 'pass' else failed).append(key)
    return dict(provided=True, visual_complete=all(key in passed for key in VISUAL),
                observed_passes=passed, failed=failed, pending=pending)


def audit_exports(source, launch, package, events):
    """Compare actual archived PNGs, not the exporter success flag."""
    manifest = read(package/'hdmi_business_package.json')
    require(digest(package/'hdmi_business_package.json') == launch['package_manifest_sha256'],
            'Export package identity differs')
    materials = manifest['materials']
    images = {digest(safe_path(package, p)): p for p in materials['images']}
    golden_source = digest(safe_path(package, materials['images'][0]))
    frames = [json.loads(line) for line in (source/'worker/frames.jsonl').read_text().splitlines() if line.strip()]
    trace = {(f['generation'], f['sequence']): (f['mode'], f['scale']) for f in frames}
    displayed = {(e['generation'], e['source_sequence']) for e in events}
    paths = sorted((source/'worker').glob('image_*/export.json'))
    known = {p.relative_to(source).as_posix() for p in paths}
    # The final status sample can precede a last export by <1 second. Discover
    # all archived exports, while checking every reported path as well.
    for name in read(source/'worker/report.json').get('exports', []):
        relative = PurePosixPath(name).relative_to(launch['remote_results']).as_posix()
        require(relative in known, 'Reported export missing from this source run')
    results = []
    for path in paths:
        export = read(path)
        require(export['kind'] == 'hdmi_image_export' and export['schema_version'] == 1,
                'Unknown PNG export format')
        identity, token = export['source_identity'], export['frame_token']
        scale = export['scale']
        key = token['generation'], token['sequence']
        require(type(scale) is int and scale in (2, 4) and
                all(type(x) is int for x in key) and trace.get(key) == (1, scale),
                'Export identity is absent from the same image submission trace')
        require(identity['manifest_sha256'] == launch['package_manifest_sha256'], 'Foreign export package')
        source_sha = identity['source_sha256']
        require(source_sha in images, 'Export source is not a packaged original image')
        original = rgb(safe_path(package, images[source_sha]))
        roi = identity['roi']
        require(set(roi) == {'x', 'y', 'width', 'height'} and all(type(v) is int for v in roi.values()), 'Invalid ROI')
        x, y, w, h = (roi[k] for k in ('x', 'y', 'width', 'height'))
        require(x >= 0 and y >= 0 and w > 0 and h > 0 and
                x+w <= original.shape[1] and y+h <= original.shape[0], 'ROI outside original image')
        files = export['files']
        require(len(files) == 3 and {f['path'] for f in files} == {'source.png', 'nearest.png', 'sr.png'}
                and export['quality'] is None, 'Unexpected reference or export files')
        arrays = {}
        for item in files:
            image_path = safe_path(path.parent, item['path'])
            array = rgb(image_path)
            require(digest(image_path) == item['sha256'] and
                    hashlib.sha256(array.tobytes()).hexdigest() == item['rgb_sha256'], 'Changed export PNG')
            arrays[item['path']] = array
        crop = original[y:y+h, x:x+w]
        nearest = np.repeat(np.repeat(crop, scale, axis=0), scale, axis=1)
        require(np.array_equal(arrays['source.png'], crop), 'Exported source is not the stated original ROI')
        require(np.array_equal(arrays['nearest.png'], nearest), 'Wrong nearest-neighbour comparison')
        require(arrays['sr.png'].shape == nearest.shape, 'Wrong exported SR geometry')
        golden_checked = source_sha == golden_source
        if golden_checked:
            golden = rgb(safe_path(package, materials['golden'][str(scale)]))
            require(np.array_equal(arrays['sr.png'], golden[y*scale:(y+h)*scale, x*scale:(x+w)*scale]),
                    'Exported SR pixels differ from the frozen golden ROI')
        results.append(dict(path=path.relative_to(source).as_posix(), scale=scale, roi=roi,
                            frame_token=token, source_and_nearest_exact=True,
                            sr_golden_exact=True if golden_checked else None,
                            complete_scan_recorded=key in displayed))
    return dict(exports=results, count=len(results),
                golden_exact_scales=sorted({r['scale'] for r in results if r['sr_golden_exact'] is True}),
                other_source_sr_pending=sum(r['sr_golden_exact'] is None for r in results))


def reconcile(run, source, tools, observation_path=None):
    check_tools(tools)
    run, source, tools = run.resolve(strict=True), source.resolve(strict=True), tools.resolve(strict=True)
    require(run != source and run not in source.parents and source not in run.parents,
            'Run and source evidence must be separate directories')
    before = dict(run=inventory(run), source=inventory(source))
    index_path = source.with_name(source.name+'_download_index.json')
    external = dict(tool_manifest_sha256=digest(tools/'manifest.json'), source_index_sha256=digest(index_path),
                    observation_sha256=digest(observation_path) if observation_path else None)
    independent = audit(run, source)
    launch, contract = read(run/'source_launch.json'), read(CONTRACT)
    binding = dict(run_id=launch['run_id'], candidate='26101201',
                   package_manifest_sha256=launch['package_manifest_sha256'],
                   tool_manifest_sha256=external['tool_manifest_sha256'],
                   raw_sha256=independent['raw_sha256'],
                   source_trace_sha256=digest(source/'worker/frames.jsonl'),
                   inputs_sha256=canonical_sha(before))
    visual = observations(read(observation_path) if observation_path else None, binding)
    exports = audit_exports(source, launch, resolve_payload(contract, ROOT), independent['events'])
    videos = audit_video(source, launch, resolve_payload(contract, ROOT))
    require(before == dict(run=inventory(run), source=inventory(source)), 'Evidence changed during reconciliation')
    require(external == dict(tool_manifest_sha256=digest(tools/'manifest.json'), source_index_sha256=digest(index_path),
                             observation_sha256=digest(observation_path) if observation_path else None),
            'External evidence changed during reconciliation')
    machine = independent['session_audit_passed'] is True and videos['artifact_checks_passed']
    closed = machine and visual['visual_complete'] and not visual['failed']
    return dict(schema='n5_business_reconciliation_v1', binding=binding,
                session_evidence_closed=closed,
                status='closed' if closed else 'pending_observation' if machine and not visual['failed'] else 'failed',
                independent_session_audit=independent, observation_summary=visual, png_exports=exports,
                video_artifacts=videos,
                feature_coverage=dict(image_roi_golden=independent['pixel_audit']['checks']['image_pixels_recomputed'],
                                      video_rgb_golden=independent['pixel_audit']['checks']['video_pixels_recomputed'],
                                      cross_board_identity=machine, png_export_x2_x4=exports['golden_exact_scales'] == [2, 4]),
                remaining_acceptance=PENDING, inputs=before, external_inputs=external,
                reconciler_sha256=digest(Path(__file__)),
                n5_passed=False, n6_passed=False, formal_release=False), observation_template(binding)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ('run', 'source', 'tools', 'out'):
        parser.add_argument('--'+name, type=Path, required=True)
    parser.add_argument('--observations', type=Path)
    args = parser.parse_args()
    out = args.out.resolve()
    for evidence in (args.run, args.source, args.tools):
        require(out != evidence.resolve() and evidence.resolve() not in out.parents,
                'Write reconciliation outside original evidence')
    require(not out.exists(), 'Use a new reconciliation directory')
    report, template = reconcile(args.run, args.source, args.tools, args.observations)
    out.mkdir(parents=True, exist_ok=False)
    for name, value in (('result.json', report), ('observations_template.json', template)):
        (out/name).write_text(json.dumps(value, indent=2, ensure_ascii=False, allow_nan=False)+'\n', encoding='utf-8')
    print(json.dumps({k:report[k] for k in ('status', 'session_evidence_closed', 'feature_coverage', 'n5_passed')}))


if __name__ == '__main__':
    main()
