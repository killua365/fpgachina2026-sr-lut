import copy
import json
from pathlib import Path
import sys
import tempfile
import types
import unittest
from unittest.mock import patch

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from checks.validation import (compare_arrays, completion_rate, sha256,
                               timing_summary, utilization_summary,
                               validate_hwh, verify_files)
from examples.mock_backend import MockBackend
from examples.reference import golden, make_input
from run_checks import run_config
from templates.session import BatchSession, PINNED_SESSIONS
from templates.pynq_backend import PynqBackend
from templates.control_hooks import HlsControlHooks

CONTRACT = {"input": {"shape": [32, 48], "dtype": "u1"},
            "output": {"shape": [32, 48], "dtype": "u1"}}
HWH = """<EDKSYSTEM><SYSTEMINFO DEVICE="synthetic"/>
<MODULES><MODULE INSTANCE="dma" FULLNAME="/dma" VLNV="xilinx.com:ip:axi_dma:7.1">
<PARAMETERS><PARAMETER NAME="C_INCLUDE_SG" VALUE="0"/>
<PARAMETER NAME="C_INCLUDE_MM2S" VALUE="1"/>
<PARAMETER NAME="C_INCLUDE_S2MM" VALUE="1"/></PARAMETERS>
</MODULE></MODULES></EDKSYSTEM>"""
TIMING = "Design Timing Summary\n0.1 0.0 0 10 0.2 0.0 0 10 0.3 0.0 0 10\nClock Summary"
UTIL = """| Instance | Module | Total LUTs | FFs | RAMB36 | RAMB18 | DSP Blocks |
| top | (top) | 123 | 456 | 2 | 3 | 4 |"""


class ValidationTests(unittest.TestCase):
    def test_integer_corruption(self):
        self.assertFalse(compare_arrays(np.array([0], "u1"), np.array([255], "u1"))["passed"])

    def test_int64_extremes_no_wrap(self):
        value = compare_arrays(np.array([-2**63], "i8"), np.array([2**63-1], "i8"))
        self.assertEqual(value["mismatches"], 1)
        self.assertGreater(value["maximum_absolute_error"], 2**63)

    def test_float_tolerance(self):
        self.assertTrue(compare_arrays(np.array([1.001]), np.array([1.0]), atol=.002)["passed"])
        self.assertFalse(compare_arrays(np.array([1.01]), np.array([1.0]), atol=.002)["passed"])

    def test_invalid_arrays(self):
        for actual, expected in ((np.array([]), np.array([])),
                                 (np.array([np.nan]), np.array([np.nan])),
                                 (np.zeros(2), np.zeros(3)),
                                 (np.zeros(2, "u1"), np.zeros(2, "i2"))):
            with self.subTest(actual=actual), self.assertRaises(ValueError):
                compare_arrays(actual, expected)

    def test_integer_tolerance_forbidden(self):
        with self.assertRaises(ValueError):
            compare_arrays(np.zeros(1, "u1"), np.zeros(1, "u1"), atol=1)

    def test_all_timing_domains(self):
        self.assertTrue(timing_summary(TIMING)["passed"])
        for old, new in (("0.1", "-0.1"), ("0.2", "-0.2"), ("0.3", "-0.3"),
                         ("0.0 0 10", "0.0 1 10")):
            with self.subTest(domain=old):
                self.assertFalse(timing_summary(TIMING.replace(old, new))["passed"])
        with self.assertRaises(ValueError):
            timing_summary("WNS 0.3")

    def test_utilization_explicit_row(self):
        self.assertEqual(utilization_summary(UTIL, "top")["bram_36k_equivalent"], 3.5)
        with self.assertRaises(ValueError):
            utilization_summary(UTIL, "missing")

    def test_completion_duplicates_and_window(self):
        rows = [dict(time_s=t, id=i, stage=s, epoch=e, complete=c)
                for t, i, s, e, c in ((0, 1, "display", 1, True),
                                      (.1, 1, "display", 1, True),
                                      (.2, 2, "submit", 1, True),
                                      (.3, 3, "display", 2, True),
                                      (.4, 4, "display", 1, False),
                                      (1, 5, "display", 1, True))]
        result = completion_rate(rows, 0, 1, "display", 1)
        self.assertEqual(result["completed_unique"], 1)
        self.assertEqual(result["duplicates_excluded"], 1)
        self.assertEqual(result["units_per_second"], 1)

    def test_invalid_clock_and_duration(self):
        rows = [dict(time_s=t, id=1, stage="done", epoch=1, complete=True) for t in (1, 0)]
        with self.assertRaises(ValueError):
            completion_rate(rows, 0, 2, "done", 1)
        with self.assertRaises(ValueError):
            completion_rate([], 0, 0, "done", 1)

    def test_completion_before_window_not_recounted(self):
        rows = [dict(time_s=t, id=1, stage="done", epoch=1, complete=True)
                for t in (0.1, 1.1)]
        result = completion_rate(rows, 1, 2, "done", 1)
        self.assertEqual(result["completed_unique"], 0)
        self.assertEqual(result["duplicates_excluded"], 1)

    def test_unparseable_and_duplicate_resources(self):
        with self.assertRaises(ValueError):
            utilization_summary(UTIL.replace("123", "unknown"), "top")
        with self.assertRaises(ValueError):
            utilization_summary(UTIL + "\n" + UTIL, "top")

    def test_duplicate_manifest_and_non_dma(self):
        with tempfile.TemporaryDirectory() as folder:
            p = Path(folder)/"test.hwh"
            p.write_text(HWH, "utf-8")
            entry = {"path": p.name, "sha256": sha256(p)}
            with self.assertRaises(ValueError):
                verify_files(folder, [entry, entry])
            p.write_text(HWH.replace(":axi_dma:", ":axi_vdma:"), "utf-8")
            with self.assertRaises(ValueError):
                validate_hwh(p, ["dma"], "dma")

    def test_hash_and_hwh(self):
        with tempfile.TemporaryDirectory() as folder:
            p = Path(folder) / "test.hwh"
            p.write_text(HWH, "utf-8")
            entries = [{"path": p.name, "sha256": sha256(p)}]
            verify_files(folder, entries)
            validate_hwh(p, ["dma"], "dma")
            with self.assertRaises(ValueError):
                validate_hwh(p, ["other"])
            p.write_text(HWH.replace('C_INCLUDE_SG" VALUE="0"', 'C_INCLUDE_SG" VALUE="1"'), "utf-8")
            with self.assertRaises(ValueError):
                validate_hwh(p, ["dma"], "dma")
            with self.assertRaises(ValueError):
                verify_files(folder, entries)
            with self.assertRaises(ValueError):
                verify_files(folder, [{"path": "../escape", "sha256": "0"*64}])

    def test_empty_configuration_fails(self):
        with tempfile.TemporaryDirectory() as folder:
            p = Path(folder) / "test.json"
            p.write_text("{}", "utf-8")
            with self.assertRaises(ValueError):
                run_config(p, Path(folder)/"out")


class SessionTests(unittest.TestCase):
    def test_two_independent_examples(self):
        for name in ("image_threshold", "sensor_gain"):
            with self.subTest(name=name), tempfile.TemporaryDirectory() as folder:
                result = run_config(ROOT/"examples"/(name+".json"), Path(folder))
                self.assertTrue(result["passed"])
                self.assertFalse(result["hardware_access"])

    def test_boundary_reference(self):
        source = np.array([[0, 127, 128, 255]], "u1")
        np.testing.assert_array_equal(golden(source, "image_threshold", 128), [[0, 0, 255, 255]])
        source = np.array([[-32768, -1, 0, 1, 32767]], "<i2")
        np.testing.assert_array_equal(golden(source, "sensor_gain", 3), [[-32768, -3, 0, 3, 32767]])

    def test_protocol_order_reuse_snapshot(self):
        backend = MockBackend("image_threshold", 128)
        with BatchSession(backend, CONTRACT) as session:
            first, _ = session.run(np.zeros((32, 48), "u1"))
            second, _ = session.run(np.full((32, 48), 255, "u1"))
            self.assertEqual(len(backend.allocations), 2)
            self.assertEqual(int(first.max()), 0)
            self.assertEqual(int(second.min()), 255)
        e = backend.events
        self.assertLess(e.index("sync_to_device"), e.index("receive"))
        self.assertLess(e.index("receive"), e.index("start"))
        self.assertLess(e.index("start"), e.index("send"))
        self.assertLess(e.index("complete"), e.index("sync_from_device"))
        self.assertLess(e.index("halt"), e.index("free"))

    def test_noncontiguous_input(self):
        source = np.arange(32*96, dtype=np.uint8).reshape(32, 96)[:, ::2]
        with BatchSession(MockBackend("image_threshold", 128), CONTRACT) as session:
            actual, _ = session.run(source)
        np.testing.assert_array_equal(actual, golden(source, "image_threshold", 128))

    def test_implicit_cast_rejected(self):
        with BatchSession(MockBackend("image_threshold", 128), CONTRACT) as session:
            with self.assertRaises(ValueError):
                session.run(np.zeros((32, 48), "i2"))

    def test_allocation_failure_releases_only_allocated_buffer(self):
        backend = MockBackend("image_threshold", 128)
        allocate = backend.allocate
        def fail_second(shape, dtype):
            if backend.allocations:
                raise MemoryError("synthetic exhausted CMA")
            return allocate(shape, dtype)
        backend.allocate = fail_second
        with self.assertRaises(MemoryError):
            BatchSession(backend, CONTRACT)
        self.assertTrue(backend.allocations[0].freed)

    def test_bad_contract_before_allocation(self):
        for dtype, shape in (("complex64", [2]), ("u1", [0]), ("u1", [True])):
            contract = copy.deepcopy(CONTRACT)
            contract["input"] = {"dtype": dtype, "shape": shape}
            backend = MockBackend("image_threshold", 128)
            with self.subTest(dtype=dtype, shape=shape), self.assertRaises(ValueError):
                BatchSession(backend, contract)
            self.assertEqual(len(backend.allocations), 0)

    def test_timeout_halt_before_free(self):
        backend = MockBackend("image_threshold", 128, stalled=True)
        session = BatchSession(backend, CONTRACT, timeout_s=.002)
        with self.assertRaises(TimeoutError):
            session.run(np.zeros((32, 48), "u1"))
        self.assertTrue(session.closed)
        self.assertLess(backend.events.index("halt"), backend.events.index("free"))

    def test_failed_halt_retains_buffers(self):
        backend = MockBackend("image_threshold", 128, stalled=True, halt_ok=False)
        session = BatchSession(backend, CONTRACT, timeout_s=.002)
        with self.assertRaises(RuntimeError):
            session.run(np.zeros((32, 48), "u1"))
        self.assertTrue(session.quarantined)
        self.assertFalse(any(b.freed for b in backend.allocations))
        self.assertTrue(any(s is session for s in PINNED_SESSIONS))
        with self.assertRaises(RuntimeError):
            session.run(np.zeros((32, 48), "u1"))
        backend.halt_ok = True
        session.close()
        self.assertTrue(session.closed)


class HardwareAdapterTests(unittest.TestCase):
    """Fake MMIO and fake PYNQ only. These are not physical board tests."""

    def test_download_is_explicit(self):
        with self.assertRaises(ValueError):
            PynqBackend.load({}, ".", None)

    def test_simple_dma_status_and_early_tlast(self):
        registers = {4: 2, 0x34: 2, 0x28: 16, 0x58: 16, 0: 1, 0x30: 1}
        mmio = types.SimpleNamespace(read=lambda addr: registers[addr],
                                     write=lambda addr, value: registers.__setitem__(addr, value))
        hooks = types.SimpleNamespace(done=lambda ol: True)
        backend = PynqBackend(None, types.SimpleNamespace(mmio=mmio), None, hooks)
        backend.expected_input_bytes = backend.expected_output_bytes = 16
        self.assertTrue(backend.poll())
        registers[0x58] = 8
        with self.assertRaises(RuntimeError):
            backend.poll()
        registers[4] = 0x10
        with self.assertRaises(RuntimeError):
            backend.poll()

    def test_halt_timeout_and_success(self):
        values = {0: 1, 0x30: 1, 4: 2, 0x34: 2}
        mmio = types.SimpleNamespace(read=lambda n: values[n],
                                     write=lambda n, v: values.__setitem__(n, v))
        backend = PynqBackend(None, types.SimpleNamespace(mmio=mmio), None, None)
        self.assertFalse(backend.halt(.002))
        values[4] = values[0x34] = 1
        self.assertTrue(backend.halt(.002))

    def test_hls_done_is_latched(self):
        values, writes = {0: 4}, []
        ip = types.SimpleNamespace(read=lambda n: values[n],
                                   write=lambda n, v: writes.append((n, v)))
        ol = types.SimpleNamespace(core=ip)
        hooks = HlsControlHooks("core", [(0x10, 128)])
        hooks.configure(ol, CONTRACT)
        hooks.start(ol)
        values[0] = 2
        self.assertTrue(hooks.done(ol))
        values[0] = 0
        self.assertTrue(hooks.done(ol))
        self.assertEqual(writes, [(0x10, 128), (0, 1)])

    def test_preflight_before_download_fake_pynq(self):
        events = []
        hooks = types.SimpleNamespace(configure=lambda *a: None, start=lambda *a: None,
                                      done=lambda *a: True)
        class FakeOverlay:
            def __init__(self, name, download):
                events.append(("parse", download))
                self.ip_dict = {"dma": {"type": "xilinx.com:ip:axi_dma:7.1",
                    "parameters": {"c_include_sg": "0", "c_include_mm2s": "1", "c_include_s2mm": "1"}}}
                self.dma = object()
            def download(self):
                events.append(("download", True))
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            (root/"test.bit").write_bytes(b"SYNTHETIC-NOT-A-BITSTREAM")
            (root/"test.hwh").write_text(HWH, "utf-8")
            profile = {"bit": "test.bit", "hwh": "test.hwh", "dma_ip": "dma",
                       "required_ips": ["dma"], "files": [
                           {"path": n, "sha256": sha256(root/n)} for n in ("test.bit", "test.hwh")]}
            fake = types.SimpleNamespace(Overlay=FakeOverlay, allocate=lambda **k: None)
            with patch.dict(sys.modules, {"pynq": fake}):
                PynqBackend.load(profile, root, hooks, allow_download=True)
                self.assertEqual(events, [("parse", False), ("download", True)])
                events.clear()
                profile["files"][0]["sha256"] = "0"*64
                with self.assertRaises(ValueError):
                    PynqBackend.load(profile, root, hooks, allow_download=True)
                self.assertEqual(events, [])


if __name__ == "__main__":
    unittest.main()
