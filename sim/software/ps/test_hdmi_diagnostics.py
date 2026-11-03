import json
from pathlib import Path
import tempfile
import threading
import unittest

from hdmi_display.diagnostics import evaluate_interval, run_scanout_test, scan_delta
import test_hdmi_scanout


class DiagnosticsTests(unittest.TestCase):
    def state(self, **overrides):
        return {"scan_frames": 0, "dma_frame_switches": 0, "locked": True,
                "counters_saturated": False, "underflow_events": 0, "overflow_events": 0,
                "running": True, "dma_status": 0, "allocated_bytes": 11059200, **overrides}

    def test_wrap_and_repeated_scans_are_not_new_processed_frames(self):
        self.assertEqual(scan_delta(0xFFFFFFFE, 2), 4)
        result = evaluate_interval(self.state(scan_frames=0xFFFFFFE0),
            self.state(scan_frames=568, dma_frame_switches=300), 10, locked_samples=True)
        self.assertTrue(result["passed"])
        self.assertEqual(result["measured_scan_hz"], 60)
        self.assertEqual(result["acknowledged_pattern_fps"], 30)
        self.assertEqual(result["repeated_scans_estimate"], 300)

    def test_lost_lock_saturation_underflow_and_frozen_pointer_fail(self):
        baseline = self.state()
        good = self.state(scan_frames=600, dma_frame_switches=300)
        for changes in ({"underflow_events": 1}, {"locked": False},
                        {"counters_saturated": True}, {"dma_frame_switches": 0},
                        {"dma_status": 0x20}, {"scan_frames": 0}):
            result = evaluate_interval(baseline, {**good, **changes}, 10, locked_samples=True)
            self.assertFalse(result["passed"], changes)
        self.assertFalse(evaluate_interval(baseline, good, 10, locked_samples=False)["passed"])

    def test_run_saves_native_patterns_but_does_not_claim_monitor_acceptance(self):
        fixture = test_hdmi_scanout.ScanoutTests()
        fixture.setUp()
        fixture.monitor.values[8] = 1 << 30

        def sleep(delay):
            fixture.now += delay
            fixture.dma.frame_boundary()
            fixture.monitor.values[0] = round(fixture.now * 60)

        fixture.driver.sleep = sleep
        with tempfile.TemporaryDirectory() as temp:
            destination = Path(temp) / "diagnostic"
            report = run_scanout_test(fixture.driver, destination,
                hardware_identity={"scale": 2, "bit_sha256": "a" * 64, "hwh_sha256": "b" * 64},
                seconds_per_pattern=2, warmup_seconds=0.1,
                clock=lambda: fixture.now, sleep=sleep)
            self.assertTrue(report["register_checks_passed"], report)
            self.assertTrue(report["display_stopped"])
            self.assertFalse(report["physical_hdmi_verified"])
            self.assertIsNone(report["sr_processing_fps"])
            self.assertEqual(len(report["images"]), 4)
            self.assertEqual(len(report["intervals"]), 4)
            self.assertEqual(json.loads((destination / "report.json").read_text()), report)
            self.assertTrue(all(frame.freed for frame in fixture.allocated))

    def test_cancelled_run_writes_failure_without_starting_hardware(self):
        cancelled = threading.Event()
        cancelled.set()
        with tempfile.TemporaryDirectory() as temp:
            report = run_scanout_test(None, Path(temp) / "cancelled",
                hardware_identity={"scale": 4, "bit_sha256": "a" * 64, "hwh_sha256": "b" * 64},
                cancel=cancelled)
            self.assertFalse(report["register_checks_passed"])
            self.assertIn("cancelled before start", report["errors"][0])

    def test_observation_retains_live_buffers_and_continues_frames_until_stop(self):
        fixture = test_hdmi_scanout.ScanoutTests()
        fixture.setUp()
        fixture.monitor.values[8] = 1 << 30
        cancelled = threading.Event()
        holding = []

        def on_hold(state):
            self.assertTrue(state["running"])
            self.assertEqual(state["allocated_bytes"], 11059200)
            self.assertTrue(all(not frame.freed for frame in fixture.allocated))
            holding.append((fixture.now, state["submitted"]))

        def sleep(delay):
            fixture.now += delay
            fixture.dma.frame_boundary()
            fixture.monitor.values[0] = round(fixture.now * 60)
            if holding and fixture.now - holding[0][0] >= 1.5:
                self.assertTrue(fixture.driver.running)
                self.assertGreater(fixture.driver.submitted, holding[0][1])
                self.assertTrue(all(not frame.freed for frame in fixture.allocated))
                cancelled.set()

        fixture.driver.sleep = sleep
        with tempfile.TemporaryDirectory() as temp:
            report = run_scanout_test(fixture.driver, Path(temp) / "hold",
                hardware_identity={"scale": 4, "bit_sha256": "a" * 64, "hwh_sha256": "b" * 64},
                seconds_per_pattern=2, warmup_seconds=0.1, hold_display=True,
                on_hold=on_hold, cancel=cancelled, clock=lambda: fixture.now, sleep=sleep)
            self.assertEqual(len(holding), 1)
            self.assertTrue(report["hold"]["ended_by_operator"])
            self.assertTrue(report["register_checks_passed"], report)
            self.assertTrue(report["display_stopped"])
            self.assertFalse(report["physical_hdmi_verified"])
            self.assertTrue(all(frame.freed for frame in fixture.allocated))


if __name__ == "__main__":
    unittest.main()
