"""Explicit, one-shot maintenance campaign inside the supervised owner."""
import json
from pathlib import Path
import re
import time

from .compositor import Composition
from .controls import ControlEvent


CASES = (('sr', .5, 310), ('split', .5, 310),
         ('wipe', .25, 70), ('wipe', .5, 70), ('wipe', .75, 70),
         ('wipe_drag', .5, 70))


def consume_request(run_id, home=Path('/home/xilinx')):
    if not isinstance(run_id, str) or not re.fullmatch(r'[0-9a-f]{32}', run_id):
        return False
    path = home / ('.sr_lut_presentation_' + run_id)
    try:
        value = path.read_text('ascii')
    except FileNotFoundError:
        return False
    if value.strip() != run_id:
        return False
    path.unlink()
    return True


def run(runtime, ui, report, budget, cancel, checkpoint, samples):
    if runtime.profile.input_size != (480, 270) or not runtime.engine.camera_copy_enabled:
        raise ValueError('Presentation campaign requires the qualified full-resolution copy path')
    if runtime.snapshot_status().get('state') == 'running':
        raise RuntimeError('Finish the snapshot export before requesting performance capture')
    result = report['presentation_performance'] = dict(cases=[], capture_complete=False,
        target_fps=20, rate_scope='PYNQ submissions; independent Nexys complete-scan audit required')

    def active():
        if not budget.active(cancel):
            raise InterruptedError('Presentation campaign cancelled or session deadline reached')

    def save(phase):
        report.update(runtime=runtime.status(), display=runtime.engine.display.status(),
                      workspace=ui.status(), exports=list(ui.exports), monotonic=time.monotonic())
        checkpoint(phase)
        samples.write(json.dumps(dict(monotonic=report['monotonic'], runtime=report['runtime'],
            display=report['display'], presentation_case=result.get('active_case'))) + '\n')
        samples.flush()

    if runtime.camera_frozen:
        runtime.resume_camera()
    runtime._control_event(ControlEvent('camera', 1, True))
    for name, fraction, seconds in CASES:
        active()
        config = Composition(layout='wipe' if name == 'wipe_drag' else name, wipe_fraction=fraction)
        runtime.set_composition(config)
        runtime._invalidate()
        deadline = time.monotonic() + 30
        while runtime.last_pair is None:
            active()
            runtime.tick(ui.draw)
            if time.monotonic() >= deadline:
                raise TimeoutError('Camera did not produce a frame')
            time.sleep(.001)
        runtime.reset_measurement()
        case = dict(name=name, layout=config.layout, fraction=fraction, requested_seconds=seconds,
            generation=runtime.engine.generation, first_sequence=runtime.last_pair.token.sequence,
            start_monotonic=time.monotonic())
        result['active_case'] = case
        last_save = 0
        while time.monotonic() - case['start_monotonic'] < seconds:
            active()
            if name == 'wipe_drag':
                phase = ((time.monotonic() - case['start_monotonic']) % 4) / 2
                runtime.set_composition(Composition(layout='wipe', wipe_fraction=phase if phase <= 1 else 2-phase))
            runtime.tick(ui.draw)
            if runtime.engine.generation != case['generation'] or runtime.camera.state != 'streaming':
                raise RuntimeError('Camera or physical controls changed during measurement')
            if time.monotonic() - last_save >= 1:
                save('presentation_performance_running')
                last_save = time.monotonic()
            time.sleep(.001)
        status = runtime.status()
        case.update(end_monotonic=time.monotonic(), last_sequence=runtime.last_pair.token.sequence,
            submitted_pairs=status['window_submitted_pairs'], submitted_pair_fps=status['submitted_pair_fps'],
            input_size=status['input_size'], sr_size=status['sr_size'], camera=status['camera'])
        result['cases'].append(case)
        result['active_case'] = None
        save('presentation_performance_case_complete')
    result['capture_complete'] = True
    runtime.set_composition(Composition(layout='sr'))
    save('presentation_performance_complete')
