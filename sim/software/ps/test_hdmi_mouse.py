from collections import deque
from types import SimpleNamespace
import unittest

from hdmi_display.mouse import Mouse


EC = SimpleNamespace(EV_SYN=0, EV_KEY=1, EV_REL=2, SYN_REPORT=0, SYN_DROPPED=3,
                     REL_X=0, REL_Y=1, REL_WHEEL=8, BTN_LEFT=272, BTN_RIGHT=273, BTN_MIDDLE=274)


class Device:
    path, name = "/dev/input/event1", "Test Mouse"

    def __init__(self):
        self.queue = deque()
        self.keys = []
        self.closed = self.disconnected = False

    def capabilities(self):
        return {EC.EV_KEY: [EC.BTN_LEFT], EC.EV_REL: [EC.REL_X, EC.REL_Y]}

    def active_keys(self):
        return self.keys

    def close(self):
        self.closed = True

    def read_one(self):
        if self.disconnected:
            raise OSError("unplugged")
        return self.queue.popleft() if self.queue else None

    def add(self, kind, code, value):
        self.queue.append(SimpleNamespace(type=kind, code=code, value=value))


class MouseTests(unittest.TestCase):
    def setUp(self):
        self.now = 0
        self.device = Device()
        self.api = SimpleNamespace(ecodes=EC, list_devices=lambda: [self.device.path],
                                   InputDevice=lambda _: self.device)
        self.mouse = Mouse(api=self.api, clock=lambda: self.now)

    def test_motion_clamps_and_click_repeat_does_not_double_count(self):
        self.device.add(EC.EV_REL, EC.REL_X, 9000)
        self.device.add(EC.EV_REL, EC.REL_Y, -9000)
        for value in (1, 2, 0):
            self.device.add(EC.EV_KEY, EC.BTN_LEFT, value)
        state = self.mouse.poll()
        self.assertEqual((state["x"], state["y"]), (1279, 0))
        self.assertEqual(state["clicks"], 1)
        self.assertEqual(state["buttons"], [])

    def test_unplug_clears_held_button_and_reconnect_is_rate_limited(self):
        self.device.add(EC.EV_KEY, EC.BTN_LEFT, 1)
        self.mouse.poll()
        self.device.disconnected = True
        self.assertFalse(self.mouse.poll()["connected"])
        self.assertEqual(self.mouse.status()["buttons"], [])
        self.device = Device()
        self.assertFalse(self.mouse.poll()["connected"])
        self.now = 1.1
        self.assertTrue(self.mouse.poll()["connected"])
        self.assertEqual(self.mouse.status()["connects"], 2)

    def test_lost_events_resync_buttons_and_ignore_corrupt_relative_deltas(self):
        self.mouse.poll()
        self.device.keys = [EC.BTN_RIGHT]
        self.device.add(EC.EV_SYN, EC.SYN_DROPPED, 0)
        self.device.add(EC.EV_REL, EC.REL_X, 100)
        self.device.add(EC.EV_SYN, EC.SYN_REPORT, 0)
        state = self.mouse.poll()
        self.assertEqual(state["x"], 640)
        self.assertEqual(state["buttons"], [EC.BTN_RIGHT])
        self.assertEqual(state["dropped"], 1)

    def test_input_flood_cannot_starve_render_loop(self):
        for _ in range(1000):
            self.device.add(EC.EV_REL, EC.REL_X, 1)
        state = self.mouse.poll()
        self.assertEqual(state["events"], 256)
        self.assertEqual(len(self.device.queue), 744)


if __name__ == "__main__":
    unittest.main()
