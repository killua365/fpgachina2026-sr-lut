"""Real Windows job-tree test with harmless Python processes, no board access."""
import ctypes as C
from ctypes import wintypes as W
import sys,tempfile,time,unittest
from pathlib import Path
ROOT=Path(__file__).resolve().parents[2];sys.path.insert(0,str(ROOT/'board/host'))
from longrun_process import OwnedProcess,qpc_ns

class JobTests(unittest.TestCase):
    def test_close_kills_owned_descendant_and_clock_advances(self):
        with tempfile.TemporaryDirectory(prefix='nexys job test ') as tmp:
            root=Path(tmp);code=root/'child.py';marker=root/'pid.txt'
            code.write_text("import os,sys,time,subprocess\nfrom pathlib import Path\n"
                "if len(sys.argv)==2:\n p=subprocess.Popen([sys.executable,__file__,sys.argv[1],'child']);p.wait()\n"
                "else:\n Path(sys.argv[1]).write_text(str(os.getpid()));time.sleep(60)\n")
            proc=OwnedProcess(root/'job',sys.executable,[str(code),str(marker)],ROOT)
            handle=None;k=C.WinDLL('kernel32',use_last_error=True)
            k.OpenProcess.argtypes=[W.DWORD,W.BOOL,W.DWORD];k.OpenProcess.restype=W.HANDLE
            k.WaitForSingleObject.argtypes=[W.HANDLE,W.DWORD];k.WaitForSingleObject.restype=W.DWORD
            k.CloseHandle.argtypes=[W.HANDLE]
            try:
                deadline=time.monotonic()+15
                while not marker.exists() and time.monotonic()<deadline and proc.poll() is None:time.sleep(.1)
                self.assertTrue(marker.exists(),(root/'job/console.txt').read_text())
                handle=k.OpenProcess(0x100000,False,int(marker.read_text()));self.assertTrue(handle)
                first=qpc_ns();time.sleep(.02);self.assertGreater(qpc_ns(),first)
                proc.close();self.assertIsNotNone(proc.poll())
                self.assertEqual(k.WaitForSingleObject(handle,5000),0)
            finally:
                proc.close()
                if handle:k.CloseHandle(handle)

if __name__=='__main__':unittest.main(verbosity=2)
