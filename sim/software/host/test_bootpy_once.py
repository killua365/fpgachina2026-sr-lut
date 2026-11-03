import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from types import SimpleNamespace
from contextlib import ExitStack

from legacy_service_rollback import require_bootpy_completed
from hdmi_business_app import restore_legacy
from install_bootpy_once import change_file, CONTENT
import install_bootpy_once as installer


def good():
    return dict(LoadState='loaded', Type='oneshot', RemainAfterExit='yes',
                ActiveState='active', SubState='exited', Result='success',
                ExecMainStatus='0', ExecMainCode='1', MainPID='0', ControlPID='0',
                ExecMainExitTimestampMonotonic='12345', cgroup_processes=[])


class BootOnceTests(unittest.TestCase):
    def test_successful_exited_initialization_is_not_a_live_owner(self):
        self.assertEqual(require_bootpy_completed(good()), good())

    def test_old_busy_failed_and_incomplete_states_are_rejected(self):
        invalid = [('RemainAfterExit','no'), ('ActiveState','inactive'), ('SubState','start'),
                   ('Result','timeout'), ('ExecMainStatus','1'), ('MainPID','123'),
                   ('ControlPID','123'), ('ExecMainExitTimestampMonotonic','0'),
                   ('cgroup_processes',['456']), ('Type','simple'), ('LoadState','not-found')]
        for key, value in invalid:
            with self.subTest(key=key):
                state=good();state[key]=value
                with self.assertRaises(RuntimeError):require_bootpy_completed(state)

    def test_install_is_idempotent_and_rollback_removes_only_own_file(self):
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'bootpy.service.d/90-sr-lut-once.conf'
            self.assertIsNone(change_file(path,'install'))
            self.assertEqual(path.read_bytes(),CONTENT)
            self.assertEqual(change_file(path,'install'),CONTENT)
            self.assertEqual(change_file(path,'rollback'),CONTENT)
            self.assertFalse(path.exists())
            self.assertIsNone(change_file(path,'rollback'))

    def test_foreign_dropin_is_preserved(self):
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'custom.conf';path.write_bytes(b'foreign')
            for action in ('install','rollback'):
                with self.assertRaises(RuntimeError):change_file(path,action)
                self.assertEqual(path.read_bytes(),b'foreign')

    def test_recovery_never_starts_service_with_uncompleted_boot(self):
        with tempfile.TemporaryDirectory() as directory, \
             patch('legacy_service_rollback.require_bootpy_completed', side_effect=RuntimeError('boot incomplete')), \
             patch('hdmi_business_app.subprocess.run') as command:
            report=dict(legacy_restored=False,errors=[])
            restore_legacy(Path(directory),report)
            command.assert_not_called()
            self.assertFalse(report['legacy_restored'])
            self.assertIn('boot incomplete',report['errors'][0])
            self.assertEqual(json.loads((Path(directory)/'recovery_status.json').read_text())['recovery_phase'],'restore_failed')

    def test_unexpected_boot_rerun_is_not_success(self):
        with tempfile.TemporaryDirectory() as directory, \
             patch('legacy_service_rollback.require_bootpy_completed', side_effect=[good(),dict(good(),ExecMainExitTimestampMonotonic='12346')]), \
             patch('legacy_service_rollback.active', side_effect=['inactive','active']), \
             patch('legacy_service_rollback.wait_status', return_value={}), \
             patch('hdmi_business_app.subprocess.run'):
            report=dict(legacy_restored=False,errors=[])
            restore_legacy(Path(directory),report)
            self.assertFalse(report['legacy_restored'])
            self.assertIn('reran',report['errors'][0])

    def installer_case(self, fail_reload=False):
        with tempfile.TemporaryDirectory() as directory, ExitStack() as stack:
            target=Path(directory)/'bootpy.service.d/90-sr-lut-once.conf'
            output=Path(directory)/'receipt.json'
            stack.enter_context(patch.object(installer,'TARGET',target))
            stack.enter_context(patch.object(installer,'PINS',{}))
            stack.enter_context(patch.object(installer.os,'geteuid',return_value=0,create=True))
            stack.enter_context(patch.object(installer,'root_card',return_value=installer.CID))
            stack.enter_context(patch.object(installer,'active',side_effect=['active','inactive']))
            stack.enter_context(patch.object(installer,'wait_status',return_value={'identity_verified':True}))
            original=Path.read_text
            stack.enter_context(patch.object(Path,'read_text',lambda p,*a,**kw:
                'test-boot' if p.as_posix()=='/proc/sys/kernel/random/boot_id' else original(p,*a,**kw)))
            calls=[]
            def systemctl(*args):
                calls.append(args)
                self.assertIn(args[0],('show','daemon-reload'))
                if args[0]=='daemon-reload':
                    return SimpleNamespace(returncode=int(fail_reload),stderr='injected reload failure')
                if '--value' in args:
                    return SimpleNamespace(returncode=0,stdout='yes\n')
                return SimpleNamespace(returncode=0,stdout='Type=oneshot\nMainPID=0\nControlPID=0\nActiveState=inactive\nSubState=dead\nDropInPaths=\n')
            stack.enter_context(patch.object(installer,'systemctl',side_effect=systemctl))
            if fail_reload:
                with self.assertRaisesRegex(RuntimeError,'daemon-reload failed'):
                    installer.run('install',output)
                self.assertFalse(target.exists())
            else:
                installer.run('install',output)
                self.assertEqual(target.read_bytes(),CONTENT)
            receipt=json.loads(output.read_text())
            self.assertEqual(receipt['passed'],not fail_reload)
            self.assertFalse(receipt['service_restart_performed'])
            self.assertFalse(receipt['reboot_performed'])

    def test_installer_does_not_start_services_or_reboot(self):
        self.installer_case()

    def test_installer_reverts_file_when_reload_fails(self):
        self.installer_case(True)


if __name__ == '__main__':unittest.main()
