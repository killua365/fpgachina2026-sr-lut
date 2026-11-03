"""Manifest-selected camera campaign within the existing supervised session."""
import hashlib
import json
import statistics
import time

from .compositor import Composition
from .controls import ControlEvent
from .media import RawClip
from .pixels import validate_rgbx


def run(runtime, ui, root, manifest, destination, report, budget, cancel, checkpoint, samples):
    engine = runtime.engine
    result = report['camera_performance'] = dict(
        target_fps=20, input_size=[480,270], sr_size=[1920,1080], cases=[],
        passed=False, direct_display=engine.camera_direct_enabled,
        fused_copy_display=engine.camera_copy_enabled, nexys_complete_scan_verification_required=True)
    if runtime.profile.input_size != (480,270) or not engine.camera_pipeline_enabled:
        raise ValueError('Camera campaign requires the full-resolution Nexys profile')

    def active():
        if not budget.active(cancel):
            raise InterruptedError('Camera campaign interrupted or deadline expired')

    def snapshot(phase):
        report.update(runtime=runtime.status(), display=engine.display.status(),
                      workspace=ui.status(), monotonic=time.monotonic())
        checkpoint(phase)
        samples.write(json.dumps(dict(monotonic=report['monotonic'], runtime=report['runtime'],
            display=report['display'], camera_case=result.get('active_case'))) + '\n')
        samples.flush()

    # Exercise both alternating DMA allocations and the overlap callback using
    # original RGB goldens, before any live-camera speed result is considered.
    checkpoint('camera_pipeline_golden')
    golden = json.loads((root/manifest['materials']['video_golden']).read_text())['4']
    engine.select('camera', 1)
    previous = None
    checks = []
    with RawClip(root/manifest['materials']['clip']) as clip:
        for expected in golden:
            active()
            index = expected['source_index']
            source = clip.frame(index)
            if hashlib.sha256(source.tobytes()).hexdigest() != expected['source_rgb_sha256']:
                raise ValueError('Camera pipeline golden source changed')
            def show_previous():
                if previous is not None:
                    engine.present(previous, Composition(layout='sr'))
            pair = engine.process(source, index/float(clip.fps), borrow_camera=True, camera_overlap=show_previous)
            raw = pair.session.output_buffers[pair.buffer_index]
            import numpy as np
            if not validate_rgbx(np.asarray(raw).view(np.uint8).reshape(1080,1920,4)):
                raise RuntimeError('Camera golden X validation failed')
            digest = hashlib.sha256(pair.output_rgb.tobytes()).hexdigest()
            checks.append(dict(source_index=index, output_rgb_sha256=digest,
                               expected_rgb_sha256=expected['output_rgb_sha256']))
            if digest != expected['output_rgb_sha256']:
                raise RuntimeError('Camera borrowed-buffer golden mismatch at '+str(index))
            previous = pair
    if len(checks) < 30:
        raise ValueError('Camera pipeline needs at least 30 exact golden frames')
    result['golden'] = dict(passed=True, checks=checks, full_x_byte_validation=True)
    previous = None
    # Direct SR and scanout share allocations; verify all three rotating slots
    # before UI overwrites any result pixels, then validate again on submit.
    direct_checks = []
    if not (engine.camera_direct_enabled or engine.camera_copy_enabled):
        raise RuntimeError('Optimized camera display path is not enabled')
    with RawClip(root/manifest['materials']['clip']) as clip:
        for expected in golden:
            active()
            def show_direct():
                if previous is not None:
                    engine.present(previous, Composition(layout='sr'))
            index = expected['source_index']
            pair = engine.process(clip.frame(index), index/float(clip.fps), borrow_camera=True,
                                  camera_direct=engine.camera_direct_enabled,
                                  camera_overlap=show_direct if engine.camera_direct_enabled else None)
            digest = hashlib.sha256(pair.output_rgb.tobytes()).hexdigest()
            if digest != expected['output_rgb_sha256']:
                raise RuntimeError('Direct camera golden mismatch at '+str(index))
            if engine.camera_copy_enabled:
                from .frame_marker import draw
                from .frames import FramePair
                layout='sr' if len(direct_checks)<len(golden)//2 else 'split'
                config=Composition(layout=layout)
                runtime.last_pair=pair
                runtime.composition=config
                expected_pixels=np.empty((1080,1920,4),np.uint8)
                reference=FramePair(pair.token,pair.source_rgb,pair.output_rgb.copy(),4,pair.source_time)
                engine.compositor.compose(reference,expected_pixels,config)
                ui._draw_uncached(expected_pixels)
                if engine.identity_marker: draw(expected_pixels,pair.token,'camera',4)
                engine.present(pair,config,ui.draw)
                slot=engine.display.pending
                actual=engine.display.buffers[slot]
                if not np.array_equal(actual,expected_pixels):
                    raise RuntimeError('Checked copy display pixels differ from reference')
                direct_checks.append(dict(source_index=index,output_rgb_sha256=digest,
                    expected_rgb_sha256=expected['output_rgb_sha256'],slot=slot,layout=layout,
                    exact_display_rgbx_match=True))
            else:
                direct_checks.append(dict(source_index=index, output_rgb_sha256=digest,
                    expected_rgb_sha256=expected['output_rgb_sha256'], slot=pair.buffer_index))
            previous = pair
    if engine.camera_direct_enabled:
        engine.present(previous, Composition(layout='sr'))
    result['direct_golden' if engine.camera_direct_enabled else 'copy_golden'] = dict(
        passed=True, checks=direct_checks, full_x_byte_validation=True)
    previous = None
    engine.select('image', 1)
    runtime._control_event(ControlEvent('camera', 1, True))
    ready_deadline = time.monotonic()+30
    while runtime.last_pair is None:
        active()
        runtime.tick(ui.draw)
        if time.monotonic() >= ready_deadline:
            raise TimeoutError('Camera did not produce a processed frame within 30 seconds')
        snapshot('camera_performance_warmup')
        time.sleep(.01)

    # The host must count complete new Nexys scans using its hardware clock.
    # 310 seconds leaves guard frames around a strict 300-second scan window.
    for stage, seconds in (('diagnostic',30), ('acceptance_capture',310)):
        if stage == 'acceptance_capture' and any(c['submitted_pair_fps'] < 20 for c in result['cases']):
            result['acceptance_capture_skipped'] = 'diagnostic submissions below 20 fps'
            break
        for layout in ('sr','split'):
            active()
            runtime.set_composition(Composition(layout=layout))
            runtime._invalidate()
            # Establish a first completed pair, then measure only steady state.
            while runtime.last_pair is None:
                active()
                runtime.tick(ui.draw)
                time.sleep(.001)
            runtime.reset_measurement()
            generation = engine.generation
            case = dict(stage=stage, layout=layout, generation=generation,
                        start_monotonic=time.monotonic(), requested_seconds=seconds,
                        first_sequence=runtime.last_pair.token.sequence)
            main_cpu_started = time.process_time()
            result['active_case'] = case
            timings = {}
            last_save = 0
            while time.monotonic()-case['start_monotonic'] < seconds:
                active()
                tick_started = time.monotonic()
                submitted = runtime.tick(ui.draw)
                timings.setdefault('tick_wall_seconds',[]).append(time.monotonic()-tick_started)
                if engine.generation != generation or runtime.camera.state != 'streaming':
                    raise RuntimeError('Camera generation/stream changed inside performance window')
                if submitted:
                    for key, value in engine.last_timings.items():
                        if key.endswith('_seconds') and isinstance(value,(int,float)):
                            timings.setdefault(key,[]).append(value)
                now = time.monotonic()
                if now-last_save >= 1:
                    snapshot('camera_performance_running')
                    last_save = now
                time.sleep(.001)
            status = runtime.status()
            case.update(end_monotonic=time.monotonic(), last_sequence=runtime.last_pair.token.sequence,
                submitted_pairs=status['window_submitted_pairs'], submitted_pair_fps=status['submitted_pair_fps'],
                camera=status['camera'], input_size=status['input_size'], sr_size=status['sr_size'],
                timings={key:dict(count=len(v), median=statistics.median(v),
                    mean=statistics.mean(v), total=sum(v),
                    p95=sorted(v)[min(len(v)-1,int(.95*len(v)))]) for key,v in timings.items()},
                main_cpu_seconds=time.process_time()-main_cpu_started,
                rate_scope='PYNQ submissions; independent Nexys 300-second acceptance still required')
            result['cases'].append(case)
            result['active_case'] = None
            snapshot('camera_performance_case_complete')
    result['capture_complete'] = True
    snapshot('camera_performance_complete')
