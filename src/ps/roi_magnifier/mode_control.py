"""Board-button GPIO and serialized application-mode transitions."""

from __future__ import annotations

from dataclasses import dataclass
import threading
import time
from typing import Any, Callable, Mapping, Optional


USER_MODES = ("idle", "image", "video", "camera")
MODE_LEDS = {
    "idle": 0b0000,
    "image": 0b0001,
    "video": 0b0010,
    "camera": 0b0100,
    "switching": 0b1000,
    "error": 0b1000,
}
# BTN3 is intentionally an action rather than a normal operating mode.  The
# application handles it as a global reset and then publishes ``idle``.
BUTTON_MODES = {0: "image", 1: "video", 2: "camera", 3: "global_reset"}


class ModeTransitionError(RuntimeError):
    """Raised after a failed transition has been recorded in status."""


class BoardModeGPIO:
    """Read four buttons and drive four LEDs through AXI GPIO or raw MMIO."""

    BASE_ADDRESS = 0x41230000
    ADDRESS_RANGE = 0x10000
    DATA = 0x00
    TRI = 0x04
    DATA2 = 0x08
    TRI2 = 0x0C
    MASK = 0x0F

    def __init__(
        self,
        *,
        gpio_ip: Any = None,
        mmio: Any = None,
        base_address: int = BASE_ADDRESS,
        access_lock: Optional[threading.RLock] = None,
        mmio_factory: Optional[Callable[..., Any]] = None,
    ) -> None:
        if (gpio_ip is None) == (mmio is None):
            raise ValueError("provide exactly one of gpio_ip or mmio")
        self._gpio_ip = gpio_ip
        self._mmio = mmio
        self._base_address = int(base_address)
        self._mmio_factory = mmio_factory
        # Overlay downloads temporarily remove this AXI slave. Sharing the
        # FPGA lock prevents button polling or LED refresh from touching MMIO
        # while the PL is being reconfigured.
        self._lock = access_lock or threading.RLock()
        self._last_leds = 0
        self._last_buttons = 0
        self._rebind_required = False
        if self._gpio_ip is not None:
            if not hasattr(self._gpio_ip, "channel1") or not hasattr(
                self._gpio_ip, "channel2"
            ):
                raise ValueError("AXI GPIO must expose channel1 and channel2")
            self._gpio_ip.channel1.setdirection("in")
            self._gpio_ip.channel2.setdirection("out")
        else:
            self._mmio.write(self.TRI, self.MASK)
            self._mmio.write(self.TRI2, 0)
        self.write_leds(0)

    @classmethod
    def open(
        cls,
        *,
        overlay: Any = None,
        mmio_factory: Optional[Callable[..., Any]] = None,
        base_address: int = BASE_ADDRESS,
        access_lock: Optional[threading.RLock] = None,
    ) -> "BoardModeGPIO":
        gpio_ip = getattr(overlay, "axi_gpio_mode_0", None) if overlay else None
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
            mmio_factory=mmio_factory,
        )

    def request_rebind(self) -> None:
        """Recreate the MMIO view after a bitstream/Overlay reload."""

        if self._gpio_ip is not None or self._mmio_factory is None:
            return
        with self._lock:
            self._rebind_required = True

    def _rebind_locked(self) -> None:
        if not self._rebind_required or self._mmio_factory is None:
            return
        self._mmio = self._mmio_factory(self._base_address, self.ADDRESS_RANGE)
        self._mmio.write(self.TRI, self.MASK)
        self._mmio.write(self.TRI2, 0)
        self._mmio.write(self.DATA2, self._last_leds)
        self._rebind_required = False

    def read_buttons(self) -> int:
        with self._lock:
            self._rebind_locked()
            if self._gpio_ip is not None:
                value = int(self._gpio_ip.channel1.read()) & self.MASK
            else:
                value = int(self._mmio.read(self.DATA)) & self.MASK
            self._last_buttons = value
            return value

    def write_leds(self, value: int) -> None:
        normalized = int(value) & self.MASK
        with self._lock:
            self._rebind_locked()
            if self._gpio_ip is not None:
                self._gpio_ip.channel2.write(normalized, self.MASK)
            else:
                self._mmio.write(self.DATA2, normalized)
            self._last_leds = normalized

    def status(self) -> dict[str, Any]:
        result = {
            "available": True,
            "backend": "axigpio" if self._gpio_ip is not None else "mmio",
            "base_address": f"0x{self._base_address:08X}",
            "last_leds": self._last_leds,
            "last_buttons": self._last_buttons,
            "rebind_pending": self._rebind_required,
        }
        return result


class ModeController:
    """Serialize transitions and publish a monotonically sequenced status."""

    def __init__(
        self,
        *,
        initial_mode: str = "image",
        transition: Optional[
            Callable[[str, str, str], Optional[Mapping[str, Any]]]
        ] = None,
        gpio: Optional[BoardModeGPIO] = None,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        if initial_mode not in USER_MODES:
            raise ValueError(f"unknown initial mode: {initial_mode}")
        self._transition = transition
        self._gpio = gpio
        self._clock = clock
        self._state_lock = threading.Lock()
        self._transition_lock = threading.Lock()
        self._state = initial_mode
        self._active_mode = initial_mode
        self._requested_mode = initial_mode
        self._source = "startup"
        self._sequence = 0
        self._last_error: Optional[str] = None
        self._details: dict[str, Any] = {}
        self._updated_at = self._clock()
        self._write_leds(initial_mode)

    def set_transition_handler(
        self,
        transition: Callable[[str, str, str], Optional[Mapping[str, Any]]],
    ) -> None:
        self._transition = transition

    def _write_leds(self, state: str) -> None:
        if self._gpio is not None:
            self._gpio.write_leds(MODE_LEDS[state])

    def refresh_leds(self) -> None:
        """Restore the current indication after an Overlay reconfiguration."""

        with self._state_lock:
            state = self._state
        self._write_leds(state)

    def report_error(self, message: str, *, source: str = "runtime") -> dict[str, Any]:
        normalized = str(message).strip()
        if not normalized:
            raise ValueError("error message must not be empty")
        self._update(
            state="error",
            source=str(source),
            last_error=normalized,
            details={},
        )
        self._write_leds("error")
        return self.status()

    def _update(self, **values: Any) -> None:
        with self._state_lock:
            for key, value in values.items():
                setattr(self, f"_{key}", value)
            self._sequence += 1
            self._updated_at = self._clock()

    def request(
        self, target: str, *, source: str = "api", force: bool = False
    ) -> dict[str, Any]:
        if target not in USER_MODES:
            raise ValueError(f"unknown target mode: {target}")
        with self._transition_lock:
            with self._state_lock:
                previous = self._active_mode
                unchanged = target == previous and self._state == target
            if unchanged and not force:
                return self.status()
            self._update(
                state="switching",
                requested_mode=target,
                source=str(source),
                last_error=None,
                details={},
            )
            self._write_leds("switching")
            try:
                details = (
                    self._transition(previous, target, str(source))
                    if self._transition is not None
                    else None
                )
            except BaseException as exc:
                message = f"{type(exc).__name__}: {exc}"
                self._update(state="error", last_error=message, details={})
                self._write_leds("error")
                raise ModeTransitionError(message) from exc
            self._update(
                state=target,
                active_mode=target,
                requested_mode=target,
                last_error=None,
                details=dict(details or {}),
            )
            self._write_leds(target)
            return self.status()

    def status(self) -> dict[str, Any]:
        with self._state_lock:
            result = {
                "state": self._state,
                "active_mode": self._active_mode,
                "requested_mode": self._requested_mode,
                "source": self._source,
                "sequence": self._sequence,
                "last_error": self._last_error,
                "details": dict(self._details),
                "updated_monotonic": self._updated_at,
                "button_map": {
                    "BTN0": "image",
                    "BTN1": "video",
                    "BTN2": "camera",
                    "BTN3": "global_reset",
                },
            }
        result["gpio"] = (
            {"available": False}
            if self._gpio is None
            else self._gpio.status()
        )
        return result


class ButtonModeMonitor:
    """Poll buttons with stable-time debounce and emit rising-edge requests."""

    def __init__(
        self,
        gpio: BoardModeGPIO,
        callback: Callable[[str], Any],
        *,
        debounce_seconds: float = 0.04,
        poll_seconds: float = 0.01,
        led_refresh: Optional[Callable[[], None]] = None,
        led_refresh_seconds: float = 0.25,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        if debounce_seconds <= 0 or poll_seconds <= 0 or led_refresh_seconds <= 0:
            raise ValueError("debounce, poll and LED refresh intervals must be positive")
        self.gpio = gpio
        self.callback = callback
        self.debounce_seconds = float(debounce_seconds)
        self.poll_seconds = float(poll_seconds)
        self.led_refresh = led_refresh
        self.led_refresh_seconds = float(led_refresh_seconds)
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
            name="board-mode-buttons",
            daemon=True,
        )
        self._thread.start()

    def stop(self, timeout_seconds: float = 1.0) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout_seconds)

    def _run(self) -> None:
        stable = candidate = 0
        candidate_since = self.clock()
        next_led_refresh = candidate_since + self.led_refresh_seconds
        while not self._stop.wait(self.poll_seconds):
            try:
                raw = self.gpio.read_buttons()
                now = self.clock()
                if self.led_refresh is not None and now >= next_led_refresh:
                    self.led_refresh()
                    next_led_refresh = now + self.led_refresh_seconds
                if raw != candidate:
                    candidate = raw
                    candidate_since = now
                    continue
                if candidate == stable or now - candidate_since < self.debounce_seconds:
                    continue
                pressed = candidate & ~stable
                stable = candidate
                if not pressed:
                    continue
                # Reset wins if it is pressed together with another button.
                bit = 3 if pressed & (1 << 3) else next(
                    index for index in range(3) if pressed & (1 << index)
                )
                self.callback(BUTTON_MODES[bit])
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
            "led_refresh_ms": self.led_refresh_seconds * 1000.0,
        }


__all__ = [
    "BoardModeGPIO",
    "ButtonModeMonitor",
    "ModeController",
    "ModeTransitionError",
    "USER_MODES",
]
