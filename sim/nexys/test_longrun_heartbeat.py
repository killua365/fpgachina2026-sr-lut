"""Real Windows PowerShell writer / Python reader concurrency, without UART."""
import os
from pathlib import Path
import sys
import tempfile
import time
import unittest
ROOT=Path(__file__).resolve().parents[2];sys.path.insert(0,str(ROOT/'board/host'))
from longrun_heartbeat import latest
from longrun_process import OwnedProcess,powershell

class HeartbeatTests(unittest.TestCase):
    def test_partial_last_record_uses_previous_complete_record(self):
        with tempfile.TemporaryDirectory() as temp:
            p=Path(temp)/'heartbeat.log'
            for data,expected in [(b'',None),(b'123',None),(b'123\n',123),(b'123\n456',123),(b'123\r\n456\r\n',456)]:
                p.write_bytes(data);self.assertEqual(latest(p),expected)
    def test_tail_bound_and_malformed_record(self):
        with tempfile.TemporaryDirectory() as temp:
            p=Path(temp)/'heartbeat.log'
            p.write_bytes(b''.join((str(i)+'\n').encode() for i in range(2000)))
            self.assertEqual(latest(p),1999)
            for data in (b'-1\n',b'1e6\n',b'x\n',b'9'*21+b'\n'):
                p.write_bytes(data);self.assertIsNone(latest(p))
    @unittest.skipUnless(os.name=='nt','Windows PowerShell integration')
    def test_real_append_writer_survives_concurrent_open_readers(self):
        with tempfile.TemporaryDirectory(prefix='heartbeat_share_') as temp:
            folder=Path(temp).resolve();script=folder/'writer.ps1';target=folder/'heartbeat.log'
            script.write_text('''param([string]$Helper,[string]$Out)
$ErrorActionPreference='Stop'
. $Helper
$taskWriter=New-HeartbeatWriter -Path $Out
try {
    for($taskIndex=1;$taskIndex -le 20000;$taskIndex++) {
        Write-Heartbeat -Writer $taskWriter -Value $taskIndex
        if($taskIndex % 100 -eq 0){Start-Sleep -Milliseconds 1}
    }
} finally {$taskWriter.Dispose()}
''',encoding='utf-8')
            process=OwnedProcess(folder/'job',powershell(),['-NoProfile','-NonInteractive','-File',script,
                '-Helper',ROOT/'board/host/longrun_heartbeat.ps1','-Out',target],ROOT)
            reads=0;previous=0;held=None;deadline=time.monotonic()+40
            try:
                while process.poll() is None:
                    if time.monotonic()>deadline:self.fail('Writer timed out under readers')
                    if target.exists():
                        if held is None:held=target.open('rb')
                        value=latest(target)
                        if value is not None:
                            self.assertGreaterEqual(value,previous);previous=value;reads+=1
                    time.sleep(.001)
                self.assertEqual(process.poll(),0)
                self.assertGreater(reads,10)
                self.assertEqual(latest(target),20000)
                self.assertEqual(target.read_text('ascii').splitlines(),[str(i) for i in range(1,20001)])
            finally:
                if held:held.close()
                process.close()

if __name__=='__main__':unittest.main(verbosity=2)
