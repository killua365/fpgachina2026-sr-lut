"""Regress the observed stale export report at timed business shutdown."""
import json
from pathlib import Path
import sys
import time
from types import SimpleNamespace
import unittest
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[3]
sys.path.insert(0,str(ROOT/'src/ps'))
from hdmi_business_app import close_business_runtime
from test_hdmi_interactive_export import InteractiveExportTests


class FinalMediaTests(unittest.TestCase):
    def setUp(self):
        self.fixture=InteractiveExportTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.rt=self.fixture.rt

    def test_shutdown_during_probe_records_cancel_and_removes_partial(self):
        done=SimpleNamespace(returncode=0,poll=lambda:0)
        probe=SimpleNamespace(returncode=None)
        probe.poll=lambda:probe.returncode
        probe.terminate=lambda:setattr(probe,'returncode',-15)
        probe.wait=lambda **kwargs:probe.returncode
        with patch('hdmi_display.export.subprocess.Popen',side_effect=[done,probe]):
            self.fixture.start()
            for _ in range(self.fixture.clip.frame_count+2):self.rt.tick()
            self.assertEqual(self.rt.export_status['phase'],'verifying')
            report={'runtime':json.loads(json.dumps(self.rt.status()))}
            close_business_runtime(self.rt,report)
        final=report['runtime']['video_export']
        self.assertEqual((final['state'],final['reason']),('cancelled','runtime_close'))
        self.assertEqual(probe.returncode,-15)
        self.assertFalse((self.fixture.root/'video').exists())
        self.assertFalse(list(self.fixture.root.glob('.sr_export_*')))
        self.assertEqual(report['runtime']['video_export_history'][-1],final)
        self.assertTrue(report['runtime_finalized'])

    def test_completed_file_is_preserved_and_final_success_replaces_stale_sample(self):
        self.fixture.start()
        report={'runtime':json.loads(json.dumps(self.rt.status()))}
        deadline=time.monotonic()+30
        while self.rt.export_active and time.monotonic()<deadline:
            self.rt.tick();time.sleep(.005)
        self.assertEqual(self.rt.export_status['state'],'complete',self.rt.export_status)
        close_business_runtime(self.rt,report)
        self.assertEqual(report['runtime']['video_export']['state'],'complete')
        self.assertTrue(Path(report['runtime']['video_export']['report']).is_file())

    def test_failed_hardware_close_does_not_claim_finalization(self):
        report={}
        with patch.object(self.rt.engine,'close',side_effect=RuntimeError('DMA retained')):
            with self.assertRaisesRegex(RuntimeError,'DMA retained'):
                close_business_runtime(self.rt,report)
        self.assertNotIn('runtime_finalized',report)


if __name__=='__main__':unittest.main()
