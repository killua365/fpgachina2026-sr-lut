"""Fixed HDMI VTC profiles and parked MM2S VDMA with three RGBX buffers.

Register definitions follow AMD PG020 / embeddedsw xilinx_v2025.2 xvtc and
PYNQ v3.0.1 video.dma. PYNQ supplies MMIO and CMA allocation on the board.
The fixed clock design has no axi_dynclk, so PYNQ's HDMIOut frontend cannot
configure it. This module owns the VDMA exclusively; do not also start its
PYNQ writechannel. Hardware scanout acceptance is separate from host tests.
"""

from __future__ import annotations

from dataclasses import dataclass
import math
import time

import numpy as np


class DisplayError(RuntimeError):
    pass


@dataclass(frozen=True)
class DisplayMode:
    """Validated HDMI raster mode used by both the diagnostic and business paths.

    720p60 remains the default for backwards compatibility. 1080p30 is an
    opt-in mode and must be enabled by a separately implemented/verified
    Vivado design; selecting it here does not claim that the hardware passed
    timing or performance acceptance.
    """

    width: int
    height: int
    refresh_hz: int
    pixel_clock_hz: int
    name: str

    @property
    def frame_bytes(self):
        return self.width * self.height * 4


MODE_720P60 = DisplayMode(1280, 720, 60, 74_250_000, "1280x720@60")
MODE_1080P30 = DisplayMode(1920, 1080, 30, 74_250_000, "1920x1080@30")
MODE_1080P60 = DisplayMode(1920, 1080, 60, 148_500_000, "1920x1080@60")


class FixedTiming:
    """VTC programming for the supported fixed-raster profiles."""

    def __init__(self, mmio, mode=MODE_720P60, trace=None):
        if mode not in (MODE_720P60, MODE_1080P30, MODE_1080P60):
            raise ValueError("Unsupported HDMI display mode")
        self.mmio, self.mode = mmio, mode
        self.trace = trace or (lambda _: None)

    @property
    def width(self): return self.mode.width

    @property
    def height(self): return self.mode.height

    @property
    def refresh_hz(self): return self.mode.refresh_hz

    def _write(self, offset, value):
        self.trace(f"vtc_write_before offset=0x{offset:x} value=0x{value:x}")
        self.mmio.write(offset, value)
        self.trace(f"vtc_write_done offset=0x{offset:x}")

    def configure(self):
        self._write(0, 0)
        # XVtc_ConvTiming2Signal aligns VSync to HSync; vertical start is
        # active + front porch - 1, not the unadjusted porch boundary.
        if self.mode is MODE_720P60:
            values = {
                0x60: (720 << 16) | 1280, 0x68: 0, 0x6C: 0x7F,
                0x70: 1650, 0x74: (750 << 16) | 750,
                0x78: (1430 << 16) | 1390,
                0x7C: (1280 << 16) | 1280, 0x80: (729 << 16) | 724,
                0x84: (1390 << 16) | 1390, 0x88: (1280 << 16) | 1280,
                0x8C: (729 << 16) | 724, 0x90: (1390 << 16) | 1390,
                0x94: 720 << 16,
            }
        else:
            # CEA-861 1080p progressive: 2200x1125 total. Both 1080p30
            # (74.25 MHz) and 1080p60 (148.5 MHz) use H front/sync/back
            # porches 88/44/148 and V front/sync/back 4/5/36.
            # Registers use the XVtc_ConvTiming2Signal mode-1 convention:
            # sync start = active + front - 1 for vertical timing.
            values = {
                0x60: (1080 << 16) | 1920, 0x68: 0, 0x6C: 0x7F,
                0x70: 2200, 0x74: (1125 << 16) | 1125,
                0x78: (2052 << 16) | 2008,
                0x7C: (1920 << 16) | 1920, 0x80: (1088 << 16) | 1083,
                0x84: (2008 << 16) | 2008, 0x88: (1920 << 16) | 1920,
                0x8C: (1088 << 16) | 1083, 0x90: (2008 << 16) | 2008,
                0x94: 1080 << 16,
            }
        for offset, value in values.items():
            self._write(offset, value)
        self._write(0, 0x03FDEF00 | 0x07)

    def stop(self):
        self._write(0, 0)


class Fixed720Timing(FixedTiming):
    """Compatibility name retained for existing callers and tests."""

    def __init__(self, mmio, trace=None):
        super().__init__(mmio, MODE_720P60, trace)


@dataclass(frozen=True, eq=False)
class WriteBuffer:
    index: int
    pixels: np.ndarray


class Scanout:
    """Single-threaded owner of free, writing, pending and scanning buffers.

    submit() transfers ownership; callers must discard the lease and array
    references. The previous scan buffer is recycled only after hardware's
    current frame-store register acknowledges the pending slot. A timeout on
    stop retains allocations until halt/reset is confirmed, preventing DMA
    from reading freed memory. No calls contain unbounded polling loops.
    """

    def __init__(self, dma_mmio, status_mmio, timing, allocator, *, mode=MODE_720P60,
                 timeout=2.0, clock=time.monotonic, sleep=time.sleep, trace=None, cacheable=True):
        if not math.isfinite(timeout) or timeout <= 0:
            raise ValueError("A finite positive display timeout is required")
        if mode not in (MODE_720P60, MODE_1080P30, MODE_1080P60):
            raise ValueError("Unsupported HDMI display mode")
        self.dma, self.monitor, self.timing, self.mode = dma_mmio, status_mmio, timing, mode
        self.allocator, self.timeout = allocator, timeout
        if type(cacheable) is not bool:
            raise ValueError('Scanout cache policy must be boolean')
        self.cacheable = cacheable
        self.clock, self.sleep = clock, sleep
        self.trace = trace or (lambda _: None)
        self.buffers = []
        self.active = None
        self.pending = None
        self.writing = None
        self.reserved = {}
        self.running = False
        self.submitted = 0
        self.acknowledged = 0
        self.pending_identity = self.last_acknowledged_identity = None
        self.acknowledged_source_pairs = 0
        self.acknowledged_generation = None
        self.generation_source_pairs = 0
        self.startup_samples = []
        self.last_flush_bytes = 0
        self.last_flush_strategy = None
        self.next_write = 0

    def _startup_sample(self, phase):
        errors = self.monitor.read(0x08)
        self.startup_samples.append({
            'phase': phase, 'monotonic': self.clock(),
            'scan_frames': self.monitor.read(0x00), 'locked': bool(errors & (1 << 30)),
            'underflow_events': errors & 0x7FFF, 'overflow_events': (errors >> 15) & 0x7FFF,
            'dma_status': self.dma.read(0x04)})

    def _wait(self, condition, message):
        deadline = self.clock() + self.timeout
        while not condition():
            if self.clock() >= deadline:
                raise TimeoutError(message)
            self.sleep(0.001)

    def start(self):
        if self.buffers or self.running:
            raise DisplayError("Display must be stopped before starting")
        if not self.dma.read(0x04) & 1:
            raise DisplayError("Display DMA is already owned by another runtime")
        self.startup_samples = []
        self._startup_sample('before_vdma_reset')
        self.trace("vdma_reset_before")
        self.dma.write(0, 4)
        self._wait(lambda: not self.dma.read(0) & 4, "Display DMA reset timed out")
        self.trace("vdma_reset_done")
        try:
            for _ in range(3):
                self.trace(f"display_allocate_before index={len(self.buffers)}")
                frame = self.allocator(shape=(self.mode.height, self.mode.width, 4), dtype=np.uint8, cacheable=self.cacheable)
                self.buffers.append(frame)
                address = int(frame.physical_address)
                if address % 8 or not 0 < address <= 0xFFFFFFFF - frame.nbytes:
                    raise DisplayError("Display buffer address is not a valid aligned 32-bit CMA range")
                frame.fill(0)
                frame.flush()
                self.trace(f"display_allocate_done address=0x{address:x}")
            for i, frame in enumerate(self.buffers):
                self.dma.write(0x5C + 4 * i, int(frame.physical_address))
            self.dma.write(0x28, 0)
            self.dma.write(0x54, self.mode.width * 4)
            self.dma.write(0x58, self.mode.width * 4)
            self.trace("vtc_configure_before")
            self._startup_sample('before_vtc_enable')
            self.timing.configure()
            self._startup_sample('after_vtc_enable')
            self.trace("vtc_configure_done")
            # Park mode, frame-count threshold 1, internal free-running sync.
            # VSIZE is written last to commit/start the configured transfer.
            self.dma.write(0, 0x00010001)
            self.dma.write(0x50, self.mode.height)
            self._wait(lambda: not self.dma.read(0x04) & 1, "Display DMA start timed out")
            self.active = 0
            self.running = True
            self._startup_sample('after_vdma_start')
            self.trace("vdma_started")
        except BaseException:
            self.stop()
            raise

    def _reap(self):
        if not self.running:
            raise DisplayError("Display is stopped")
        status = self.dma.read(0x04)
        if status & 0x71:
            raise DisplayError(f"Display DMA stopped or errored: 0x{status:08x}")
        current = (self.dma.read(0x28) >> 16) & 0x1F
        if self.pending is not None and current == self.pending:
            self.active, self.pending = self.pending, None
            self.acknowledged += 1
            identity, self.pending_identity = self.pending_identity, None
            if identity is not None and identity != self.last_acknowledged_identity:
                if identity[0] != self.acknowledged_generation:
                    self.acknowledged_generation = identity[0]
                    self.generation_source_pairs = 0
                self.acknowledged_source_pairs += 1
                self.generation_source_pairs += 1
                self.last_acknowledged_identity = identity
        elif current != self.active:
            raise DisplayError("Unexpected VDMA frame store; exclusive ownership was lost")

    def acquire(self) -> WriteBuffer:
        self._reap()
        if self.writing is not None:
            raise DisplayError("Only one CPU writer may own a display buffer")
        order = range(3) if self.cacheable else ((self.next_write+n)%3 for n in range(3))
        index = next(i for i in order if i not in (self.active, self.pending) and i not in self.reserved)
        self.next_write = (index+1)%3
        self.writing = WriteBuffer(index, self.buffers[index])
        return self.writing

    def reserve(self):
        """Reserve a free slot for SR DMA, retaining any prior ready slot.

        A pending VDMA switch is acknowledged before recycling its old slot.
        Only the application owner thread calls this API or submits a lease.
        """
        def available():
            self._reap()
            return any(i not in (self.active, self.pending) and i not in self.reserved
                       and (self.writing is None or i != self.writing.index) for i in range(3))
        self._wait(available, 'No free display slot for camera DMA')
        index = next(i for i in range(3) if i not in (self.active, self.pending)
                     and i not in self.reserved and (self.writing is None or i != self.writing.index))
        lease = WriteBuffer(index, self.buffers[index])
        self.reserved[index] = lease
        return lease

    def owns_reservation(self, lease):
        return self.reserved.get(lease.index) is lease

    def discard_reserved(self, lease):
        if not self.owns_reservation(lease):
            raise DisplayError('Invalid reserved display buffer')
        del self.reserved[lease.index]

    def submit_reserved(self, lease, *, identity=None):
        if not self.owns_reservation(lease):
            raise DisplayError('Invalid reserved display buffer')
        self._submit(lease, identity)
        del self.reserved[lease.index]

    def discard(self, lease: WriteBuffer):
        if lease is not self.writing:
            raise DisplayError("Invalid or already submitted display buffer")
        self.writing = None

    def submit(self, lease: WriteBuffer, *, identity=None, dirty_rows=None):
        if lease is not self.writing:
            raise DisplayError("Invalid or already submitted display buffer")
        self._submit(lease, identity, dirty_rows)
        self.writing = None

    def _submit(self, lease, identity, dirty_rows=None):
        if identity is not None:
            identity = tuple(identity)
            if len(identity) != 2 or any(type(value) is not int or value < 0 for value in identity):
                raise ValueError('Display frame identity must be a nonnegative generation/sequence pair')

        def slot_ready():
            self._reap()
            return self.pending is None

        self._wait(slot_ready, "Display frame switch timed out")
        self._flush(lease.pixels, dirty_rows)
        pointer = self.dma.read(0x28)
        self.dma.write(0x28, (pointer & ~0x1F) | lease.index)
        self.pending = lease.index
        self.pending_identity = identity
        self.submitted += 1

    def _flush(self, pixels, dirty_rows):
        self.last_flush_bytes = pixels.nbytes
        self.last_flush_strategy = 'full'
        if dirty_rows is not None:
            for first, last in dirty_rows:
                if not (type(first) is int and type(last) is int and 0 <= first < last <= self.mode.height):
                    raise ValueError('Invalid dirty row range')
        bo = getattr(pixels, 'bo', None)
        if (dirty_rows and type(bo).__module__ == 'pyxrt' and getattr(pixels, 'offset', 0) == 0
                and pixels.flags.c_contiguous and pixels.nbytes == self.mode.frame_bytes):
            import pyxrt
            stride = self.mode.width*4
            self.last_flush_bytes = sum(last-first for first,last in dirty_rows)*stride
            self.last_flush_strategy = 'xrt-ranged'
            for first, last in dirty_rows:
                bo.sync(pyxrt.xclBOSyncDirection.XCL_BO_SYNC_BO_TO_DEVICE,
                        (last-first)*stride, first*stride)
        else:
            pixels.flush()

    def status(self):
        if self.running:
            self._reap()
        errors = self.monitor.read(0x08)
        return {
            "mode": self.mode.name, "width": self.mode.width, "height": self.mode.height,
            "allocation_cacheable_requested": self.cacheable,
            "last_flush_bytes": self.last_flush_bytes, "last_flush_strategy": self.last_flush_strategy,
            "refresh_hz": self.mode.refresh_hz, "running": self.running,
            "scan_frames": self.monitor.read(0x00), "locked": bool(errors & (1 << 30)),
            "underflow_events": errors & 0x7FFF, "overflow_events": (errors >> 15) & 0x7FFF,
            "counters_saturated": (errors & 0x7FFF) == 0x7FFF or ((errors >> 15) & 0x7FFF) == 0x7FFF,
            "dma_status": self.dma.read(0x04), "submitted": self.submitted,
            "dma_frame_switches": self.acknowledged, "allocated_bytes": sum(f.nbytes for f in self.buffers),
            "acknowledged_source_pairs": self.acknowledged_source_pairs,
            "acknowledged_generation": self.acknowledged_generation,
            "generation_source_pairs": self.generation_source_pairs,
            "last_acknowledged_identity": self.last_acknowledged_identity,
            "scanning_slot": self.active, "pending_slot": self.pending,
            "startup_samples": list(self.startup_samples),
        }

    def stop(self):
        if not self.buffers and not self.running:
            return
        self.dma.write(0, self.dma.read(0) & ~1)
        try:
            self._wait(lambda: self.dma.read(0x04) & 1, "Display DMA halt timed out")
        except TimeoutError:
            self.dma.write(0, 4)
            self._wait(lambda: not self.dma.read(0) & 4 and self.dma.read(0x04) & 1,
                       "Display DMA could not halt/reset; retaining all DMA buffers")
        self.running = False
        self.timing.stop()
        for frame in self.buffers:
            frame.freebuffer()
        self.buffers.clear()
        self.reserved.clear()
        self.active = self.pending = self.writing = None
        self.pending_identity = None

    @classmethod
    def from_overlay(cls, overlay, **kwargs):
        from pynq import allocate, MMIO

        mode = kwargs.pop("mode", MODE_720P60)

        expected = {"hdmi_vdma": 0x43010000, "hdmi_vtc": 0x43C10000, "hdmi_status": 0x41250000}
        for name, address in expected.items():
            if overlay.ip_dict.get(name, {}).get("phys_addr") != address:
                raise DisplayError(f"HDMI HWH address contract mismatch: {name}")
        params = overlay.ip_dict["hdmi_vdma"]["parameters"]
        for name, expected_value in {"C_INCLUDE_MM2S": 1, "C_INCLUDE_S2MM": 0,
                                     "C_NUM_FSTORES": 3, "C_M_AXIS_MM2S_TDATA_WIDTH": 32}.items():
            if int(params.get(name, -1)) != expected_value:
                raise DisplayError(f"Unsupported HDMI VDMA configuration: {name}")
        # Access registers directly: constructing PYNQ's AxiVDMA binds its
        # interrupt-driven channel objects, which this polling driver never uses.
        registers = {name: MMIO(address, overlay.ip_dict[name]["addr_range"], device=overlay.device)
                     for name, address in expected.items()}
        return cls(registers["hdmi_vdma"], registers["hdmi_status"],
                   FixedTiming(registers["hdmi_vtc"], mode, kwargs.get("trace")),
                   allocate, mode=mode, **kwargs)
