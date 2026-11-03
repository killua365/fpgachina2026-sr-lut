from dataclasses import dataclass
import os
import threading
import unittest
from unittest.mock import patch
from pathlib import Path

import numpy as np

from roi_magnifier.processor import RoiProcessor
from roi_magnifier.processor import validate_source_image


@dataclass
class FakeMetrics:
    values: dict

    def as_dict(self):
        return self.values


@dataclass
class FakeResult:
    image: np.ndarray
    metrics: FakeMetrics
    hardware_metrics: FakeMetrics


class RoiProcessorTests(unittest.TestCase):
    def setUp(self):
        yy, xx = np.indices((80, 100), dtype=np.uint16)
        self.image = np.stack((xx % 256, yy % 256, (xx + yy) % 256), axis=-1).astype(np.uint8)

    def test_hardware_result_is_cropped_to_exact_roi(self):
        calls = []

        def runner(window, overlay):
            calls.append((window.copy(), overlay))
            output = np.repeat(np.repeat(window, 4, axis=0), 4, axis=1)
            return FakeResult(output, FakeMetrics({"total_seconds": 0.01}), FakeMetrics({"pl_frame_cycles": 123}))

        processor = RoiProcessor(runner=runner, overlay=object())
        result = processor.process(self.image, 20, 30, 12, 7)
        expected = np.repeat(np.repeat(self.image[30:37, 20:32], 4, axis=0), 4, axis=1)
        np.testing.assert_array_equal(result.image, expected)
        self.assertEqual(result.image.shape, (28, 48, 3))
        self.assertEqual(calls[0][0].shape, (32, 32, 3))
        self.assertTrue(result.metrics["formal_fpga_result"])
        self.assertEqual(result.metrics["pl"]["pl_frame_cycles"], 123)

    def test_overlay_is_loaded_lazily_once(self):
        loads = []
        overlay = object()

        def loader():
            loads.append(True)
            return overlay

        def runner(window, actual_overlay):
            self.assertIs(actual_overlay, overlay)
            output = np.repeat(np.repeat(window, 4, axis=0), 4, axis=1)
            return FakeResult(output, FakeMetrics({}), FakeMetrics({}))

        processor = RoiProcessor(runner=runner, overlay_loader=loader)
        processor.process(self.image, 0, 0, 32, 32)
        processor.process(self.image, 10, 10, 8, 8)
        self.assertEqual(len(loads), 1)
        processor.invalidate_hardware()
        processor.process(self.image, 0, 0, 32, 32)
        self.assertEqual(len(loads), 2)

    def test_prepare_loads_hardware_once_on_calling_thread(self):
        loads = []
        overlay = object()

        def loader():
            loads.append(True)
            return overlay

        def runner(window, actual_overlay):
            output = np.repeat(np.repeat(window, 4, axis=0), 4, axis=1)
            return FakeResult(output, FakeMetrics({}), FakeMetrics({}))

        processor = RoiProcessor(runner=runner, overlay_loader=loader)
        processor.prepare()
        processor.prepare()
        processor.process(self.image, 0, 0, 32, 32)
        self.assertEqual(len(loads), 1)

    def test_reconfiguration_lock_covers_loader_but_not_frame_execution(self):
        reconfiguration_lock = threading.Lock()
        loader_holds_lock = []
        runner_can_access_gpio = []

        def loader():
            loader_holds_lock.append(
                not reconfiguration_lock.acquire(blocking=False)
            )
            return object()

        def runner(window, _overlay):
            acquired = reconfiguration_lock.acquire(blocking=False)
            runner_can_access_gpio.append(acquired)
            if acquired:
                reconfiguration_lock.release()
            output = np.repeat(np.repeat(window, 4, axis=0), 4, axis=1)
            return FakeResult(output, FakeMetrics({}), FakeMetrics({}))

        processor = RoiProcessor(
            runner=runner,
            overlay_loader=loader,
            reconfiguration_lock=reconfiguration_lock,
        )
        processor.process(self.image, 0, 0, 32, 32)

        self.assertEqual(loader_holds_lock, [True])
        self.assertEqual(runner_can_access_gpio, [True])

    def test_x4_candidate_overlay_can_be_selected_by_environment(self):
        loads = []
        overlay = object()

        def loader(**kwargs):
            loads.append(kwargs)
            return overlay

        def runner(window, actual_overlay):
            self.assertIs(actual_overlay, overlay)
            output = np.repeat(np.repeat(window, 4, axis=0), 4, axis=1)
            return FakeResult(output, FakeMetrics({}), FakeMetrics({}))

        with patch.dict(os.environ, {"SR_LUT_X4_OVERLAY_DIR": "C:/candidate-x4"}, clear=False):
            RoiProcessor(runner=runner, overlay_loader=loader).process(
                self.image, 0, 0, 32, 32, scale=4
            )
        self.assertEqual(
            loads,
            [{"overlay_dir": Path("C:/candidate-x4"), "overlay_stem": "sr_lut_x4"}],
        )

    def test_switching_scale_reloads_the_previous_overlay(self):
        loads = []

        def loader(**kwargs):
            overlay = object()
            loads.append((kwargs["overlay_stem"], overlay))
            return overlay

        def runner(window, _overlay, *, scale):
            output = np.repeat(np.repeat(window, scale, axis=0), scale, axis=1)
            return FakeResult(output, FakeMetrics({}), FakeMetrics({}))

        environment = {
            "SR_LUT_X2_OVERLAY_DIR": "C:/candidate-x2",
            "SR_LUT_X4_OVERLAY_DIR": "C:/candidate-x4",
        }
        processor = RoiProcessor(runner=runner, overlay_loader=loader)
        with patch.dict(os.environ, environment, clear=False):
            processor.process(self.image, 0, 0, 32, 32, scale=4)
            processor.process(self.image, 0, 0, 32, 32, scale=2)
            processor.process(self.image, 0, 0, 32, 32, scale=4)

        self.assertEqual([stem for stem, _ in loads], ["sr_lut_x4", "sr_lut_x2", "sr_lut_x4"])
        self.assertIsNot(loads[0][1], loads[2][1])

    def test_preview_is_explicitly_non_formal(self):
        result = RoiProcessor(preview=True).process(self.image, 1, 1, 10, 10)
        self.assertEqual(result.image.shape, (40, 40, 3))
        self.assertEqual(result.metrics["execution_mode"], "software_preview")
        self.assertFalse(result.metrics["formal_fpga_result"])
        self.assertIsNone(result.metrics["pl"])

    def test_wrong_runner_shape_is_rejected(self):
        def runner(window, overlay):
            return FakeResult(window, FakeMetrics({}), FakeMetrics({}))

        with self.assertRaisesRegex(RuntimeError, "expected"):
            RoiProcessor(runner=runner, overlay=object()).process(self.image, 1, 1, 5, 5)

    def test_invalid_source_type_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "uint8"):
            validate_source_image(self.image.astype(np.float32))

    def test_large_source_can_use_bounded_roi(self):
        source = np.zeros((400, 500, 3), dtype=np.uint8)
        result = RoiProcessor(preview=True).process(source, 100, 100, 100, 80)
        self.assertEqual(result.plan.process_window.width, 102)
        self.assertEqual(result.image.shape, (320, 400, 3))


if __name__ == "__main__":
    unittest.main(verbosity=2)
