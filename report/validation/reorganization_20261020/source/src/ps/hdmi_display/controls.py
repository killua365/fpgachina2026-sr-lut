"""Single-owner BTN/SW debounce; no thread retains GPIO across Overlay reloads."""

from __future__ import annotations

from dataclasses import dataclass
import math
import time

from roi_magnifier.mode_control import BUTTON_MODES


@dataclass(frozen=True)
class ControlEvent:
    mode: str | None
    raw_switches: int
    switches_changed: bool


class _Debounce:
    def __init__(self, initial, delay):
        self.stable = self.candidate = initial
        self.since = None
        self.delay = delay

    def sample(self, value, now):
        if value != self.candidate:
            self.candidate, self.since = value, now
        if self.candidate != self.stable and self.since is not None and now - self.since >= self.delay:
            previous, self.stable = self.stable, self.candidate
            return previous, self.stable
        return None


class PhysicalControls:
    """Sample only on the same thread that owns Engine and source lifetimes.

    Preserve this object across Overlay reloads, so a held button has only one
    rising edge. BTN3 wins simultaneous presses. SW0 selects x2/x4 regardless
    of SW1; the Engine enforces fixed camera x4 and idle's retained scanout.
    """

    def __init__(self, *, initial_switches=0, debounce_seconds=0.04, clock=time.monotonic):
        if type(initial_switches) is not int or not 0 <= initial_switches <= 3:
            raise ValueError("Initial switches must be the raw two-bit value")
        if not math.isfinite(debounce_seconds) or debounce_seconds <= 0:
            raise ValueError("Debounce interval must be finite and positive")
        self.clock = clock
        self.buttons = _Debounce(0, debounce_seconds)
        self.switches = _Debounce(initial_switches, debounce_seconds)
        self.events = 0

    def sample(self, buttons, switches):
        if type(buttons) is not int or not 0 <= buttons <= 15:
            raise ValueError("Buttons must be the raw four-bit value")
        if type(switches) is not int or not 0 <= switches <= 3:
            raise ValueError("Switches must be the raw two-bit value")
        now = self.clock()
        change = self.buttons.sample(buttons, now)
        switch_change = self.switches.sample(switches, now)
        mode = None
        if change:
            pressed = change[1] & ~change[0]
            if pressed:
                bit = 3 if pressed & 8 else next(i for i in range(3) if pressed & (1 << i))
                action = BUTTON_MODES[bit]
                mode = "idle" if action == "global_reset" else action
        if mode is None and switch_change is None:
            return None
        self.events += 1
        return ControlEvent(mode, self.switches.stable, switch_change is not None)

    def poll_engine(self, engine):
        """Read both controls before returning; do not retain either GPIO object."""
        return self.sample(*engine.controls())

    def status(self):
        return {"buttons": self.buttons.stable, "raw_switches": self.switches.stable,
                "selected_scale": 4 if self.switches.stable & 1 else 2,
                "authority": "physical", "events": self.events}
