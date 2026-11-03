"""SIMULATED device, with separate CPU/device memory to exercise synchronization."""
import numpy as np


class MockBuffer(np.ndarray):
    def __new__(cls, shape, dtype, events):
        obj = np.zeros(shape, dtype=dtype).view(cls)
        obj.device_storage = np.zeros(shape, dtype=dtype)
        obj.events, obj.freed = events, False
        return obj

    def sync_to_device(self):
        self.events.append("sync_to_device")
        np.copyto(self.device_storage, self)

    def sync_from_device(self):
        self.events.append("sync_from_device")
        np.copyto(self, self.device_storage)


class MockBackend:
    hardware_access = False

    def __init__(self, example, parameter, *, stalled=False, halt_ok=True, corrupt=False):
        self.example, self.parameter = example, parameter
        self.stalled, self.halt_ok, self.corrupt = stalled, halt_ok, corrupt
        self.events, self.allocations = [], []
        self.sent, self.received, self.started = None, None, False

    def allocate(self, shape, dtype):
        obj = MockBuffer(shape, dtype, self.events)
        self.allocations.append(obj)
        return obj

    def configure(self, contract):
        self.events.append("configure")

    def receive(self, buffer):
        self.events.append("receive")
        self.received = buffer

    def start(self):
        if self.received is None:
            raise RuntimeError("Receive must be armed before core starts")
        self.events.append("start")
        self.started = True

    def send(self, buffer):
        if not self.started:
            raise RuntimeError("Core must be armed before input")
        self.events.append("send")
        self.sent = buffer

    def poll(self):
        if self.stalled:
            return False
        if self.example == "image_threshold":
            result = np.where(self.sent.device_storage >= self.parameter, 255, 0).astype("u1")
        elif self.example == "sensor_gain":
            result = np.clip(self.sent.device_storage.astype(np.int64) * self.parameter,
                             -32768, 32767).astype("<i2")
        else:
            raise ValueError("Unknown simulated operator")
        np.copyto(self.received.device_storage, result)
        if self.corrupt:
            self.received.device_storage.flat[0] = 1
        self.events.append("complete")
        return True

    def halt(self, timeout_s):
        self.events.append("halt")
        return self.halt_ok

    def free(self, buffer):
        self.events.append("free")
        buffer.freed = True
