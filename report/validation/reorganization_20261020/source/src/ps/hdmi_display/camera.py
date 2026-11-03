"""Bounded RGB camera mailbox with a replaceable, spawn-only UVC worker.

Only the application thread calls CameraSource methods. The worker never loads
PYNQ or touches the FPGA. A stuck V4L2 open/read can be terminated without
leaving a camera thread behind inside the hardware-owning process.
"""

from __future__ import annotations

from dataclasses import dataclass
import math
import multiprocessing as mp
import time

import cv2
import numpy as np

from roi_magnifier.geometry import Rect
from .geometry import Viewport
from .pixels import camera_area_rgb, yuyv_bgr


@dataclass(frozen=True)
class CameraFrame:
    generation: int
    sequence: int
    rgb: np.ndarray
    captured_monotonic: float
    prepare_seconds: float
    source_width: int
    source_height: int
    active_region: Rect


def prepare_camera_frame(bgr, width=320, height=180):
    if not isinstance(bgr, np.ndarray) or bgr.dtype != np.uint8 or bgr.ndim != 3 or bgr.shape[2] != 3:
        raise ValueError("The camera must decode to uint8 BGR")
    source_height, source_width = bgr.shape[:2]
    if min(source_width, source_height) < 1 or max(source_width, source_height) > 4096:
        raise ValueError("Decoded camera size exceeds the supported contract")
    if not 32 <= width <= 480 or not 32 <= height <= 320:
        raise ValueError("Camera canvas exceeds the SR contract")
    active = Viewport.fit(source_width, source_height, Rect(0, 0, width, height)).bounds
    result = np.zeros((height, width, 3), dtype=np.uint8)
    target = result[active.y:active.bottom, active.x:active.right]
    if not camera_area_rgb(bgr, target):
        resized = cv2.resize(bgr, (active.width, active.height), interpolation=cv2.INTER_AREA)
        target[:] = cv2.cvtColor(resized, cv2.COLOR_BGR2RGB)
    return result, active


class _RawYuyvCapture:
    """Retain full sensor geometry; convert before the unchanged area filter."""
    def __init__(self, capture, width, height):
        self.capture = capture
        self.width, self.height = width, height
        self.bgr = np.empty((height,width,3),np.uint8)

    def read(self):
        ok, raw = self.capture.read()
        if not ok or raw is None:
            return False, None
        if raw.dtype != np.uint8 or raw.size != self.width*self.height*2 or not raw.flags.c_contiguous:
            raise RuntimeError('Raw camera buffer differs from negotiated YUYV geometry')
        return True, yuyv_bgr(raw.reshape(self.height,self.width,2),self.bgr)

    def release(self):
        self.capture.release()


def _open_camera_native(device, camera_width, camera_height, fps):
    from .pixels import _native
    capture = cv2.VideoCapture(device, cv2.CAP_V4L2)
    if not capture.isOpened():
        capture.release()
        raise RuntimeError("UVC camera is missing or cannot be opened")
    if _native is not None and getattr(_native,'hdmi_yuyv_bgr',None) is not None:
        requested = [(cv2.CAP_PROP_FOURCC,cv2.VideoWriter_fourcc(*'YUYV')),
                     (cv2.CAP_PROP_FRAME_WIDTH,camera_width),(cv2.CAP_PROP_FRAME_HEIGHT,camera_height),
                     (cv2.CAP_PROP_FPS,fps),(cv2.CAP_PROP_BUFFERSIZE,4),(cv2.CAP_PROP_CONVERT_RGB,0)]
        accepted = [capture.set(key,value) for key,value in requested]
        if all(accepted) and all(capture.get(key)==value for key,value in requested[:4]):
            return _RawYuyvCapture(capture,camera_width,camera_height)
        # Unsupported native format falls back to the original decoded mode.
        capture.release()
        capture = cv2.VideoCapture(device,cv2.CAP_V4L2)
        if not capture.isOpened():
            capture.release()
            raise RuntimeError('Camera could not reopen in decoded mode')
    capture.set(cv2.CAP_PROP_FOURCC, cv2.VideoWriter_fourcc(*"MJPG"))
    capture.set(cv2.CAP_PROP_FRAME_WIDTH, camera_width)
    capture.set(cv2.CAP_PROP_FRAME_HEIGHT, camera_height)
    capture.set(cv2.CAP_PROP_FPS, fps)
    capture.set(cv2.CAP_PROP_BUFFERSIZE, 1)
    return capture


def _open_camera(device, camera_width, camera_height, fps):
    """Prefer the format measured with concurrent full-resolution display work."""
    capture = cv2.VideoCapture(device, cv2.CAP_V4L2)
    try:
        if not capture.isOpened():
            raise RuntimeError('UVC camera is missing or cannot be opened')
        requested=[(cv2.CAP_PROP_FOURCC,cv2.VideoWriter_fourcc(*'MJPG')),
                   (cv2.CAP_PROP_FRAME_WIDTH,camera_width),(cv2.CAP_PROP_FRAME_HEIGHT,camera_height),
                   (cv2.CAP_PROP_FPS,fps)]
        if not all([capture.set(k,v) for k,v in requested]):
            raise RuntimeError('Camera rejected full-resolution MJPEG properties')
        if not all(capture.get(k)==v for k,v in requested):
            raise RuntimeError('Camera negotiated a different geometry, rate or format')
        capture.set(cv2.CAP_PROP_BUFFERSIZE,1)
        return capture
    except BaseException:
        capture.release()
        raise


def _capture_worker(factory, config, pixels, metadata, lock):
    cv2.setNumThreads(1)
    capture = None
    try:
        device, camera_width, camera_height, fps, width, height = config
        capture = factory(device, camera_width, camera_height, fps)
        destination = np.frombuffer(pixels, dtype=np.uint8).reshape(height, width, 3)
        sequence = 0
        started = time.monotonic()
        while True:
            read_started = time.monotonic()
            ok, bgr = capture.read()
            captured = time.monotonic()
            if not ok or bgr is None:
                raise RuntimeError("UVC frame read failed")
            rgb, active = prepare_camera_frame(bgr, width, height)
            prepared = time.monotonic() - captured
            sequence += 1
            # One complete frame slot. No unbounded Queue/pipe feeder and no
            # source/result mismatch when the producer outruns the renderer.
            with lock:
                np.copyto(destination, rgb)
                metadata[:] = (sequence, captured, prepared, bgr.shape[1], bgr.shape[0],
                               active.x, active.y, active.width, active.height,
                               captured-read_started, sequence/max(1e-6,time.monotonic()-started),
                               int(isinstance(capture,_RawYuyvCapture)))
    finally:
        if capture is not None:
            capture.release()


class CameraSource:
    def __init__(self, device="/dev/video0", *, camera_size=(640, 480), fps=30,
                 input_size=(320, 180), retry_seconds=1.0, read_timeout=3.0,
                 open_timeout=20.0, capture_factory=_open_camera, clock=time.monotonic):
        width, height = input_size
        if any(type(value) is not int for value in (*camera_size, fps, width, height)):
            raise ValueError("Camera geometry and rate must be integers")
        if not 32 <= width <= 480 or not 32 <= height <= 320:
            raise ValueError("Camera canvas exceeds the SR contract")
        if min(camera_size) < 1 or max(camera_size) > 4096 or not 1 <= fps <= 60:
            raise ValueError("Invalid camera size or source rate")
        if not all(math.isfinite(v) and v > 0 for v in (retry_seconds, read_timeout, open_timeout)):
            raise ValueError("Camera retry and timeout must be finite and positive")
        self.config = (device, *camera_size, fps, width, height)
        self.factory, self.clock = capture_factory, clock
        self.retry_seconds, self.read_timeout = retry_seconds, read_timeout
        self.open_timeout = open_timeout
        # Fork would inherit live MMIO/CMA mappings and FPGA file descriptors.
        self.context = mp.get_context("spawn")
        self.process = self.pixels = self.metadata = self.lock = None
        self.enabled = False
        self.state = "stopped"
        self.generation = self.sequence = self.attempts = self.recoveries = 0
        self.frames_received = self.frames_overwritten = self.timeouts = 0
        self.last_error = None
        self._last_worker_sequence = 0
        self._last_activity = self._next_retry = 0.0
        self._retiring_since = None
        self._kill_sent = False
        self._ever_streamed = False
        self.last_read_seconds = self.worker_fps = None
        self.native_yuyv = False

    def start(self):
        if self.enabled:
            return
        if self.process is not None:
            raise RuntimeError("Previous camera process has not stopped")
        self.enabled = True
        self.state = "waiting"
        self._next_retry = self.clock()

    def _spawn(self):
        width, height = self.config[-2:]
        self.pixels = self.context.RawArray("B", width * height * 3)
        self.metadata = self.context.RawArray("d", 12)
        self.lock = self.context.Lock()
        self.process = self.context.Process(target=_capture_worker,
            args=(self.factory, self.config, self.pixels, self.metadata, self.lock),
            name="hdmi-uvc-rgb", daemon=True)
        try:
            self.process.start()
        except BaseException:
            self.process.close()
            self.process = self.pixels = self.metadata = self.lock = None
            raise
        self.attempts += 1
        self.generation += 1
        self._last_worker_sequence = 0
        self._last_activity = self.clock()
        self.state = "opening"

    def _begin_retire(self, error):
        self.last_error = error
        self.state = "recovering"
        self._retiring_since = self.clock()
        self._kill_sent = False
        if self.process.is_alive():
            self.process.terminate()

    def _clear_process(self):
        self.process.join(timeout=0)
        self.process.close()
        self.process = self.pixels = self.metadata = self.lock = None
        self._retiring_since = None
        self._next_retry = self.clock() + self.retry_seconds
        self.state = "waiting" if self.enabled else "stopped"

    def poll(self):
        """Return the newest complete RGB frame, or None, within a bounded wait."""
        if not self.enabled:
            return None
        now = self.clock()
        if self.process is None:
            if now >= self._next_retry:
                try:
                    self._spawn()
                except Exception as exc:
                    self.last_error = f"{type(exc).__name__}: {exc}"
                    self._next_retry = now + self.retry_seconds
            return None
        if self._retiring_since is not None:
            if not self.process.is_alive():
                self._clear_process()
            elif now - self._retiring_since >= 0.5 and not self._kill_sent:
                self.process.kill()
                self._kill_sent = True
            elif now - self._retiring_since >= 1.0:
                self.state = "fault"
                self.last_error = "Camera process cannot stop; retaining mailbox and refusing another owner"
            return None
        if not self.process.is_alive():
            self._begin_retire(f"Camera process exited with code {self.process.exitcode}")
            return None
        frame = None
        if self.lock.acquire(timeout=0.005):
            try:
                values = tuple(self.metadata)
                worker_sequence = int(values[0])
                if worker_sequence > self._last_worker_sequence and now - values[1] < self.read_timeout:
                    width, height = self.config[-2:]
                    rgb = np.frombuffer(self.pixels, dtype=np.uint8).reshape(height, width, 3).copy()
                    self.frames_overwritten += worker_sequence - self._last_worker_sequence - 1
                    self._last_worker_sequence = worker_sequence
                    self.sequence += 1
                    self.frames_received += 1
                    self._last_activity = values[1]
                    self.last_read_seconds, self.worker_fps = values[9:11]
                    self.native_yuyv = bool(values[11])
                    frame = CameraFrame(self.generation, self.sequence, rgb, values[1], values[2],
                        int(values[3]), int(values[4]), Rect(*(int(v) for v in values[5:9])))
            finally:
                self.lock.release()
        if frame is not None:
            if self.state != "streaming":
                if self._ever_streamed:
                    self.recoveries += 1
                self._ever_streamed = True
            self.state = "streaming"
            self.last_error = None
            return frame
        timeout = self.open_timeout if self.state == 'opening' else self.read_timeout
        if now - self._last_activity >= timeout:
            self.timeouts += 1
            self._begin_retire("Camera open/read timed out")
        return None

    def stop(self):
        self.enabled = False
        if self.process is not None:
            if self.process.is_alive():
                self.process.terminate()
                self.process.join(timeout=0.5)
            if self.process.is_alive():
                self.process.kill()
                self.process.join(timeout=0.5)
            if self.process.is_alive():
                self.state = "fault"
                raise TimeoutError("Camera process cannot stop; retaining mailbox")
            self._clear_process()
        self.generation += 1
        self.state = "stopped"

    def status(self):
        return {"state": self.state, "enabled": self.enabled, "device": self.config[0],
                "native_yuyv": self.native_yuyv,
                "last_read_seconds": self.last_read_seconds, "worker_fps_since_open": self.worker_fps,
                "requested_camera_size": list(self.config[1:3]), "requested_source_fps": self.config[3],
                "open_timeout_seconds": self.open_timeout, "read_timeout_seconds": self.read_timeout,
                "input_size": list(self.config[-2:]), "scale": 4,
                "generation": self.generation, "attempts": self.attempts, "recoveries": self.recoveries,
                "frames_received": self.frames_received, "frames_overwritten": self.frames_overwritten,
                "timeouts": self.timeouts, "last_error": self.last_error,
                "worker_pid": self.process.pid if self.process is not None else None,
                "mailbox_bytes": self.config[-2] * self.config[-1] * 3}
