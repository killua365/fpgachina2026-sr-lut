"""Supervised HDMI business candidate; no boot enablement or legacy replacement."""
from __future__ import annotations
import argparse
import faulthandler
import hashlib
import json
import os
import re
from pathlib import Path
import signal
import subprocess
import sys
import tempfile
import threading
import time
from hdmi_m1 import root_card, sha256, validate_package


class InteractionBudget:
    """Timed automation or operator-ended work with a separate safety cap."""
    def __init__(self, seconds, end_mode='timed', clock=time.monotonic):
        if end_mode not in ('timed', 'operator', 'demo'):
            raise ValueError('Unknown interaction end mode')
        if end_mode == 'operator' and seconds != 1800:
            raise ValueError('Operator sessions require the 1800-second safety cap')
        self.end_mode, self.clock = end_mode, clock
        if end_mode == 'demo' and seconds is not None:
            raise ValueError('Demo duration must be None')
        self.deadline = None if end_mode == 'demo' else clock() + seconds

    @property
    def display_deadline(self):
        return self.deadline if self.end_mode == 'timed' else None

    def active(self, cancel):
        return not cancel.is_set() and (self.deadline is None or self.clock() < self.deadline)

    def finish(self, cancel, report):
        reason = 'operator' if cancel.is_set() else ('safety_timeout' if self.end_mode == 'operator' else 'duration_complete')
        report['interactive_end'] = reason
        if reason == 'safety_timeout':
            report['errors'].append('Operator session safety cap reached before completion')


def close_business_runtime(runtime, report):
    """Publish media outcomes after close cancels any unfinished operation."""
    runtime.close()
    final = runtime.status()
    saved = report.setdefault('runtime', {})
    # Preserve the last live processing/display metrics, but do not leave an
    # expired export looking 'running' after its temporary files were removed.
    for key in ('video_import', 'video_import_history', 'video_export',
                'video_export_history', 'camera'):
        saved[key] = json.loads(json.dumps(final[key]))
    report['runtime_finalized'] = True


def validate(root):
    manifest, baseline = validate_package(root, 'hdmi_business_package.json', 'hdmi_business_development')
    sys.path.insert(0, str(root / 'src/ps'))
    from hdmi_display.profiles import manifest_profile
    profile = manifest_profile(manifest)
    if manifest.get('formal_release') is not False:
        raise ValueError('Only a supervised development candidate is supported')
    if manifest.get('camera_performance_plan') not in (None,'camera20_full_resolution_v1'):
        raise ValueError('Unknown camera performance plan')
    marker = manifest.get('identity_marker', False)
    if type(marker) is not bool or (marker and profile.dp is None):
        raise ValueError('Invalid frame identity marker configuration')
    inventory = {item['path'] for item in manifest['files']}
    if len(inventory) != len(manifest['files']):
        raise ValueError('Duplicate package inventory paths')
    required = list(manifest['materials']['images']) + list(manifest['materials']['golden'].values())
    required += [manifest['materials']['clip'], 'board/pynq/run_hdmi_business.sh']
    required += [manifest['materials']['video_golden']]
    if profile.dp is not None:
        required += ['src/ps/hdmi_display/profiles.py', 'board/pynq/hdmi_hardware_validation.py']
        required += [str(Path(item['bit']).parent / ('stage_40m_scale{}_system_route_timing.rpt'.format(scale))).replace('\\', '/')
                     for scale, item in manifest['overlays'].items()]
    if marker:
        required += ['src/ps/hdmi_display/frame_marker.py']
    if manifest['materials'].get('performance_clip'):
        required += [manifest['materials']['performance_clip'], 'src/ps/hdmi_display/profiling.py']
    for item in manifest['materials'].get('image_pairs',[]):
        required += [item['hr'],item['lr']['2'],item['lr']['4']]
    if manifest.get('presentation_style') == 'aligned_compare_r10':
        required += ['src/ps/hdmi_display/compare_layout.py']
        required += ['src/ps/hdmi_display/fonts/'+n for n in ('dejavusans.ttf','dejavusans-bold.ttf','license.txt')]
    if manifest.get('pixel_backend') == 'arm_neon_full_validation':
        required += ['src/ps/hdmi_display/native/libhdmi_pixels.so', 'src/ps/hdmi_display/native/build.json',
                     'src/ps/hdmi_display/native/hdmi_pixels.c']
    required += [item[kind] for item in manifest['overlays'].values() for kind in ('bit', 'hwh')]
    if not required or any(relative not in inventory for relative in required):
        raise ValueError('Executable, overlay or material missing from hash inventory')
    return manifest, baseline


def save(path, data):
    temporary = path.with_suffix(path.suffix + '.tmp')
    temporary.write_text(json.dumps(data, indent=2, allow_nan=False) + '\n', encoding='utf-8')
    temporary.replace(path)


def dependencies(root, manifest):
    sys.path.insert(0, str(root / 'src/ps'))
    import cv2
    import numpy as np
    import evdev
    from hdmi_display.pixels import _native
    from PIL import Image
    from hdmi_display.workspace import Workspace
    from hdmi_display.media import RawClip
    from hdmi_display.profiles import manifest_profile
    profile = manifest_profile(manifest)
    if manifest.get('presentation_style') == 'aligned_compare_r10':
        from hdmi_display.metric_overlay import font
        font(22); font(30,True)
    for relative in manifest['materials']['images']:
        with Image.open(root / relative) as image:
            image.load()
    if manifest['materials'].get('image_pairs'):
        from hdmi_display.metrics import ImagePairs
        pairs=ImagePairs(root,manifest['materials']['image_pairs'])
        for index in range(len(pairs.records)):
            for scale in (2,4): pairs.load(index,scale)
    with RawClip(root / manifest['materials']['clip']) as clip:
        if profile.dp is not None and (clip.width, clip.height) != profile.input_size:
            raise ValueError('Business clip geometry differs from its profile')
        clip.frame(0)
        frames = clip.frame_count
    if manifest['materials'].get('performance_clip'):
        with RawClip(root / manifest['materials']['performance_clip']) as clip:
            if (clip.width, clip.height, clip.fps) != (*profile.input_size, profile.source_fps):
                raise ValueError('Performance fixture geometry/rate differs from its profile')
            clip.frame(0)
    if manifest.get('pixel_backend') == 'arm_neon_full_validation' and _native is None:
        raise RuntimeError('Native ARM pixel library is required by this candidate')
    fused_paint = _native is not None and getattr(_native, 'hdmi_paint_nearest', None) is not None
    if manifest.get('display_composition_backend') == 'arm_neon_fused_power_of_two_fit' and not fused_paint:
        raise RuntimeError('Native fused display painter is required by this candidate')
    return {'numpy': np.__version__, 'opencv': cv2.__version__, 'evdev_imported': True, 'video_frames': frames,
            'native_library_loaded': _native is not None, 'native_fused_paint_loaded': fused_paint}


def smoke(engine, root, manifest, destination, cancel, progress):
    import numpy as np
    from PIL import Image
    from roi_magnifier.geometry import Rect
    from hdmi_display.media import RawClip
    video_golden = json.loads((root / manifest['materials']['video_golden']).read_text())
    video_destination = destination / 'video'
    video_destination.mkdir()
    video_checks = []
    source = np.asarray(Image.open(root / manifest['materials']['images'][0]).convert('RGB'))
    h, w = source.shape[:2]
    cases = [('whole', Rect(0, 0, w, h)), ('odd', Rect(17, 19, 31, 27)),
             ('top_left', Rect(0, 0, 1, 1)), ('top_right', Rect(w-1, 0, 1, 1)),
             ('bottom_left', Rect(0, h-1, 1, 1)), ('bottom_right', Rect(w-1, h-1, 1, 1))]
    checks = []
    for scale in (2, 4):
        if cancel.is_set():
            raise InterruptedError('Cancelled before pixel smoke')
        progress('pixel_smoke_x{}'.format(scale))
        engine.select('image', 1 if scale == 4 else 0)
        expected = np.asarray(Image.open(root / manifest['materials']['golden'][str(scale)]).convert('RGB'))
        for name, roi in cases:
            if cancel.is_set():
                raise InterruptedError('Cancelled during pixel smoke')
            pair, _ = engine.process_roi(source, roi)
            wanted = expected[roi.y*scale:roi.bottom*scale, roi.x*scale:roi.right*scale]
            different = int(np.count_nonzero(np.any(pair.output_rgb != wanted, axis=2)))
            output = destination / 'x{}_{}.png'.format(scale, name)
            Image.fromarray(pair.output_rgb).save(output)
            checks.append({'scale': scale, 'case': name, 'roi': vars(roi), 'different_pixels': different,
                           'path': output.name, 'sha256': sha256(output), 'passed': different == 0})
            save(destination / 'pixels.json', {'checks': checks, 'complete': False})
            if different:
                raise RuntimeError('Golden mismatch in x{} {}: {} pixels'.format(scale, name, different))
            engine.present(pair)
        progress('video_golden_x{}'.format(scale))
        with RawClip(root / manifest['materials']['clip']) as clip:
            engine.select('video', 1 if scale == 4 else 0)
            if len(video_golden[str(scale)]) < 30:
                raise ValueError('At least 30 video golden frames are required per scale')
            for expected_frame in video_golden[str(scale)]:
                if cancel.is_set():
                    raise InterruptedError('Cancelled during video pixel smoke')
                index = expected_frame['source_index']
                source_frame = clip.frame(index)
                if hashlib.sha256(source_frame.tobytes()).hexdigest() != expected_frame['source_rgb_sha256']:
                    raise ValueError('Video golden source identity mismatch')
                pair = engine.process(source_frame, index / float(clip.fps))
                output_hash = hashlib.sha256(pair.output_rgb.tobytes()).hexdigest()
                output = video_destination / 'x{}_frame_{:04d}.png'.format(scale, index)
                Image.fromarray(pair.output_rgb).save(output, compress_level=1)
                passed = output_hash == expected_frame['output_rgb_sha256']
                video_checks.append({'scale': scale, 'source_index': index,
                    'source_rgb_sha256': expected_frame['source_rgb_sha256'], 'output_rgb_sha256': output_hash,
                    'expected_rgb_sha256': expected_frame['output_rgb_sha256'],
                    'png_sha256': sha256(output), 'path': output.name, 'exact_rgb_match': passed})
                save(video_destination / 'pixels.json', {'checks': video_checks, 'complete': False})
                if not passed:
                    raise RuntimeError('Golden video mismatch in x{} frame {}'.format(scale, index))
                engine.present(pair)
    save(destination / 'pixels.json', {'checks': checks, 'complete': True, 'passed': True,
                                      'scope': 'image and ROI smoke; not full M2 boundary acceptance'})
    save(video_destination / 'pixels.json', {'checks': video_checks, 'complete': True,
         'passed': True, 'scope': '30 original RGB frames per scale; not playback/HDMI/performance acceptance'})
    return {'images': checks, 'video_frames': len(video_checks)}


def startup_checks(engine, root, manifest, destination, cancel, progress, end_mode):
    if end_mode == 'demo':
        progress('initializing_demo')
        return dict(state='skipped',reason='normal_demo_direct_start',images=[],video_frames=0)
    return dict(smoke(engine,root,manifest,destination,cancel,progress),state='passed')


def worker(root, destination, seconds, device, lock_fd, profile_seconds=0, run_id=None, end_mode='timed'):
    import fcntl
    import cv2
    cv2.setNumThreads(1)
    from legacy_service_rollback import active, PORTAL, CANDIDATE, require_bootpy_completed
    manifest, baseline = validate(root)
    if os.geteuid() != 0 or root_card() != baseline['spare_card_readback']['card_cid']:
        raise RuntimeError('Hardware execution requires root on the verified spare')
    if lock_fd is None or os.fstat(lock_fd).st_ino != Path('/run/lock/sr-lut-hdmi.lock').stat().st_ino:
        raise RuntimeError('A supervisor-owned lock is required')
    fcntl.flock(lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    require_bootpy_completed()
    if any(active(service) not in ('inactive', 'failed', 'unknown') for service in
           (PORTAL, CANDIDATE, 'sr-lut-hdmi.service')):
        raise RuntimeError('Another hardware owner is active')
    os.environ['XILINX_XRT'] = '/usr'
    sys.path.insert(0, str(root / 'src/ps'))
    import numpy as np
    from PIL import Image
    from pynq import Device
    from hdmi_display.camera import CameraSource
    from hdmi_display.engine import Engine
    from hdmi_display.media import RawClip
    from hdmi_display.mouse import Mouse
    from hdmi_display.runtime import Runtime
    from hdmi_display.workspace import Workspace
    from hdmi_display.profiles import manifest_profile
    from hdmi_display.frame_trace import FrameTrace
    profile = manifest_profile(manifest)
    if not Device.devices:
        raise RuntimeError('PYNQ embedded device not found')
    destination.mkdir(exist_ok=False)
    report = {'kind': 'hdmi_business_worker', 'formal_release': False, 'source_commit': manifest['source_commit'],
              'manifest_sha256': sha256(root / 'hdmi_business_package.json'), 'overlays': manifest['overlays'],
              'boot_id': Path('/proc/sys/kernel/random/boot_id').read_text().strip(), 'errors': [], 'closed': False}
    report.update(run_id=run_id, started_monotonic=time.monotonic(), card_cid=root_card(),
                  requested_seconds=seconds, profile_seconds=profile_seconds, end_mode=end_mode,
                  camera_device=device, display_stopped=False, sr_dma_stopped=False)
    cancel = threading.Event()
    for signum in (signal.SIGINT, signal.SIGTERM, signal.SIGHUP):
        signal.signal(signum, lambda *_: cancel.set())
    def checkpoint(phase):
        report['phase'] = phase
        save(destination / 'status.json', report)
        print(phase, flush=True)
    report['business_profile'] = profile.metadata()
    trace = FrameTrace(destination / 'frames.jsonl') if run_id is not None and end_mode != 'demo' else None
    engine = Engine({int(scale): (root / item['bit']).parent for scale, item in manifest['overlays'].items()},
                    profile=profile, identity_marker=manifest.get('identity_marker', False), frame_observer=trace)
    runtime = Runtime(engine, camera=CameraSource(device, input_size=profile.input_size, fps=profile.source_fps))
    mouse = Mouse(profile.hdmi.width, profile.hdmi.height)
    clip = None
    try:
        report['pixel_checks'] = startup_checks(engine,root,manifest,destination,cancel,checkpoint,end_mode)
        report['startup_checks'] = dict(state=report['pixel_checks']['state'],
            reason=report['pixel_checks'].get('reason'))
        images = [root / relative for relative in manifest['materials']['images']]
        runtime.start(np.asarray(Image.open(images[0]).convert('RGB')))
        if manifest['materials'].get('image_pairs'):
            from hdmi_display.metrics import ImagePairs
            runtime.image_pairs = ImagePairs(root,manifest['materials']['image_pairs'])
            runtime.load_paired_image(0)
        if profile_seconds:
            from hdmi_display.profiling import run_profiles
            from hdmi_display.controls import ControlEvent
            performance_clip = RawClip(root / manifest['materials']['performance_clip'])
            report['profile'] = run_profiles(runtime, performance_clip, destination / 'profile',
                {'manifest_sha256': report['manifest_sha256'], 'source_commit': manifest['source_commit'],
                 'overlays': manifest['overlays'], 'boot_id': report['boot_id']},
                profile_seconds, cancel, checkpoint)
            runtime._control_event(ControlEvent('image', 0, True))
        clip = RawClip(root / manifest['materials']['clip'])
        runtime.load_clip(clip)
        clip = None
        runtime.playback.set_loop(True)
        budget = InteractionBudget(seconds, end_mode)
        ui = Workspace(runtime, images, destination, identity={'manifest_sha256': report['manifest_sha256']},
                       session_deadline=budget.display_deadline, session_manual=end_mode in ('operator','demo'))
        last_save = 0
        presentation_done = False
        from hdmi_display.presentation_performance import consume_request, run as run_presentation
        checkpoint('camera_performance_starting' if manifest.get('camera_performance_plan') else 'interactive_business')
        with (destination / 'samples.jsonl' if end_mode != 'demo' else Path(os.devnull)).open('w', encoding='utf-8') as samples:
            camera_campaign = manifest.get('camera_performance_plan') == 'camera20_full_resolution_v1'
            if camera_campaign:
                from hdmi_display.camera_performance import run as run_camera_campaign
                run_camera_campaign(runtime, ui, root, manifest, destination, report,
                                    budget, cancel, checkpoint, samples)
            while budget.active(cancel):
                if camera_campaign:
                    # Keep the final frame and DMA owner alive until the host
                    # closes Nexys sampling and requests the usual ordered stop.
                    time.sleep(.1)
                    continue
                state = mouse.poll()
                ui.poll(state)
                ui.update_metrics()
                runtime.tick(decorate=ui.draw)
                if end_mode != 'demo' and not presentation_done and consume_request(run_id):
                    presentation_done = True
                    run_presentation(runtime, ui, report, budget, cancel, checkpoint, samples)
                now = time.monotonic()
                if now - last_save >= 1:
                    report.update(runtime=runtime.status(), display=engine.display.status(), mouse=state,
                                  exports=list(ui.exports), workspace=ui.status(), monotonic=now)
                    save(destination / 'status.json', report)
                    samples.write(json.dumps({'monotonic': now, 'runtime': report['runtime'],
                                              'display': report['display'], 'mouse': state,
                                              'workspace': report['workspace']}) + '\n')
                    samples.flush()
                    last_save = now
                time.sleep(0.001)
        budget.finish(cancel, report)
    except BaseException as exc:
        report['errors'].append(type(exc).__name__ + ': ' + str(exc))
        report['hardware_fault'] = getattr(engine.session, 'last_fault', None)
    finally:
        # Retain the owner if DMA halt fails. Never reconfigure an active owner.
        while True:
            try:
                close_business_runtime(runtime, report)
                break
            except Exception as exc:
                report['retained_dma_error'] = str(exc)
                checkpoint('waiting_for_safe_hardware_stop')
                time.sleep(2)
        mouse.close()
        if clip is not None:
            clip.close()
        report['closed'] = True
        report.update(display_stopped=engine.display is None, sr_dma_stopped=engine.session is None and engine.overlay is None)
        if trace is not None:
            trace.close()
            report.update(source_trace_records=trace.count, source_trace_sha256=sha256(destination / 'frames.jsonl'))
        checkpoint('finished')
        save(destination / 'report.json', report)
    return 0 if not report['errors'] else 1


def restore_legacy(destination, report):
    """Record the exact recovery boundary before each potentially blocking call."""
    from legacy_service_rollback import active, wait_status, PORTAL, CANDIDATE, require_bootpy_completed

    def checkpoint(phase):
        save(destination / 'recovery_status.json', dict(report, recovery_phase=phase,
                                                       recovery_monotonic=time.monotonic()))

    try:
        checkpoint('checking_boot_initialization')
        report['bootpy_before_restore'] = require_bootpy_completed()
        checkpoint('checking_candidate_stopped')
        if active(CANDIDATE) not in ('inactive', 'failed'):
            raise RuntimeError('Unexpected candidate service during restore')
        checkpoint('starting_legacy_service')
        subprocess.run(['systemctl', 'start', PORTAL], check=True, timeout=120)
        checkpoint('waiting_legacy_identity')
        save(destination / 'legacy_restored.json', wait_status(8080, False))
        checkpoint('checking_legacy_active')
        report['legacy_restored'] = active(PORTAL) == 'active'
        report['bootpy_after_restore'] = require_bootpy_completed()
        if (report['bootpy_before_restore']['ExecMainExitTimestampMonotonic'] !=
                report['bootpy_after_restore']['ExecMainExitTimestampMonotonic']):
            report['legacy_restored'] = False
            raise RuntimeError('Boot initialization reran during legacy restoration')
    except Exception as exc:
        report['legacy_restored'] = False
        report['errors'].append('Legacy restore: ' + str(exc))
        checkpoint('restore_failed')
    else:
        checkpoint('restore_complete' if report['legacy_restored'] else 'restore_failed')


def supervise(root, seconds, device, profile_seconds=0, destination=None, run_id=None, end_mode='timed'):
    import fcntl
    from hdmi_m1_supervisor import watch_worker
    from legacy_service_rollback import active, wait_status, PORTAL, CANDIDATE, require_bootpy_completed
    manifest, baseline = validate(root)
    if os.geteuid() != 0 or root_card() != baseline['spare_card_readback']['card_cid']:
        raise RuntimeError('Supervisor requires root on the verified spare')
    if profile_seconds and not manifest['materials'].get('performance_clip'):
        raise RuntimeError('This package has no 30 fps performance fixture')
    if not str(Path(sys.prefix)).startswith('/home/xilinx/sr_lut_hdmi_envs/'):
        raise RuntimeError('Use the isolated HDMI interpreter')
    # Preflight child has no hardware access. The parent never imports PYNQ.
    subprocess.run([sys.executable, '-B', str(Path(__file__)), 'check', '--root', str(root)], check=True, timeout=60)
    with Path('/run/lock/sr-lut-hdmi.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        if active(PORTAL) != 'active' or active(CANDIDATE) not in ('inactive', 'failed'):
            raise RuntimeError('Begin with formal 8080 active and candidate stopped')
        before = wait_status(8080, False)
        bootpy = require_bootpy_completed()
        if destination is None:
            destination = Path(tempfile.mkdtemp(prefix='hdmi_business_results_', dir='/home/xilinx'))
        else:
            destination.mkdir(exist_ok=False)
        # Reports contain no credentials; allow the unprivileged SSH account to
        # monitor/archive them without a second sudo prompt during the test.
        destination.chmod(0o755)
        print('RESULTS=' + str(destination), flush=True)
        save(destination / 'legacy_before.json', before)
        save(destination / 'bootpy_before.json', bootpy)
        cancelled = threading.Event()
        previous = {s: signal.signal(s, lambda *_: cancelled.set()) for s in (signal.SIGINT, signal.SIGTERM, signal.SIGHUP)}
        report = {'kind': 'hdmi_business_supervisor', 'manifest_sha256': sha256(root / 'hdmi_business_package.json'),
                  'source_commit': manifest['source_commit'], 'formal_release': False, 'legacy_restored': False,
                  'errors': []}
        report.update(run_id=run_id, requested_seconds=seconds, profile_seconds=profile_seconds, end_mode=end_mode,
                      started_monotonic=time.monotonic())
        try:
            subprocess.run(['systemctl', 'stop', PORTAL, CANDIDATE], check=True, timeout=120)
            print(('Initializing demo directly. ' if end_mode == 'demo' else 'Running x2/x4 pixel smoke, then workspace. ')
                  + 'BTN0/1/2/3 select modes. Ctrl+C restores 8080.', flush=True)
            command = [sys.executable, '-B', '-X', 'faulthandler', str(Path(__file__)), '_worker',
                '--root', str(root), '--destination', str(destination / 'worker'),
                *(['--seconds',str(seconds)] if seconds is not None else []),
                '--camera-device', device, '--lock-fd', str(lock.fileno()),
                '--profile-seconds', str(profile_seconds)]
            if run_id is not None:
                command += ['--run-id', run_id]
            if end_mode in ('operator','demo'):
                command += ['--end-mode', end_mode]
            report.update(watch_worker(command, destination,
                pass_fds=(lock.fileno(),), timeout=None if end_mode == 'demo' else seconds + 240 + (6 * (profile_seconds + 15) if profile_seconds else 0),
                bounded_output=end_mode == 'demo',
                cancel=cancelled, cancel_path=destination / 'cancel.request' if run_id is not None else None))
            child_path = destination / 'worker/report.json'
            if child_path.is_file():
                child = json.loads(child_path.read_text())
                report['errors'].extend(child.get('errors', []))
                report['hardware_closed'] = child.get('closed') is True
            else:
                report['errors'].append('Worker final report missing; incomplete run')
            if report['worker_exit_code'] != 0 and not report['errors']:
                report['errors'].append('Worker exited with code ' + str(report['worker_exit_code']))
        except BaseException as exc:
            report['errors'].append(type(exc).__name__ + ': ' + str(exc))
        finally:
            restore_legacy(destination, report)
            save(destination / 'report.json', report)
            for signum, handler in previous.items():
                signal.signal(signum, handler)
        print(json.dumps(report, indent=2), flush=True)
        return 0 if report.get('worker_exit_code') == 0 and report['legacy_restored'] and not report['errors'] else 1


def main():
    faulthandler.enable(all_threads=True)
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command', choices=('check', 'run', '_worker'))
    parser.add_argument('--root', type=Path, default=Path(__file__).resolve().parents[2])
    parser.add_argument('--seconds', type=int)
    parser.add_argument('--end-mode', choices=('timed', 'operator', 'demo'), default='timed')
    parser.add_argument('--camera-device', default='/dev/video0')
    parser.add_argument('--destination', type=Path)
    parser.add_argument('--lock-fd', type=int)
    parser.add_argument('--run-id', help='Unique N5 session identity; enables submitted-frame trace')
    parser.add_argument('--profile-seconds', type=int, default=0,
                        help='Automatic video/camera profile seconds per layout (0 disables, 10..300)')
    args = parser.parse_args()
    args.root = args.root.resolve(strict=True)
    if args.seconds is None and args.end_mode != 'demo':
        args.seconds=900
    if args.end_mode == 'demo' and (args.seconds is not None or args.run_id is None):
        parser.error('Demo requires a run ID and no duration')
    if args.end_mode != 'demo' and not 10 <= args.seconds <= 1800:
        parser.error('seconds must be in 10..1800')
    if args.end_mode == 'operator' and (args.seconds != 1800 or args.run_id is None):
        parser.error('Operator mode requires an N5 run ID and the 1800-second safety cap')
    if args.profile_seconds != 0 and not 10 <= args.profile_seconds <= 300:
        parser.error('profile-seconds must be 0 or in 10..300')
    if args.run_id is not None:
        if not re.fullmatch('[0-9a-f]{32}', args.run_id) or args.destination is None:
            parser.error('N5 session requires a 32-hex run ID and explicit destination')
        expected = '/home/xilinx/n5_business_results_' + args.run_id
        if args.destination.as_posix() != expected + ('/worker' if args.command == '_worker' else ''):
            parser.error('N5 destination must match its run ID')
        if (args.end_mode == 'timed' and not 60 <= args.seconds <= 300) or args.profile_seconds or args.camera_device != '/dev/video0':
            parser.error('N5 requires 60..300 timed seconds or operator mode, no performance profiles, camera0')
    elif args.destination is not None and args.command != '_worker':
        parser.error('Explicit supervisor destination requires a run ID')
    if args.command == 'check':
        manifest, _ = validate(args.root)
        print(json.dumps({'package_verified': True, 'dependencies': dependencies(args.root, manifest),
                          'manifest_sha256': sha256(args.root / 'hdmi_business_package.json')}))
        return 0
    if args.command == '_worker':
        if args.destination is None:
            parser.error('worker needs destination')
        return worker(args.root, args.destination, args.seconds, args.camera_device, args.lock_fd, args.profile_seconds, args.run_id, end_mode=args.end_mode)
    return supervise(args.root, args.seconds, args.camera_device, args.profile_seconds, args.destination, args.run_id, end_mode=args.end_mode)


if __name__ == '__main__':
    raise SystemExit(main())
