"""Check relocated Tcl entry points without opening hardware or running Vivado."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import tkinter
import unittest

HERE = Path(__file__).resolve().parent


class PathTests(unittest.TestCase):
    def relocated(self, directory, name):
        root = Path(directory) / 'relocated project'
        candidate = root / 'build/candidates/nexys_vga_26102101'
        candidate.mkdir(parents=True)
        script = candidate / name
        shutil.copyfile(HERE / name, script)
        return root, script

    def test_loader_sources_host_tools_from_its_project(self):
        with tempfile.TemporaryDirectory() as directory:
            root, script = self.relocated(directory, 'load_candidate.tcl')
            tcl = tkinter.Tcl()
            tcl.eval('proc version {args} {return 2026.1}; proc exec {args} {return checked}')
            tcl.setvar('argc', 6)
            tcl.setvar('argv', tcl.call('list', 'python', 'candidate', str(root/'out'), 'control', 'package', 'a'*32))
            tcl.eval('rename source original_source; proc source {path} {set ::host_source $path; error STOP_BEFORE_HARDWARE}')
            with self.assertRaisesRegex(tkinter.TclError, 'STOP_BEFORE_HARDWARE'):
                tcl.call('original_source', str(script))
            self.assertEqual(Path(str(tcl.getvar('host_source'))).resolve(), (root/'board/host/longrun_hw.tcl').resolve())
            self.assertFalse((root/'out').exists())

    def test_incremental_build_uses_relocated_checkpoint(self):
        with tempfile.TemporaryDirectory() as directory:
            root, script = self.relocated(directory, 'build_incremental.tcl')
            tcl = tkinter.Tcl()
            tcl.eval('proc version {args} {return 2026.1}; proc set_param {args} {}; proc open_checkpoint {path} {set ::checkpoint $path; error STOP_BEFORE_BUILD}')
            with self.assertRaisesRegex(tkinter.TclError, 'STOP_BEFORE_BUILD'):
                tcl.call('source', str(script))
            self.assertEqual(Path(str(tcl.getvar('checkpoint'))).resolve(), (root/'build/reproduction_work/nexys_vga_26102101/route/synthesized.dcp').resolve())

    def test_vivado_environment_override(self):
        env = dict(os.environ, SR_LUT_VIVADO_BIN='D:/tools/custom vivado/bin', PYTHONDONTWRITEBYTECODE='1')
        code = "import sys,json;sys.path.insert(0,sys.argv[1]);import test_candidate;print(json.dumps(str(test_candidate.BIN)))"
        result = subprocess.run([sys.executable, '-B', '-c', code, str(HERE)], env=env,
                                capture_output=True, text=True, check=True)
        self.assertEqual(Path(json.loads(result.stdout)), Path(env['SR_LUT_VIVADO_BIN']))


if __name__ == '__main__':
    unittest.main()
