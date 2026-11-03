import unittest

from hdmi_display.controls import PhysicalControls


class ControlTests(unittest.TestCase):
    def setUp(self):
        self.now = 0.0
        self.controls = PhysicalControls(clock=lambda: self.now)

    def stable(self, buttons, switches):
        self.controls.sample(buttons, switches)
        self.now += 0.041
        return self.controls.sample(buttons, switches)

    def test_bounce_does_not_switch_and_held_button_does_not_repeat(self):
        self.assertIsNone(self.controls.sample(4, 0))
        self.now += 0.01
        self.assertIsNone(self.controls.sample(0, 0))
        event = self.stable(4, 0)
        self.assertEqual(event.mode, "camera")
        self.now += 5
        self.assertIsNone(self.controls.sample(4, 0))
        self.stable(0, 0)
        self.assertEqual(self.stable(4, 0).mode, "camera")

    def test_reset_wins_simultaneous_button_press(self):
        self.assertEqual(self.stable(15, 0).mode, "idle")

    def test_all_modes_and_all_switch_values(self):
        for switches in (0, 1, 2, 3):
            for bit, mode in enumerate(("image", "video", "camera", "idle")):
                self.stable(0, switches)
                event = self.stable(1 << bit, switches)
                self.assertEqual((event.mode, event.raw_switches), (mode, switches))
                self.assertEqual(self.controls.status()["selected_scale"], 4 if switches & 1 else 2)

    def test_switch_and_button_debounce_are_independent(self):
        self.controls.sample(0, 1)
        self.now += 0.025
        self.controls.sample(2, 1)
        self.now += 0.020
        event = self.controls.sample(2, 1)
        self.assertTrue(event.switches_changed)
        self.assertIsNone(event.mode)
        self.now += 0.025
        event = self.controls.sample(2, 1)
        self.assertEqual(event.mode, "video")
        self.assertEqual(event.raw_switches, 1)


if __name__ == "__main__":
    unittest.main()
