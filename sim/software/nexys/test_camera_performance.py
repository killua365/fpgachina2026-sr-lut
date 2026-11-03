import copy
import unittest
from analyze_camera_performance import assess


class CameraAcceptanceTests(unittest.TestCase):
    def fixture(self,fps=20,seconds=310):
        campaign=dict(capture_complete=True,input_size=[480,270],sr_size=[1920,1080],
            golden=dict(passed=True,full_x_byte_validation=True,checks=[dict(output_rgb_sha256='a',expected_rgb_sha256='a')]*30),cases=[])
        events=[]
        for generation,layout in enumerate(('sr','split'),1):
            campaign['cases'].append(dict(stage='acceptance_capture',layout=layout,generation=generation,
                input_size=[480,270],sr_size=[1920,1080],submitted_pair_fps=30,timings={},
                first_sequence=0,last_sequence=seconds*fps,requested_seconds=310))
            for sequence in range(seconds*fps):
                events.append(dict(generation=generation,mode=3,scale=4,source_sequence=sequence,
                    complete_ticks=100000+generation*400000+sequence*1000//fps,latency_cycles=10))
        return campaign,events

    def test_20fps_fixed_window_counts_exactly_6000(self):
        c,e=self.fixture()
        result=assess(c,e,1000)
        self.assertTrue(result['camera_20fps_passed'])
        self.assertEqual([c['window_new_scans'] for c in result['cases']],[6000,6000])
        self.assertFalse(result['n6_all_six_cases_passed'])

    def test_fast_submissions_cannot_hide_slow_display(self):
        c,e=self.fixture(fps=19)
        self.assertFalse(assess(c,e,1000)['camera_20fps_passed'])

    def test_short_window_replays_and_reduced_geometry_fail(self):
        c,e=self.fixture(seconds=200)
        with self.assertRaises(ValueError): assess(c,e,1000)
        c,e=self.fixture()
        e[100]=copy.deepcopy(e[99])
        with self.assertRaises(ValueError): assess(c,e,1000)
        c,e=self.fixture()
        c['cases'][0]['sr_size']=[1280,720]
        with self.assertRaises(ValueError): assess(c,e,1000)

    def test_diagnostic_only_cannot_be_promoted(self):
        c,e=self.fixture()
        for case in c['cases']: case['stage']='diagnostic'
        self.assertFalse(assess(c,e,1000)['camera_20fps_passed'])

    def test_direct_scanout_requires_all_three_slots_with_exact_pixels(self):
        c,e=self.fixture()
        c['direct_display']=True
        with self.assertRaises(ValueError): assess(c,e,1000)
        c['direct_golden']=dict(passed=True,full_x_byte_validation=True,
            checks=[dict(slot=i%3,output_rgb_sha256='a',expected_rgb_sha256='a') for i in range(30)])
        self.assertTrue(assess(c,e,1000)['camera_20fps_passed'])
        c['direct_golden']['checks'][-1]['output_rgb_sha256']='b'
        with self.assertRaises(ValueError): assess(c,e,1000)

    def test_checked_copy_requires_both_layouts_and_exact_scanout(self):
        c,e=self.fixture()
        c['fused_copy_display']=True
        with self.assertRaises(ValueError):assess(c,e,1000)
        c['copy_golden']=dict(passed=True,full_x_byte_validation=True,checks=[
            dict(slot=i%3,layout=('sr','split')[i%2],output_rgb_sha256='a',
                 expected_rgb_sha256='a',exact_display_rgbx_match=True) for i in range(30)])
        self.assertTrue(assess(c,e,1000)['camera_20fps_passed'])
        c['copy_golden']['checks'][-1]['exact_display_rgbx_match']=False
        with self.assertRaises(ValueError):assess(c,e,1000)


if __name__=='__main__': unittest.main()
