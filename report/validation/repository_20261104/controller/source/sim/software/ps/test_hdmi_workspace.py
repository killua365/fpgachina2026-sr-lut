import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import numpy as np
from PIL import Image
from hdmi_display.workspace import Workspace
from roi_magnifier.geometry import Rect
import test_hdmi_runtime


class WorkspaceTests(unittest.TestCase):
    def setUp(self):
        self.fixture = test_hdmi_runtime.RuntimeTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.rt = self.fixture.runtime
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name)
        self.source = self.path / 'source.png'
        Image.fromarray(self.rt.image).save(self.source)
        self.ui = Workspace(self.rt, [self.source], self.path)
        self.rt.tick()

    def mouse(self, x, y, transitions=(), **extras):
        self.ui.poll({'x': x, 'y': y, 'buttons': [], 'connected': True, 'wheel': 0,
                      'transitions': list(transitions), **extras})

    def test_camera_cached_overlay_matches_drawing_and_preserves_background(self):
        self.rt.engine.mode = 'camera'
        rng = np.random.default_rng(1720)
        for visible, pointer, notice in ((True,(640,360),None), (False,(4,4),None),
                (True,(1279,719),dict(message='Camera restored',kind='saved',until=100))):
            self.ui.visible, self.ui.pointer, self.ui.notice = visible, pointer, notice
            self.ui.clock = lambda: 1
            self.ui.session_manual = True
            source = rng.integers(0,256,(720,1280,4),dtype=np.uint8)
            source[...,3] = 0
            for _ in range(2):
                expected, actual = source.copy(), source.copy()
                self.ui._draw_uncached(expected)
                self.ui.draw(actual)
                np.testing.assert_array_equal(actual, expected)

    def test_camera_content_band_only_excludes_fully_opaque_rows(self):
        self.rt.engine.mode='camera'
        self.ui.session_manual=True
        self.ui.draw(np.zeros((720,1280,4),np.uint8))
        first,last=self.ui.camera_content_band()
        self.assertGreaterEqual(first,0)
        self.assertLess(last,720)
        canvas,spans=self.ui._camera_ui_cache
        mask=np.zeros(canvas.nbytes,bool)
        for start,length in spans:mask[int(start):int(start+length)]=True
        rows=mask.reshape(720,-1)
        self.assertTrue(rows[:first].all())
        self.assertTrue(rows[last:].all())
        self.assertFalse(rows[first:last].all())
        self.ui.pointer=(27,31)
        with self.assertRaises(RuntimeError):self.ui.camera_content_band()

    def test_short_click_changes_layout_without_changing_physical_mode_or_scale(self):
        events = [{'button': 272, 'pressed': pressed, 'x': 30, 'y': 650} for pressed in (True, False)]
        self.mouse(30, 650, events)
        self.assertEqual(self.rt.composition.layout, 'sr')
        self.assertEqual((self.rt.engine.mode, self.rt.engine.scale), ('image', 2))

    def test_moving_cursor_reuses_static_overlay_but_invalidates_slot_key(self):
        self.rt.engine.mode = 'camera'
        self.ui.session_manual = True
        self.ui.clock = lambda: 1
        source = np.full((720,1280,4), 97, np.uint8)
        source[...,3] = 0
        cached = None
        prior_key = None
        for point in ((100,100),(640,360),(1279,719),(4,4)):
            self.ui.pointer = point
            actual, expected = source.copy(), source.copy()
            self.ui.draw(actual)
            self.ui._draw_uncached(expected)
            np.testing.assert_array_equal(actual, expected)
            if cached is not None:
                self.assertIs(self.ui._camera_ui_cache, cached)
                self.assertNotEqual(self.ui.camera_cache_key(), prior_key)
            cached, prior_key = self.ui._camera_ui_cache, self.ui.camera_cache_key()

    def test_partial_camera_band_restores_pointer_over_content(self):
        self.rt.engine.mode = 'camera'
        self.ui.pointer = (640,360)
        self.ui.clock = lambda: 1
        original = np.zeros((720,1280,4),np.uint8)
        self.ui.draw(original)
        first,last = self.ui.camera_content_band()
        actual = original.copy()
        actual[first:last] = (67,89,123,0)
        self.ui.draw_camera_band(actual,first,last)
        expected = np.empty_like(original)
        expected[:] = (67,89,123,0)
        self.ui._draw_uncached(expected)
        np.testing.assert_array_equal(actual, expected)

    def test_selection_background_cached_and_invalidated_on_new_image(self):
        self.ui.selecting = True
        canvas = np.zeros((720,1280,4),np.uint8)
        from hdmi_display.workspace import resize_nearest
        with patch('hdmi_display.workspace.resize_nearest', wraps=resize_nearest) as resize:
            self.ui.draw(canvas)
            original = self.ui._selection_base
            self.ui.preview_roi = Rect(2,3,7,8)
            self.ui.pointer = (300,300)
            self.ui.draw(canvas)
            self.assertIs(self.ui._selection_base, original)
            self.assertEqual(resize.call_count, 1)
            self.rt.set_image(np.full_like(self.rt.image, 123))
            self.ui.draw(canvas)
            self.assertIsNot(self.ui._selection_base, original)
            self.assertEqual(resize.call_count, 2)

    def test_selection_repaint_skips_hidden_sr_composition(self):
        self.ui.selecting = True
        self.rt.request_redraw()
        with patch.object(self.rt.engine.compositor,'compose',side_effect=AssertionError('Hidden SR redraw')):
            self.rt.tick(self.ui.draw)

    def test_selection_cached_pixels_match_reference_with_notice_and_roi_changes(self):
        self.ui.selecting = True
        self.ui.clock = lambda: 1
        for roi,notice in ((Rect(0,0,3,3),None), (Rect(0,0,8,8),
                dict(message='Selection notice',kind='saved',until=10)), (Rect(2,2,8,8),None)):
            self.ui.preview_roi,self.ui.notice = roi,notice
            actual=np.zeros((720,1280,4),np.uint8)
            expected=np.zeros_like(actual)
            self.ui.draw(actual)
            self.ui._draw_uncached(expected)
            np.testing.assert_array_equal(actual,expected)

    def test_cursor_motion_repaints_static_image_without_processing(self):
        before = self.rt.engine.processed_frames
        self.mouse(100, 100)
        self.assertTrue(self.rt._redraw)
        self.rt.tick(self.ui.draw)
        self.assertEqual(self.rt.engine.processed_frames, before)

    def test_roi_move_commits_original_coordinates_only_on_release(self):
        self.rt.set_roi(Rect(5, 5, 10, 10))
        self.ui.selecting = True
        view = self.ui.viewport()
        box = view.display_rect(Rect(7, 7, 1, 1))
        point = (box.x, box.y)
        self.mouse(*point, [{'button': 272, 'pressed': True, 'x': point[0], 'y': point[1]}])
        later = (point[0]+50, point[1]+30)
        self.mouse(*later)
        self.assertEqual(self.rt.roi, Rect(5, 5, 10, 10))
        wanted = self.ui.preview_roi
        self.mouse(*later, [{'button': 272, 'pressed': False, 'x': later[0], 'y': later[1]}])
        self.assertEqual(self.rt.roi, wanted)
        self.assertNotEqual(wanted, Rect(5, 5, 10, 10))

    def test_export_decodes_exact_pixels_and_no_reference_quality_is_invented(self):
        self.ui.action('save')
        record = json.loads(Path(self.ui.exports[0]).read_text())
        self.assertIsNone(record['quality'])
        actual = np.asarray(Image.open(Path(self.ui.exports[0]).parent / 'sr.png'))
        np.testing.assert_array_equal(actual, self.rt.last_pair.output_rgb)

    def test_save_feedback_remains_visible_when_toolbar_hides_and_is_reported(self):
        now = [100.]
        self.ui.clock = lambda: now[0]
        self.ui.action('save')
        self.ui.visible = False
        canvas = np.zeros((self.ui.height, self.ui.width, 4), dtype=np.uint8)
        with patch.object(self.ui, 'text') as draw_text:
            self.ui.draw(canvas)
        self.assertTrue(any('Saved PNG #1' in str(c) for c in draw_text.call_args_list))
        state = self.ui.status()
        self.assertEqual(state['last_notice']['kind'], 'saved')
        self.assertEqual(state['exports'], 1)
        now[0] += 11
        with patch.object(self.ui, 'text') as draw_text:
            self.ui.draw(canvas)
        self.assertFalse(any('Saved PNG' in str(c) for c in draw_text.call_args_list))
        self.assertEqual(self.ui.status()['last_notice'], state['last_notice'])

    def test_save_failure_has_visible_error_without_false_success(self):
        button = next(bounds for name, _, bounds in self.ui.buttons() if name == 'save')
        point = (button.x+5, button.y+5)
        with patch('hdmi_display.workspace.export_image', side_effect=OSError('Disk full')):
            self.mouse(*point, [{'button':272, 'pressed':p, 'x':point[0], 'y':point[1]} for p in (True, False)])
        self.assertEqual(self.ui.status()['last_notice']['kind'], 'error')
        self.assertEqual(self.ui.status()['message'], 'Disk full')
        self.assertEqual(self.ui.exports, [])

    def test_session_countdown_does_not_depend_on_toolbar_visibility(self):
        self.ui.clock = lambda: 10.
        self.ui.session_deadline = 18.
        self.ui.visible = False
        canvas = np.zeros((self.ui.height, self.ui.width, 4), dtype=np.uint8)
        with patch.object(self.ui, 'text') as draw_text:
            self.ui.draw(canvas)
        self.assertTrue(any('Session ends in 8s' in str(c) for c in draw_text.call_args_list))
        self.assertEqual(self.ui.status()['session_remaining_seconds'], 8)

    def test_disconnect_cancels_drag_instead_of_committing_a_phantom_roi(self):
        self.ui.selecting = True
        self.ui.drag = ('move', (100, 100), (5, 5), self.rt.roi)
        self.mouse(100, 100, connected=False)
        self.assertIsNone(self.ui.drag)

    def test_manual_session_hides_with_toolbar_without_leaving_background(self):
        self.ui.session_manual = True
        self.ui.visible = False
        from dataclasses import replace
        for mode in ('image', 'video', 'camera'):
            self.rt.engine.mode = mode
            for layout in ('sr', 'split', 'baseline', 'wipe'):
                with self.subTest(mode=mode, layout=layout):
                    self.rt.composition = replace(self.rt.composition, layout=layout)
                    canvas = np.full((self.ui.height, self.ui.width, 4), 79, dtype=np.uint8)
                    baseline = canvas.copy()
                    self.ui.session_manual = False
                    self.ui.draw(baseline)
                    self.ui.session_manual = True
                    with patch.object(self.ui, 'text', wraps=self.ui.text) as draw_text:
                        self.ui.draw(canvas)
                    np.testing.assert_array_equal(canvas, baseline)
                    messages = str(draw_text.call_args_list)
                    self.assertNotIn('Manual session', messages)
                    self.assertNotIn('Session ends', messages)
        self.assertIsNone(self.ui.status()['session_remaining_seconds'])

    def test_manual_session_returns_when_toolbar_is_visible(self):
        self.ui.session_manual = True
        self.ui.visible = True
        canvas = np.zeros((self.ui.height, self.ui.width, 4), dtype=np.uint8)
        with patch.object(self.ui, 'text') as draw_text:
            self.ui.draw(canvas)
        self.assertIn('Manual session', str(draw_text.call_args_list))
        self.assertNotIn('Session ends', str(draw_text.call_args_list))

    def test_single_pixel_roi_displays_and_exports_on_board_opencv(self):
        self.rt.set_roi(Rect(0, 0, 1, 1))
        self.rt.tick(self.ui.draw)
        self.ui.action('save')
        output = Path(self.ui.exports[0]).parent
        source = np.asarray(Image.open(output / 'source.png'))
        self.assertEqual(source.shape, (1, 1, 3))
        nearest = np.asarray(Image.open(output / 'nearest.png'))
        np.testing.assert_array_equal(nearest, np.tile(source, (2, 2, 1)))


if __name__ == '__main__':
    unittest.main()
