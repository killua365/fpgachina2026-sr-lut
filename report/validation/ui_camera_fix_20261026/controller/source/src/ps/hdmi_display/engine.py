"""Serialized SR/ROI/display lifecycle for the two HDMI Overlays.

The application owns source producers and must stop them before mode/scale
changes. No background thread in this module accesses GPIO during reloading.
Package identity and M0 deployment checks belong to the installation/launcher
gate; constructing an Engine itself does not load any hardware.
"""

from __future__ import annotations

from pathlib import Path
from dataclasses import replace
import math
import time

import numpy as np

from roi_magnifier.geometry import plan_roi
from roi_magnifier.mode_control import BoardModeGPIO, MODE_LEDS
from roi_magnifier.processor import validate_source_image
from roi_magnifier.scale_control import BoardScaleGPIO, ScaleController
from sr_lut_x4_driver import CONTROL_SOFT_RESET, REG_CONTROL
from video_pipeline.frame_session import ReusableFrameSession

from .compositor import Composition, Compositor
from .frames import FrameGeneration, FramePair, FrameToken, BorrowedFramePair, DirectFramePair
from .frame_marker import draw as draw_frame_marker
from .scanout import Scanout, MODE_720P60, MODE_1080P30
from .pixels import blit_rgb, unpack_rgb, fused_unpack_rgb, paint_nearest, fill_rgbx
from .profiles import get_profile, NEXYS
from sr_lut_x4_driver import DriverError


def wait_until(predicate, timeout, message):
    deadline = time.monotonic() + timeout
    while not predicate():
        if time.monotonic() >= deadline:
            raise TimeoutError(message)
        time.sleep(0.001)


def halt_frame_dma(mmio, timeout=2.0):
    for offset in (0, 0x30):
        mmio.write(offset, mmio.read(offset) & ~1)
    try:
        wait_until(lambda: all(mmio.read(offset + 4) & 1 for offset in (0, 0x30)),
                   timeout, "SR DMA halt timed out")
    except TimeoutError:
        # AXI DMA reset affects both channels. Never free buffers before the
        # reset self-clears and both channels report Halted (AMD PG021).
        mmio.write(0, 4)
        wait_until(lambda: not mmio.read(0) & 4 and
                   all(mmio.read(offset + 4) & 1 for offset in (0, 0x30)),
                   timeout, "SR DMA cannot halt/reset; retaining its buffers")


class HdmiFrameSession(ReusableFrameSession):
    def __init__(self, overlay, *args, fused_camera_copy=False, **kwargs):
        from .acp_receive import qualified_overlay
        self.acp_completion_qualified = qualified_overlay(overlay)
        super().__init__(overlay, *args, **kwargs)
        self.buffer_epochs = [0] * self.buffer_count
        self.next_camera_buffer = 0
        self.defer_validation = False
        self.fused_camera_copy = fused_camera_copy

    def run_camera_direct(self, source, index, overlap=None):
        if (self.buffer_count != 3 or self.owns_output_buffers or self.scale != 4
                or self.top_byte_validation != 'full' or not self.acp_completion_qualified):
            raise DriverError('Direct camera requires qualified ACP and reserved display outputs')
        self.buffer_epochs[index] += 1
        self.defer_validation = True
        try:
            result = self._run(source, 'rgb', output_order='rgbx', zero_copy=True,
                               buffer_index=index, overlap=overlap)
        finally:
            self.defer_validation = False
        return result, index, self.buffer_epochs[index]

    def run_camera(self, source, overlap=None):
        if self.buffer_count != 2 or self.scale != 4 or self.top_byte_validation != 'full':
            raise DriverError('Camera pipeline requires two x4 buffers and full validation')
        index = self.next_camera_buffer
        self.buffer_epochs[index] += 1
        self.defer_validation = self.fused_camera_copy
        try:
            result = self._run(source, 'rgb', output_order='rgbx', zero_copy=True,
                               buffer_index=index, overlap=overlap)
        finally:
            self.defer_validation = False
        self.next_camera_buffer = 1 - index
        return result, index, self.buffer_epochs[index]

    def _wait_transfer(self, channel):
        from .acp_receive import qualified_channel, wait_acp_receive
        if (channel is self.dma.recvchannel and self.output_transfer_coherency == 'acp-hardware'
                and self.acp_completion_qualified and getattr(self, 'use_acp_completion', True)
                and qualified_channel(channel)):
            return wait_acp_receive(channel, self.timeout_seconds)
        return super()._wait_transfer(channel)

    def _pack(self, frame, order, input_buffer):
        if order != 'rgb':
            return super()._pack(frame, order, input_buffer)
        target = np.asarray(input_buffer).view(np.uint8).reshape(self.height, self.width, 4)
        # DMA input X is always zero. Avoid reading the uncached destination
        # just to retain that byte: write complete RGBX words in one pass.
        if paint_nearest(target, frame):
            self.selected_input_packer = 'neon-rgbx-zero-x'
        else:
            blit_rgb(target, frame)
            target[..., 3] = 0
            self.selected_input_packer = 'opencv-rgb-mixchannels'

    def _unpack(self, output_buffer, order, destination=None, *, zero_copy=False):
        if order == 'rgbx' and zero_copy and self.top_byte_validation == 'full':
            from .pixels import validate_rgbx
            source = np.asarray(output_buffer).view(np.uint8).reshape(self.output_height, self.output_width, 4)
            if self.defer_validation:
                # Full validation is mandatory in present(), before decoration
                # or VDMA submission, while the next PL frame is in flight.
                return source, 0.0, 0.0, 0, False, 'rgbx-awaiting-display-validation'
            started = time.perf_counter()
            if not validate_rgbx(source):
                raise DriverError('output DMA word has a non-zero top byte')
            return source, time.perf_counter()-started, 0.0, self.output_pixels, True, 'validated-rgbx-view'
        if order != 'rgb' or zero_copy:
            return super()._unpack(output_buffer, order, destination, zero_copy=zero_copy)
        if self.top_byte_validation == 'full':
            source = np.asarray(output_buffer).view(np.uint8).reshape(self.output_height, self.output_width, 4)
            image = (np.empty((self.output_height, self.output_width, 3), np.uint8)
                     if destination is None else np.asarray(destination))
            started = time.perf_counter()
            valid = fused_unpack_rgb(source, image)
            if valid is not None:
                elapsed = time.perf_counter() - started
                if not valid:
                    raise DriverError('output DMA word has a non-zero top byte')
                self.selected_bgr_converter = 'neon-rgb-copy-with-full-validation'
                # The two operations share one pass; combined time is accounted
                # once in validation rather than counted a second time in copy.
                return image, elapsed, 0.0, self.output_pixels, True, self.selected_bgr_converter
        # Retain the inherited full top-byte check and all DMA/status checks.
        source, validation, _, words, full, _ = super()._unpack(
            output_buffer, 'rgbx', zero_copy=True)
        started = time.perf_counter()
        image = unpack_rgb(source, destination)
        self.selected_bgr_converter = 'opencv-rgba-to-rgb'
        return image, validation, time.perf_counter() - started, words, full, self.selected_bgr_converter

    def _recover(self):
        # Preserve protocol evidence before soft reset clears the error registers.
        self.last_fault = {
            'control': {hex(offset): int(self.control.read(offset))
                        for offset in (0x04, 0x08, 0x0c, 0x10, 0x14, 0x18, 0x34)},
            'dma': {hex(offset): int(self.dma.mmio.read(offset)) for offset in (0x04, 0x34)},
            'configured_scale': self.scale,
        }
        halt_frame_dma(self.dma.mmio)
        self.control.write(REG_CONTROL, CONTROL_SOFT_RESET)

    def close(self):
        if getattr(self, "closed", False):
            return
        try:
            wait_until(lambda: all(self.dma.mmio.read(offset + 4) & 3 for offset in (0, 0x30)),
                       2.0, "SR DMA still owns a frame buffer")
        except TimeoutError:
            self._recover()
        super().close()


def load_hdmi_overlay(directory, scale):
    from pynq import Overlay

    directory = Path(directory)
    bit = directory / f"sr_lut_x{scale}.bit"
    hwh = directory / f"sr_lut_x{scale}.hwh"
    if not bit.is_file() or not hwh.is_file():
        raise FileNotFoundError("The selected HDMI BIT/HWH pair is incomplete")
    # No fallback to a legacy x4 package when a requested HDMI file is missing.
    return Overlay(str(bit))


class Engine:
    def __init__(self, overlays, *, loader=load_hdmi_overlay,
                 display_factory=Scanout.from_overlay, session_factory=HdmiFrameSession,
                 display_mode=None, profile=None, identity_marker=False, frame_observer=None,
                 mode_gpio_factory=BoardModeGPIO.open, scale_gpio_factory=BoardScaleGPIO.open):
        if set(overlays) != {2, 4}:
            raise ValueError("Both HDMI x2 and x4 directories are required")
        self.overlays = dict(overlays)
        self.loader, self.display_factory, self.session_factory = loader, display_factory, session_factory
        self.profile = get_profile(NEXYS if profile is None and display_mode == MODE_1080P30 else profile)
        display_mode = display_mode or self.profile.hdmi
        if display_mode not in (MODE_720P60, MODE_1080P30):
            raise ValueError("Unsupported HDMI display mode")
        if display_mode != self.profile.hdmi:
            raise ValueError('Display mode and business profile disagree')
        self.display_mode = display_mode
        if type(identity_marker) is not bool:
            raise ValueError('Identity marker switch must be boolean')
        if identity_marker and self.profile.dp is None:
            raise ValueError('Frame identity markers require the dual-board profile')
        self.identity_marker = identity_marker
        self.frame_observer = frame_observer
        self.mode_gpio_factory, self.scale_gpio_factory = mode_gpio_factory, scale_gpio_factory
        self.overlay = self.display = self.session = None
        self.mode_gpio = self.scale_gpio = None
        self.mode, self.scale = "idle", None
        self.generations = FrameGeneration()
        self.generation = 0
        self.compositor = Compositor()
        self.last_presented_sequence = -1
        self.processed_frames = 0
        self.last_timings = {}
        self.faulted = False
        self.camera_pipeline_enabled = session_factory is HdmiFrameSession
        self.camera_direct_enabled = session_factory is HdmiFrameSession and display_factory == Scanout.from_overlay
        from .pixels import _native
        self.camera_copy_enabled = (self.camera_direct_enabled and self.display_mode == MODE_1080P30
            and _native is not None and hasattr(_native, 'hdmi_copy_rgbx_checked'))
        if self.camera_copy_enabled:
            self.camera_direct_enabled = False
        self._split_slots = {}

    def _invalidate(self):
        self._split_slots.clear()
        self.compositor.clear_cache()
        if self.display is not None and hasattr(self.display, 'reserved'):
            for lease in list(self.display.reserved.values()):
                self.display.discard_reserved(lease)
        self.generation = self.generations.invalidate()
        self.last_presented_sequence = -1

    def invalidate_source(self):
        """Discard results after a source/ROI/seek change on the owning thread."""
        self._invalidate()

    def _release(self, *, keep_overlay=False):
        # Failure deliberately aborts reconfiguration with references retained.
        if self.overlay is not None:
            halt_frame_dma(self.overlay.axi_dma_0.mmio)
        if self.session is not None:
            self.session.close()
            self.session = None
        if self.display is not None:
            self.display.stop()
            self.display = None
        if not keep_overlay:
            self.mode_gpio = self.scale_gpio = self.overlay = None
            self.scale = None

    def select(self, mode, raw_switches):
        if mode not in ("idle", "image", "video", "camera"):
            raise ValueError("Unknown input mode")
        scale = 4 if mode == "camera" else ScaleController(
            raw_switches=raw_switches, board_control_only=True).selected_scale()
        # BTN3 keeps the current valid display configuration, regardless of SW.
        if mode == "idle" and self.scale is not None and not self.faulted:
            scale = self.scale
        if mode != self.mode or scale != self.scale or self.faulted:
            self._invalidate()
        cacheable = not (mode == 'camera' and self.camera_copy_enabled)
        if mode == 'idle' and self.display is not None:
            cacheable = getattr(self.display, 'cacheable', True)
        change_cache = (self.camera_copy_enabled and self.display is not None
                        and self.display.cacheable != cacheable)
        reload_overlay = scale != self.scale or self.faulted
        if reload_overlay or change_cache:
            self.faulted = True
            # A display-buffer policy change needs new DMA buffers, but the
            # same-scale SR core and GPIO remain valid without reprogramming PL.
            self._release(keep_overlay=not reload_overlay)
            if reload_overlay:
                self.overlay = self.loader(self.overlays[scale], scale)
            if self.display_mode == MODE_720P60:
                self.display = self.display_factory(self.overlay)
            else:
                self.display = self.display_factory(self.overlay, mode=self.display_mode,
                    **({'cacheable':cacheable} if self.camera_copy_enabled else {}))
            self.display.start()
            self.mode_gpio = self.mode_gpio_factory(overlay=self.overlay)
            self.scale_gpio = self.scale_gpio_factory(overlay=self.overlay)
            self.scale = scale
            self.faulted = False
        self.mode = mode
        self.mode_gpio.write_leds(MODE_LEDS[mode])

    def controls(self):
        if self.overlay is None or self.faulted:
            raise RuntimeError("GPIO is unavailable until the HDMI Overlay is ready")
        return self.mode_gpio.read_buttons(), self.scale_gpio.read_switches()

    def process(self, source_rgb, source_time=0.0, *, camera_overlap=None, borrow_camera=False, camera_direct=False):
        if self.overlay is None or self.faulted or self.mode == "idle":
            raise RuntimeError("Choose an active input mode before processing")
        if not math.isfinite(source_time) or source_time < 0:
            raise ValueError("Source time must be finite and nonnegative")
        height, width = validate_source_image(source_rgb)
        if width > 480 or height > 320:
            raise ValueError("Use an ROI or the demo input profile for larger images")
        if borrow_camera and (self.mode != 'camera' or self.scale != 4):
            raise ValueError('Borrowed buffers are restricted to x4 camera processing')
        if camera_direct and (not borrow_camera or (width*4, height*4) != (self.display_mode.width, self.display_mode.height)):
            raise ValueError('Direct camera requires a full-size x4 borrowed frame')
        count = 3 if camera_direct else 2 if borrow_camera else 1
        if (self.session is None or (self.session.width, self.session.height) != (width, height)
                or getattr(self.session, 'buffer_count', 1) != count):
            if self.session is not None:
                self.session.close()
                self.session = None
            self.session = self.session_factory(self.overlay, width, height, scale=self.scale,
                                                buffer_count=count,
                                                **({'external_output_buffers': self.display.buffers} if camera_direct else {}),
                                                **({'fused_camera_copy':True} if self.camera_copy_enabled and borrow_camera else {}),
                                                output_buffer_policy="acp-cacheable", timeout_seconds=2.0)
        token = self.generations.issue()
        copy_started = time.monotonic()
        source = np.array(source_rgb, copy=True, order="C")
        copy_seconds = time.monotonic() - copy_started
        lease = self.display.reserve() if camera_direct else None
        try:
            if camera_direct:
                result, index, epoch = self.session.run_camera_direct(source, lease.index, camera_overlap)
            elif borrow_camera:
                result, index, epoch = self.session.run_camera(source, camera_overlap)
            else:
                result = self.session.run_rgb(source)
        except BaseException:
            self.faulted = True
            self._invalidate()
            raise
        self.processed_frames += 1
        self.last_timings = {"prepare_seconds": copy_seconds + result.pack_seconds,
                             "dma_sr_seconds": result.dma_and_pl_seconds,
                             "unpack_seconds": result.unpack_seconds,
                             "validation_seconds": getattr(result, 'output_validation_seconds', None),
                             "color_convert_seconds": getattr(result, 'color_convert_seconds', None),
                             "processing_seconds": copy_seconds + result.total_seconds}
        metrics = getattr(result, 'hardware_metrics', None)
        if metrics is not None:
            self.last_timings.update(pl_frame_cycles=metrics.frame_cycles,
                pl_clock_hz=metrics.clock_hz, pl_frame_seconds=metrics.frame_seconds,
                pl_input_backpressure_cycles=metrics.input_backpressure_cycles,
                pl_output_backpressure_cycles=metrics.output_backpressure_cycles,
                pl_input_starvation_cycles=metrics.input_starvation_cycles)
        if borrow_camera:
            view = result.image[..., :3].view()
            view.flags.writeable = False
            args = (token, source, view, self.scale, source_time, self.session, index, epoch)
            return DirectFramePair(*args, lease) if camera_direct else BorrowedFramePair(*args)
        return FramePair(token, source, result.image, self.scale, source_time)

    def process_roi(self, source_rgb, roi):
        height, width = validate_source_image(source_rgb)
        if self.scale is None:
            raise RuntimeError("HDMI Overlay is not loaded")
        plan = plan_roi(width, height, roi.x, roi.y, roi.width, roi.height, self.scale)
        window = plan.process_window
        pair = self.process(source_rgb[window.y:window.bottom, window.x:window.right])
        crop = plan.output_crop
        result = np.ascontiguousarray(pair.output_rgb[crop.y:crop.bottom, crop.x:crop.right])
        original = np.ascontiguousarray(source_rgb[roi.y:roi.bottom, roi.x:roi.right]).copy()
        return FramePair(pair.token, original, result, self.scale, 0), plan

    def snapshot_pair(self, pair):
        if isinstance(pair, DirectFramePair):
            raise RuntimeError('Freeze requires the qualified camera copy path')
        if pair.token.generation != self.generation:
            raise RuntimeError('Camera frame changed; retry Freeze')
        if isinstance(pair, BorrowedFramePair):
            if not pair.valid:
                raise RuntimeError('Camera buffer was recycled; retry Freeze')
            h, w = pair.output_rgb.shape[:2]
            raw = np.asarray(pair.session.output_buffers[pair.buffer_index]).view(np.uint8).reshape(h, w, 4)
            output = np.empty((h, w, 3), np.uint8)
            valid = fused_unpack_rgb(raw, output)
            if valid is None:
                from .pixels import validate_rgbx
                valid = validate_rgbx(raw)
                output[:] = raw[..., :3]
            if not valid:
                self.faulted = True
                raise DriverError('Snapshot SR output contains a non-zero X byte')
        else:
            output = np.array(pair.output_rgb, copy=True, order='C')
        source = np.array(pair.source_rgb, copy=True, order='C')
        source.flags.writeable = output.flags.writeable = False
        return FramePair(pair.token, source, output, pair.scale, pair.source_time)

    def present(self, pair, composition=Composition(), decorate=None):
        if self.display is None or self.faulted:
            raise RuntimeError("Display is unavailable")
        if isinstance(pair, BorrowedFramePair) and not isinstance(pair, DirectFramePair) and not pair.valid:
            raise DriverError('Camera result buffer was recycled or closed')
        if pair.scale != self.scale or pair.token.generation != self.generation or pair.token.sequence < self.last_presented_sequence:
            return False
        if isinstance(pair, DirectFramePair):
            if self.display.owns_reservation(pair.display_lease):
                if not pair.valid or composition.layout != 'sr' or composition.pixel_view:
                    raise DriverError('Direct camera lease/composition mismatch')
                return self._present_direct(pair, decorate)
            # A submitted buffer includes decorations and may now be scanned.
            # Regenerate only for an explicit UI repaint, retaining identity.
            regenerated = self.process(pair.source_rgb, pair.source_time)
            pair = replace(regenerated, token=pair.token)
        if isinstance(pair, BorrowedFramePair) and not pair.valid:
            raise DriverError('Camera result buffer was recycled or closed')
        if isinstance(pair, BorrowedFramePair) and self.camera_copy_enabled:
            if composition.layout in ('sr','split','wipe') and not composition.pixel_view:
                return self._present_camera_copy(pair, composition, decorate)
            # Other compositions retain their existing pixels and full check.
            from .pixels import validate_rgbx
            raw = np.asarray(pair.session.output_buffers[pair.buffer_index]).view(np.uint8).reshape(1080,1920,4)
            if not validate_rgbx(raw):
                self.faulted = True
                raise DriverError('output DMA word has a non-zero top byte')
        lease = self.display.acquire()
        started = time.monotonic()
        ui = getattr(decorate, '__self__', None)
        layout = ui.compare_layout(pair,composition) if hasattr(ui,'compare_layout') else None
        key = ui.camera_cache_key() if hasattr(ui, 'camera_cache_key') else None
        split_key = ((self.generation, composition, key) if self.mode == 'camera'
            and composition.layout == 'split' and not composition.pixel_view and key is not None
            and pair.source_rgb.shape == (270,480,3) and lease.pixels.shape == (1080,1920,4) else None)
        partial = (split_key is not None and self._split_slots.get(lease.index) == split_key
                   and getattr(ui, '_camera_ui_key', None) == key)
        try:
            if partial:
                self.compositor.compose(pair, lease.pixels, composition, preserve_letterbox=True,compare_layout=layout)
                ui.draw_camera_band(lease.pixels, 270, 810)
            elif not (hasattr(ui, 'replaces_content') and ui.replaces_content()):
                self.compositor.compose(pair, lease.pixels, composition,compare_layout=layout)
            if decorate is not None and not partial:
                decorate(lease.pixels)
            if self.identity_marker:
                draw_frame_marker(lease.pixels, pair.token, self.mode, pair.scale)
        except BaseException:
            self.display.discard(lease)
            raise
        composed = time.monotonic()
        try:
            self.display.submit(lease, identity=(pair.token.generation, pair.token.sequence),
                **({'dirty_rows': [(0,2),(270,810)]} if partial else {}))
        except BaseException:
            self.faulted = True
            raise
        self.last_presented_sequence = pair.token.sequence
        if hasattr(lease, 'index'):
            self._split_slots[lease.index] = split_key
        self.last_timings.update(compose_seconds=composed - started,
                                 display_submit_seconds=time.monotonic() - composed)
        if self.frame_observer is not None:
            self.frame_observer(pair.token, self.mode, pair.scale)
        return True

    def _present_camera_copy(self, pair, composition, decorate):
        from .pixels import copy_rgbx_checked
        lease = self.display.acquire()
        raw = np.asarray(pair.session.output_buffers[pair.buffer_index]).view(np.uint8).reshape(1080,1920,4)
        target = np.asarray(lease.pixels)
        started = time.monotonic()
        ui = getattr(decorate, '__self__', None)
        key = ui.camera_cache_key() if hasattr(ui, 'camera_cache_key') else None
        split_key = (self.generation, composition, key) if key is not None else None
        partial = split_key is not None and self._split_slots.get(lease.index) == split_key and ui._camera_ui_key == key
        try:
            if composition.layout == 'wipe':
                from .pixels import camera_wipe_checked
                self.compositor.clear_cache()
                if not 0 <= composition.wipe_fraction <= 1:
                    raise ValueError('Invalid wipe fraction')
                valid = camera_wipe_checked(raw, pair.source_rgb, target,
                                            round(target.shape[1]*composition.wipe_fraction))
                partial = False
            elif composition.layout == 'sr':
                first, last = ui.camera_content_band() if partial else (0,1080)
                from .pixels import validate_rgbx
                # Toolbar-covered SR pixels still receive complete X validation.
                # Only writes hidden behind unchanged, already-drawn UI are saved.
                valid = True
                if first:
                    valid &= validate_rgbx(raw[:first])
                if last < 1080:
                    valid &= validate_rgbx(raw[last:])
                if first < last:
                    valid &= copy_rgbx_checked(raw[first:last],target[first:last])
            else:
                first, last = 270,810
                if not partial:
                    background = (17,23,32,0) if hasattr(ui,'compare_layout') else 0
                    fill_rgbx(target[:270],background)
                    fill_rgbx(target[810:],background)
                valid = copy_rgbx_checked(raw,target[270:810,960:])
                if not paint_nearest(target[270:810,:960],pair.source_rgb):
                    from .compositor import resize_nearest
                    target[270:810,:960,3]=0
                    blit_rgb(target[270:810,:960],resize_nearest(pair.source_rgb,(960,540)))
            if not valid:
                raise DriverError('output DMA word has a non-zero top byte')
            copied = time.monotonic()
            if partial:
                ui.draw_camera_band(target,first,last)
            elif decorate is not None:
                decorate(target)
            if self.identity_marker:
                draw_frame_marker(target,pair.token,self.mode,pair.scale)
            composed = time.monotonic()
            # Keep the allocator/driver's sync API even for uncached buffers.
            self.display.submit(lease,identity=(pair.token.generation,pair.token.sequence))
        except BaseException:
            self.faulted = True
            if self.display.writing is lease:
                self.display.discard(lease)
            raise
        self._split_slots[lease.index] = split_key
        self.last_presented_sequence = pair.token.sequence
        self.last_timings.update(compose_seconds=composed-started,
            fused_copy_validation_seconds=copied-started,
            display_submit_seconds=time.monotonic()-composed,
            full_x_validation_fused=True)
        if self.frame_observer is not None:
            self.frame_observer(pair.token,self.mode,pair.scale)
        return True

    def _present_direct(self, pair, decorate):
        from .pixels import validate_rgbx
        lease = pair.display_lease
        self._split_slots.pop(lease.index, None)
        started = time.monotonic()
        try:
            if not validate_rgbx(np.asarray(lease.pixels)):
                raise DriverError('output DMA word has a non-zero top byte')
            validated = time.monotonic()
            if decorate is not None:
                decorate(lease.pixels)
            if self.identity_marker:
                draw_frame_marker(lease.pixels, pair.token, self.mode, pair.scale)
            composed = time.monotonic()
            # ACP and HP1 still require the full cache clean before scanout.
            self.display.submit_reserved(lease, identity=(pair.token.generation, pair.token.sequence))
        except BaseException:
            self.faulted = True
            raise
        self.last_presented_sequence = pair.token.sequence
        self.last_timings.update(compose_seconds=composed-validated,
            display_validation_seconds=validated-started,
            display_submit_seconds=time.monotonic()-composed, direct_camera_display=True)
        if self.frame_observer is not None:
            self.frame_observer(pair.token, self.mode, pair.scale)
        return True

    def close(self):
        self._invalidate()
        self._release()
        self.mode = "idle"

    def present_surface(self, draw):
        """Repaint idle/capture-wait/UI without asserting a new SR frame."""
        if self.display is None or self.faulted:
            raise RuntimeError("Display is unavailable")
        lease = self.display.acquire()
        self._split_slots.clear()
        try:
            lease.pixels.fill(0)
            draw(lease.pixels)
            if self.identity_marker:
                draw_frame_marker(lease.pixels, FrameToken(self.generation, 0), 'idle', 0, has_result=False)
        except BaseException:
            self.display.discard(lease)
            raise
        try:
            self.display.submit(lease)
        except BaseException:
            self.faulted = True
            raise
