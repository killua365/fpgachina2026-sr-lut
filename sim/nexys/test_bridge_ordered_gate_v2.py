import json,sys,tempfile,unittest
from pathlib import Path
from unittest.mock import patch
ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT/'board/host'));sys.path.insert(0,str(ROOT/'board/pynq'))
import bridge_ordered_gate_v2 as gate
import hdmi_hardware_validation as hdmi
from bridge_ordered_remote import ZIP_SHA


class GateTests(unittest.TestCase):
    def check_case(self,fault=None):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp).resolve();host=root/'report/validation/host';host.mkdir(parents=True)
            package=root/'build/frozen/tools';package.mkdir(parents=True)
            tool=root/'tool.py';tool.write_text('original\n')
            r=dict(passed=True,hardware_access=False,contract=gate.CONTRACT,
                   modules={'fault_test.py':0},inputs=[gate.old.entry(tool,'tool.py')])
            if fault=='missing_tests':r['modules']={}
            if fault=='test_failed':r['modules']['fault_test.py']=1
            if fault=='hardware_scope':r['hardware_access']=True
            (host/'result.json').write_text(json.dumps(r))
            (host/'manifest.json').write_text(json.dumps(dict(files=[gate.old.entry(host/'result.json','result.json')])))
            manifest=dict(contract=gate.CONTRACT,host='report/validation/host',host_sha256=gate.old.sha(host/'manifest.json'),source_commit='f'*40)
            if fault=='wrong_host':manifest['host_sha256']='0'*64
            (package/'manifest.json').write_text(json.dumps(manifest))
            committed_tool=tool.read_bytes();committed_manifest=(host/'manifest.json').read_bytes()
            if fault=='source_changed':tool.write_text('modified\n')
            def git(args):
                if args[1].endswith(':tool.py'):return b'other\n' if fault=='not_committed' else committed_tool
                return committed_manifest
            with patch.object(gate,'ROOT',root),patch.object(gate,'INPUTS',('tool.py',)),patch.object(gate,'MODULES',('fault_test.py',)),\
                 patch.object(gate.old,'check_package'),patch.object(gate.old,'git',side_effect=git),\
                 patch.object(hdmi,'check_archive',return_value={'sha256':ZIP_SHA}):
                return gate.check(package)

    def test_gate_accepts_only_bound_offline_qualification(self):
        r=self.check_case();self.assertTrue(r['passed']);self.assertFalse(r['hardware_access']);self.assertFalse(r['n4_passed'])

    def test_missing_failed_or_wrong_scope_tests_reject(self):
        for fault in ('missing_tests','test_failed','hardware_scope'):
            with self.subTest(fault=fault),self.assertRaises(ValueError):self.check_case(fault)

    def test_changed_host_source_or_git_identity_reject(self):
        for fault in ('wrong_host','source_changed','not_committed'):
            with self.subTest(fault=fault),self.assertRaises(ValueError):self.check_case(fault)


if __name__=='__main__':unittest.main()
