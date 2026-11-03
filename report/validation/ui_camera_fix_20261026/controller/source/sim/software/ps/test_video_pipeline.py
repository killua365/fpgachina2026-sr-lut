from __future__ import annotations

from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import zlib

import numpy as np

from sr_lut_x4_driver import CONTROL_SOFT_RESET, CONTROL_START
from sr_lut_x4_driver import REG_CONTROL, REG_ERROR_CODE, REG_FRAME_HEIGHT, REG_FRAME_WIDTH
from sr_lut_x4_driver import REG_INPUT_WORDS, REG_OUTPUT_WORDS, REG_STATUS
from sr_lut_x4_driver import REG_OUTPUT_MODE, REG_OUTPUT_CRC32
from sr_lut_x4_driver import OUTPUT_MODE_MB420, OUTPUT_MODE_RGBX
from sr_lut_x4_driver import STATUS_DONE, STATUS_ERROR, STATUS_IDLE
from video_pipeline.board_pipeline import process_video_yuv420
from video_pipeline.frame_session import ReusableFrameSession
from video_pipeline.input_pack import BgrxInputPacker
from video_pipeline.identity import VIDEO_PIPELINE
from video_pipeline.yuv420 import Yuv420Converter, rgbx_to_yuv420p


class FakeBuffer:
    def __init__(self, shape, dtype, *, cacheable=False):
        self.array = np.zeros(shape, dtype=dtype)
        self.cacheable = bool(cacheable)
        self.coherent = False
        self.to_device_count = 0
        self.from_device_count = 0
        self.free_count = 0

    def __getitem__(self, key):
        return self.array[key]

    def __setitem__(self, key, value):
        self.array[key] = value

    def __array__(self, dtype=None):
        return np.asarray(self.array, dtype=dtype)

    def sync_to_device(self):
        self.to_device_count += 1

    def sync_from_device(self):
        self.from_device_count += 1

    def freebuffer(self):
        self.free_count += 1


class BufferAllocator:
    def __init__(self):
        self.buffers = []

    def __call__(self, *, shape, dtype, cacheable=False):
        buffer = FakeBuffer(shape, dtype, cacheable=cacheable)
        self.buffers.append(buffer)
        return buffer


class FakeControl:
    def __init__(self, events, fail=False):
        self.events = events
        self.width = 0
        self.height = 0
        self.status = STATUS_IDLE
        self.error_code = 0
        self.fail = fail
        self.soft_reset_count = 0
        self.output_mode = OUTPUT_MODE_RGBX
        self.output_crc32 = 0

    def write(self, offset, value):
        if offset == REG_FRAME_WIDTH:
            self.width = int(value)
        elif offset == REG_FRAME_HEIGHT:
            self.height = int(value)
        elif offset == REG_CONTROL and value == CONTROL_START:
            self.events.append("control.start")
            self.status = STATUS_ERROR if self.fail else STATUS_DONE | STATUS_IDLE
            self.error_code = 7 if self.fail else 0
        elif offset == REG_CONTROL and value == CONTROL_SOFT_RESET:
            self.soft_reset_count += 1
            self.status = STATUS_IDLE
            self.error_code = 0
            self.output_mode = OUTPUT_MODE_RGBX
            self.output_crc32 = 0
        elif offset == REG_OUTPUT_MODE:
            self.output_mode = int(value)

    def read(self, offset):
        return {
            REG_STATUS: self.status,
            REG_ERROR_CODE: self.error_code,
            REG_INPUT_WORDS: self.width * self.height,
            REG_OUTPUT_WORDS: self.width * self.height * (
                6 if self.output_mode == OUTPUT_MODE_MB420 else 16
            ),
            REG_OUTPUT_CRC32: self.output_crc32,
        }.get(offset, 0)


class FakeChannel:
    def __init__(self, dma, name):
        self.dma = dma
        self.name = name
        self.buffer = None
        self.stop_count = 0

    def transfer(self, buffer):
        self.dma.events.append(f"{self.name}.transfer")
        self.buffer = buffer
        if self.name == "recv":
            self.dma.output_buffer = buffer

    def wait(self):
        self.dma.events.append(f"{self.name}.wait")
        if self.name != "send" or self.dma.control.fail:
            return
        if self.dma.control.output_mode == OUTPUT_MODE_MB420:
            target = np.asarray(self.dma.output_buffer).view(np.uint8).reshape(-1)
            target[:] = np.arange(target.size, dtype=np.uint32).astype(np.uint8)
            self.dma.control.output_crc32 = zlib.crc32(target) & 0xFFFFFFFF
            return
        source = np.asarray(self.buffer).view(np.uint8).reshape(
            self.dma.control.height, self.dma.control.width, 4
        )
        expanded = np.repeat(np.repeat(source[..., :3], 4, axis=0), 4, axis=1)
        target = np.asarray(self.dma.output_buffer).view(np.uint8).reshape(
            self.dma.control.height * 4, self.dma.control.width * 4, 4
        )
        target[..., :3] = expanded
        target[..., 3] = 0

    def stop(self):
        self.stop_count += 1


class FakeDma:
    def __init__(self, fail=False):
        self.events = []
        self.control = FakeControl(self.events, fail)
        self.sendchannel = FakeChannel(self, "send")
        self.recvchannel = FakeChannel(self, "recv")
        self.output_buffer = None


class FakeOverlay:
    def __init__(self, fail=False):
        self.axi_dma_0 = FakeDma(fail)
        self.sr_lut_x4_dynamic_0 = self.axi_dma_0.control


class FakeCapture:
    def __init__(self, frames):
        self.frames = [frame.copy() for frame in frames]
        self.index = 0
        self.released = False

    def isOpened(self):
        return True

    def get(self, key):
        return 25.0 if key == FakeCv2.CAP_PROP_FPS else len(self.frames)

    def read(self):
        if self.index >= len(self.frames):
            return False, None
        frame = self.frames[self.index]
        self.index += 1
        return True, frame

    def set(self, key, value):
        if key != FakeCv2.CAP_PROP_POS_FRAMES or value != 0:
            return False
        self.index = 0
        return True

    def release(self):
        self.released = True


class FakeCv2:
    CAP_PROP_FPS = 1
    CAP_PROP_FRAME_COUNT = 2
    CAP_PROP_POS_FRAMES = 3
    COLOR_RGBA2YUV_I420 = 4

    def __init__(self, frames):
        self.capture = FakeCapture(frames)
        self.threads = 2

    def VideoCapture(self, _):
        return self.capture

    def getNumThreads(self):
        return self.threads

    def setNumThreads(self, threads):
        self.threads = int(threads)

    @staticmethod
    def cvtColor(source, code, dst=None):
        if code != FakeCv2.COLOR_RGBA2YUV_I420:
            raise ValueError(code)
        height, width = source.shape[:2]
        value = np.arange(height * width * 3 // 2, dtype=np.uint8).reshape(height * 3 // 2, width)
        if dst is None:
            return value
        dst[...] = value
        return dst


class FakeSink:
    instances = []

    def __init__(self, host, port, session, **_):
        self.host = host
        self.port = port
        self.session = session
        self.frames = []
        self.total_payload_bytes = 0
        self.finished_report = None
        self.closed = False
        self.hardware_crc_frames = 0
        self.crc_seconds = []
        self.send_seconds = []
        type(self).instances.append(self)

    def send_frame(self, index, payload, **metrics):
        self.frames.append((index, bytes(payload), metrics))
        self.total_payload_bytes += len(payload)
        if metrics.get("payload_crc32") is not None:
            self.hardware_crc_frames += 1

    def finish(self, report):
        self.finished_report = report
        self.closed = True

    def fail(self):
        self.closed = True

    def close(self):
        self.closed = True


class CountingConverter:
    instances = []

    def __init__(self, *, backend, cv2_module):
        self.name = f"test-{backend}"
        self.cv2 = cv2_module
        self.convert_count = 0
        type(self).instances.append(self)

    def convert(self, source, destination=None):
        self.convert_count += 1
        return Yuv420Converter(
            backend="opencv",
            cv2_module=self.cv2,
        ).convert(source, destination)


class FakeIrqTuner:
    instances = []

    def __init__(self, *, interface, target_cpu):
        self.interface = interface
        self.target_cpu = target_cpu
        self.restored = False
        type(self).instances.append(self)

    def apply(self):
        return {
            "enabled": True,
            "interface": self.interface,
            "target_cpu": self.target_cpu,
            "irq_numbers": [40],
            "apply_status": "applied",
            "restore_status": "not-run",
        }

    def restore(self):
        self.restored = True
        return {
            "enabled": True,
            "interface": self.interface,
            "target_cpu": self.target_cpu,
            "irq_numbers": [40],
            "apply_status": "applied",
            "restore_status": "restored",
        }


class VideoPipelineTests(unittest.TestCase):
    def test_external_display_allocations_are_not_freed_by_sr_session(self):
        allocator=BufferAllocator()
        external=[FakeBuffer((32*33*16,),np.uint32,cacheable=True) for _ in range(3)]
        with ReusableFrameSession(FakeOverlay(),33,32,allocator=allocator,buffer_count=3,
                external_output_buffers=external,output_buffer_policy='acp-cacheable') as session:
            self.assertEqual(len(allocator.buffers),3)
            session.run_rgbx(np.zeros((32,33,3),np.uint8),buffer_index=2,zero_copy=True)
        self.assertTrue(all(b.free_count==0 for b in external))
        self.assertTrue(all(b.free_count==1 for b in allocator.buffers))

    def test_overlap_executes_after_dma_launch_before_wait_and_recovers_on_error(self):
        overlay = FakeOverlay()
        allocator = BufferAllocator()
        frame = np.zeros((32,33,3),np.uint8)
        with ReusableFrameSession(overlay,33,32,allocator=allocator,timeout_seconds=1) as session:
            def overlap():
                self.assertEqual(overlay.axi_dma_0.events[-3:],
                    ['recv.transfer','control.start','send.transfer'])
                overlay.axi_dma_0.events.append('paint_previous')
            session._run(frame,'rgb',overlap=overlap)
            self.assertLess(overlay.axi_dma_0.events.index('paint_previous'),
                            overlay.axi_dma_0.events.index('send.wait'))
            def failed_presentation():
                raise RuntimeError('display failed')
            with self.assertRaisesRegex(RuntimeError,'display failed'):
                session._run(frame,'rgb',overlap=failed_presentation)
            self.assertEqual(overlay.axi_dma_0.sendchannel.stop_count,1)
            self.assertEqual(overlay.axi_dma_0.recvchannel.stop_count,1)
            self.assertEqual(overlay.axi_dma_0.control.soft_reset_count,1)
            self.assertTrue(all(b.free_count == 0 for b in allocator.buffers))

    def test_frame_session_reuses_dma_buffers_and_preserves_rgbx(self):
        overlay = FakeOverlay()
        allocator = BufferAllocator()
        frame = np.zeros((32, 33, 3), dtype=np.uint8)
        frame[0, 0] = [9, 27, 81]
        with ReusableFrameSession(overlay, 33, 32, allocator=allocator, timeout_seconds=1) as session:
            first = session.run_rgbx(frame)
            second = session.run_rgbx(frame)
            self.assertEqual(len(allocator.buffers), 2)
            self.assertEqual(first.image[0, 0].tolist(), [81, 27, 9, 0])
            self.assertEqual(second.image.shape, (128, 132, 4))
        self.assertEqual([item.free_count for item in allocator.buffers], [1, 1])
        self.assertEqual(allocator.buffers[0].to_device_count, 2)
        self.assertEqual(allocator.buffers[1].from_device_count, 2)

    def test_frame_session_exposes_owned_zero_copy_dma_slots(self):
        overlay = FakeOverlay()
        allocator = BufferAllocator()
        frame = np.zeros((32, 33, 3), dtype=np.uint8)
        frame[0, 0] = [9, 27, 81]
        with ReusableFrameSession(
            overlay,
            33,
            32,
            allocator=allocator,
            timeout_seconds=1,
            buffer_count=3,
        ) as session:
            first = session.run_rgbx(frame, buffer_index=0, zero_copy=True)
            first_snapshot = first.image.copy()
            second = session.run_rgbx(frame, buffer_index=1, zero_copy=True)
            self.assertEqual(len(allocator.buffers), 6)
            self.assertTrue(
                np.shares_memory(first.image, np.asarray(allocator.buffers[3]))
            )
            self.assertTrue(
                np.shares_memory(second.image, np.asarray(allocator.buffers[4]))
            )
            np.testing.assert_array_equal(first.image, first_snapshot)
            self.assertEqual(first.bgr_converter, "rgbx-zero-copy")
            self.assertEqual(allocator.buffers[3].to_device_count, 0)
        self.assertTrue(all(item.free_count == 1 for item in allocator.buffers))

    def test_frame_session_exposes_mb420_words_and_hardware_crc(self):
        overlay = FakeOverlay()
        allocator = BufferAllocator()
        frame = np.zeros((32, 33, 3), dtype=np.uint8)
        with ReusableFrameSession(
            overlay,
            33,
            32,
            allocator=allocator,
            timeout_seconds=1,
            buffer_count=2,
            output_mode="mb420",
        ) as session:
            result = session.run_mb420(frame, buffer_index=1)
            self.assertEqual(session.output_words, 33 * 32 * 6)
            self.assertEqual(result.image.size, 33 * 32 * 24)
            self.assertEqual(
                result.hardware_crc32,
                zlib.crc32(result.image) & 0xFFFFFFFF,
            )
            self.assertEqual(result.output_word_count, 33 * 32 * 6)
            self.assertEqual(result.bgr_converter, "fpga-mb420-zero-copy")
            self.assertEqual(overlay.axi_dma_0.control.output_mode, OUTPUT_MODE_MB420)
        self.assertTrue(all(item.free_count == 1 for item in allocator.buffers))

    def test_frame_session_requires_cacheable_acp_output_buffers(self):
        overlay = FakeOverlay()
        allocator = BufferAllocator()
        with ReusableFrameSession(
            overlay,
            33,
            32,
            allocator=allocator,
            timeout_seconds=1,
            buffer_count=2,
            output_mode="mb420",
            output_buffer_policy="acp-cacheable",
        ) as session:
            frame = np.zeros((32, 33, 3), dtype=np.uint8)
            session.run_mb420(frame, buffer_index=1)
            policy = session.buffer_policy_report()
            self.assertEqual(policy["policy"], "acp-cacheable")
            self.assertFalse(
                policy["input"]["allocation_cacheable_requested"]
            )
            self.assertFalse(policy["input"]["cacheable"])
            self.assertFalse(policy["input"]["pynq_cacheable_property"])
            self.assertTrue(policy["output"]["cacheable"])
            self.assertTrue(
                policy["output"]["allocation_cacheable_requested"]
            )
            self.assertTrue(policy["output"]["pynq_cacheable_property"])
            self.assertEqual(
                policy["output"]["pynq_cacheable_property_semantics"],
                "not-coherent",
            )
            self.assertIn(
                policy["input"]["packer"],
                ("opencv-mixchannels", "numpy-bgr-fallback"),
            )
            self.assertEqual(
                policy["output"]["transfer_coherency"],
                "acp-hardware",
            )
            self.assertFalse(policy["output"]["explicit_sync_from_device"])
            self.assertEqual(allocator.buffers[3].from_device_count, 0)
            self.assertEqual(
                [item.cacheable for item in allocator.buffers],
                [False, False, True, True],
            )

    def test_yuv_conversion_reuses_destination_and_validates_geometry(self):
        cv2 = FakeCv2([])
        source = np.zeros((128, 132, 4), dtype=np.uint8)
        destination = np.empty((128 * 132 * 3 // 2,), dtype=np.uint8)
        result = rgbx_to_yuv420p(source, cv2_module=cv2, destination=destination)
        self.assertIs(result, destination)
        with self.assertRaisesRegex(ValueError, "even"):
            rgbx_to_yuv420p(np.zeros((127, 132, 4), dtype=np.uint8), cv2_module=cv2)

    def test_converter_backend_selection_and_native_fallback(self):
        class Native:
            name = "native-test"

            @staticmethod
            def convert(source, destination):
                destination.fill(17)
                return destination

        source = np.zeros((8, 10, 4), dtype=np.uint8)
        native = Yuv420Converter(backend="auto", native_library=Native())
        self.assertEqual(native.name, "native-test")
        self.assertTrue(np.all(native.convert(source) == 17))
        opencv = Yuv420Converter(
            backend="opencv",
            cv2_module=FakeCv2([]),
            native_library=Native(),
        )
        self.assertEqual(opencv.name, "opencv-rgba-i420")
        packed = np.empty((2, 3, 4), dtype=np.uint8)
        bgr = np.arange(18, dtype=np.uint8).reshape(2, 3, 3)

        class NativePack:
            name = "native-test-pack"

            @staticmethod
            def pack(source, destination):
                destination[..., :3] = source[..., ::-1]
                destination[..., 3] = 0

        packer = BgrxInputPacker(native_packer=NativePack())
        packer.pack(bgr, packed)
        self.assertEqual(packer.name, "native-test-pack")
        np.testing.assert_array_equal(packed[..., :3], bgr[..., ::-1])
        self.assertTrue(np.all(packed[..., 3] == 0))

    def test_converter_rejects_non_contiguous_input(self):
        converter = Yuv420Converter(backend="opencv", cv2_module=FakeCv2([]))
        source = np.zeros((8, 12, 4), dtype=np.uint8)[:, ::2]
        with self.assertRaisesRegex(ValueError, "contiguous"):
            converter.convert(source)

    def test_converter_falls_back_only_in_auto_mode(self):
        with tempfile.TemporaryDirectory() as temporary:
            missing = Path(temporary) / "missing.so"
            converter = Yuv420Converter(
                backend="auto",
                cv2_module=FakeCv2([]),
                native_path=missing,
            )
            self.assertEqual(converter.name, "opencv-rgba-i420")
            with self.assertRaisesRegex(RuntimeError, "unavailable"):
                Yuv420Converter(
                    backend="native",
                    cv2_module=FakeCv2([]),
                    native_path=missing,
                )

    def test_full_pipeline_processes_ordered_dynamic_frames_with_cached_handoff(self):
        frames = []
        for index in range(4):
            frame = np.zeros((32, 33, 3), dtype=np.uint8)
            frame[..., index % 3] = 30 + index
            frames.append(frame)
        cv2 = FakeCv2(frames)
        FakeSink.instances.clear()
        CountingConverter.instances.clear()
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            input_path = root / "input.mp4"
            input_path.write_bytes(b"synthetic-video")
            report = process_video_yuv420(
                input_path,
                root / "report.json",
                "127.0.0.1",
                19037,
                overlay=FakeOverlay(),
                allocator=BufferAllocator(),
                cv2_module=cv2,
                sink_factory=FakeSink,
                yuv_converter_factory=CountingConverter,
                timeout_seconds=2,
                repeat_count=2,
                rgbx_handoff="cached-copy",
            )
        sink = FakeSink.instances[-1]
        self.assertEqual(report["status"], "pass")
        self.assertEqual(report["input"]["processed_frames"], 8)
        self.assertEqual(report["input"]["source_declared_frames"], 4)
        self.assertEqual(report["input"]["repeat_count"], 2)
        self.assertEqual(report["input"]["declared_frames"], 8)
        self.assertEqual((report["output"]["width"], report["output"]["height"]), (132, 128))
        self.assertEqual([item[0] for item in sink.frames], list(range(8)))
        self.assertEqual(sink.finished_report["video_pipeline"], VIDEO_PIPELINE)
        self.assertEqual(report["pipeline"]["rgbx_handoff"], "cached-copy")
        self.assertFalse(report["pipeline"]["zero_copy_rgbx"])
        self.assertTrue(report["pipeline"]["cached_rgbx_for_arm_conversion"])
        self.assertTrue(report["pipeline"]["separate_converter_and_sender"])
        self.assertEqual(report["pipeline"]["preallocated_dma_output_buffers"], 3)
        self.assertEqual(report["pipeline"]["preallocated_cached_rgbx_buffers"], 3)
        self.assertEqual(report["pipeline"]["yuv420_backend"], "test-auto")
        self.assertEqual(len(CountingConverter.instances), 1)
        self.assertEqual(CountingConverter.instances[0].convert_count, 8)
        self.assertIn("frame_session_total_seconds", report["performance"])
        self.assertIn("initialization_seconds", report["performance"])
        self.assertIn("cached_rgbx_copy_seconds", report["performance"])
        self.assertIn("p95", report["performance"]["pipeline_latency_seconds"])
        self.assertIn("p99", report["performance"]["pipeline_latency_seconds"])
        self.assertTrue(cv2.capture.released)

    def test_full_pipeline_retains_selectable_zero_copy_handoff(self):
        frames = [np.zeros((32, 34, 3), dtype=np.uint8) for _ in range(2)]
        cv2 = FakeCv2(frames)
        FakeSink.instances.clear()
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            input_path = root / "input.mp4"
            input_path.write_bytes(b"synthetic-video")
            report = process_video_yuv420(
                input_path,
                root / "report.json",
                "127.0.0.1",
                19037,
                overlay=FakeOverlay(),
                allocator=BufferAllocator(),
                cv2_module=cv2,
                sink_factory=FakeSink,
                yuv_converter_factory=CountingConverter,
                timeout_seconds=2,
                rgbx_handoff="zero-copy",
                cpu_affinity=False,
            )
        self.assertEqual(report["pipeline"]["rgbx_handoff"], "zero-copy")
        self.assertTrue(report["pipeline"]["zero_copy_rgbx"])

    def test_full_pipeline_sends_fpga_mb420_without_arm_conversion(self):
        frames = [np.zeros((32, 33, 3), dtype=np.uint8) for _ in range(3)]
        cv2 = FakeCv2(frames)
        FakeSink.instances.clear()
        CountingConverter.instances.clear()
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            input_path = root / "input.mp4"
            input_path.write_bytes(b"synthetic-video")
            report = process_video_yuv420(
                input_path,
                root / "report.json",
                "127.0.0.1",
                19037,
                overlay=FakeOverlay(),
                allocator=BufferAllocator(),
                cv2_module=cv2,
                sink_factory=FakeSink,
                yuv_converter_factory=CountingConverter,
                timeout_seconds=2,
                fpga_output="mb420",
            )
        sink = FakeSink.instances[-1]
        self.assertEqual(len(CountingConverter.instances), 0)
        self.assertEqual(sink.session.pixel_format_name, "mb420")
        self.assertEqual(len(sink.frames), 3)
        self.assertTrue(
            all(len(payload) == 33 * 32 * 24 for _, payload, _ in sink.frames)
        )
        self.assertTrue(
            all(metrics["payload_crc32"] is not None for _, _, metrics in sink.frames)
        )
        self.assertEqual(report["pipeline"]["fpga_output"], "mb420")
        self.assertEqual(report["pipeline"]["preallocated_yuv420p_buffers"], 0)
        self.assertEqual(report["performance"]["hardware_crc_frames"], 3)
        self.assertIsNone(report["performance"]["rgbx_to_yuv420p_seconds"])
        self.assertFalse(report["pipeline"]["cached_rgbx_for_arm_conversion"])
        self.assertEqual(report["pipeline"]["preallocated_cached_rgbx_buffers"], 0)
        self.assertEqual(report["pipeline"]["cpu_affinity"]["enabled"], False)

    def test_mb420_dual_core_roles_and_network_irq_are_reported(self):
        frames = [np.zeros((32, 33, 3), dtype=np.uint8) for _ in range(2)]
        cv2 = FakeCv2(frames)
        FakeSink.instances.clear()
        FakeIrqTuner.instances.clear()
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            input_path = root / "input.mp4"
            input_path.write_bytes(b"synthetic-video")
            with patch(
                "video_pipeline.board_pipeline._available_cpus",
                return_value=[0, 1],
            ), patch(
                "video_pipeline.board_pipeline._set_current_thread_affinity",
                side_effect=lambda cpu: {
                    "requested_cpu": cpu,
                    "effective_cpus": [cpu],
                    "status": "applied",
                },
            ):
                report = process_video_yuv420(
                    input_path,
                    root / "report.json",
                    "127.0.0.1",
                    19037,
                    overlay=FakeOverlay(),
                    allocator=BufferAllocator(),
                    cv2_module=cv2,
                    sink_factory=FakeSink,
                    timeout_seconds=2,
                    fpga_output="mb420",
                    cpu_affinity=True,
                    network_irq_affinity=True,
                    irq_tuner_factory=FakeIrqTuner,
                )
        roles = report["pipeline"]["cpu_affinity"]["role_map"]
        self.assertEqual(roles["dma_and_decode"], 0)
        self.assertEqual(roles["network_sender"], 1)
        self.assertEqual(
            report["pipeline"]["cpu_affinity"]["sender"]["effective_cpus"],
            [1],
        )
        irq = report["pipeline"]["network_irq_affinity"]
        self.assertTrue(irq["requested"])
        self.assertEqual(irq["target_cpu"], 1)
        self.assertEqual(irq["restore_status"], "restored")
        self.assertTrue(FakeIrqTuner.instances[-1].restored)

    def test_network_irq_tuning_requires_cpu_affinity(self):
        frame = np.zeros((32, 34, 3), dtype=np.uint8)
        cv2 = FakeCv2([frame])
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            input_path = root / "input.mp4"
            input_path.write_bytes(b"synthetic-video")
            with self.assertRaisesRegex(ValueError, "requires cpu_affinity"):
                process_video_yuv420(
                    input_path,
                    root / "report.json",
                    "127.0.0.1",
                    19037,
                    overlay=FakeOverlay(),
                    allocator=BufferAllocator(),
                    cv2_module=cv2,
                    sink_factory=FakeSink,
                    network_irq_affinity=True,
                )

    def test_full_pipeline_rejects_unknown_rgbx_handoff(self):
        frame = np.zeros((32, 34, 3), dtype=np.uint8)
        cv2 = FakeCv2([frame])
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            input_path = root / "input.mp4"
            input_path.write_bytes(b"synthetic-video")
            with self.assertRaisesRegex(ValueError, "rgbx_handoff"):
                process_video_yuv420(
                    input_path,
                    root / "report.json",
                    "127.0.0.1",
                    19037,
                    overlay=FakeOverlay(),
                    allocator=BufferAllocator(),
                    cv2_module=cv2,
                    sink_factory=FakeSink,
                    rgbx_handoff="unknown",
                )

if __name__ == "__main__":
    unittest.main(verbosity=2)
