"""Board-switch GPIO and synchronized native-scale selection."""

from __future__ import annotations

import threading
import time
from typing import Any, Callable, Optional


SUPPORTED_SCALES = (2, 4)


class ScaleLockedError(RuntimeError):
    """Raised when browser scale control is disabled by SW1."""


class BoardScaleGPIO:
    """Read SW0/SW1 through a dedicated AXI GPIO or raw MMIO."""

    BASE_ADDRESS = 0x41240000
    ADDRESS_RANGE = 0x10000
    DATA = 0x00
    TRI = 0x04
    MASK = 0x03

    def __init__(
        self,
        *,
        gpio_ip: Any = None,
        mmio: Any = None,
        base_address: int = BASE_ADDRESS,
        access_lock: Optional[threading.RLock] = None,
    ) -> None:
        if (gpio_ip is None) == (mmio is None):
            raise ValueError("provide exactly one of gpio_ip or mmio")
        self._gpio_ip = gpio_ip
        self._mmio = mmio
        self._base_address = int(base_address)
        self._lock = access_lock or threading.RLock()
        if self._gpio_ip is not None:
            if not hasattr(self._gpio_ip, "channel1"):
                raise ValueError("AXI GPIO must expose channel1")
            self._gpio_ip.channel1.setdirection("in")
        else:
            self._mmio.write(self.TRI, self.MASK)

    @classmethod
    def open(
        cls,
        *,
        overlay: Any = None,
        mmio_factory: Optional[Callable[..., Any]] = None,
        base_address: int = BASE_ADDRESS,
        access_lock: Optional[threading.RLock] = None,
    ) -> "BoardScaleGPIO":
        gpio_ip = getattr(overlay, "axi_gpio_scale_0", None) if overlay else None
        if gpio_ip is not None and hasattr(gpio_ip, "channel1"):
            return cls(gpio_ip=gpio_ip, access_lock=access_lock)
        if mmio_factory is None:
            try:
                from pynq import MMIO
            except ImportError as exc:  # pragma: no cover - PYNQ-only path
                raise RuntimeError("PYNQ MMIO is unavailable") from exc
            mmio_factory = MMIO
        return cls(
            mmio=mmio_factory(int(base_address), cls.ADDRESS_RANGE),
            base_address=int(base_address),
            access_lock=access_lock,
        )

    def read_switches(self) -> int:
        with self._lock:
            if self._gpio_ip is not None:
                return int(self._gpio_ip.channel1.read()) & self.MASK
            return int(self._mmio.read(self.DATA)) & self.MASK

    def status(self) -> dict[str, Any]:
        return {
            "available": True,
            "backend": "axigpio" if self._gpio_ip is not None else "mmio",
            "base_address": f"0x{self._base_address:08X}",
        }


class ScaleController:
    """Resolve browser selection or the physical SW1/SW0 authority."""

    def __init__(
        self,
        *,
        browser_scale: int = 4,
        raw_switches: int = 0,
        gpio: Optional[BoardScaleGPIO] = None,
        board_control_only: bool = False,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._validate_scale(browser_scale)
        self._lock = threading.Lock()
        self._browser_scale = int(browser_scale)
        self._raw_switches = int(raw_switches) & BoardScaleGPIO.MASK
        self._gpio = gpio
        self._board_control_only = bool(board_control_only)
        self._clock = clock
        self._sequence = 0
        self._updated_at = self._clock()

    @staticmethod
    def _validate_scale(scale: int) -> None:
        if int(scale) not in SUPPORTED_SCALES:
            raise ValueError("scale must be 2 or 4")

    @staticmethod
    def _resolved(
        browser_scale: int,
        raw_switches: int,
        board_control_only: bool = False,
    ) -> tuple[int, str, bool]:
        if board_control_only:
            # In the formal board-only UI SW0 selects the scale in every
            # switch position; SW1 remains a physical, non-browser control.
            return (4 if raw_switches & 0b01 else 2), "switches", True
        switch_lock = bool(raw_switches & 0b10)
        if switch_lock:
            return (4 if raw_switches & 0b01 else 2), "switches", True
        return int(browser_scale), "browser", False

    def set_browser_scale(self, scale: int) -> dict[str, Any]:
        self._validate_scale(scale)
        with self._lock:
            _, _, switch_lock = self._resolved(
                self._browser_scale, self._raw_switches, self._board_control_only
            )
            if switch_lock:
                raise ScaleLockedError("SW1 is locking the scale selection")
            if self._browser_scale != int(scale):
                self._browser_scale = int(scale)
                self._sequence += 1
                self._updated_at = self._clock()
        return self.status()

    def update_switches(self, raw_switches: int) -> dict[str, Any]:
        normalized = int(raw_switches) & BoardScaleGPIO.MASK
        with self._lock:
            if normalized != self._raw_switches:
                self._raw_switches = normalized
                self._sequence += 1
                self._updated_at = self._clock()
        return self.status()

    def selected_scale(self) -> int:
        with self._lock:
            selected, _, _ = self._resolved(
                self._browser_scale, self._raw_switches, self._board_control_only
            )
            return selected

    def status(self) -> dict[str, Any]:
        with self._lock:
            selected, source, switch_lock = self._resolved(
                self._browser_scale, self._raw_switches, self._board_control_only
            )
            result = {
                "supported_scales": list(SUPPORTED_SCALES),
                "browser_scale": self._browser_scale,
                "selected_scale": selected,
                "effective_scale": selected,
                "source": source,
                "switch_lock": switch_lock,
                "control_source": "switches" if self._board_control_only else source,
                "board_control_only": self._board_control_only,
                "raw_switches": self._raw_switches,
                "camera_fixed_x4": True,
                "sequence": self._sequence,
                "updated_monotonic": self._updated_at,
            }
        result["gpio"] = (
            {"available": False} if self._gpio is None else self._gpio.status()
        )
        return result


class ScaleSwitchMonitor:
    """Debounce both switches and publish stable two-bit values."""

    def __init__(
        self,
        gpio: BoardScaleGPIO,
        callback: Callable[[int], Any],
        *,
        debounce_seconds: float = 0.04,
        poll_seconds: float = 0.01,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        if debounce_seconds <= 0 or poll_seconds <= 0:
            raise ValueError("debounce and poll intervals must be positive")
        self.gpio = gpio
        self.callback = callback
        self.debounce_seconds = float(debounce_seconds)
        self.poll_seconds = float(poll_seconds)
        self.clock = clock
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._last_error: Optional[str] = None
        self._events = 0

    def start(self) -> None:
        if self._thread is not None and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(
            target=self._run,
            name="board-scale-switches",
            daemon=True,
        )
        self._thread.start()

    def stop(self, timeout_seconds: float = 1.0) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout_seconds)

    def _run(self) -> None:
        try:
            stable = candidate = self.gpio.read_switches()
            self.callback(stable)
            self._last_error = None
        except BaseException as exc:
            stable = candidate = 0
            self._last_error = f"{type(exc).__name__}: {exc}"
        candidate_since = self.clock()
        while not self._stop.wait(self.poll_seconds):
            try:
                raw = self.gpio.read_switches()
                now = self.clock()
                if raw != candidate:
                    candidate = raw
                    candidate_since = now
                    continue
                if candidate == stable or now - candidate_since < self.debounce_seconds:
                    continue
                stable = candidate
                self.callback(stable)
                self._events += 1
                self._last_error = None
            except BaseException as exc:
                self._last_error = f"{type(exc).__name__}: {exc}"

    def status(self) -> dict[str, Any]:
        return {
            "running": self._thread is not None and self._thread.is_alive(),
            "events": self._events,
            "last_error": self._last_error,
            "debounce_ms": self.debounce_seconds * 1000.0,
            "poll_ms": self.poll_seconds * 1000.0,
        }


__all__ = [
    "BoardScaleGPIO",
    "ScaleController",
    "ScaleLockedError",
    "ScaleSwitchMonitor",
    "SUPPORTED_SCALES",
]
