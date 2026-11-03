import threading
import tempfile
from pathlib import Path
import unittest
from unittest.mock import patch
from hdmi_business_app import InteractionBudget
from business_session_policy import Monitor
from bounded_logs import BoundedLog


class DemoTests(unittest.TestCase):
    def test_normal_demo_skips_all_golden_work_and_writes_no_test_files(self):
        from hdmi_business_app import startup_checks
        with tempfile.TemporaryDirectory() as folder, patch('hdmi_business_app.smoke') as smoke:
            phases=[]
            result=startup_checks(None,None,None,Path(folder),threading.Event(),phases.append,'demo')
            smoke.assert_not_called()
            self.assertEqual(result['state'],'skipped')
            self.assertEqual((result['images'],result['video_frames']),([],0))
            self.assertEqual(phases,['initializing_demo'])
            self.assertEqual(list(Path(folder).iterdir()),[])

    def test_explicit_test_modes_keep_golden_checks(self):
        from hdmi_business_app import startup_checks
        for mode in ('timed','operator'):
            with patch('hdmi_business_app.smoke',return_value=dict(images=['checked'],video_frames=60)) as smoke:
                result=startup_checks(None,None,None,None,None,None,mode)
                smoke.assert_called_once()
                self.assertEqual((result['state'],result['video_frames']),('passed',60))

    def test_unlimited_budget_and_controller_cross_all_old_limits(self):
        now=[0.];cancel=threading.Event()
        budget=InteractionBudget(None,'demo',lambda:now[0])
        m=Monitor(0,'business','demo')
        m.tick(1,active=True);m.tick(31,active=True);m.tick(32,active=True,start_ack=True)
        for value in (1801,2401,2941,86401):
            now[0]=value
            self.assertTrue(budget.active(cancel))
            self.assertEqual(m.tick(value,active=True),[])
            self.assertEqual(m.failures,[])
        actions=m.tick(86402,active=True,close=True)
        self.assertIn('close_sample',actions)
        self.assertIn('stop_source',m.tick(86403,active=True,close_ack=True))
        self.assertIn('restore',m.tick(86404,source_verified=True,input_absent=True))
        self.assertIn('done',m.tick(86405,loader_done=True))
        cancel.set();self.assertFalse(budget.active(cancel))

    def test_unlimited_retains_start_and_stop_timeouts(self):
        m=Monitor(0,'business','demo');m.tick(181)
        self.assertIn('load_timeout',m.failures)
        m=Monitor(0,'business','demo');m.tick(1,active=True);m.tick(31,active=True)
        m.tick(32,start_ack=True);m.tick(33,close=True);m.tick(34,close_ack=True)
        m.tick(335)
        self.assertIn('source_stop_timeout',m.failures)

    def test_log_rotation_bounds_all_retained_files(self):
        with tempfile.TemporaryDirectory() as folder:
            p=Path(folder)/'demo.log'
            with BoundedLog(p,limit=64,copies=4) as log:
                for i in range(100):log.write('event %03d\n'%i);log.flush()
            files=list(Path(folder).glob('*'))
            self.assertLessEqual(len(files),4)
            self.assertTrue(all(x.stat().st_size<=64 for x in files))
            self.assertIn('099',p.read_text())

    def test_uart_demo_has_no_old_capture_deadline(self):
        from capture_ftdi_uart import capture
        from test_ftdi_capture import Device
        with tempfile.TemporaryDirectory() as folder:
            out=Path(folder)/'capture'
            device=Device(out,[(b'demo bytes',0)])
            now=iter((0,86401,86402))
            result=capture(out,{},device,lambda:1,1000,seconds=None,demo=True,
                           monotonic=lambda:next(now),sleep=lambda _:None)
            self.assertTrue(result['success'])
            self.assertEqual((out/'capture.bin').read_bytes(),b'demo bytes')


if __name__=='__main__':unittest.main()
