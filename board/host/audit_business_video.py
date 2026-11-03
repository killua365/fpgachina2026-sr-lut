"""Read archived import/export artifacts without running the FPGA or trusting flags.

The source collector and caller bind this directory to a run. Encoded geometry
and input frame hashes can be recomputed; discarded pre-encode RGB and the
original external video cannot. Those quality checks remain explicitly pending.
"""
import hashlib
import json
import math
from fractions import Fraction
from pathlib import PurePosixPath
import re
import subprocess

from audit_hdmi_business_pixels import digest, safe_path
from business_session_remote import require


def read(path):
    return json.loads(path.read_text('utf-8-sig'))


def valid_sha(value):
    return isinstance(value, str) and re.fullmatch('[0-9a-f]{64}', value) is not None


def clip_info(path, expected_size=None):
    value = read(path)
    require(value.get('kind') == 'hdmi_rgb_clip' and value.get('schema_version') == 1
            and value.get('pixel_format') == 'rgb24', 'Unknown RGB clip format')
    w, h, n = (value[k] for k in ('width', 'height', 'frame_count'))
    require(all(type(v) is int for v in (w, h, n)) and 32 <= w <= 480 and 32 <= h <= 320 and n > 0,
            'Invalid clip dimensions or frame count')
    if expected_size is not None:
        require([w, h] == list(expected_size), 'Clip differs from session input geometry')
    numerator, denominator = value['fps_numerator'], value['fps_denominator']
    require(type(numerator) is int and type(denominator) is int and numerator > 0 and denominator > 0,
            'Invalid clip frame rate')
    rate = Fraction(numerator, denominator)
    require(rate <= 30, 'Clip exceeds supported frame rate')
    raw = safe_path(path.parent, 'frames.rgb')
    require(raw.stat().st_size == w*h*3*n and digest(raw) == value['frames_sha256'],
            'Changed imported RGB content')
    rect = value['active_rect']
    require(set(rect) == {'x', 'y', 'width', 'height'} and all(type(v) is int for v in rect.values())
            and rect['x'] >= 0 and rect['y'] >= 0 and rect['width'] > 0 and rect['height'] > 0
            and rect['x']+rect['width'] <= w and rect['y']+rect['height'] <= h,
            'Invalid active image rectangle')
    require(valid_sha(value['source_sha256']), 'Missing original source identity')
    return value, raw, rate


def probe_movie(path, frame_count, rate, ffprobe):
    # Small metadata output only; -count_frames actually decodes the video stream.
    result = subprocess.run([ffprobe, '-v', 'error', '-select_streams', 'v:0', '-count_frames',
        '-show_entries', 'stream=nb_read_frames,width,height,codec_name,pix_fmt,avg_frame_rate',
        '-of', 'json', str(path)], capture_output=True, text=True,
        timeout=max(30, frame_count/float(rate)*10), check=True)
    require(not result.stderr.strip(), 'Encoded video produced decoder errors')
    streams = json.loads(result.stdout)['streams']
    require(len(streams) == 1, 'Missing encoded video stream')
    return streams[0]


def audit_video(source, launch, package, *, ffprobe='ffprobe', ffmpeg='ffmpeg'):
    manifest = read(package/'hdmi_business_package.json')
    require(digest(package/'hdmi_business_package.json') == launch['package_manifest_sha256'],
            'Video package identity differs')
    worker = source/'worker'
    imports, exports, clips = [], [], []
    size = manifest.get('business_profile', {}).get('input_size')
    paths = sorted(worker.glob('clip_*/clip.json'))
    # A packaged demo need only be opened when an export names it. Older source
    # runs without any new media artifacts remain readable.
    if list(worker.glob('video_*/export.json')):
        paths.insert(0, safe_path(package, manifest['materials']['clip']))
    for path in paths:
        value, raw, rate = clip_info(path, size)
        imported = path.is_relative_to(worker)
        clips.append((value, raw, rate, path, imported))
        if imported:
            probe = value.get('source_probe', {})
            require(probe.get('codec') in ('h264', 'mjpeg'), 'Unsupported declared imported codec')
            imports.append(dict(path=path.relative_to(source).as_posix(), codec=probe['codec'],
                frames=value['frame_count'], frames_sha256=value['frames_sha256'],
                source_sha256=value['source_sha256'], archived_rgb_verified=True,
                original_decode_recomputed=False, displayed_on_board_verified=False))
    for path in sorted(worker.glob('video_*/export.json')):
        value = read(path)
        require(value.get('kind') == 'hdmi_video_export' and value.get('schema_version') == 1,
                'Unknown video export format')
        scale = value['scale']
        require(type(scale) is int and scale in (2, 4), 'Invalid video export scale')
        identity, overlay = value['processing_identity'], manifest['overlays'][str(scale)]
        require(identity.get('backend') == 'fpga' and identity.get('package_sha256') == launch['package_manifest_sha256']
                and all(identity.get(k+'_sha256') == overlay[k+'_sha256'] for k in ('bit', 'hwh')),
                'Export backend or hardware/package identity differs')
        matches = [c for c in clips if c[0] == value['input_clip']]
        require(bool(matches), 'Export input is absent from this run and its package')
        clip, raw, rate, clip_path, imported = matches[0]
        count, w, h = clip['frame_count'], clip['width']*scale, clip['height']*scale
        require(type(value['frame_count']) is int and value['frame_count'] == count and
                (value['output_width'], value['output_height']) == (w, h), 'Wrong export geometry or count')
        movie, frames = safe_path(path.parent, 'result.mp4'), safe_path(path.parent, 'frames.jsonl')
        require(digest(movie) == value['result_mp4_sha256'] and digest(frames) == value['frame_log_sha256'],
                'Changed exported video or frame log')
        with raw.open('rb') as pixels, frames.open(encoding='utf-8') as log:
            for index in range(count):
                row = json.loads(log.readline())
                require(type(row['index']) is int and row['index'] == index and
                        type(row['time']) in (int, float) and math.isfinite(row['time']) and
                        abs(row['time']-index/float(rate)) < 1e-7, 'Export frame order/time mismatch')
                require(row['input_rgb_sha256'] == hashlib.sha256(pixels.read(clip['width']*clip['height']*3)).hexdigest(),
                        'Export frame belongs to a different input clip')
                require(valid_sha(row['output_rgb_sha256']), 'Invalid pre-encode frame identity')
            require(not log.read().strip(), 'Extra export frame records')
        require(valid_sha(value['processed_rgb_sha256']), 'Invalid pre-encode stream identity')
        retained = value.get('raw_output_retained', False)
        require(type(retained) is bool, 'Invalid raw retention declaration')
        pixels_result = None
        if retained:
            from audit_video_pixels import measure
            output = safe_path(path.parent, 'output.rgb')
            require(output.stat().st_size == count*w*h*3 and digest(output) == value['processed_rgb_sha256'],
                    'Changed retained pre-encode RGB')
            with output.open('rb') as stream, frames.open(encoding='utf-8') as log:
                for line in log:
                    row = json.loads(line)
                    require(hashlib.sha256(stream.read(w*h*3)).hexdigest() == row['output_rgb_sha256'],
                            'Retained output frame differs from its log')
            pixels_result = measure(raw, output, movie, clip, scale, ffmpeg=ffmpeg)
        else:
            require(not (path.parent/'output.rgb').exists(), 'Undeclared raw video artifact')
        info = probe_movie(movie, count, rate, ffprobe)
        require((int(info['nb_read_frames']), info['width'], info['height'], info['codec_name'], info['pix_fmt']) ==
                (count, w, h, 'h264', 'yuv420p') and Fraction(info['avg_frame_rate']) == rate,
                'Encoded stream differs from clip timeline or geometry')
        require(all(str(value['encoded_stream'].get(k)) == str(info[k]) for k in
                    ('nb_read_frames', 'width', 'height', 'codec_name', 'pix_fmt')), 'Recorded stream metadata differs')
        exports.append(dict(path=path.relative_to(source).as_posix(), scale=scale, frames=count,
            input_kind='imported' if imported else 'packaged', input_rgb_verified=True,
            input_frames_sha256=clip['frames_sha256'], encoded_stream_verified=True,
            preencode_rgb_recomputed=retained, sr_golden_exact=pixels_result['sr_golden_exact'] if retained else None,
            pixel_audit=pixels_result, encoded_quality_passed=None,
            displayed_on_board_verified=False))
    # Samples may omit the final sub-second operation. Discover artifacts first;
    # reject a reported success whose result was not actually archived.
    status = read(worker/'report.json').get('runtime', {})
    known = {item['path'] for item in imports+exports}
    for kind, filename in (('video_import', 'clip.json'), ('video_export', 'export.json')):
        records = status.get(kind+'_history', []) + [status.get(kind, {})]
        for record in records:
            if record.get('state') == 'complete':
                relative = PurePosixPath(record['destination']).relative_to(launch['remote_results'])/filename
                require(relative.as_posix() in known, 'Completed media operation missing from archived run')
    return dict(imports=imports, exports=exports, import_count=len(imports), export_count=len(exports),
                imports_original_decode_pending=bool(imports),
                export_raw_pending=any(not e['preencode_rgb_recomputed'] for e in exports),
                export_raw_and_quality_pending=bool(exports),
                artifact_checks_passed=all(e['sr_golden_exact'] is not False for e in exports),
                progress_cancel_failure_observation_pending=True, n5_passed=False)
