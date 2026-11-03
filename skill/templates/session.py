"""Single-owner batch session. Backend supplies PYNQ or explicit simulated I/O."""
from __future__ import annotations

import time
import numpy as np

# A failed halt must not cause automatic garbage collection of DMA-owned memory.
PINNED_SESSIONS = []


def array_contract(spec):
    shape = tuple(spec["shape"])
    if not shape or any(type(n) is not int or n <= 0 for n in shape):
        raise ValueError("Contract dimensions must be positive integers")
    dtype = np.dtype(spec["dtype"])
    if dtype.kind not in "uif" or not dtype.isnative:
        raise ValueError("Contract requires a native-endian real numeric dtype")
    return shape, dtype


class BatchSession:
    """Backend contract: allocate/configure/receive/start/send/poll/halt/free."""

    def __init__(self, backend, contract, timeout_s=2.0):
        if not np.isfinite(timeout_s) or timeout_s <= 0:
            raise ValueError("timeout_s must be positive")
        self.backend, self.contract = backend, contract
        self.timeout_s, self.closed, self.quarantined = timeout_s, False, False
        self.input_shape, self.input_dtype = array_contract(contract["input"])
        self.output_shape, self.output_dtype = array_contract(contract["output"])
        self.buffers = []
        try:
            self.buffers.append(backend.allocate(self.input_shape, self.input_dtype))
            self.buffers.append(backend.allocate(self.output_shape, self.output_dtype))
        except BaseException:
            for buffer in self.buffers:
                backend.free(buffer)
            self.closed = True
            raise

    def run(self, source):
        if self.closed or self.quarantined:
            raise RuntimeError("Session is closed or quarantined")
        source = np.asarray(source)
        if source.shape != self.input_shape or source.dtype != self.input_dtype:
            raise ValueError("Input differs from contract; implicit casts are forbidden")
        if not np.isfinite(source).all():
            raise ValueError("Nonfinite input")
        input_buffer, output_buffer = self.buffers
        begin = time.monotonic()
        try:
            np.copyto(input_buffer, source)
            input_buffer.sync_to_device()
            # Clear stale output and flush dirty CPU lines before hardware writes.
            output_buffer.fill(0)
            output_buffer.sync_to_device()
            self.backend.configure(self.contract)
            self.backend.receive(output_buffer)
            self.backend.start()
            self.backend.send(input_buffer)
            deadline = time.monotonic() + self.timeout_s
            while not self.backend.poll():
                if time.monotonic() >= deadline:
                    raise TimeoutError("DMA/core completion timeout")
                time.sleep(0.0001)
            output_buffer.sync_from_device()
            result = np.array(output_buffer, copy=True)
        except BaseException:
            self.close()
            raise
        return result, {"host_total_seconds": time.monotonic() - begin,
                        "hardware_access": self.backend.hardware_access}

    def close(self):
        if self.closed:
            return
        try:
            stopped = self.backend.halt(self.timeout_s)
        except BaseException:
            stopped = False
        if not stopped:
            self.quarantined = True
            if not any(item is self for item in PINNED_SESSIONS):
                PINNED_SESSIONS.append(self)
            raise RuntimeError("DMA stop unconfirmed: buffers retained; session quarantined")
        for buffer in self.buffers:
            self.backend.free(buffer)
        self.buffers.clear()
        PINNED_SESSIONS[:] = [item for item in PINNED_SESSIONS if item is not self]
        self.closed = True

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()
