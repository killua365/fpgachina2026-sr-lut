import hashlib
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from business_evidence_files import INVENTORY, validate_inventory, verify_copy, transfer_timeout

ROOT=Path(__file__).resolve().parents[2]


class EvidenceFilesTests(unittest.TestCase):
    def test_inventory_and_verification_stream_large_files_and_reject_changes(self):
        with tempfile.TemporaryDirectory(dir=ROOT/'build') as name:
            root=Path(name)
            (root/'worker').mkdir()
            for name in ('report.json','worker/report.json','legacy_restored.json'):
                (root/name).write_text(json.dumps({'worker_reaped':True}))
            raw=root/'worker/output.rgb'
            with raw.open('wb') as f:
                for _ in range(5): f.write(b'a'*(1024*1024))
            command=INVENTORY.replace('files=[]',
                "Path.read_bytes=lambda *a: (_ for _ in ()).throw(AssertionError('unbounded read'))\nfiles=[]")
            value=subprocess.run([sys.executable,'-B','-',str(root)],input=command,
                capture_output=True,text=True,check=True,timeout=20)
            files=validate_inventory(json.loads(value.stdout))
            with patch.object(Path,'read_bytes',side_effect=AssertionError('unbounded read')):
                verify_copy(root,files)
                with raw.open('r+b') as f: f.write(b'b')
                with self.assertRaisesRegex(ValueError,'bytes changed'): verify_copy(root,files)

    def test_transfer_budget_remains_bounded_for_retained_video(self):
        self.assertEqual(transfer_timeout([dict(size=100)]),180)
        self.assertGreater(transfer_timeout([dict(size=2*1024**3)]),1000)
        self.assertEqual(transfer_timeout([dict(size=100*1024**3)]),3600)
