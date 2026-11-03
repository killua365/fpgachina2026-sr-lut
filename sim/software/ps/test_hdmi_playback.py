import unittest
from fractions import Fraction

import numpy as np

from hdmi_display.playback import Playback


class Clip:
    frame_count = 90
    fps = Fraction(30)

    def frame(self, index):
        return np.full((32, 32, 3), index, dtype=np.uint8)


class PlaybackTests(unittest.TestCase):
    def setUp(self):
        self.now = 100.0
        self.player = Playback(Clip(), 4, clock=lambda: self.now)

    def complete(self, request):
        return request.pair(np.repeat(np.repeat(request.source_rgb, request.scale, 0), request.scale, 1))

    def test_pause_step_and_no_duplicate_processing(self):
        request = self.player.request()
        self.assertEqual(request.source_index, 0)
        self.assertIsNone(self.player.request())
        self.player.step(1)
        self.now += 20
        self.assertEqual(self.player.request().source_index, 1)
        self.player.step(-1)
        self.assertEqual(self.player.request().source_index, 0)

    def test_seek_scale_and_source_reject_old_results(self):
        request = self.player.request()
        self.player.seek(20)
        self.assertFalse(self.player.accept(self.complete(request)))
        request = self.player.request()
        self.player.set_scale(2)
        self.assertFalse(self.player.accept(self.complete(request)))
        request = self.player.request()
        self.player.load(Clip())
        self.assertFalse(self.player.accept(self.complete(request)))
        fresh = self.complete(self.player.request())
        self.assertTrue(self.player.accept(fresh))
        self.assertFalse(self.player.accept(fresh))

    def test_speed_change_keeps_position_and_real_time_can_skip(self):
        self.player.play()
        self.now += 1
        self.assertEqual(self.player.request().source_index, 30)
        self.player.set_speed(0.5)
        self.assertEqual(self.player.index, 30)
        self.now += 1
        self.assertEqual(self.player.request().source_index, 45)
        self.player.set_speed(2)
        self.now += 0.5
        self.assertEqual(self.player.request().source_index, 75)

    def test_end_loop_and_restart(self):
        self.player.play()
        self.now += 4
        self.assertEqual(self.player.request().source_index, 89)
        self.assertFalse(self.player.playing)
        self.player.play()
        self.assertEqual(self.player.index, 0)
        self.player.set_loop(True)
        self.now += 4
        self.assertEqual(self.player.request().source_index, 30)

    def test_out_of_order_completion_is_rejected(self):
        first = self.player.request()
        self.player.play()
        self.now += 1
        second = self.player.request()
        self.assertTrue(self.player.accept(self.complete(second)))
        self.assertFalse(self.player.accept(self.complete(first)))


if __name__ == "__main__":
    unittest.main()
