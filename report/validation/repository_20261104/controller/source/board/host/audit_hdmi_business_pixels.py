"""Recompute pixel comparisons from archived PNGs and a verified package.

This audits algorithm evidence only. It cannot authorize HDMI activation.
"""
import hashlib
import json
from pathlib import Path

import numpy as np
from PIL import Image


def safe_path(root, relative):
    if not isinstance(relative, str) or not relative or '\\' in relative:
        raise ValueError('A nonempty POSIX relative evidence path is required')
    rel = Path(relative)
    if rel.is_absolute() or '..' in rel.parts:
        raise ValueError('Evidence path escapes its directory')
    path = (root / rel).resolve(strict=True)
    path.relative_to(root.resolve())
    if not path.is_file():
        raise ValueError('Evidence must be a regular file')
    return path


def digest(path):
    result = hashlib.sha256()
    with path.open('rb') as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b''):
            result.update(chunk)
    return result.hexdigest()


def rgb(path):
    with Image.open(path) as image:
        if image.mode != 'RGB' or max(image.size) > 4096:
            raise ValueError('Expected bounded original RGB image')
        return np.array(image)


def compare_png(path, expected):
    actual = rgb(path)
    if actual.shape != expected.shape:
        raise ValueError('Original output geometry differs from golden geometry')
    return int(np.count_nonzero(np.any(actual != expected, axis=2)))


def audit(root, package_root, expected_manifest_sha256, images, videos):
    result = {'kind': 'hdmi_decoded_pixel_audit', 'checks': {}, 'errors': [],
              'decoded_images': [], 'decoded_video_frames': [], 'formal_release': False}
    checks = result['checks']
    checks.update(package_verified=False, image_scope=False, video_scope=False,
                  image_pixels_recomputed=False, video_pixels_recomputed=False)
    try:
        manifest_path = safe_path(package_root, 'hdmi_business_package.json')
        if digest(manifest_path) != expected_manifest_sha256:
            raise ValueError('Package manifest identity differs from archived manifest')
        manifest = json.loads(manifest_path.read_text())
        inventory = manifest['files']
        if not inventory or len({i['path'] for i in inventory}) != len(inventory):
            raise ValueError('Invalid package inventory')
        for item in inventory:
            if digest(safe_path(package_root, item['path'])) != item['sha256']:
                raise ValueError('Package file mismatch: ' + item['path'])
        if set(manifest['overlays']) != {'2', '4'}:
            raise ValueError('Wrong overlay contract')
        indexed = {i['path']: i['sha256'] for i in inventory}
        for overlay in manifest['overlays'].values():
            for kind in ('bit', 'hwh'):
                if indexed.get(overlay[kind]) != overlay[kind + '_sha256']:
                    raise ValueError('Overlay identity differs from package inventory')
        material = manifest['materials']
        required = list(material['golden'].values()) + [material['images'][0], material['video_golden'], material['clip']]
        required += [str(Path(material['clip']).parent / 'frames.rgb').replace('\\', '/')]
        if any(path not in indexed for path in required):
            raise ValueError('Material omitted from verified package inventory')
        checks['package_verified'] = True
        source = rgb(safe_path(package_root, material['images'][0]))
        h, w = source.shape[:2]
        rois = {'whole': (0, 0, w, h), 'odd': (17, 19, 31, 27),
                'top_left': (0, 0, 1, 1), 'top_right': (w-1, 0, 1, 1),
                'bottom_left': (0, h-1, 1, 1), 'bottom_right': (w-1, h-1, 1, 1)}
        expected_keys = {(s, case) for s in (2, 4) for case in rois}
        actual_keys = [(i['scale'], i['case']) for i in images['checks']]
        checks['image_scope'] = (set(actual_keys) == expected_keys and len(actual_keys) == 12
                                 and images.get('complete') is True)
        goldens = {s: rgb(safe_path(package_root, material['golden'][str(s)])) for s in (2, 4)}
        for item in images['checks']:
            scale, case = item['scale'], item['case']
            x, y, width, height = rois[case]
            if item['roi'] != dict(x=x, y=y, width=width, height=height):
                raise ValueError('Image ROI differs from defined case')
            expected = goldens[scale][y*scale:(y+height)*scale, x*scale:(x+width)*scale]
            path = safe_path(root, item['path'])
            difference = compare_png(path, expected)
            result['decoded_images'].append({'scale': scale, 'case': case, 'different_pixels': difference})
            if difference or digest(path) != item['sha256']:
                raise ValueError('Image pixel or file identity mismatch')
        checks['image_pixels_recomputed'] = checks['image_scope']

        golden_video = json.loads(safe_path(package_root, material['video_golden']).read_text())
        expected_keys = {(s, i) for s in (2, 4) for i in range(30)}
        actual_keys = [(i['scale'], i['source_index']) for i in videos['checks']]
        checks['video_scope'] = (set(actual_keys) == expected_keys and len(actual_keys) == 60
                                 and videos.get('complete') is True)
        clip_path = safe_path(package_root, material['clip'])
        clip = json.loads(clip_path.read_text())
        raw_path = safe_path(clip_path.parent, 'frames.rgb')
        frame_bytes = clip['width'] * clip['height'] * 3
        if raw_path.stat().st_size != frame_bytes * clip['frame_count'] or digest(raw_path) != clip['frames_sha256']:
            raise ValueError('Clip raw data identity mismatch')
        with raw_path.open('rb') as stream:
            for item in videos['checks']:
                scale, index = item['scale'], item['source_index']
                refs = [r for r in golden_video[str(scale)] if r['source_index'] == index]
                if len(refs) != 1 or type(index) is not int or not 0 <= index < clip['frame_count']:
                    raise ValueError('Invalid or duplicate video golden entry')
                ref = refs[0]
                stream.seek(index * frame_bytes)
                input_sha = hashlib.sha256(stream.read(frame_bytes)).hexdigest()
                path = safe_path(root / 'video', item['path'])
                output = rgb(path)
                if output.shape != (clip['height'] * scale, clip['width'] * scale, 3):
                    raise ValueError('Video output geometry mismatch')
                output_sha = hashlib.sha256(output.tobytes()).hexdigest()
                passed = (input_sha == ref['source_rgb_sha256'] == item['source_rgb_sha256']
                          and output_sha == ref['output_rgb_sha256'] == item['output_rgb_sha256']
                          and digest(path) == item['png_sha256'])
                result['decoded_video_frames'].append({'scale': scale, 'source_index': index,
                    'output_rgb_sha256': output_sha, 'passed': passed})
                if not passed:
                    raise ValueError('Decoded video pixels differ from bound golden reference')
        checks['video_pixels_recomputed'] = checks['video_scope']
    except (KeyError, TypeError, ValueError, OSError) as exc:
        result['errors'].append(type(exc).__name__ + ': ' + str(exc))
    result['passed'] = all(checks.values()) and not result['errors']
    return result
