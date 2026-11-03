"""Bounded evdev mouse polling with hotplug and lost-event recovery."""

import time


class Mouse:
    def __init__(self, width=1280, height=720, *, api=None, clock=time.monotonic):
        if api is None:
            try:
                import evdev as api
            except ImportError as exc:
                # HDMI physical-link diagnostics remain useful without the
                # optional mouse package. Record this as a missing input so it
                # cannot be mistaken for successful mouse acceptance.
                api = None
                self._import_error = str(exc)
        else:
            self._import_error = None
        self.api, self.clock = api, clock
        self.width, self.height = width, height
        self.x, self.y = width // 2, height // 2
        self.buttons = set()
        self.device = None
        self.retry_at = 0.0
        self.events = self.moves = self.clicks = self.wheel = 0
        self.connects = self.disconnects = self.dropped = 0
        self.last_error = None
        self.resync = False
        self.transitions = []

    def close(self):
        if self.device is not None:
            self.device.close()
        self.device = None
        self.buttons.clear()
        self.resync = False

    def _connect(self):
        if self.api is None:
            return
        if self.clock() < self.retry_at:
            return
        self.retry_at = self.clock() + 1.0
        ec = self.api.ecodes
        for path in sorted(self.api.list_devices()):
            candidate = None
            try:
                candidate = self.api.InputDevice(path)
                capabilities = candidate.capabilities()
                relative = capabilities.get(ec.EV_REL, [])
                keys = capabilities.get(ec.EV_KEY, [])
                if ec.REL_X in relative and ec.REL_Y in relative and ec.BTN_LEFT in keys:
                    self.device = candidate
                    self.buttons = set(candidate.active_keys()) & {ec.BTN_LEFT, ec.BTN_RIGHT, ec.BTN_MIDDLE}
                    self.connects += 1
                    self.last_error = None
                    return
            except OSError as exc:
                self.last_error = str(exc)
            finally:
                if candidate is not None and candidate is not self.device:
                    candidate.close()

    def poll(self):
        self.transitions = []
        if self.api is None:
            return self.status()
        ec = self.api.ecodes
        try:
            if self.device is None:
                self._connect()
            if self.device is None:
                return self.status()
            for _ in range(256):
                event = self.device.read_one()
                if event is None:
                    break
                self.events += 1
                if event.type == ec.EV_SYN and event.code == ec.SYN_DROPPED:
                    self.resync = True
                    self.dropped += 1
                elif self.resync:
                    if event.type == ec.EV_SYN and event.code == ec.SYN_REPORT:
                        self.buttons = set(self.device.active_keys()) & {ec.BTN_LEFT, ec.BTN_RIGHT, ec.BTN_MIDDLE}
                        self.resync = False
                elif event.type == ec.EV_REL:
                    if event.code == ec.REL_X:
                        self.x = min(self.width - 1, max(0, self.x + event.value))
                        self.moves += 1
                    elif event.code == ec.REL_Y:
                        self.y = min(self.height - 1, max(0, self.y + event.value))
                        self.moves += 1
                    elif event.code == ec.REL_WHEEL:
                        self.wheel += event.value
                elif event.type == ec.EV_KEY and event.code in (ec.BTN_LEFT, ec.BTN_RIGHT, ec.BTN_MIDDLE):
                    if event.value == 1:
                        self.buttons.add(event.code)
                        self.clicks += 1
                    elif event.value == 0:
                        self.buttons.discard(event.code)
                    if event.value in (0, 1):
                        self.transitions.append({"button": event.code, "pressed": bool(event.value),
                                                 "x": self.x, "y": self.y})
        except OSError as exc:
            self.last_error = str(exc)
            self.disconnects += 1
            self.close()
            self.retry_at = self.clock() + 1.0
        return self.status()

    def status(self):
        return {"connected": self.device is not None,
                "device": self.device.path if self.device else None,
                "name": self.device.name if self.device else None,
                "x": self.x, "y": self.y, "buttons": sorted(self.buttons),
                "events": self.events, "moves": self.moves, "clicks": self.clicks,
                "wheel": self.wheel, "connects": self.connects,
                "disconnects": self.disconnects, "dropped": self.dropped,
                "transitions": list(self.transitions),
                "last_error": self.last_error or getattr(self, "_import_error", None)}
