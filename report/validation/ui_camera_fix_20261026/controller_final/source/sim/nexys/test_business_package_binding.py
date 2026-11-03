"""Reject stale launch records and missing or altered r3 package dependencies."""
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import business_session_gate as gate
from business_session_remote import validate_launch
from test_business_session import fixture


class PackageBindingTests(unittest.TestCase):
    def test_previous_launches_cannot_start_current_session(self):
        contract, launch, _ = fixture()
        for identity in ('1', '2'):
            previous = dict(package_sha256=identity*64, package_manifest_sha256=identity*64,
                            source_commit=identity*40, remote_package='/home/xilinx/stale_'+identity)
            self.assertNotEqual(contract['package_manifest_sha256'], previous['package_manifest_sha256'])
            stale = dict(launch, **{k: previous[k] for k in
                ('package_sha256', 'package_manifest_sha256', 'source_commit', 'remote_package')})
            with self.assertRaisesRegex(ValueError, 'Wrong source contract'):
                validate_launch(stale, contract)

    def test_inventory_and_all_python_modules_are_bound(self):
        with tempfile.TemporaryDirectory(dir=gate.ROOT/'build') as tmp:
            root = Path(tmp)
            hardware, package = root/'hardware', root/'package'
            hardware.mkdir(); package.mkdir()
            (hardware/'manifest.json').write_text('{"files": []}')
            (hardware/'result.json').write_text(json.dumps(dict(candidate_id='26101201',
                offline_tests_passed=True, complete_vdma_identity_simulation=True, bitstream_generated=True)))
            for name in ('app.py', 'helper.py'):
                (root/name).write_bytes(b'# fixture\n')
                (package/name).write_bytes(b'# fixture\n')
            (root/'package.zip').write_bytes(b'synthetic ZIP identity fixture')
            manifest = dict(source_commit='a'*40, files=[dict(path=n, sha256=gate.sha(package/n))
                for n in ('app.py', 'helper.py')])
            (package/'hdmi_business_package.json').write_text(json.dumps(manifest))
            contract = dict(archive='package.zip', package_sha256=gate.sha(root/'package.zip'),
                directory='package',package_manifest_sha256=gate.sha(package/'hdmi_business_package.json'),
                source_commit='a'*40,files=2,changed_sources=['app.py','helper.py'])
            def save(): (root/'contract.json').write_text(json.dumps(contract))
            save()
            with patch.multiple(gate, ROOT=root, HARDWARE=hardware, HARDWARE_SHA=gate.sha(hardware/'manifest.json'), SOURCE='contract.json'):
                gate.check_artifacts()
                (package/'helper.py').write_bytes(b'changed')
                with self.assertRaisesRegex(ValueError, 'Changed packaged file'): gate.check_artifacts()
                (package/'helper.py').write_bytes(b'# fixture\n')
                contract['changed_sources'] = ['app.py']; save()
                with self.assertRaisesRegex(ValueError, 'all packaged Python'): gate.check_artifacts()
                contract['changed_sources'].append('helper.py'); save()
                (root/'helper.py').write_bytes(b'unqualified source')
                with self.assertRaisesRegex(ValueError, 'differs from package'): gate.check_artifacts()
