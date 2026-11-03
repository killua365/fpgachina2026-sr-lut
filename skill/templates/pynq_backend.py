"""Explicit hardware adapter for PYNQ + full Overlay + bidirectional simple AXI DMA.

This newly extracted adapter has offline/fake-device coverage, not new board
qualification. No import-time download. No SR-LUT imports or board addresses.
"""
from __future__ import annotations

import time
from pathlib import Path

from checks.validation import validate_hwh, verify_files


def resolve_ip(overlay, name):
    obj = overlay
    for part in name.split("/"):
        obj = getattr(obj, part)
    return obj


class PynqBackend:
    hardware_access = True

    def __init__(self, overlay, dma, allocator, hooks):
        self.overlay, self.dma = overlay, dma
        self.allocator, self.hooks = allocator, hooks

    @classmethod
    def load(cls, profile, root, hooks, *, allow_download=False):
        """hooks: configure(overlay,contract), start(overlay), done(overlay)->bool.

        The caller must first stop existing producers/consumers and own the board.
        This function cannot infer whether an unrelated application is running.
        """
        if not allow_download:
            raise ValueError("Explicit allow_download=True is required")
        for name in ("configure", "start", "done"):
            if not callable(getattr(hooks, name, None)):
                raise ValueError("Missing algorithm hook: " + name)
        root = Path(root).resolve()
        verify_files(root, profile["files"])
        from checks.validation import local_path
        bit = local_path(root, profile["bit"])
        hwh = local_path(root, profile["hwh"])
        declared = {item["path"] for item in profile["files"]}
        if profile["bit"] not in declared or profile["hwh"] not in declared:
            raise ValueError("BIT and HWH must both be hashed")
        if bit.suffix != ".bit" or hwh != bit.with_suffix(".hwh"):
            raise ValueError("PYNQ requires adjacent same-stem .bit/.hwh")
        validate_hwh(hwh, profile["required_ips"], profile["dma_ip"])
        from pynq import Overlay, allocate
        overlay = Overlay(str(bit), download=False)
        for name in profile["required_ips"]:
            if name not in overlay.ip_dict:
                raise ValueError("PYNQ metadata IP missing: " + name)
        # Parameter checks precede download and creation of a DMA driver.
        entry = overlay.ip_dict[profile["dma_ip"]]
        if ":axi_dma:" not in entry["type"]:
            raise ValueError("Expected AXI DMA")
        params = entry["parameters"]
        for name, wanted in (("c_include_sg", 0), ("c_include_mm2s", 1),
                             ("c_include_s2mm", 1)):
            if int(params[name]) != wanted:
                raise ValueError("Only bidirectional simple-mode DMA is supported")
        overlay.download()
        dma = resolve_ip(overlay, profile["dma_ip"])
        return cls(overlay, dma, allocate, hooks)

    def allocate(self, shape, dtype):
        return self.allocator(shape=shape, dtype=dtype)

    def configure(self, contract):
        self.hooks.configure(self.overlay, contract)

    def receive(self, buffer):
        self.dma.recvchannel.transfer(buffer)
        self.expected_output_bytes = buffer.nbytes

    def start(self):
        self.hooks.start(self.overlay)

    def send(self, buffer):
        self.dma.sendchannel.transfer(buffer)
        self.expected_input_bytes = buffer.nbytes

    def poll(self):
        # Avoid the driver's unbounded wait/stop loops. Status offsets are
        # AXI DMA simple mode (PG021), NOT VDMA or custom accelerator registers.
        mmio = self.dma.mmio
        states = [mmio.read(offset) for offset in (0x04, 0x34)]
        if any(state & 0x70 for state in states):
            raise RuntimeError("AXI DMA error: " + repr(states))
        if any(state & 1 for state in states):
            raise RuntimeError("AXI DMA unexpectedly halted")
        if not all(state & 2 for state in states):
            return False
        if mmio.read(0x28) != self.expected_input_bytes:
            raise RuntimeError("Input byte count mismatch")
        if mmio.read(0x58) != self.expected_output_bytes:
            raise RuntimeError("Output byte count mismatch / early TLAST")
        return bool(self.hooks.done(self.overlay))

    def halt(self, timeout_s):
        mmio = self.dma.mmio
        for offset in (0x00, 0x30):
            mmio.write(offset, mmio.read(offset) & ~1)
        deadline = time.monotonic() + timeout_s
        while not all(mmio.read(offset) & 1 for offset in (0x04, 0x34)):
            if time.monotonic() >= deadline:
                return False
            time.sleep(0.0001)
        # No automatic IP reset or Overlay replacement on unknown hardware.
        return True

    def free(self, buffer):
        buffer.freebuffer()
