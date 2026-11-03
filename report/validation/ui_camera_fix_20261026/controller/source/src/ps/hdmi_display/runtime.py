"""Serialized source/control integration, independent of the mouse renderer.

The launcher supplies an authorized Engine after completing M0. All public
methods run on one application thread. CameraSource alone owns a child process;
neither it nor any UI thread can reconfigure the FPGA behind this runtime.
"""

from __future__ import annotations

import time
from dataclasses import replace

import numpy as np

from roi_magnifier.geometry import Rect, plan_roi
from roi_magnifier.processor import validate_source_image

from .camera import CameraSource
from .compositor import Composition, Compositor
from .controls import PhysicalControls
from .playback import Playback


class Runtime:
    def __init__(self, engine, *, camera=None, controls=None, clock=time.monotonic):
        self.engine = engine
        self.profile = engine.profile
        self.clock = clock
        self.camera = camera if camera is not None else CameraSource(
            input_size=self.profile.input_size, fps=self.profile.source_fps)
        self.controls = controls if controls is not None else PhysicalControls(clock=clock)
        self.image = self.roi = self.playback = self.last_pair = None
        self.composition = Composition()
        self.raw_switches = 0
        self.image_dirty = False
        self.last_error = None
        self.submitted_new_frames = 0
        self.processed_source_frames = 0
        self._metrics_started = None
        self._metrics_processed_base = self._metrics_submitted_base = 0
        self.last_source_index = None
        self._camera_generation = None
        self._camera_pending = None
        self._camera_started = None
        self._redraw = False
        self._export_steps = None
        self.export_status = {"state": "idle"}
        self.export_history = []
        self._import_steps = None
        self.import_status = {'state': 'idle'}
        self.import_history = []
        self.camera_frozen = False
        self.frozen_metadata = None
        self._camera_resume_after = None
        self.snapshot_export = None
        self.image_pairs = None
        self.image_pair_index = 0
        self.image_reference = self.image_source_path = self.image_record = None
        self.quality_worker = None
        self.view_layout = None

    def load_paired_image(self, index):
        if self.image_pairs is None:
            return False
        lr, hr, path, record = self.image_pairs.load(index, self.engine.scale or 2)
        self.set_image(lr)
        self.image_pair_index = index % len(self.image_pairs.records)
        self.image_reference, self.image_source_path, self.image_record = hr, path, record
        return True

    def quality_reference(self):
        if self.image_reference is None or self.roi is None:
            return None
        s, r = self.engine.scale, self.roi
        return self.image_reference[r.y*s:r.bottom*s, r.x*s:r.right*s]

    def quality_status(self):
        return self.quality_worker.status() if self.quality_worker is not None else dict(state='unavailable')

    def snapshot_status(self):
        return self.snapshot_export.status() if self.snapshot_export is not None else {'state': 'idle'}

    def freeze_camera(self):
        if self.engine.mode != 'camera' or self.last_pair is None:
            raise RuntimeError('Wait for a camera frame before freezing')
        if self.camera_frozen:
            return
        self.last_pair = self.engine.snapshot_pair(self.last_pair)
        self._camera_pending = None
        self.camera_frozen = True
        self.frozen_metadata = dict(frozen_monotonic=self.clock(), source_index=self.last_source_index,
                                    camera_generation=self._camera_generation)
        self.reset_measurement()
        self.request_redraw()

    def resume_camera(self):
        if self.snapshot_status()['state'] == 'running':
            raise RuntimeError('Wait for snapshot saving to finish')
        if self.camera_frozen:
            self._camera_resume_after = self.clock()
            self._invalidate()
            self.reset_measurement()

    def save_snapshot(self, destination, identity=None):
        if self.engine.mode != 'camera' or not self.camera_frozen or self.last_pair is None:
            raise RuntimeError('Freeze the camera before saving')
        if self.snapshot_status()['state'] == 'running':
            raise RuntimeError('Snapshot is already saving')
        from .snapshot import SnapshotExport
        self.snapshot_export = SnapshotExport(self.last_pair, destination, self.composition,
            (self.engine.display_mode.width, self.engine.display_mode.height),
            {**(identity or {}), **self.frozen_metadata})
        self.request_redraw()

    @property
    def import_active(self):
        return self._import_steps is not None

    def start_video_import(self, source, destination, **tools):
        from .media import import_video_steps
        if self.import_active or self.export_active or self.engine.mode != 'video':
            raise RuntimeError('Finish or cancel the current operation and select video mode first')
        if self.playback is not None:
            self.playback.pause()
        self._import_steps = import_video_steps(source, destination, profile=self.profile, **tools)
        self.export_status = {'state': 'idle'}
        self.import_status = dict(state='running', phase='probing', done=0, total=0,
                                  source=str(source), destination=str(destination))
        self.request_redraw()

    def _finish_import(self, state, **details):
        self._import_steps = None
        self.import_status.update(state=state, **details)
        self.import_history.append(dict(self.import_status))
        self.request_redraw()

    def cancel_video_import(self, reason='operator'):
        if self._import_steps is not None:
            self._import_steps.close()
            self._finish_import('cancelled', reason=reason)

    def _tick_import(self, decorate):
        from .media import RawClip
        try:
            phase, done, total = next(self._import_steps)
            if (phase, done, total) != tuple(self.import_status.get(k) for k in ('phase', 'done', 'total')):
                self.import_status.update(phase=phase, done=done, total=total)
                self.request_redraw()
        except StopIteration as result:
            self._import_steps = None
            clip = None
            try:
                # The same owned iterator just hashed and atomically published
                # this private directory. Avoid a second blocking full-file hash.
                clip = RawClip(result.value, verify_hash=False)
                self.load_clip(clip)
                self.playback.pause()
                self._finish_import('complete', manifest=str(result.value))
            except Exception as exc:
                if clip is not None and (self.playback is None or self.playback.clip is not clip): clip.close()
                self._finish_import('failed', error=f'{type(exc).__name__}: {exc}')
        except Exception as exc:
            self._finish_import('failed', error=f'{type(exc).__name__}: {exc}')
        if self._redraw and decorate is not None:
            if self.last_pair is not None: self.engine.present(self.last_pair, self.composition, decorate)
            else: self.engine.present_surface(decorate)
            self._redraw = False
        return False

    @property
    def export_active(self):
        return self._export_steps is not None

    def start_video_export(self, destination, *, processing_identity, **tools):
        from .export import export_video_steps
        if self.import_active or self.export_active or self.engine.mode != 'video' or self.playback is None:
            raise RuntimeError('Select a video and finish or cancel the current export first')
        self.playback.pause()
        self.import_status = {'state': 'idle'}
        self._export_steps = export_video_steps(self.playback.clip, destination, scale=self.engine.scale,
            process_rgb=lambda pixels: self.engine.process(pixels).output_rgb,
            processing_identity=processing_identity, **tools)
        self.export_status = dict(state='running', phase='processing', done=0,
                                  total=self.playback.clip.frame_count, destination=str(destination))
        self.request_redraw()

    def _finish_export(self, state, **details):
        self._export_steps = None
        self.export_status.update(state=state, **details)
        self.export_history.append(dict(self.export_status))
        self.reset_measurement()
        self.request_redraw()

    def cancel_video_export(self, reason='operator'):
        if self._export_steps is not None:
            # Called only between iterator advances, after Engine.process has
            # returned. Closing cancels owned codec processes, never a DMA owner.
            self._export_steps.close()
            self._finish_export('cancelled', reason=reason)

    def _tick_export(self, decorate):
        try:
            phase, done, total = next(self._export_steps)
            if (phase, done, total) != tuple(self.export_status.get(k) for k in ('phase', 'done', 'total')):
                self.export_status.update(phase=phase, done=done, total=total)
                self.request_redraw()
        except StopIteration as result:
            self._finish_export('complete', report=str(result.value))
        except Exception as exc:
            self._finish_export('failed', error=f'{type(exc).__name__}: {exc}')
            if self.engine.faulted:
                # Preserve the normal worker fault/ordered shutdown path.
                raise
        if self._redraw and decorate is not None:
            if self.last_pair is not None:
                self.engine.present(self.last_pair, self.composition, decorate)
            else:
                self.engine.present_surface(decorate)
            self._redraw = False
        return False  # Exported RGB is not a new monitor scan or live frame.

    def start(self, image):
        self.engine.select("image", self.raw_switches)
        self.set_image(image)

    def request_redraw(self):
        self._redraw = True

    def reset_measurement(self):
        self._metrics_started = self.clock()
        self._metrics_processed_base = self.processed_source_frames
        self._metrics_submitted_base = self.submitted_new_frames

    def _invalidate(self):
        if getattr(self, 'quality_worker', None) is not None:
            self.quality_worker.invalidate()
        self.engine.invalidate_source()
        self.camera_frozen = False
        self.frozen_metadata = None
        self._camera_pending = None
        self.last_pair = None
        self.last_source_index = None
        self.image_dirty = True
        self._redraw = True

    def _default_roi(self, image):
        height, width = image.shape[:2]
        if width <= 480 and height <= 320:
            return Rect(0, 0, width, height)
        # Interior ROI edges need one real source pixel of context on each side.
        roi_width = min(width, self.profile.input_size[0], 478 if width > 480 else width)
        roi_height = min(height, self.profile.input_size[1], 318 if height > 320 else height)
        return Rect((width - roi_width) // 2, (height - roi_height) // 2, roi_width, roi_height)

    def set_image(self, image):
        height, width = validate_source_image(image)
        roi = self._default_roi(image)
        plan_roi(width, height, roi.x, roi.y, roi.width, roi.height, self.engine.scale or 2)
        self.image = np.array(image, copy=True, order="C")
        self.image_reference = self.image_source_path = self.image_record = None
        self.roi = roi
        if self.engine.mode == "image":
            self.composition = Composition(layout=self.composition.layout)
            self._invalidate()

    def set_roi(self, roi):
        if self.image is None:
            raise RuntimeError("No image is loaded")
        height, width = self.image.shape[:2]
        plan = plan_roi(width, height, roi.x, roi.y, roi.width, roi.height, self.engine.scale or 2)
        self.roi = plan.roi
        if self.engine.mode == "image":
            self._invalidate()

    def reset_roi(self):
        if self.image is not None:
            self.set_roi(self._default_roi(self.image))

    def load_clip(self, clip):
        if self.profile.dp is not None and (clip.width, clip.height) != self.profile.input_size:
            raise ValueError('Clip geometry differs from the dual-board input profile')
        # Ownership transfers only after constructing the new timeline.
        timeline = Playback(clip, self.engine.scale or 2, clock=self.clock)
        self.cancel_video_import('clip_replaced')
        self.cancel_video_export('clip_replaced')
        old = self.playback
        self.playback = timeline
        if old is not None and old.clip is not clip:
            old.clip.close()
        if self.engine.mode == "video":
            self.composition = Composition(layout=self.composition.layout)
            self._invalidate()
            timeline.play()

    def seek(self, index):
        if self.export_active or self.import_active:
            raise RuntimeError('Cancel export before seeking')
        if self.playback is None:
            raise RuntimeError("No video is loaded")
        self.playback.seek(index)
        if self.engine.mode == "video":
            self._invalidate()

    def step_video(self, direction):
        if self.export_active or self.import_active:
            raise RuntimeError('Cancel export before stepping')
        if self.playback is None:
            raise RuntimeError("No video is loaded")
        self.playback.step(direction)
        if self.engine.mode == "video":
            self._invalidate()

    def set_composition(self, composition):
        # Geometry validation remains in Compositor; no source is reprocessed
        # merely to pan the matching baseline/SR pair or move a wipe boundary.
        if self.last_pair is not None:
            layout = self.view_layout(self.last_pair,composition) if self.view_layout is not None else None
            composition = Compositor.normalize(self.last_pair, self.engine.display_mode.width,
                                                self.engine.display_mode.height, composition, layout)
        if (composition.layout, composition.pixel_view) != (self.composition.layout, self.composition.pixel_view):
            self.reset_measurement()
        if (self.engine.mode == 'camera' and getattr(self.engine, 'camera_direct_enabled', False)
                and (composition.layout, composition.pixel_view) !=
                    (self.composition.layout, self.composition.pixel_view)):
            self._invalidate()
        self.composition = composition
        self._redraw = True

    def _control_event(self, event):
        self.cancel_video_import('physical_control')
        self.cancel_video_export('physical_control')
        previous_mode, previous_scale = self.engine.mode, self.engine.scale
        mode = event.mode or previous_mode
        self.raw_switches = event.raw_switches
        if previous_mode == "camera" and mode != "camera":
            if self.snapshot_export is not None:
                self.snapshot_export.cancelled.set()
            self.camera.stop()
        if self.playback is not None and mode != "video":
            self.playback.pause()
        self.engine.select(mode, self.raw_switches)
        changed = mode != previous_mode or self.engine.scale != previous_scale
        if changed or event.mode == "idle":
            self.composition = Composition(layout=self.composition.layout)
            self._camera_resume_after = None
            self._invalidate()
            self.reset_measurement()
        if mode == 'image' and changed and getattr(self, 'image_pairs', None) is not None:
            self.load_paired_image(self.image_pair_index)
        if self.playback is not None:
            self.playback.set_scale(self.engine.scale)
            if mode == "video" and previous_mode != "video":
                self.playback.play()
        if mode == "camera" and previous_mode != "camera":
            self._camera_started = self.clock()
            self._camera_generation = None
            self.camera.start()
        if event.mode == "idle":
            self.composition = Composition()
            self.reset_roi()
            if self.playback is not None:
                self.playback.pause()
                self.playback.seek(0)

    def _tick_camera_pipeline(self, frame, decorate):
        """One owner thread, two SR allocations, at most one ready result.

        Submit the ready result while PL computes the next one. Capture keeps
        only its newest frame; this method never accumulates a source queue.
        """
        presented = False
        display_timings = {}

        def present_ready():
            nonlocal presented
            if self._camera_pending is None:
                return
            pair, sequence = self._camera_pending
            if self.engine.present(pair, self.composition, decorate):
                self.last_pair, self.last_source_index = pair, sequence
                self.submitted_new_frames += 1
                self._redraw = False
                presented = True
                display_timings.update({k: self.engine.last_timings[k]
                    for k in ('compose_seconds', 'display_submit_seconds', 'display_validation_seconds', 'fused_copy_validation_seconds')
                    if k in self.engine.last_timings})
            self._camera_pending = None

        if frame is not None:
            if frame.generation != self._camera_generation:
                self._invalidate()
                self._camera_generation = frame.generation
            elapsed = max(0, frame.captured_monotonic - self._camera_started)
            before = self.engine.processed_frames
            direct = (getattr(self.engine, 'camera_direct_enabled', False)
                      and self.composition.layout == 'sr' and not self.composition.pixel_view)
            pair = self.engine.process(frame.rgb, elapsed, camera_overlap=present_ready, borrow_camera=True,
                                       **({'camera_direct': True} if direct else {}))
            self.processed_source_frames += self.engine.processed_frames - before
            self._camera_pending = (pair, frame.sequence)
            self.engine.last_timings.update(display_timings, pipeline_overlap=True)
            self.last_error = None
        else:
            present_ready()
            if not presented and self._redraw:
                if self.last_pair is not None:
                    self.engine.present(self.last_pair, self.composition, decorate)
                elif decorate is not None:
                    self.engine.present_surface(decorate)
                self._redraw = False
        return presented

    def tick(self, decorate=None):
        """Poll current GPIO and process at most one source frame per UI tick.

        Return True only for a newly submitted source/SR pair. A repaint of the
        same pair is excluded. Submission is not proof of a monitor frame or a
        scanout acknowledgement and is labelled accordingly in status.
        """
        try:
            event = self.controls.poll_engine(self.engine)
            if event is not None:
                self._control_event(event)
            if self.import_active:
                return self._tick_import(decorate)
            if self.export_active:
                return self._tick_export(decorate)
            mode = self.engine.mode
            if self._metrics_started is None:
                self.reset_measurement()
            processed_before = self.engine.processed_frames
            pair = None
            if mode == "image" and self.image is not None and self.image_dirty:
                pair, _ = self.engine.process_roi(self.image, self.roi)
                self.image_dirty = False
                self.last_source_index = None
            elif mode == "video" and self.playback is not None:
                request = self.playback.request()
                if request is not None:
                    pair = self.engine.process(request.source_rgb, request.source_time)
                    # Engine and playback have separate token sequences. Never
                    # feed a playback token to the display generation gate.
                    if not self.playback.accept(request.pair(pair.output_rgb)):
                        pair = None
                    else:
                        self.last_source_index = request.source_index
            elif mode == "camera":
                frame = self.camera.poll()
                if self.camera_frozen:
                    if self._redraw:
                        self.engine.present(self.last_pair, self.composition, decorate)
                        self._redraw = False
                    return False
                if frame is not None and self._camera_resume_after is not None:
                    if frame.captured_monotonic < self._camera_resume_after:
                        frame = None
                    else:
                        self._camera_resume_after = None
                if getattr(self.engine, 'camera_pipeline_enabled', False):
                    return self._tick_camera_pipeline(frame, decorate)
                if frame is not None:
                    if frame.generation != self._camera_generation:
                        self._invalidate()
                        self._camera_generation = frame.generation
                    elapsed = max(0, frame.captured_monotonic - self._camera_started)
                    pair = self.engine.process(frame.rgb, elapsed)
                    self.last_source_index = frame.sequence
            if pair is not None:
                self.processed_source_frames += self.engine.processed_frames - processed_before
                # Decorators must see the current pair, including its real dimensions.
                if mode == 'image':
                    self.last_pair = pair
                if mode == 'image' and getattr(self, 'image_reference', None) is not None:
                    from .metrics import QualityWorker
                    if self.quality_worker is None:
                        self.quality_worker = QualityWorker()
                    key = (self.image_pair_index, pair.scale, self.roi.x, self.roi.y,
                           self.roi.width, self.roi.height, pair.token.generation, pair.token.sequence)
                    self.quality_worker.submit(key, pair, self.quality_reference())
                if self.engine.present(pair, self.composition, decorate):
                    self.last_pair = pair
                    self._redraw = False
                    self.submitted_new_frames += 1
                    self.last_error = None
                    return True
            elif self._redraw and self.last_pair is not None and mode != "idle":
                self.engine.present(self.last_pair, self.composition, decorate)
                self._redraw = False
            elif self._redraw and decorate is not None:
                self.engine.present_surface(decorate)
                self._redraw = False
            return False
        except Exception as exc:
            self.last_error = f"{type(exc).__name__}: {exc}"
            raise

    def status(self):
        pair = self.last_pair
        elapsed = None
        processing_fps = new_frame_fps = None
        if self._metrics_started is not None:
            elapsed = max(0.0, self.clock() - self._metrics_started)
            if elapsed > 0:
                processing_fps = (self.processed_source_frames - self._metrics_processed_base) / elapsed
                new_frame_fps = (self.submitted_new_frames - self._metrics_submitted_base) / elapsed
        return {"mode": self.engine.mode, "scale": self.engine.scale,
                "business_profile": self.profile.metadata(),
                "identity_marker_enabled": self.engine.identity_marker,
                "generation": self.engine.generation,
                "raw_switches": self.raw_switches, "layout": self.composition.layout,
                "pixel_view": self.composition.pixel_view,
                "pan": [self.composition.pan_x, self.composition.pan_y],
                "wipe_fraction": self.composition.wipe_fraction,
                "camera_frozen": self.camera_frozen, "frozen_metadata": self.frozen_metadata,
                "snapshot_export": self.snapshot_status(),
                "image_quality": self.quality_status(),
                "image_pair_name": self.image_record['name'] if getattr(self,'image_record',None) else None,
                "source_index": self.last_source_index, "submitted_new_frames": self.submitted_new_frames,
                "processed_source_frames": self.processed_source_frames,
                "window_submitted_pairs": self.submitted_new_frames - self._metrics_submitted_base,
                "measurement_elapsed_seconds": elapsed,
                "processing_fps": processing_fps,
                "submitted_pair_fps": new_frame_fps,
                "rate_scope": "current mode/scale/layout; submissions, not scan acknowledgements",
                "input_size": list(pair.source_rgb.shape[1::-1]) if pair is not None else None,
                "sr_size": list(pair.output_rgb.shape[1::-1]) if pair is not None else None,
                "last_timings": dict(self.engine.last_timings), "last_error": self.last_error,
                "camera": self.camera.status(), "controls": self.controls.status(),
                "video_export": dict(self.export_status), "video_export_history": list(self.export_history),
                "video_import": dict(self.import_status), "video_import_history": list(self.import_history)}

    def close(self):
        if getattr(self, 'quality_worker', None) is not None:
            self.quality_worker.close()
        if self.snapshot_export is not None:
            self.snapshot_export.cancelled.set()
        self.cancel_video_import('runtime_close')
        self.cancel_video_export('runtime_close')
        self.camera.stop()
        self.engine.close()
        if self.snapshot_export is not None:
            self.snapshot_export.close()
        if self.playback is not None:
            self.playback.clip.close()
            self.playback = None
        self.last_pair = None
        self._camera_pending = None
        self.camera_frozen = False
        self.frozen_metadata = None
