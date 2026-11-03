import sys
import time
import unittest
import xml.etree.ElementTree as ET
from pathlib import Path

import numpy as np


PROJECT_ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(Path(__file__).resolve().parent))

from sr_lut_x4_driver import CONTROL_START
from sr_lut_x4_driver import MAX_FRAME_HEIGHT, MAX_FRAME_WIDTH
from sr_lut_x4_driver import MIN_FRAME_HEIGHT, MIN_FRAME_WIDTH
from sr_lut_x4_driver import REG_CONTROL, REG_ERROR_CODE, REG_FRAME_HEIGHT
from sr_lut_x4_driver import REG_FRAME_WIDTH, REG_INPUT_WORDS
from sr_lut_x4_driver import REG_OUTPUT_WORDS, REG_STATUS
from sr_lut_x4_driver import REG_OUTPUT_MODE, OUTPUT_MODE_RGBX
from sr_lut_x4_driver import STATUS_DONE, STATUS_IDLE
from sr_lut_x4_driver import compare_rgb_frames
from sr_lut_x4_driver import frame_geometry
from sr_lut_x4_driver import load_overlay
from sr_lut_x4_driver import pack_rgb_frame
from sr_lut_x4_driver import run_frame
from sr_lut_x4_driver import read_hardware_metrics
from sr_lut_x4_driver import unpack_rgb_frame


TEST_HEIGHT = 32
TEST_WIDTH = 33
TEST_INPUT_PIXELS = TEST_HEIGHT * TEST_WIDTH
TEST_OUTPUT_PIXELS = TEST_INPUT_PIXELS * 16
MAX_OUTPUT_BYTES = MAX_FRAME_WIDTH * MAX_FRAME_HEIGHT * 16 * 4


class FakeBuffer:
    def __init__(self, shape, dtype):
        self.array = np.zeros(shape, dtype=dtype)
        self.flush_count = 0
        self.invalidate_count = 0
        self.free_count = 0

    def __getitem__(self, key):
        return self.array[key]

    def __setitem__(self, key, value):
        self.array[key] = value

    def __array__(self, dtype=None):
        return np.asarray(self.array, dtype=dtype)

    def flush(self):
        self.flush_count += 1

    def invalidate(self):
        self.invalidate_count += 1

    def freebuffer(self):
        self.free_count += 1


class FakeChannel:
    def __init__(self, dma, name):
        self.dma = dma
        self.name = name
        self.buffer = None
        self.stop_count = 0

    def transfer(self, buffer):
        self.dma.events.append(self.name + ".transfer")
        self.buffer = buffer
        if self.name == "recv":
            self.dma.output_buffer = buffer

    def wait(self):
        self.dma.events.append(self.name + ".wait")
        if self.name == "send":
            source = np.asarray(self.buffer, dtype=np.uint32)
            height = self.dma.control.height
            width = self.dma.control.width
            rgb = np.empty((height * width, 3), dtype=np.uint8)
            rgb[:, 0] = source & 0xFF
            rgb[:, 1] = (source >> 8) & 0xFF
            rgb[:, 2] = (source >> 16) & 0xFF
            rgb = rgb.reshape(height, width, 3)
            expanded = np.repeat(np.repeat(rgb, 4, axis=0), 4, axis=1)
            out = (
                expanded[..., 0].astype(np.uint32)
                | (expanded[..., 1].astype(np.uint32) << 8)
                | (expanded[..., 2].astype(np.uint32) << 16)
            ).reshape(-1)
            self.dma.output_buffer[:] = out

    def stop(self):
        self.stop_count += 1
        self.dma.events.append(self.name + ".stop")


class FakeDma:
    def __init__(self, control=None):
        self.control = control or FakeControl()
        self.events = []
        self.output_buffer = None
        self.sendchannel = FakeChannel(self, "send")
        self.recvchannel = FakeChannel(self, "recv")


class FakeOverlay:
    def __init__(self, dma):
        self.axi_dma_0 = dma
        self.sr_lut_x4_dynamic_0 = dma.control


class FakeControl:
    def __init__(self):
        self.width = 0
        self.height = 0
        self.status = STATUS_IDLE
        self.error = 0
        self.output_mode = 1

    def write(self, offset, value):
        if offset == REG_FRAME_WIDTH:
            self.width = value
        elif offset == REG_FRAME_HEIGHT:
            self.height = value
        elif offset == REG_OUTPUT_MODE:
            self.output_mode = int(value)
        elif offset == REG_CONTROL and value == CONTROL_START:
            self.status = STATUS_DONE

    def read(self, offset):
        return {
            REG_STATUS: self.status,
            REG_ERROR_CODE: self.error,
            REG_INPUT_WORDS: self.width * self.height,
            REG_OUTPUT_WORDS: self.width * self.height * 16,
        }.get(offset, 0)


class FakeRegisterIp:
    def __init__(self, channel_1, channel_2):
        self.values = {0x00: channel_1, 0x08: channel_2}

    def read(self, offset):
        return self.values[offset]


class HangingChannel(FakeChannel):
    def wait(self):
        self.dma.events.append(self.name + ".wait")
        time.sleep(0.05)


class DriverTests(unittest.TestCase):
    def test_geometry_and_pack_unpack_contract(self):
        self.assertEqual((MIN_FRAME_WIDTH, MIN_FRAME_HEIGHT), (32, 32))
        self.assertEqual((MAX_FRAME_WIDTH, MAX_FRAME_HEIGHT), (480, 320))
        image = np.zeros((TEST_HEIGHT, TEST_WIDTH, 3), dtype=np.uint8)
        image[0, 0] = [1, 2, 3]
        self.assertEqual(frame_geometry(image), (TEST_HEIGHT, TEST_WIDTH))
        words = pack_rgb_frame(image)
        self.assertEqual(words.dtype, np.dtype(np.uint32))
        self.assertEqual(words.size, TEST_INPUT_PIXELS)
        self.assertEqual(int(words[0]), 0x00030201)

        output_words = np.zeros(TEST_OUTPUT_PIXELS, dtype=np.uint32)
        output_words[0] = 0x00030201
        result = unpack_rgb_frame(output_words, TEST_WIDTH, TEST_HEIGHT)
        self.assertEqual(result.shape, (TEST_HEIGHT * 4, TEST_WIDTH * 4, 3))
        self.assertEqual(result[0, 0].tolist(), [1, 2, 3])

        bad_words = output_words.copy()
        bad_words[0] |= 0xAB000000
        with self.assertRaises(ValueError):
            unpack_rgb_frame(bad_words, TEST_WIDTH, TEST_HEIGHT)

    def test_rejects_out_of_range_or_invalid_input(self):
        with self.assertRaises(ValueError):
            pack_rgb_frame(np.zeros((31, 32, 3), dtype=np.uint8))
        with self.assertRaises(ValueError):
            pack_rgb_frame(np.zeros((32, 481, 3), dtype=np.uint8))
        with self.assertRaises(ValueError):
            pack_rgb_frame(np.zeros((32, 32, 3), dtype=np.float32))

    def test_compare_reports_exact_match_and_error(self):
        expected = np.zeros((2, 2, 3), dtype=np.uint8)
        actual = expected.copy()
        self.assertEqual(compare_rgb_frames(actual, expected)["exact_pixels"], 4)
        actual[0, 0, 0] = 10
        report = compare_rgb_frames(actual, expected)
        self.assertEqual(report["changed_pixels"], 1)
        self.assertEqual(report["max_abs_error"], 10)

    def test_run_frame_arms_receive_before_send_and_syncs_buffers(self):
        image = np.arange(TEST_INPUT_PIXELS * 3, dtype=np.uint8).reshape(
            TEST_HEIGHT, TEST_WIDTH, 3
        )
        dma = FakeDma()
        allocated = []

        def allocator(*, shape, dtype):
            buffer = FakeBuffer(shape, dtype)
            allocated.append(buffer)
            return buffer

        result = run_frame(
            image,
            FakeOverlay(dma),
            allocator=allocator,
            timeout_seconds=1.0,
        )
        self.assertEqual(
            dma.events[:4],
            ["recv.transfer", "send.transfer", "send.wait", "recv.wait"],
        )
        self.assertEqual(dma.control.output_mode, OUTPUT_MODE_RGBX)
        self.assertEqual(allocated[0].flush_count, 1)
        self.assertEqual(allocated[1].flush_count, 1)
        self.assertEqual(allocated[1].invalidate_count, 1)
        self.assertEqual(allocated[0].free_count, 1)
        self.assertEqual(allocated[1].free_count, 1)
        expected = np.repeat(np.repeat(image, 4, axis=0), 4, axis=1)
        np.testing.assert_array_equal(result.image, expected)

    def test_load_overlay_uses_x4_bitstream(self):
        seen = []

        class OverlayStub:
            def __init__(self, path):
                seen.append(Path(path))

        overlay_dir = PROJECT_ROOT / "build" / "artifacts" / "pynq_x4"
        overlay = load_overlay(OverlayStub, overlay_dir)
        self.assertIsInstance(overlay, OverlayStub)
        self.assertEqual(seen[0].name, "sr_lut_x4.bit")

    def test_reads_pl_counters_without_using_host_timing(self):
        overlay = FakeOverlay(FakeDma())
        overlay.axi_gpio_perf_0 = FakeRegisterIp(11_352_600, 9_000_000)
        overlay.axi_gpio_perf_1 = FakeRegisterIp(123, 7)

        metrics = read_hardware_metrics(overlay)

        self.assertEqual(metrics.frame_cycles, 11_352_600)
        self.assertEqual(metrics.input_backpressure_cycles, 9_000_000)
        self.assertEqual(metrics.output_backpressure_cycles, 123)
        self.assertEqual(metrics.input_starvation_cycles, 7)
        self.assertAlmostEqual(metrics.frame_seconds, 0.113526)
        self.assertAlmostEqual(metrics.fps, 1.0 / 0.113526)

    def test_hwh_contains_dma_and_pl_performance_registers(self):
        hwh_path = (
            PROJECT_ROOT
            / "build"
            / "artifacts"
            / "pynq_x4"
            / "sr_lut_x4.hwh"
        )
        root = ET.parse(hwh_path).getroot()
        dma_module = next(
            module
            for module in root.findall(".//MODULE")
            if module.attrib.get("INSTANCE") == "axi_dma_0"
        )
        parameters = {
            parameter.attrib["NAME"]: parameter.attrib["VALUE"]
            for parameter in dma_module.findall("./PARAMETERS/PARAMETER")
        }
        self.assertEqual(parameters["C_INCLUDE_SG"], "0")
        self.assertEqual(parameters["C_M_AXIS_MM2S_TDATA_WIDTH"], "32")
        self.assertEqual(parameters["C_S_AXIS_S2MM_TDATA_WIDTH"], "32")
        length_width = int(parameters["C_SG_LENGTH_WIDTH"])
        self.assertGreaterEqual((1 << length_width) - 1, MAX_OUTPUT_BYTES)

        modules = {
            module.attrib.get("INSTANCE"): module
            for module in root.findall(".//MODULE")
        }
        expected_gpio = {
            "axi_gpio_perf_0": ("0x41200000", "0x4120FFFF"),
            "axi_gpio_perf_1": ("0x41210000", "0x4121FFFF"),
        }
        for instance, (base_address, high_address) in expected_gpio.items():
            self.assertIn(instance, modules)
            gpio_parameters = {
                parameter.attrib["NAME"]: parameter.attrib["VALUE"]
                for parameter in modules[instance].findall("./PARAMETERS/PARAMETER")
            }
            self.assertEqual(gpio_parameters["C_BASEADDR"], base_address)
            self.assertEqual(gpio_parameters["C_HIGHADDR"], high_address)
            self.assertEqual(gpio_parameters["C_IS_DUAL"], "1")
            self.assertEqual(gpio_parameters["C_GPIO_WIDTH"], "32")
            self.assertEqual(gpio_parameters["C_GPIO2_WIDTH"], "32")
            self.assertEqual(gpio_parameters["C_ALL_INPUTS"], "1")
            self.assertEqual(gpio_parameters["C_ALL_INPUTS_2"], "1")

    def test_timeout_is_reported(self):
        dma = FakeDma()
        dma.sendchannel = HangingChannel(dma, "send")
        image = np.zeros((TEST_HEIGHT, TEST_WIDTH, 3), dtype=np.uint8)
        allocator = lambda *, shape, dtype: FakeBuffer(shape, dtype)
        with self.assertRaises(TimeoutError):
            run_frame(image, FakeOverlay(dma), allocator=allocator, timeout_seconds=0.001)
        self.assertEqual(dma.sendchannel.stop_count, 1)
        self.assertEqual(dma.recvchannel.stop_count, 1)


if __name__ == "__main__":
    unittest.main(verbosity=2)
