import unittest
from types import SimpleNamespace
from unittest.mock import patch

import numpy as np

from hdmi_display.scanout import (DisplayError, Fixed720Timing, FixedTiming,
                                   MODE_1080P30, MODE_1080P60, Scanout)


class Buffer(np.ndarray):
    def flush(self):
        self.flushes += 1

    def freebuffer(self):
        self.freed = True


class Registers:
    def __init__(self):
        self.values = {4: 1}
        self.writes = []
        self.stuck = False

    def read(self, offset):
        return self.values.get(offset, 0)

    def write(self, offset, value):
        self.writes.append((offset, value))
        if offset == 0x28:
            value = (self.values.get(offset, 0) & (0x1F << 16)) | (value & 0xFFFF)
        self.values[offset] = value
        if offset == 0 and not self.stuck:
            if value & 4:
                self.values[0] = 0
                self.values[4] = 1
            else:
                self.values[4] = 0 if value & 1 else 1

    def frame_boundary(self):
        pointer = self.values[0x28]
        self.values[0x28] = (pointer & 0xFFFF) | ((pointer & 0x1F) << 16)


class ScanoutTests(unittest.TestCase):
    def setUp(self):
        self.dma, self.monitor, self.vtc = Registers(), Registers(), Registers()
        self.allocated = []
        self.now = 0.0

        def allocate(**kwargs):
            result = np.zeros(kwargs["shape"], kwargs["dtype"]).view(Buffer)
            result.physical_address = 0x10000000 + len(self.allocated) * 0x400000
            result.flushes, result.freed = 0, False
            self.allocated.append(result)
            return result

        self.driver = Scanout(self.dma, self.monitor, Fixed720Timing(self.vtc), allocate,
                              timeout=0.01, clock=lambda: self.now, sleep=self.sleep)

    def sleep(self, delay):
        self.now += delay

    def test_three_buffers_do_not_recycle_scanning_or_pending(self):
        self.driver.start()
        first = self.driver.acquire()
        self.assertEqual(first.index, 1)
        self.driver.submit(first)
        second = self.driver.acquire()
        self.assertEqual(second.index, 2)
        with self.assertRaises(TimeoutError):
            self.driver.submit(second)
        self.assertEqual(self.driver.active, 0)
        self.assertEqual(self.driver.pending, 1)
        self.dma.frame_boundary()
        self.driver.submit(second)
        self.assertEqual(self.driver.active, 1)
        third = self.driver.acquire()
        self.assertEqual(third.index, 0)
        self.assertEqual(len(self.allocated), 3)
        self.assertEqual(self.driver.status()["allocated_bytes"], 11059200)
        self.driver.discard(third)
        self.driver.stop()
        self.assertTrue(all(frame.freed for frame in self.allocated))

    def test_cache_flush_precedes_submit_and_duplicate_lease_is_rejected(self):
        self.driver.start()
        lease = self.driver.acquire()
        self.assertEqual(lease.pixels.flushes, 1)
        self.driver.submit(lease)
        self.assertEqual(lease.pixels.flushes, 2)
        with self.assertRaises(DisplayError):
            self.driver.submit(lease)
        self.driver.stop()

    def test_direct_camera_reservations_do_not_alias_active_pending_or_ready(self):
        self.driver.start()
        ready = self.driver.reserve()
        inflight = self.driver.reserve()
        self.assertEqual({ready.index, inflight.index}, {1, 2})
        with self.assertRaises(TimeoutError):
            self.driver.reserve()
        self.driver.submit_reserved(ready, identity=(1, 1))
        self.assertTrue(self.driver.owns_reservation(inflight))
        with self.assertRaises(TimeoutError):
            self.driver.reserve()
        self.dma.frame_boundary()
        next_frame = self.driver.reserve()
        self.assertEqual(next_frame.index, 0)
        self.driver.submit_reserved(inflight, identity=(1, 2))
        with self.assertRaises(DisplayError):
            self.driver.submit_reserved(inflight)
        self.driver.discard_reserved(next_frame)
        self.driver.stop()

    def test_ranged_sync_uses_exact_rows_and_rejects_out_of_bounds(self):
        self.driver.start()
        lease=self.driver.acquire()
        calls=[]
        FakeBo=type('bo',(),{'__module__':'pyxrt','sync':lambda self,*args:calls.append(args)})
        lease.pixels.bo=FakeBo()
        xrt=SimpleNamespace(xclBOSyncDirection=SimpleNamespace(XCL_BO_SYNC_BO_TO_DEVICE=7))
        with patch.dict('sys.modules',{'pyxrt':xrt}):
            self.driver._flush(lease.pixels,[(0,2),(180,540)])
        self.assertEqual(calls,[(7,2*1280*4,0),(7,360*1280*4,180*1280*4)])
        with self.assertRaises(ValueError): self.driver._flush(lease.pixels,[(0,721)])
        self.driver.discard(lease)
        self.driver.stop()

    def test_failed_stop_retains_memory_until_later_confirmed_halt(self):
        self.driver.start()
        self.dma.stuck = True
        with self.assertRaisesRegex(TimeoutError, "retaining"):
            self.driver.stop()
        self.assertFalse(any(frame.freed for frame in self.allocated))
        self.assertEqual(len(self.driver.buffers), 3)
        self.dma.stuck = False
        self.driver.stop()
        self.assertTrue(all(frame.freed for frame in self.allocated))

    def test_dma_errors_are_not_silently_cleared(self):
        self.driver.start()
        self.dma.values[4] = 0x20
        with self.assertRaises(DisplayError):
            self.driver.acquire()
        self.assertEqual(self.dma.values[4], 0x20)
        self.driver.stop()

    def test_running_dma_is_not_stolen(self):
        self.dma.values[4] = 0
        with self.assertRaisesRegex(DisplayError, "another runtime"):
            self.driver.start()
        self.assertEqual(self.dma.writes, [])

    def test_source_counts_require_hardware_ack_and_exclude_ui_and_repeat_pairs(self):
        self.driver.start()
        self.driver.submit(self.driver.acquire(), identity=(3, 10))
        self.assertEqual(self.driver.status()['acknowledged_source_pairs'], 0)
        self.dma.frame_boundary()
        self.assertEqual(self.driver.status()['acknowledged_source_pairs'], 1)
        for identity in ((3, 10), None, (3, 10)):
            self.driver.submit(self.driver.acquire(), identity=identity)
            self.dma.frame_boundary()
            self.assertEqual(self.driver.status()['acknowledged_source_pairs'], 1)
        self.driver.submit(self.driver.acquire(), identity=(4, 11))
        self.dma.frame_boundary()
        status = self.driver.status()
        self.assertEqual(status['acknowledged_source_pairs'], 2)
        self.assertEqual(status['acknowledged_generation'], 4)
        self.assertEqual(status['generation_source_pairs'], 1)
        self.driver.stop()

    def test_720p_timing_reconstructs_standard_clock_and_sync(self):
        Fixed720Timing(self.vtc).configure()
        horizontal = self.vtc.read(0x70)
        vertical = self.vtc.read(0x74) & 0xFFFF
        self.assertEqual(horizontal * vertical * 60, 74250000)
        hsync = self.vtc.read(0x78)
        vsync = self.vtc.read(0x80)
        self.assertEqual((hsync >> 16) - (hsync & 0xFFFF), 40)
        self.assertEqual((vsync >> 16) - (vsync & 0xFFFF), 5)
        self.assertEqual((vsync & 0xFFFF) + 1 - 720, 5)

    def test_1080p_timing_uses_cea_progressive_porches_for_both_rates(self):
        for mode, pixel_clock in ((MODE_1080P30, 74250000), (MODE_1080P60, 148500000)):
            regs = Registers()
            FixedTiming(regs, mode).configure()
            self.assertEqual(regs.read(0x70) * (regs.read(0x74) & 0xFFFF) * mode.refresh_hz,
                             pixel_clock)
            hsync, vsync = regs.read(0x78), regs.read(0x80)
            self.assertEqual((hsync & 0xFFFF, hsync >> 16), (2008, 2052))
            self.assertEqual((vsync & 0xFFFF, vsync >> 16), (1083, 1088))
            self.assertEqual(regs.read(0x84), (2008 << 16) | 2008)

    def test_overlay_factory_uses_mmio_without_binding_interrupt_driver(self):
        class Overlay:
            device = object()
            ip_dict = {name: {"phys_addr": address, "addr_range": 65536} for name, address in
                       {"hdmi_vdma": 0x43010000, "hdmi_vtc": 0x43C10000, "hdmi_status": 0x41250000}.items()}

            @property
            def hdmi_vdma(self):
                raise AssertionError("Must not construct the interrupt-based AxiVDMA driver")

        overlay = Overlay()
        overlay.ip_dict["hdmi_vdma"]["parameters"] = {
            "C_INCLUDE_MM2S": "1", "C_INCLUDE_S2MM": "0", "C_NUM_FSTORES": "3",
            "C_M_AXIS_MM2S_TDATA_WIDTH": "32"}
        calls = []

        def mmio(address, length, device):
            calls.append((address, length, device))
            return Registers()

        with patch.dict("sys.modules", {"pynq": SimpleNamespace(MMIO=mmio, allocate=object())}):
            driver = Scanout.from_overlay(overlay)
        self.assertIsInstance(driver, Scanout)
        self.assertEqual({call[0] for call in calls}, {0x43010000, 0x43C10000, 0x41250000})
        self.assertTrue(all(length == 65536 and device is overlay.device for _, length, device in calls))


if __name__ == "__main__":
    unittest.main()
