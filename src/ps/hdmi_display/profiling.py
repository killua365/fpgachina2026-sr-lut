"""Bounded, explicitly automatic business profiling, not physical controls QA."""
import json
import math
from pathlib import Path
import time

import cv2
import numpy as np
from PIL import Image

from .compositor import Composition
from .controls import ControlEvent
from .diagnostics import scan_delta
from .media import sha256_file


class ProfileControls:
    def poll_engine(self, engine):
        return None

    def status(self):
        return {'authority': 'automatic_profile', 'physical_matrix_acceptance': False}


def evaluate(samples, seconds, *, expected_refresh_hz=60):
    if expected_refresh_hz not in (30, 60):
        raise ValueError('Unsupported HDMI refresh rate')
    reasons = []
    if len(samples) < 2:
        return {'passed': False, 'failure_reasons': ['insufficient_samples'], 'new_frame_fps': None}
    first, last = samples[0], samples[-1]
    elapsed = last['monotonic'] - first['monotonic']
    display0, display1 = first['display'], last['display']
    delta = display1['acknowledged_source_pairs'] - display0['acknowledged_source_pairs']
    if elapsed <= 0 or elapsed < seconds:
        reasons.append('incomplete_interval')
    if delta < 0:
        reasons.append('ack_counter_reset')
    identities = {(s['runtime']['mode'], s['runtime']['scale'], s['runtime']['layout'],
                   s['runtime']['generation']) for s in samples}
    if len(identities) != 1:
        reasons.append('mode_or_generation_changed')
    for a, b in zip(samples, samples[1:]):
        if b['monotonic'] <= a['monotonic'] or b['display']['acknowledged_source_pairs'] < a['display']['acknowledged_source_pairs']:
            reasons.append('nonmonotonic_samples')
            break
    if any(not s['display']['running'] or not s['display']['locked'] or
           s['display']['counters_saturated'] or s['display']['dma_status'] & 0x71 for s in samples):
        reasons.append('display_fault')
    for field in ('underflow_events', 'overflow_events'):
        if any(s['display'][field] != display0[field] for s in samples):
            reasons.append(field + '_changed')
    fps = delta / elapsed if elapsed > 0 else None
    refresh = scan_delta(display0['scan_frames'], display1['scan_frames']) / elapsed if elapsed > 0 else None
    if fps is None or fps < 20:
        reasons.append('below_20_fps')
    if refresh is None or abs(refresh - expected_refresh_hz) > 5:
        reasons.append('refresh_out_of_range')
    if any(s['runtime'].get('last_error') for s in samples):
        reasons.append('runtime_error')
    return {'passed': not reasons, 'failure_reasons': reasons, 'elapsed_seconds': elapsed,
            'new_frame_fps': fps, 'acknowledged_new_pairs': delta, 'refresh_hz': refresh,
            'scope': 'VDMA acknowledgements, not physical monitor proof',
            'nexys_new_display_fps': None, 'n6_passed': False,
            'formal_release': False}


def run_profiles(runtime, clip, destination, identity, seconds, cancel, progress):
    if not math.isfinite(seconds) or not 10 <= seconds <= 300:
        raise ValueError('Profile duration must be 10..300 seconds per case')
    destination = Path(destination)
    destination.mkdir(exist_ok=False)
    report = {'kind': 'hdmi_business_profile', **identity, 'formal_release': False,
              'business_profile': runtime.profile.metadata(), 'n6_passed': False,
              'physical_matrix_acceptance': False, 'requested_seconds_per_case': seconds,
              'cases': []}
    original_controls = runtime.controls
    runtime.controls = ProfileControls()
    def save():
        (destination / 'report.json').write_text(json.dumps(report, indent=2, allow_nan=False) + '\n')
    def sample():
        return {'monotonic': time.monotonic(), 'runtime': runtime.status(),
                'display': runtime.engine.display.status()}
    try:
        runtime.load_clip(clip)
        runtime.playback.set_loop(True)
        for mode, scale in (('video', 2), ('video', 4), ('camera', 4)):
            for layout in ('sr', 'split'):
                name = '{}_x{}_{}'.format(mode, scale, layout)
                progress('automatic_profile_' + name)
                runtime._control_event(ControlEvent(mode, int(scale == 4), True))
                runtime.set_composition(Composition(layout=layout))
                case = {'name': name, 'mode': mode, 'scale': scale, 'layout': layout,
                        'input_size': list(runtime.profile.input_size),
                        'sr_size': list(runtime.profile.sr_size(scale)),
                        'display_mode': runtime.engine.display_mode.name,
                        'dp_mode': runtime.profile.dp.name if runtime.profile.dp else None,
                        'source_fps': float(clip.fps) if mode == 'video' else 30,
                        'source_fps_is_requested': mode == 'camera',
                        'startup': runtime.engine.display.status()}
                case_dir = destination / name
                case_dir.mkdir()
                def decorate(pixels):
                    cv2.putText(pixels, 'AUTO PROFILE | {} | x{} | {} | {}'.format(
                                mode, scale, layout, runtime.engine.display_mode.name),
                                (18, 27), cv2.FONT_HERSHEY_SIMPLEX, 0.65, (255, 255, 90, 0), 1)
                warmup = time.monotonic()
                initial_count = runtime.submitted_new_frames
                while time.monotonic() - warmup < 3 or runtime.submitted_new_frames < initial_count + 2:
                    if cancel.is_set():
                        raise InterruptedError('Profile cancelled')
                    runtime.tick(decorate=decorate)
                    if time.monotonic() - warmup > (runtime.camera.open_timeout + 2 if mode == 'camera' else 12):
                        break
                    time.sleep(0.001)
                samples = [sample()]
                started = samples[0]['monotonic']
                # Hardware switching/warmup and PNG writes are outside this interval.
                with (case_dir / 'samples.jsonl').open('x') as stream, (case_dir / 'frames.jsonl').open('x') as frames:
                    stream.write(json.dumps(samples[0]) + '\n')
                    while time.monotonic() - started < seconds:
                        if cancel.is_set():
                            raise InterruptedError('Profile cancelled')
                        new = runtime.tick(decorate=decorate)
                        now = time.monotonic()
                        if new:
                            pair = runtime.last_pair
                            frames.write(json.dumps({'monotonic': now, 'generation': pair.token.generation,
                                'sequence': pair.token.sequence, 'source_index': runtime.last_source_index,
                                'timings': runtime.engine.last_timings}) + '\n')
                        if now - samples[-1]['monotonic'] >= 1:
                            samples.append(sample())
                            stream.write(json.dumps(samples[-1]) + '\n')
                            stream.flush()
                        time.sleep(0.001)
                    samples.append(sample())
                    stream.write(json.dumps(samples[-1]) + '\n')
                case.update(evaluate(samples, seconds, expected_refresh_hz=runtime.engine.display_mode.refresh_hz))
                case['samples_sha256'] = sha256_file(case_dir / 'samples.jsonl')
                case['frames_sha256'] = sha256_file(case_dir / 'frames.jsonl')
                case['artifacts'] = []
                pair = runtime.last_pair
                if pair is not None:
                    for label, image in (('input', pair.source_rgb), ('output', pair.output_rgb)):
                        path = case_dir / (label + '.png')
                        Image.fromarray(image).save(path)
                        case['artifacts'].append({'path': path.name, 'sha256': sha256_file(path)})
                    display_mode = runtime.engine.display_mode
                    canvas = np.empty((display_mode.height, display_mode.width, 4), np.uint8)
                    runtime.engine.compositor.compose(pair, canvas, runtime.composition)
                    decorate(canvas)
                    path = case_dir / 'composition.png'
                    Image.fromarray(canvas[..., :3]).save(path)
                    case['artifacts'].append({'path': path.name, 'sha256': sha256_file(path),
                        'scope': 'reconstructed composition of final pair; not a photograph or scan capture'})
                report['cases'].append(case)
                save()
                progress('profile_result_{}_{:.2f}fps'.format(name, case['new_frame_fps'] or 0))
    finally:
        runtime.camera.stop()
        runtime.controls = original_controls
        save()
    return report
